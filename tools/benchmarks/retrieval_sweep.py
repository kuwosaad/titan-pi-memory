from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it",
    "of", "on", "or", "should", "that", "the", "to", "use", "we", "what", "when", "with",
}

LEARNING_PREFIXES = (
    "the user wants ",
    "the user requested ",
    "the user prefers ",
    "the user is ",
    "karu recommends ",
    "karu proposed ",
    "karu suggests ",
    "the biggest risk is ",
    "the simplest workflow ",
    "to evaluate retrieval, ",
    "the current retrieval logic ",
)


@dataclass(frozen=True)
class MemoryRow:
    id: str
    session_id: str
    turn: int
    text: str
    stream: str
    memory_type: str
    ts: str
    source_reliability: float


@dataclass(frozen=True)
class EvalCase:
    gold_id: str
    support_id: str
    session_id: str
    query: str
    gold_text: str
    support_text: str
    overlap: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded retrieval settings sweep against a shadow Titan memory store.")
    parser.add_argument("--live-db", required=True, help="Path to the source memory_store.db to copy into the isolated run.")
    parser.add_argument("--run-root", required=True, help="Night-run root directory for this sweep.")
    parser.add_argument("--sample-size", type=int, default=80, help="Maximum number of mined eval cases.")
    parser.add_argument("--max-turn-gap", type=int, default=8, help="Maximum turn gap between support and learning.")
    parser.add_argument("--min-overlap", type=int, default=2, help="Minimum lexical overlap for a mined pair.")
    parser.add_argument("--per-variant-limit", type=int, default=80, help="Max eval cases to score per variant.")
    return parser.parse_args()


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def content_tokens(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in STOPWORDS and len(token) > 2}


def normalize_learning_text(text: str) -> str:
    lowered = text.strip().lower()
    for prefix in LEARNING_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip() or text.strip()
    return text.strip()


def anchor_tokens(text: str) -> set[str]:
    anchors = set(re.findall(r"`([^`]+)`", text))
    anchors.update(re.findall(r"\b[a-zA-Z0-9_./:-]{6,}\b", text))
    return {anchor.lower() for anchor in anchors}


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.expanduser().resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_rows(db_path: Path) -> List[MemoryRow]:
    query = """
    SELECT id, session_id, turn, text, type, stream, ts, source_reliability
    FROM memories
    WHERE COALESCE(text, '') != ''
    ORDER BY session_id, turn, ts
    """
    rows: List[MemoryRow] = []
    with connect_readonly(db_path) as conn:
        for row in conn.execute(query).fetchall():
            rows.append(
                MemoryRow(
                    id=str(row["id"]),
                    session_id=str(row["session_id"]),
                    turn=int(row["turn"]),
                    text=str(row["text"]),
                    stream=str(row["stream"] or "rough"),
                    memory_type=str(row["type"] or "unknown"),
                    ts=str(row["ts"]),
                    source_reliability=float(row["source_reliability"] or 0.0),
                )
            )
    return rows


