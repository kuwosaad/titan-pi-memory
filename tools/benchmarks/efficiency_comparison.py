#!/usr/bin/env python3
"""Paired, isolated Titan efficiency comparison with deterministic providers.

The public ``run`` command launches every observation in a fresh Python process.
Fixtures, Titan state, and result artifacts must live outside both source trees.
No model endpoint, listener, or live Titan namespace is used.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import statistics
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping


BASELINE_REVISION = "ee6d6345e17bb0cdece53c1f5599ad88c7b0fa85"
RNG_SEED = 1729
DIMENSIONS = 768
TOPICS = (
    "database",
    "deployment",
    "authentication",
    "logging",
    "backup",
    "indexing",
    "caching",
    "routing",
    "encryption",
    "monitoring",
)
PROVIDER_STUB = {
    "kind": "deterministic_provider_stub",
    "algorithm": "numpy-pcg64-topic-centers",
    "seed": RNG_SEED,
    "dimensions": DIMENSIONS,
    "network_calls": False,
}
HEAVY_MODULE_SENTINELS = (
    "fastapi",
    "matplotlib",
    "mcp",
    "networkx",
    "numpy",
    "scipy",
    "sklearn",
    "torch",
    "uvicorn",
)
PROVIDER_ENV_MARKERS = (
    "API_KEY",
    "OPENAI",
    "OPENROUTER",
    "GEMINI",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OLLAMA_HOST",
)
FLOAT_ABS_TOLERANCE = 1e-12
FLOAT_REL_TOLERANCE = 1e-12
SNAPSHOT_PATHS = ("app", "entrypoints", "tests", "tools/benchmarks")
SNAPSHOT_UNTRACKED_SUFFIXES = {
    ".c", ".css", ".h", ".html", ".js", ".json", ".md", ".py", ".sh",
    ".toml", ".ts", ".tsx", ".yaml", ".yml",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _compact_sequence(value: list[Any], key: str | None) -> Any:
    """Hash large vector fields while retaining their full content identity."""

    if key in {"embedding", "query_embedding", "vector", "vectors"} and len(value) >= 32:
        normalized = [_normalize(item) for item in value]
        return {
            "__sequence_sha256__": _sha256_bytes(_json_bytes(normalized)),
            "length": len(value),
        }
    return [_normalize(item) for item in value]


def _normalize(value: Any, *, key: str | None = None) -> Any:
    """Convert project/numpy values into a complete deterministic JSON shape."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__float__": "nan"}
        if math.isinf(value):
            return {"__float__": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, bytes):
        return {"__bytes_sha256__": _sha256_bytes(value), "length": len(value)}
    if isinstance(value, (bytearray, memoryview)):
        raw = bytes(value)
        return {"__bytes_sha256__": _sha256_bytes(raw), "length": len(raw)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _normalize(item_value, key=str(item_key))
            for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return _compact_sequence(list(value), key)
    if isinstance(value, set):
        normalized = [_normalize(item) for item in value]
        return sorted(normalized, key=lambda item: _json_bytes(item))
    if hasattr(value, "dtype") and hasattr(value, "shape") and hasattr(value, "tobytes"):
        # Normalize arrays through values so an ndarray and an equivalent
        # public list compare alike; large embedding fields are still hashed.
        return _normalize(value.tolist(), key=key)
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump())
    if hasattr(value, "item"):
        try:
            return _normalize(value.item())
        except (TypeError, ValueError):
            pass
    return {"__type__": type(value).__qualname__, "repr": repr(value)}


def _fingerprint(value: Any) -> str:
    return _sha256_bytes(_json_bytes(_normalize(value)))


