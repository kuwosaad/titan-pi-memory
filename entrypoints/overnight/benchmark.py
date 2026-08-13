"""
Retrieval benchmarking harness for overnight Titan runs.

Each benchmark run:
1. Sets up isolated TITAN_BASE_DIR
2. Runs pre-flight health checks
3. Executes manifest-defined queries
4. Records metrics (recall@k, gold-in-pool, etc.)
5. Logs all results to artifact directory

Strict constraints enforced:
- bounded runtime (max_hours, max_queries)
- narrow search space (top_k limits)
- no production writes
- all artifacts logged, never deleted
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from .manifest import HarnessManifest, load_manifest
from .health import run_all_health_checks

LOGGER = logging.getLogger(__name__)


class QueryTimeoutError(TimeoutError):
    pass


def _timeout_handler(signum: int, frame: object) -> None:
    raise QueryTimeoutError("query exceeded timeout")


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def recall_at_k(hits: List[Dict[str, Any]], gold_session_ids: List[str], k: int) -> float:
    """
    Compute recall@k: fraction of gold sessions represented in top-k results.
    A result 'represents' a gold session if its memory.session_id is in gold_session_ids.
    """
    if not gold_session_ids:
        return 1.0 if hits else 0.0

    retrieved_sessions = {hit.get("memory", {}).get("session_id") for hit in hits[:k]}
    gold_sessions = set(gold_session_ids)
    intersection = retrieved_sessions & gold_sessions
    return len(intersection) / len(gold_sessions)


def gold_in_pool_rate(hits: List[Dict[str, Any]], gold_session_ids: List[str]) -> float:
    """Fraction of gold sessions that appear anywhere in the result pool."""
    if not gold_session_ids:
        return 1.0 if hits else 0.0

    retrieved_sessions = {hit.get("memory", {}).get("session_id") for hit in hits}
    gold_sessions = set(gold_session_ids)
    intersection = retrieved_sessions & gold_sessions
    return len(intersection) / len(gold_sessions)


def theme_hit_rate(hits: List[Dict[str, Any]], expected_themes: List[str]) -> float:
    """
    Fraction of expected themes that appear in any retrieved memory text.
    Themes are matched case-insensitively as substrings.
    """
    if not expected_themes:
        return 1.0 if hits else 0.0

    all_text = " ".join(
        str(hit.get("memory", {}).get("text", "")).lower()
        for hit in hits
    )
    hits_count = sum(1 for theme in expected_themes if theme.lower() in all_text)
    return hits_count / len(expected_themes)


def _format_hit(hit: Dict[str, Any], index: int) -> Dict[str, Any]:
    mem = hit.get("memory", {})
    return {
        "rank": index + 1,
        "memory_id": mem.get("id"),
        "session_id": mem.get("session_id"),
        "score": float(hit.get("score", 0.0)),
        "text_preview": str(mem.get("text", ""))[:120],
        "stream": mem.get("stream"),
        "type": mem.get("type"),
    }


def _run_single_query(
    query: str,
    mode: str,
    top_k: int,
    session_id: Optional[str],
    gold_session_ids: List[str],
    expected_themes: List[str],
    timeout_seconds: float,
) -> Dict[str, Any]:
    """Execute one retrieval query and return scored results."""
    start = time.monotonic()
    error: Optional[str] = None
    hits: List[Dict[str, Any]] = []

    try:
        from app.retrieval_pipeline.retriever import retrieve_memories

        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        hits = retrieve_memories(
            query=query,
            session_id=session_id,
            mode=mode,
            top_k=top_k,
        )
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    except QueryTimeoutError as exc:
        error = str(exc)
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    result: Dict[str, Any] = {
        "query": query,
        "mode": mode,
        "top_k": top_k,
        "session_id": session_id,
        "elapsed_ms": elapsed_ms,
        "hit_count": len(hits),
        "hits": [_format_hit(h, i) for i, h in enumerate(hits[:top_k])],
        "error": error,
    }

    if not error:
        result["metrics"] = {
            "recall_at_k": recall_at_k(hits, gold_session_ids, top_k),
            "recall_at_20": recall_at_k(hits, gold_session_ids, min(20, len(hits))) if len(hits) >= 20 else None,
            "gold_in_pool_rate": gold_in_pool_rate(hits, gold_session_ids),
            "theme_hit_rate": theme_hit_rate(hits, expected_themes),
        }

    return result


def _summarize_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics across all query results."""
    recall_vals = [r.get("metrics", {}).get("recall_at_k") for r in results if "metrics" in r and r["metrics"].get("recall_at_k") is not None]
    gold_pool_vals = [r.get("metrics", {}).get("gold_in_pool_rate") for r in results if "metrics" in r and r["metrics"].get("gold_in_pool_rate") is not None]
    theme_hit_vals = [r.get("metrics", {}).get("theme_hit_rate") for r in results if "metrics" in r and r["metrics"].get("theme_hit_rate") is not None]
    elapsed_vals = [r.get("elapsed_ms", 0) for r in results if not r.get("error")]

    def _stats(vals: List[float]) -> Dict[str, Any]:
        if not vals:
            return {}
        return {
            "mean": float(np.mean(vals)),
            "std": float(np.std(vals)) if len(vals) > 1 else 0.0,
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            "count": len(vals),
        }

    return {
        "recall_at_k": _stats(recall_vals),
        "gold_in_pool_rate": _stats(gold_pool_vals),
        "theme_hit_rate": _stats(theme_hit_vals),
        "elapsed_ms": _stats(elapsed_vals) if elapsed_vals else {},
        "total_queries": len(results),
        "error_count": sum(1 for r in results if r.get("error")),
    }


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------