def mine_eval_cases(rows: Sequence[MemoryRow], sample_size: int, min_overlap: int, max_turn_gap: int) -> List[EvalCase]:
    by_session: Dict[str, List[MemoryRow]] = {}
    for row in rows:
        by_session.setdefault(row.session_id, []).append(row)

    mined: List[tuple[tuple[int, int, int], EvalCase]] = []
    seen_gold: set[str] = set()

    for session_id, session_rows in by_session.items():
        rough_rows = [row for row in session_rows if row.stream == "rough"]
        learnings = [row for row in session_rows if row.stream == "learnings"]
        for learning in learnings:
            if learning.id in seen_gold:
                continue
            gold_tokens = content_tokens(normalize_learning_text(learning.text))
            gold_anchors = anchor_tokens(learning.text)
            if len(gold_tokens) < 1 and not gold_anchors:
                continue

            best_support: Optional[MemoryRow] = None
            best_overlap = 0
            best_anchor_overlap = 0
            best_gap = 999999

            for rough in rough_rows:
                turn_gap = abs(learning.turn - rough.turn)
                if turn_gap > max_turn_gap:
                    continue
                overlap = len(gold_tokens & content_tokens(rough.text))
                anchor_overlap = len(gold_anchors & anchor_tokens(rough.text))
                if not (anchor_overlap >= 1 or overlap >= min_overlap or (turn_gap <= 1 and overlap >= 1)):
                    continue

                if (
                    anchor_overlap > best_anchor_overlap
                    or (anchor_overlap == best_anchor_overlap and overlap > best_overlap)
                    or (anchor_overlap == best_anchor_overlap and overlap == best_overlap and turn_gap < best_gap)
                ):
                    best_support = rough
                    best_overlap = overlap
                    best_anchor_overlap = anchor_overlap
                    best_gap = turn_gap

            if best_support is None:
                continue

            seen_gold.add(learning.id)
            mined.append(
                ((-best_anchor_overlap, -best_overlap, best_gap), EvalCase(
                    gold_id=learning.id,
                    support_id=best_support.id,
                    session_id=session_id,
                    query=best_support.text,
                    gold_text=learning.text,
                    support_text=best_support.text,
                    overlap=best_overlap,
                ))
            )

    mined.sort(key=lambda item: item[0])
    return [case for _, case in mined[:sample_size]]


def build_settings(base_settings: Dict[str, Any], variant: Dict[str, Any], shadow_db: Path) -> Dict[str, Any]:
    settings = dict(base_settings)
    retrieval = dict(base_settings.get("retrieval") or {})
    settings["memory_store_sqlite_path"] = str(shadow_db)
    settings["retrieval_top_k"] = variant["retrieval_top_k"]
    settings["retrieval_recency_days"] = variant["retrieval_recency_days"]
    settings["retrieval_session_bias"] = variant["retrieval_session_bias"]
    retrieval["min_reliability"] = variant["min_reliability"]
    settings["retrieval"] = retrieval
    return settings


def rank_metrics(ranks: List[Optional[int]], final_k: int) -> Dict[str, float]:
    total = len(ranks)
    present = [rank for rank in ranks if rank is not None]
    hit1 = sum(1 for rank in ranks if rank is not None and rank <= 1) / total if total else 0.0
    hit3 = sum(1 for rank in ranks if rank is not None and rank <= 3) / total if total else 0.0
    hitk = sum(1 for rank in ranks if rank is not None and rank <= final_k) / total if total else 0.0
    mrr = sum((1.0 / rank) for rank in present) / total if total else 0.0
    gold_present = len(present) / total if total else 0.0
    return {
        "cases": total,
        "gold_present_rate": gold_present,
        "hit_at_1": hit1,
        "hit_at_3": hit3,
        f"hit_at_{final_k}": hitk,
        "mrr": mrr,
    }


def score_variant(cases: Sequence[EvalCase], final_k: int) -> Dict[str, Any]:
    from app.retrieval_pipeline.retriever import retrieve_memories

    ranks: List[Optional[int]] = []
    examples: List[Dict[str, Any]] = []
    for case in cases:
        hits = retrieve_memories(
            query=case.query,
            session_id=case.session_id,
            mode="both",
            top_k=final_k,
        )
        ordered_ids = [str(hit.get("memory", {}).get("id")) for hit in hits]
        rank = None
        for index, memory_id in enumerate(ordered_ids, start=1):
            if memory_id == case.gold_id:
                rank = index
                break
        ranks.append(rank)
        if len(examples) < 8:
            examples.append({
                "query": case.query,
                "gold_id": case.gold_id,
                "rank": rank,
                "top_ids": ordered_ids,
            })
    return {
        "metrics": rank_metrics(ranks, final_k),
        "examples": examples,
    }