def _first_mismatch(left: Any, right: Any, path: str = "$") -> str | None:
    """Return the first structural/value mismatch; floats alone get a tiny tolerance."""

    if isinstance(left, bool) or isinstance(right, bool):
        return None if left is right else f"{path}: {left!r} != {right!r}"
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return None
        if math.isclose(left, right, abs_tol=FLOAT_ABS_TOLERANCE, rel_tol=FLOAT_REL_TOLERANCE):
            return None
        return f"{path}: {left!r} != {right!r}"
    if type(left) is not type(right):
        return f"{path}: types {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return f"{path}: keys {sorted(left)} != {sorted(right)}"
        for key in left:
            mismatch = _first_mismatch(left[key], right[key], f"{path}.{key}")
            if mismatch:
                return mismatch
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: lengths {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            mismatch = _first_mismatch(left_item, right_item, f"{path}[{index}]")
            if mismatch:
                return mismatch
        return None
    return None if left == right else f"{path}: {left!r} != {right!r}"


def _rss_mib() -> float:
    output = subprocess.check_output(
        ["/bin/ps", "-o", "rss=", "-p", str(os.getpid())], text=True
    ).strip()
    return int(output) / 1024


def _peak_rss_mib() -> float:
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maximum / (1024 * 1024 if sys.platform == "darwin" else 1024)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[int(percentile * (len(ordered) - 1))]


def _topic_centers():
    import numpy as np

    rng = np.random.default_rng(RNG_SEED)
    centers = rng.normal(size=(len(TOPICS), DIMENSIONS)).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    return centers


class DeterministicEmbeddingStub:
    def __init__(self) -> None:
        self.centers = _topic_centers()
        self.calls: list[list[str]] = []

    def reset(self) -> None:
        self.calls.clear()

    def __call__(self, texts: Iterable[str]):
        import numpy as np

        batch = [str(text) for text in texts]
        self.calls.append(batch)
        vectors = []
        for text in batch:
            lowered = text.lower()
            topic_index = next((index for index, topic in enumerate(TOPICS) if topic in lowered), None)
            if topic_index is not None:
                vectors.append(self.centers[topic_index].copy())
                continue
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            seed = int.from_bytes(digest[:8], "big")
            rng = np.random.default_rng(seed)
            vector = rng.normal(size=DIMENSIONS).astype(np.float32)
            vector /= np.linalg.norm(vector)
            vectors.append(vector)
        return vectors

    def report(self) -> dict[str, Any]:
        return {
            "identity": PROVIDER_STUB,
            "calls": len(self.calls),
            "texts": sum(len(batch) for batch in self.calls),
            "texts_per_call": [len(batch) for batch in self.calls],
            "input_fingerprint": _fingerprint(self.calls),
        }


def _queries() -> list[str]:
    return [f"What is the {topic} specification?" for topic in TOPICS]


def _install_root(root: Path) -> None:
    sys.path.insert(0, str(root))


def _seed_memories(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    import numpy as np
    from app.storage.memories import SqliteMemoryRepository

    # Match the earlier bbb454b -> ee6d634 fixture exactly: one RNG creates the
    # centers, then continues through row noise and synthetic words.
    rng = np.random.default_rng(RNG_SEED)
    centers = rng.normal(size=(len(TOPICS), DIMENSIONS)).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    repository = SqliteMemoryRepository(args.db)
    rows = []
    for index in range(args.offset + args.count):
        noise = rng.normal(size=DIMENSIONS).astype(np.float32)
        noise /= np.linalg.norm(noise)
        vector = 0.8 * centers[index % len(TOPICS)] + 0.6 * noise
        vector /= np.linalg.norm(vector)
        words = [f"spec{int(value)}" for value in rng.integers(0, 1_000_000, size=6)]
        if index < args.offset:
            continue
        local_index = index - args.offset
        prior_shape = bool(args.prior_shape)
        rows.append(
            {
                "id": f"{args.prefix}:0:{index}",
                "session_id": "bench" if prior_shape else f"{args.prefix}-session",
                "turn": 0 if prior_shape else index,
                "scene_id": f"scene-{index}" if prior_shape else f"{args.prefix}-scene-{index}",
                "text": f"{TOPICS[index % len(TOPICS)]} " + " ".join(words),
                "type": "fact",
                "stream": "learnings",
                "embedding": vector.tolist(),
                "ts": "2026-09-01T00:00:00+00:00",
                "source_reliability": 0.9,
                "source_type": "user",
                "provenance": {} if prior_shape else {
                    "user": f"synthetic fixture request {index}",
                    "assistant": f"synthetic fixture response {index}",
                },
                "tau": 0.5,
                "h": 0.0,
                "source_event_ids": [] if prior_shape else [f"event-{index}"],
                "outgoing_weights": (
                    {
                        f"{args.prefix}:0:{args.offset + ((local_index + delta + 1) % args.count)}": 0.2
                        for delta in range(4)
                    }
                    if local_index % 4 == 0
                    else None
                ),
            }
        )
    repository.append_memories(rows)
    with repository._connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {
        "action": "seed-memories",
        "count": repository.get_memory_count(),
        "db": str(args.db),
        "sha256": _sha256_file(args.db),
    }


def _seed_scenes(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    from app.storage.scenes import SqliteSceneRepository

    repository = SqliteSceneRepository(args.db)
    scenes = []
    for index in range(args.count):
        scene_id = f"{args.prefix}-scene-{index}"
        event_id = f"{args.prefix}-event-{index}"
        scenes.append(
            {
                "scene_id": scene_id,
                "session_id": f"{args.prefix}-session",
                "turn": index,
                "kind": "message_exchange",
                "scene_seq": index + 1,
                "start_event_seq": index + 1,
                "end_event_seq": index + 1,
                "anchor_event_id": event_id,
                "ts": "2026-09-01T00:00:00+00:00",
                "source_event_ids": [event_id],
                "raw_events": [
                    {
                        "seq": index + 1,
                        "session_id": f"{args.prefix}-session",
                        "event_id": event_id,
                        "event_type": "user_message",
                        "payload": {"content": f"scene {index}"},
                    }
                ],
                "evidence_version": 1,
                "evidence_status": "complete",
                "missing_source_event_ids": [],
                "messages": [
                    {
                        "role": "user",
                        "content": f"scene {index}",
                        "message_id": f"message-{index}",
                        "event_id": event_id,
                    }
                ],
                "tool_calls": [],
                "extraction_user_text": f"scene {index}",
                "extraction_assistant_text": "fixture response",
                "used_context_fallback": False,
            }
        )
    repository.append_scenes(scenes)
    return {
        "action": "seed-scenes",
        "count": len(scenes),
        "db": str(args.db),
        "sha256": _sha256_file(args.db),
    }


def _timed_queries(
    operation: Callable[[str], Any],
    *,
    warmups: int,
    queries: int,
    stub: DeterministicEmbeddingStub,
) -> dict[str, Any]:
    schedule = _queries()
    for index in range(warmups):
        operation(schedule[index % len(schedule)])
    warm_rss = _rss_mib()
    stub.reset()
    latencies = []
    outputs = []
    cpu_seconds = 0.0
    wall_seconds = 0.0
    for index in range(queries):
        cpu_start = time.process_time()
        wall_start = time.perf_counter()
        raw_output = operation(schedule[index % len(schedule)])
        wall_elapsed = time.perf_counter() - wall_start
        cpu_elapsed = time.process_time() - cpu_start
        wall_seconds += wall_elapsed
        cpu_seconds += cpu_elapsed
        latencies.append(wall_elapsed * 1000)
        # Normalize only after the measured operation. Retaining compact
        # normalized outputs proves equivalence without retaining every full
        # 768d result graph and contaminating the RSS observation.
        outputs.append(_normalize(raw_output))
    return {
        "cpu_ms_per_query": cpu_seconds * 1000 / queries,
        "wall_ms_per_query": wall_seconds * 1000 / queries,
        "latency_median_ms": statistics.median(latencies),
        "latency_p95_ms_descriptive": _percentile(latencies, 0.95),
        "rss_warm_mib": warm_rss,
        "rss_after_mib": _rss_mib(),
        "peak_rss_mib": _peak_rss_mib(),
        "outputs": outputs,
        "output_digests": [_fingerprint(output) for output in outputs],
        "provider": stub.report(),
    }


def _measure_local(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    import entrypoints.main  # retain the prior benchmark's service import footprint
    from app.retrieval_pipeline import retriever
    from app.storage.memories import SqliteMemoryRepository

    repository = SqliteMemoryRepository(args.db, initialize=False)
    stub = DeterministicEmbeddingStub()
    retriever.embed = stub
    metrics = _timed_queries(
        lambda query: retriever.retrieve_memories(query, repository=repository),
        warmups=args.warmups,
        queries=args.queries,
        stub=stub,
    )
    return {
        "mode": "local-retrieval",
        "timing_scope": "retrieval-operation-only-normalization-excluded",
        "count": args.count,
        **metrics,
    }


def _measure_federated(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    from app.retrieval_pipeline import retriever
    from app.retrieval_pipeline.federated import FederatedRecall

    paths = {
        item.split("=", 1)[0]: Path(item.split("=", 1)[1])
        for item in args.source_db
    }
    active_agent = next(iter(paths))
    recall = FederatedRecall(
        active_agent=active_agent,
        memory_paths=paths,
        include_active_buffer=False,
    )
    stub = DeterministicEmbeddingStub()
    retriever.embed = stub
    metrics = _timed_queries(
        lambda query: recall.query_hits(query, limit=8, sources=list(paths)),
        warmups=args.warmups,
        queries=args.queries,
        stub=stub,
    )
    return {
        "mode": "federated-retrieval",
        "timing_scope": "retrieval-operation-only-normalization-excluded",
        "count": args.count,
        "sources": list(paths),
        **metrics,
    }


class _CountedSceneRepository:
    def __init__(self, repository: Any) -> None:
        self.repository = repository
        self.reference_calls = 0
        self.reference_ids = 0

    def get_scene_references(self, scene_ids: list[str]) -> list[dict[str, Any]]:
        self.reference_calls += 1
        self.reference_ids += len(scene_ids)
        return self.repository.get_scene_references(scene_ids)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)


def _measure_scene_refs(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    from app.retrieval_pipeline.federated import FederatedRecall
    from app.storage.scenes import SqliteSceneRepository

    paths = {
        item.split("=", 1)[0]: Path(item.split("=", 1)[1])
        for item in args.source_db
    }
    repositories = {
        source: _CountedSceneRepository(SqliteSceneRepository(path, initialize=False))
        for source, path in paths.items()
    }
    active_agent = next(iter(paths))
    recall = FederatedRecall(active_agent=active_agent, scene_repositories=repositories)
    memories = []
    for source in paths:
        memories.extend(
            {"id": f"{source}-memory-{index}", "scene_id": f"{source}-scene-{index}", "source_agent": source}
            for index in range(args.scene_ids)
        )
        memories.append(dict(memories[-1]))  # duplicate pointer must not duplicate output
        memories.append(
            {"id": f"{source}-missing", "scene_id": f"{source}-scene-missing", "source_agent": source}
        )

    outputs = []
    latencies = []
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    for _ in range(args.iterations):
        started = time.perf_counter()
        outputs.append(_normalize(recall.scene_references(memories, sources=list(paths))))
        latencies.append((time.perf_counter() - started) * 1000)
    cpu = time.process_time() - cpu_start
    wall = time.perf_counter() - wall_start
    return {
        "mode": "scene-references",
        "timing_scope": "aggregate-includes-output-normalization",
        "sources": list(paths),
        "scene_ids_per_source": args.scene_ids,
        "iterations": args.iterations,
        "cpu_ms_per_iteration": cpu * 1000 / args.iterations,
        "wall_ms_per_iteration": wall * 1000 / args.iterations,
        "latency_median_ms": statistics.median(latencies),
        "repository_calls": sum(repo.reference_calls for repo in repositories.values()),
        "requested_ids": sum(repo.reference_ids for repo in repositories.values()),
        "outputs": outputs,
        "output_digests": [_fingerprint(output) for output in outputs],
        "rss_after_mib": _rss_mib(),
        "peak_rss_mib": _peak_rss_mib(),
    }


def _semantic_cursor(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            (Path(key).name if os.path.isabs(str(key)) else key): _semantic_cursor(item)
            for key, item in value.items()
            if key != "updated_at"
        }
    if isinstance(value, list):
        return [_semantic_cursor(item) for item in value]
    return value


def _measure_idle_cursor(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    from app.storage import traces

    spool_dir = Path(os.environ["TITAN_SPOOL_DIR"])
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool_file = spool_dir / "idle-session.jsonl"
    spool_file.write_bytes(b" \n")
    os.utime(spool_file, ns=(1_800_000_000_000_000_000, 1_800_000_000_000_000_000))
    traces.ingest_spool_file("idle-session", spool_dir)  # prime the EOF cursor

    original_save = traces.save_spool_cursors
    save_calls = 0

    def counted_save(cursors: dict[str, Any]) -> None:
        nonlocal save_calls
        save_calls += 1
        original_save(cursors)

    traces.save_spool_cursors = counted_save
    outputs = []
    cursor_transitions = 0
    previous_digest = _sha256_file(traces.SPOOL_CURSOR_FILE)
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    for _ in range(args.iterations):
        outputs.append(_normalize(traces.ingest_spool_file("idle-session", spool_dir)))
        current_digest = _sha256_file(traces.SPOOL_CURSOR_FILE)
        cursor_transitions += int(current_digest != previous_digest)
        previous_digest = current_digest
    cpu = time.process_time() - cpu_start
    wall = time.perf_counter() - wall_start
    stable_cursor = _semantic_cursor(traces.load_spool_cursors())
    idle_save_calls = save_calls

    spool_file.write_bytes(b"\n")
    os.utime(spool_file, ns=(1_800_000_001_000_000_000, 1_800_000_001_000_000_000))
    truncated = _normalize(traces.ingest_spool_file("idle-session", spool_dir))
    spool_file.write_bytes(b"X\n")
    os.utime(spool_file, ns=(1_800_000_002_000_000_000, 1_800_000_002_000_000_000))
    changed_in_place = _normalize(traces.ingest_spool_file("idle-session", spool_dir))
    return {
        "mode": "idle-cursor",
        "scope": "synthetic-blank-spool-ingest_spool_file-microbenchmark-not-full-worker-idle",
        "timing_scope": "aggregate-includes-normalization-and-cursor-sha256-reads",
        "iterations": args.iterations,
        "cpu_ms_per_tick": cpu * 1000 / args.iterations,
        "wall_ms_per_tick": wall * 1000 / args.iterations,
        "estimated_cpu_percent_one_core_at_3s": (cpu * 1000 / args.iterations) / 30,
        "cursor_save_calls": idle_save_calls,
        "cursor_file_transitions": cursor_transitions,
        "outputs": outputs,
        "output_digests": [_fingerprint(output) for output in outputs],
        "semantic_cursor": _normalize(stable_cursor),
        "truncation_case": truncated,
        "in_place_change_case": changed_in_place,
        "rss_after_mib": _rss_mib(),
        "peak_rss_mib": _peak_rss_mib(),
    }


def _loaded_sentinels() -> list[str]:
    return [name for name in HEAVY_MODULE_SENTINELS if name in sys.modules]


def _measure_import(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    before = set(sys.modules)
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    importlib.import_module(args.import_target)
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    imported = sorted(set(sys.modules) - before)
    return {
        "mode": "startup-import",
        "target": args.import_target,
        "cpu_ms": cpu * 1000,
        "wall_ms": wall * 1000,
        "rss_after_mib": _rss_mib(),
        "peak_rss_mib": _peak_rss_mib(),
        "new_module_count": len(imported),
        "heavy_module_sentinels": _loaded_sentinels(),
        "imported_module_digest": _fingerprint(imported),
    }


def _measure_first_request(args: argparse.Namespace) -> dict[str, Any]:
    _install_root(args.root)
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    module = importlib.import_module("entrypoints.mcp_server")
    import_wall = time.perf_counter() - wall_start
    import_cpu = time.process_time() - cpu_start

    from app.retrieval_pipeline import retriever

    stub = DeterministicEmbeddingStub()
    retriever.embed = stub
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    output = asyncio.run(
        module.query_memories(query=_queries()[0], limit=8, sources=[os.environ["TITAN_AGENT_NAME"]])
    )
    request_wall = time.perf_counter() - wall_start
    request_cpu = time.process_time() - cpu_start
    normalized = _normalize(output)
    return {
        "mode": "mcp-first-request",
        "import_cpu_ms": import_cpu * 1000,
        "import_wall_ms": import_wall * 1000,
        "first_request_cpu_ms": request_cpu * 1000,
        "first_request_wall_ms": request_wall * 1000,
        "rss_after_mib": _rss_mib(),
        "peak_rss_mib": _peak_rss_mib(),
        "heavy_module_sentinels": _loaded_sentinels(),
        "output": normalized,
        "output_digest": _fingerprint(normalized),
        "provider": stub.report(),
    }


def _git_output(root: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip())
    return process.stdout


def _snapshot(root: Path) -> dict[str, Any]:
    # Limit snapshot metadata to the runtime/test/harness surfaces in scope.
    # In particular, never enumerate or hash unrelated personal/untracked files.
    diff = _git_output(root, "diff", "HEAD", "--binary", "--", *SNAPSHOT_PATHS)
    untracked_raw = _git_output(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *SNAPSHOT_PATHS,
    )
    untracked = [
        item
        for item in untracked_raw.split("\0")
        if item and Path(item).suffix.lower() in SNAPSHOT_UNTRACKED_SUFFIXES
    ]
    untracked_digest = hashlib.sha256()
    for relative in sorted(untracked):
        path = root / relative
        untracked_digest.update(relative.encode("utf-8") + b"\0")
        if path.is_symlink():
            untracked_digest.update(b"<symlink-not-read>")
        elif path.is_file():
            untracked_digest.update(path.read_bytes())
    return {
        "root": str(root),
        "head": _git_output(root, "rev-parse", "HEAD").strip(),
        "status": _git_output(root, "status", "--short", "--", *SNAPSHOT_PATHS).splitlines(),
        "tracked_diff_sha256": _sha256_bytes(diff.encode("utf-8")),
        "untracked_files": untracked,
        "untracked_sha256": untracked_digest.hexdigest(),
    }


def _snapshot_identity(snapshot: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        snapshot["head"],
        snapshot["tracked_diff_sha256"],
        tuple(snapshot["untracked_files"]),
        snapshot["untracked_sha256"],
    )


def _child_environment(
    *,
    root: Path,
    state: Path,
    settings: Path,
    memory_db: Path | None = None,
) -> dict[str, str]:
    environment = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("TITAN_") or any(marker in upper for marker in PROVIDER_ENV_MARKERS):
            continue
        environment[key] = value
    agent_home = state / "shared" / "agents" / "benchmark"
    environment.update(
        {
            "TITAN_HOME": str(agent_home),
            "TITAN_SHARED_HOME": str(state / "shared"),
            "TITAN_BASE_DIR": str(agent_home),
            "TITAN_RUNTIME_DIR": str(state / "runtime"),
            "TITAN_AGENT_NAME": "benchmark",
            "TITAN_SPOOL_DIR": str(state / "spool"),
            "TITAN_SETTINGS_PATH": str(settings),
            "TITAN_AUTO_INGEST_ENABLED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    if memory_db is not None:
        environment["TITAN_MEMORY_DB_PATH"] = str(memory_db)
    return environment


def _invoke(
    args: argparse.Namespace,
    *,
    action: str,
    root: Path,
    state: Path,
    settings: Path,
    extra: list[str],
    memory_db: Path | None = None,
) -> dict[str, Any]:
    state.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.python),
        str(Path(__file__).resolve()),
        "child",
        "--action",
        action,
        "--root",
        str(root),
        *extra,
    ]
    process = subprocess.run(
        command,
        cwd=root,
        env=_child_environment(root=root, state=state, settings=settings, memory_db=memory_db),
        text=True,
        capture_output=True,
        timeout=args.timeout,
    )
    if process.returncode:
        raise RuntimeError(
            f"child failed ({action}, {root}):\nSTDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
        )
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"child produced no JSON ({action}, {root})")
    return json.loads(lines[-1])


def _seed(
    args: argparse.Namespace,
    *,
    baseline: Path,
    settings: Path,
    action: str,
    db: Path,
    count: int,
    prefix: str,
    offset: int = 0,
    prior_shape: bool = False,
) -> dict[str, Any]:
    return _invoke(
        args,
        action=action,
        root=baseline,
        state=args.artifact_dir / "seed-state" / prefix,
        settings=settings,
        extra=[
            "--db",
            str(db),
            "--count",
            str(count),
            "--prefix",
            prefix,
            "--offset",
            str(offset),
            *(["--prior-shape"] if prior_shape else []),
        ],
        memory_db=db,
    )


def _paired_order(trial: int) -> tuple[str, str]:
    return ("baseline", "candidate") if trial % 2 == 0 else ("candidate", "baseline")


def _result_payload(result: dict[str, Any]) -> Any:
    mode = result["mode"]
    if mode in {"local-retrieval", "federated-retrieval", "scene-references", "idle-cursor"}:
        payload = {"outputs": result["outputs"]}
        if mode == "idle-cursor":
            payload.update(
                {
                    "semantic_cursor": result["semantic_cursor"],
                    "truncation_case": result["truncation_case"],
                    "in_place_change_case": result["in_place_change_case"],
                }
            )
        return payload
    if mode == "mcp-first-request":
        return result["output"]
    return None


def _assert_equivalent(baseline: dict[str, Any], candidate: dict[str, Any], label: str) -> None:
    left = _result_payload(baseline)
    right = _result_payload(candidate)
    if left is None and right is None:
        return
    mismatch = _first_mismatch(left, right)
    if mismatch:
        raise RuntimeError(f"equivalence failure for {label}: {mismatch}")


def _metric_summary(pairs: list[tuple[float, float]], *, lower_is_better: bool = True) -> dict[str, Any]:
    ratios = [candidate / baseline for baseline, candidate in pairs if baseline != 0]
    differences = [candidate - baseline for baseline, candidate in pairs]
    wins = sum(candidate < baseline if lower_is_better else candidate > baseline for baseline, candidate in pairs)
    median_ratio = statistics.median(ratios)
    return {
        "pairs": len(pairs),
        "wins": wins,
        "median_candidate_to_baseline_ratio": median_ratio,
        "median_change_percent": (median_ratio - 1) * 100,
        "ratio_min": min(ratios),
        "ratio_max": max(ratios),
        "median_absolute_difference": statistics.median(differences),
        "robust_direction_8_of_9": len(pairs) >= 9 and wins >= 8,
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    metric_names = {
        "local-retrieval": ("cpu_ms_per_query", "wall_ms_per_query", "rss_after_mib", "peak_rss_mib"),
        "federated-retrieval": ("cpu_ms_per_query", "wall_ms_per_query", "rss_after_mib", "peak_rss_mib"),
        "scene-references": ("cpu_ms_per_iteration", "wall_ms_per_iteration", "repository_calls"),
        "idle-cursor": ("cpu_ms_per_tick", "wall_ms_per_tick", "cursor_save_calls"),
        "startup-import": ("cpu_ms", "wall_ms", "rss_after_mib", "peak_rss_mib", "new_module_count"),
        "mcp-first-request": ("import_cpu_ms", "import_wall_ms", "first_request_cpu_ms", "first_request_wall_ms", "rss_after_mib", "peak_rss_mib"),
    }
    groups: dict[tuple[Any, ...], dict[str, dict[int, dict[str, Any]]]] = {}
    for result in results:
        key = (result["mode"], result.get("count"), result.get("target"))
        groups.setdefault(key, {}).setdefault(result["version"], {})[int(result["trial"])] = result
    for key, versions in groups.items():
        common_trials = sorted(set(versions.get("baseline", {})) & set(versions.get("candidate", {})))
        mode = str(key[0])
        label = ":".join(str(part) for part in key if part is not None)
        summary[label] = {}
        for metric in metric_names[mode]:
            pairs = [
                (
                    float(versions["baseline"][trial][metric]),
                    float(versions["candidate"][trial][metric]),
                )
                for trial in common_trials
            ]
            summary[label][metric] = _metric_summary(pairs)
            metric_summary = summary[label][metric]
            if metric in {
                "cpu_ms_per_query",
                "wall_ms_per_query",
                "cpu_ms_per_iteration",
                "wall_ms_per_iteration",
                "cpu_ms_per_tick",
                "wall_ms_per_tick",
                "import_cpu_ms",
                "import_wall_ms",
                "first_request_cpu_ms",
                "first_request_wall_ms",
            }:
                metric_summary["headline_eligible"] = bool(
                    metric_summary["robust_direction_8_of_9"]
                    and metric_summary["median_candidate_to_baseline_ratio"] <= 0.90
                )
            if metric in {"rss_after_mib", "peak_rss_mib"}:
                metric_summary["headline_eligible"] = bool(
                    metric_summary["robust_direction_8_of_9"]
                    and metric_summary["median_absolute_difference"] <= -5.0
                )
        if mode in {"local-retrieval", "federated-retrieval"}:
            summary[label]["provider_calls"] = {
                version: [versions[version][trial]["provider"]["calls"] for trial in common_trials]
                for version in ("baseline", "candidate")
            }
    return summary


def _record(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def _require_clean_project_bytecode(root: Path) -> None:
    # PYTHONDONTWRITEBYTECODE prevents writes, not reads. Unequal valid caches
    # bias startup CPU and can leave compiler allocations in retained RSS.
    for package in ("app", "entrypoints"):
        cached = next((root / package).rglob("*.pyc"), None)
        if cached is not None:
            raise ValueError(
                f"project bytecode cache found: {cached}; benchmark fresh source "
                "worktrees without __pycache__/*.pyc, using the same dependency venv"
            )


def _orchestrate(args: argparse.Namespace) -> int:
    baseline = args.baseline_root.expanduser().resolve()
    candidate = args.candidate_root.expanduser().resolve()
    artifact = args.artifact_dir.expanduser().resolve()
    for root in (baseline, candidate):
        if artifact == root or root in artifact.parents:
            raise ValueError("--artifact-dir must be outside both source roots")
        _require_clean_project_bytecode(root)
    baseline_snapshot = _snapshot(baseline)
    if baseline_snapshot["head"] != BASELINE_REVISION:
        raise ValueError(f"baseline must be exact {BASELINE_REVISION}, got {baseline_snapshot['head']}")
    allowed_harness_overlay = {
        "?? tools/benchmarks/EFFICIENCY_COMPARISON.md",
        "?? tools/benchmarks/efficiency_comparison.py",
    }
    source_status = [line for line in baseline_snapshot["status"] if line not in allowed_harness_overlay]
    if source_status:
        raise ValueError(f"baseline source must be clean apart from this harness: {source_status}")
    artifact.mkdir(parents=True, exist_ok=False)
    args.artifact_dir = artifact
    settings = artifact / "settings.yaml"
    shutil.copy2(baseline / "config" / "settings.yaml", settings)
    snapshots = {"baseline": baseline_snapshot, "candidate": _snapshot(candidate)}
    manifest = {
        "schema": 1,
        "bytecode_policy": "no-project-pyc-before-or-after; shared-dependency-venv; no-bytecode-writes",
        "created_at_epoch": time.time(),
        "snapshots": snapshots,
        "python": str(args.python),
        "counts": args.counts,
        "trials": args.trials,
        "queries": args.queries,
        "warmups": args.warmups,
        "dimensions": DIMENSIONS,
        "rng_seed": RNG_SEED,
        "fixture": "synthetic-10-topic-25-percent-four-outgoing-links",
        "provider": PROVIDER_STUB,
        "float_abs_tolerance": FLOAT_ABS_TOLERANCE,
        "float_rel_tolerance": FLOAT_REL_TOLERANCE,
        "settings_sha256": _sha256_file(settings),
        "interpretation": {
            "process_is_independent_unit": True,
            "p95_is_descriptive_only": True,
            "rss_is_coarse_process_residency": True,
            "provider_and_model_residency_excluded": True,
            "historical_bbb454b_results_not_folded_into_delta": True,
            "idle_cursor_is_blank_spool_microbenchmark_not_full_worker_idle": True,
        },
    }
    (artifact / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    raw_path = artifact / "results.jsonl"
    results: list[dict[str, Any]] = []

    local_fixtures: dict[int, Path] = {}
    federation_fixtures: dict[int, dict[str, Path]] = {}
    for count in args.counts:
        fixture = artifact / "fixtures" / f"local-{count}.db"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        _seed(
            args,
            baseline=baseline,
            settings=settings,
            action="seed-memories",
            db=fixture,
            count=count,
            prefix="bench",
            prior_shape=True,
        )
        local_fixtures[count] = fixture
        per_source, remainder = divmod(count, args.federation_sources)
        sources = {}
        offset = 0
        for source_index in range(args.federation_sources):
            source = f"agent{source_index + 1}"
            source_count = per_source + int(source_index < remainder)
            db = artifact / "fixtures" / f"federated-{count}-{source}.db"
            _seed(
                args,
                baseline=baseline,
                settings=settings,
                action="seed-memories",
                db=db,
                count=source_count,
                prefix=source,
                offset=offset,
            )
            sources[source] = db
            offset += source_count
        federation_fixtures[count] = sources

    scene_fixtures = {}
    for source_index in range(args.scene_sources):
        source = f"sceneagent{source_index + 1}"
        db = artifact / "fixtures" / f"scenes-{source}.db"
        _seed(
            args,
            baseline=baseline,
            settings=settings,
            action="seed-scenes",
            db=db,
            count=args.scene_ids,
            prefix=source,
        )
        scene_fixtures[source] = db

    roots = {"baseline": baseline, "candidate": candidate}

    def run_pair(trial: int, label: str, action: str, extra_for: Callable[[str], tuple[list[str], Path | None]]) -> None:
        paired: dict[str, dict[str, Any]] = {}
        for version in _paired_order(trial):
            extra, memory_db = extra_for(version)
            result = _invoke(
                args,
                action=action,
                root=roots[version],
                state=artifact / "state" / label / str(trial) / version,
                settings=settings,
                extra=extra,
                memory_db=memory_db,
            )
            result.update({"version": version, "trial": trial, "pair_label": label})
            paired[version] = result
            results.append(result)
            _record(raw_path, result)
        _assert_equivalent(paired["baseline"], paired["candidate"], f"{label}/trial-{trial}")

    for count in args.counts:
        for trial in range(args.trials):
            def local_extra(version: str, *, _count: int = count, _trial: int = trial) -> tuple[list[str], Path]:
                db = artifact / "state" / f"local-{_count}" / str(_trial) / version / "memory.db"
                db.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_fixtures[_count], db)
                return (["--db", str(db), "--count", str(_count), "--queries", str(args.queries), "--warmups", str(args.warmups)], db)

            run_pair(trial, f"local-{count}", "measure-local", local_extra)

            def federation_extra(version: str, *, _count: int = count, _trial: int = trial) -> tuple[list[str], None]:
                copied = []
                for source, fixture in federation_fixtures[_count].items():
                    db = artifact / "state" / f"federated-{_count}" / str(_trial) / version / f"{source}.db"
                    db.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fixture, db)
                    copied.extend(["--source-db", f"{source}={db}"])
                return (copied + ["--count", str(_count), "--queries", str(args.queries), "--warmups", str(args.warmups)], None)

            run_pair(trial, f"federated-{count}", "measure-federated", federation_extra)

    for trial in range(args.trials):
        def scene_extra(version: str, *, _trial: int = trial) -> tuple[list[str], None]:
            copied = []
            for source, fixture in scene_fixtures.items():
                db = artifact / "state" / "scene-references" / str(_trial) / version / f"{source}.db"
                db.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fixture, db)
                copied.extend(["--source-db", f"{source}={db}"])
            return (copied + ["--scene-ids", str(args.scene_ids), "--iterations", str(args.target_iterations)], None)

        run_pair(trial, "scene-references", "measure-scene-refs", scene_extra)
        run_pair(
            trial,
            "idle-cursor",
            "measure-idle-cursor",
            lambda _version: (["--iterations", str(args.target_iterations)], None),
        )
        for target in args.import_targets:
            run_pair(
                trial,
                f"import-{target}",
                "measure-import",
                lambda _version, _target=target: (["--import-target", _target], None),
            )

        def first_request_extra(version: str, *, _trial: int = trial) -> tuple[list[str], Path]:
            db = artifact / "state" / "mcp-first-request" / str(_trial) / version / "memory.db"
            db.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_fixtures[min(args.counts)], db)
            return ([], db)

        run_pair(trial, "mcp-first-request", "measure-first-request", first_request_extra)

    summary = _summarize(results)
    for root in (baseline, candidate):
        _require_clean_project_bytecode(root)
    final_snapshots = {"baseline": _snapshot(baseline), "candidate": _snapshot(candidate)}
    for version in ("baseline", "candidate"):
        if _snapshot_identity(final_snapshots[version]) != _snapshot_identity(snapshots[version]):
            raise RuntimeError(
                f"{version} source identity changed during benchmark; refusing to summarize mixed snapshots"
            )
    (artifact / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_dir": str(artifact), "observations": len(results), "summary": summary}, indent=2))
    return 0


def _child(args: argparse.Namespace) -> int:
    actions: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
        "seed-memories": _seed_memories,
        "seed-scenes": _seed_scenes,
        "measure-local": _measure_local,
        "measure-federated": _measure_federated,
        "measure-scene-refs": _measure_scene_refs,
        "measure-idle-cursor": _measure_idle_cursor,
        "measure-import": _measure_import,
        "measure-first-request": _measure_first_request,
    }
    print(json.dumps(actions[args.action](args), sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run an isolated paired comparison")
    run.add_argument("--baseline-root", type=Path, required=True)
    run.add_argument("--candidate-root", type=Path, required=True)
    run.add_argument("--artifact-dir", type=Path, required=True)
    run.add_argument("--python", type=Path, default=Path(sys.executable))
    run.add_argument("--counts", type=int, nargs="+", default=[1000, 5000])
    run.add_argument("--trials", type=int, default=9)
    run.add_argument("--queries", type=int, default=25)
    run.add_argument("--warmups", type=int, default=5)
    run.add_argument("--federation-sources", type=int, default=3)
    run.add_argument("--scene-sources", type=int, default=4)
    run.add_argument("--scene-ids", type=int, default=100)
    run.add_argument("--target-iterations", type=int, default=10)
    run.add_argument(
        "--import-targets",
        nargs="+",
        default=["app.storage.memories", "app.retrieval_pipeline.retriever", "entrypoints.mcp_server", "entrypoints.main"],
    )
    run.add_argument("--timeout", type=int, default=300)

    child = subparsers.add_parser("child", help=argparse.SUPPRESS)
    child.add_argument("--action", required=True)
    child.add_argument("--root", type=Path, required=True)
    child.add_argument("--db", type=Path)
    child.add_argument("--count", type=int, default=1000)
    child.add_argument("--offset", type=int, default=0)
    child.add_argument("--prefix", default="bench")
    child.add_argument("--prior-shape", action="store_true")
    child.add_argument("--queries", type=int, default=25)
    child.add_argument("--warmups", type=int, default=5)
    child.add_argument("--source-db", action="append", default=[])
    child.add_argument("--scene-ids", type=int, default=100)
    child.add_argument("--iterations", type=int, default=10)
    child.add_argument("--import-target", default="entrypoints.mcp_server")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        if args.trials < 1 or args.queries < 1 or args.warmups < 0:
            raise ValueError("trials/queries must be positive and warmups non-negative")
        if any(count < 1 for count in args.counts):
            raise ValueError("counts must be positive")
        return _orchestrate(args)
    return _child(args)


if __name__ == "__main__":
    raise SystemExit(main())