def run_overnight_benchmark(
    manifest_path: Optional[Path] = None,
    manifest: Optional[HarnessManifest] = None,
) -> Dict[str, Any]:
    """
    Top-level entry point for the overnight retrieval harness.

    Returns a results dict containing:
    - run_id, timestamp, manifest summary
    - health check results
    - per-benchmark results and summaries
    - artifact paths
    """
    if manifest is None:
        if manifest_path is None:
            manifest_path = load_manifest.__code__.co_filename
            manifest_path = Path(__file__).resolve().parents[2] / "config" / "overnight_manifest.yaml"
        manifest = load_manifest(manifest_path)

    run_id = f"{manifest.get('isolation', {}).get('label', 'run')}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    artifact_cfg = manifest.get("artifacts") or {}
    output_dir = Path(
        artifact_cfg.get("output_dir", str(Path.home() / ".titan-overnight" / "artifacts"))
    ).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_level_str = artifact_cfg.get("log_level", "INFO")
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    log_file = output_dir / f"overnight_{run_id}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    logging.basicConfig(
        level=log_level,
        handlers=[file_handler, logging.StreamHandler(sys.stdout)],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    LOGGER.info("=== Overnight Harness Run %s ===", run_id)
    LOGGER.info("Manifest version: %s", manifest.get("version"))
    LOGGER.info("Output directory: %s", output_dir)

    # Isolate from live Titan storage
    base_dir = Path(
        manifest.get("isolation", {}).get("base_dir", str(Path.home() / ".titan-overnight"))
    ).expanduser()
    env_vars = {
        "TITAN_BASE_DIR": str(base_dir),
        "TITAN_HOME": str(base_dir),
        "TITAN_OVERNIGHT_RUN": "1",
        "TITAN_OVERNIGHT_LABEL": str(manifest.get("isolation", {}).get("label", run_id)),
    }
    for key, val in env_vars.items():
        os.environ[key] = val
    LOGGER.info("Isolation env: %s", {k: v for k, v in env_vars.items()})

    # Pre-flight health checks
    health_result = run_all_health_checks(manifest)
    LOGGER.info("\n%s", health_result.summary())

    if not health_result.ok:
        LOGGER.warning("Health checks failed — proceeding anyway for artifact logging")

    # Warmup queries
    runtime_cfg = manifest.get("runtime") or {}
    warmup = runtime_cfg.get("warmup_queries", 3)
    if warmup > 0:
        LOGGER.info("Running %d warmup queries...", warmup)
        warmup_results: List[Dict[str, Any]] = []
        for i in range(warmup):
            try:
                from app.retrieval_pipeline.retriever import retrieve_memories

                r = retrieve_memories(query=f"warmup probe {i}", mode="both", top_k=4)
                warmup_results.append({"ok": True, "hit_count": len(r)})
            except Exception as exc:
                warmup_results.append({"ok": False, "error": str(exc)})
        LOGGER.info("Warmup complete: %s", warmup_results)

    # Run benchmarks
    benchmarks = manifest.get("benchmarks") or []
    runtime_cfg = manifest.get("runtime") or {}
    max_hours = runtime_cfg.get("max_hours", 3.0)
    max_queries = runtime_cfg.get("max_queries", 50)
    query_timeout = runtime_cfg.get("query_timeout_seconds", 30.0)
    start_time = time.monotonic()
    max_runtime_seconds = max_hours * 3600.0

    all_results: List[Dict[str, Any]] = []
    benchmark_summaries: List[Dict[str, Any]] = []

    for bench_def in benchmarks:
        bench_id = bench_def.get("id", "unknown")
        LOGGER.info("--- Benchmark: %s ---", bench_id)

        queries = bench_def.get("queries", [])
        bench_results: List[Dict[str, Any]] = []

        for q_idx, query_def in enumerate(queries):
            elapsed_since_start = time.monotonic() - start_time
            if elapsed_since_start >= max_runtime_seconds:
                LOGGER.warning("Deadline reached (%.1fh); stopping benchmark loop", max_hours)
                break
            if len(all_results) >= max_queries:
                LOGGER.warning("Max query limit (%d) reached", max_queries)
                break

            q_text = query_def.get("q", "")
            q_mode = query_def.get("mode", "both")
            q_top_k = query_def.get("top_k", 12)
            q_session_id = query_def.get("session_id")
            q_gold = query_def.get("gold_session_ids", [])
            q_themes = query_def.get("expected_themes", [])

            LOGGER.info("  Query %d: %s [mode=%s, k=%d]", q_idx + 1, q_text[:60], q_mode, q_top_k)
            qr = _run_single_query(
                query=q_text,
                mode=q_mode,
                top_k=q_top_k,
                session_id=q_session_id,
                gold_session_ids=q_gold,
                expected_themes=q_themes,
                timeout_seconds=query_timeout,
            )
            bench_results.append(qr)
            all_results.append(qr)

            if qr.get("error"):
                LOGGER.warning("  Query %d error: %s", q_idx + 1, qr["error"])
            else:
                metrics = qr.get("metrics", {})
                LOGGER.info(
                    "  Query %d: recall@%d=%.3f gold_pool=%.3f theme=%.3f",
                    q_idx + 1,
                    q_top_k,
                    metrics.get("recall_at_k", 0.0),
                    metrics.get("gold_in_pool_rate", 0.0),
                    metrics.get("theme_hit_rate", 0.0),
                )

        summary = _summarize_metrics(bench_results)
        benchmark_summaries.append({
            "benchmark_id": bench_id,
            "description": bench_def.get("description", ""),
            "query_count": len(bench_results),
            "metrics": summary,
        })
        LOGGER.info("  Summary: %s", json.dumps(summary, indent=2))

    # Aggregate summary
    aggregate = _summarize_metrics(all_results)

    # Write artifact JSON
    results_artifact = output_dir / f"results_{run_id}.json"
    run_record: Dict[str, Any] = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": int(time.monotonic() - start_time),
        "manifest_version": manifest.get("version"),
        "isolation_label": manifest.get("isolation", {}).get("label"),
        "health": health_result.as_dict(),
        "aggregate_metrics": aggregate,
        "benchmark_summaries": benchmark_summaries,
        "all_query_results": all_results,
        "artifacts": {
            "log_file": str(log_file),
            "results_json": str(results_artifact),
            "output_dir": str(output_dir),
        },
    }
    results_artifact.write_text(json.dumps(run_record, indent=2, default=str), encoding="utf-8")
    LOGGER.info("Results written to: %s", results_artifact)
    LOGGER.info("=== Overnight Harness Run %s Complete ===", run_id)

    return run_record