def main() -> int:
    args = parse_args()
    started = time.time()

    live_db = Path(args.live_db).expanduser()
    run_root = Path(args.run_root).expanduser()
    shadow_home = run_root / "shadow-home"
    output_dir = run_root / "artifacts"
    config_dir = run_root / "configs"
    output_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    (shadow_home / "out" / "memories").mkdir(parents=True, exist_ok=True)

    shadow_db = shadow_home / "out" / "memories" / "memory_store.db"
    shutil.copy2(live_db, shadow_db)

    base_settings_path = ROOT_DIR / "config" / "settings.yaml"
    base_settings = yaml.safe_load(base_settings_path.read_text(encoding="utf-8")) or {}

    rows = load_rows(shadow_db)
    cases = mine_eval_cases(rows, sample_size=args.sample_size, min_overlap=args.min_overlap, max_turn_gap=args.max_turn_gap)
    cases = cases[: args.per_variant_limit]

    variants: List[Dict[str, Any]] = [
        {
            "name": "baseline",
            "retrieval_top_k": 8,
            "retrieval_recency_days": 30,
            "retrieval_session_bias": True,
            "min_reliability": 0.4,
        },
        {
            "name": "larger_pool",
            "retrieval_top_k": 16,
            "retrieval_recency_days": 30,
            "retrieval_session_bias": True,
            "min_reliability": 0.4,
        },
        {
            "name": "no_session_bias",
            "retrieval_top_k": 8,
            "retrieval_recency_days": 30,
            "retrieval_session_bias": False,
            "min_reliability": 0.4,
        },
        {
            "name": "no_recency_limit",
            "retrieval_top_k": 8,
            "retrieval_recency_days": None,
            "retrieval_session_bias": True,
            "min_reliability": 0.4,
        },
        {
            "name": "low_reliability_gate",
            "retrieval_top_k": 8,
            "retrieval_recency_days": 30,
            "retrieval_session_bias": True,
            "min_reliability": 0.0,
        },
        {
            "name": "low_reliability_large_pool",
            "retrieval_top_k": 16,
            "retrieval_recency_days": 30,
            "retrieval_session_bias": True,
            "min_reliability": 0.0,
        },
        {
            "name": "low_reliability_no_session_bias",
            "retrieval_top_k": 8,
            "retrieval_recency_days": 30,
            "retrieval_session_bias": False,
            "min_reliability": 0.0,
        },
        {
            "name": "recall_combo",
            "retrieval_top_k": 16,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "min_reliability": 0.0,
        },
    ]

    os.environ["TITAN_BASE_DIR"] = str(shadow_home)
    os.environ["TITAN_HOME"] = str(shadow_home)
    os.environ["TITAN_OVERNIGHT_RUN"] = "1"
    os.environ["TITAN_OVERNIGHT_LABEL"] = run_root.name

    results: List[Dict[str, Any]] = []
    for variant in variants:
        settings_payload = build_settings(base_settings, variant, shadow_db)
        settings_path = config_dir / f"{variant['name']}.yaml"
        settings_path.write_text(yaml.safe_dump(settings_payload, sort_keys=False), encoding="utf-8")
        os.environ["TITAN_SETTINGS_PATH"] = str(settings_path)
        scored = score_variant(cases, final_k=int(variant["retrieval_top_k"]))
        results.append({
            "variant": variant,
            "settings_path": str(settings_path),
            "metrics": scored["metrics"],
            "examples": scored["examples"],
        })

    results.sort(key=lambda item: (item["metrics"]["hit_at_1"], item["metrics"]["mrr"], item["metrics"]["gold_present_rate"]), reverse=True)
    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 2),
        "run_root": str(run_root),
        "shadow_db": str(shadow_db),
        "live_db": str(live_db),
        "cases_mined": len(cases),
        "variants": results,
    }
    artifact_path = output_dir / "retrieval_sweep_results.json"
    artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps({
        "artifact": str(artifact_path),
        "cases": len(cases),
        "leaderboard": [
            {
                "name": item["variant"]["name"],
                "metrics": item["metrics"],
            }
            for item in results
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
