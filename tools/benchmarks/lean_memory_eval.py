#!/usr/bin/env python3
"""Isolated, paired Titan behavior evaluation (synthetic by default).

Run this file from the experiment checkout; it imports each target only inside
its own subprocess. No snapshot is modified. See docs/lean-memory-evaluation.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/lean_memory_cases.json"
FROZEN_SHA256 = "62a75dc2d9a75887d402bff0b1af77584ef6d7c4c7ae7785e586c9387904f188"


def runtime_fingerprint(snapshot):
    digest = hashlib.sha256()
    paths = list((snapshot / "app").rglob("*.py")) + list((snapshot / "config").glob("*.yaml"))
    for path in sorted(paths):
        digest.update(str(path.relative_to(snapshot)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def summarize(values):
    return {"n": len(values), "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95)}


def validate_fixture(fixture):
    errors = []
    rows = fixture.get("memories", [])
    ids = {row["id"] for row in rows}
    if len(ids) != len(rows):
        errors.append("duplicate memory IDs")
    for case in fixture.get("queries", []):
        if not set(case["expected"]) <= ids:
            errors.append(f"{case['id']}: unknown expected IDs")
        if set(case["expected"]) & set(case.get("forbidden", [])):
            errors.append(f"{case['id']}: expected/forbidden overlap")
    if not rows or not fixture.get("queries") or not fixture.get("capture_cases"):
        errors.append("fixture sections must be nonempty")
    return errors


def validate_response(response):
    errors = []
    if not isinstance(response, dict):
        return ["response must be an object"]
    rows = response.get("memories")
    if not isinstance(rows, list):
        return ["memories must be a list"]
    if response.get("count") != len(rows):
        errors.append("count differs from memories length")
    if not isinstance(response.get("brief"), str):
        errors.append("brief must be a string")
    identities = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("memory must be an object")
            continue
        identity = (row.get("source_agent"), row.get("id"))
        if not all(isinstance(value, str) and value for value in identity):
            errors.append("memory requires source-qualified identity")
        if identity in identities:
            errors.append("duplicate displayed source-qualified identity")
        identities.add(identity)
        if not isinstance(row.get("text"), str) or not row.get("text"):
            errors.append("full memory requires nonempty text")
        if not isinstance(row.get("source_event_ids", []), list):
            errors.append("source_event_ids must be a list")
        if not isinstance((row.get("evidence_refs") or []), list):
            errors.append("evidence_refs must be a list")
        for reference in (row.get("evidence_refs") or []):
            if not isinstance(reference, dict) or not (reference.get("memory_id") or reference.get("id")) or not reference.get("scene_id"):
                errors.append("evidence reference requires memory and scene identities")
    if not isinstance(response.get("scene_refs"), list):
        errors.append("scene_refs must be a list")
    return errors


def response_evidence(response):
    """Preserve alternate provenance rather than crediting display IDs alone."""
    evidence = []
    for row in response.get("memories", []):
        evidence.append({key: row.get(key) for key in ("id", "source_agent", "scene_id", "source_event_ids")})
        for ref in (row.get("evidence_refs") or []):
            evidence.append({**ref, "id": ref.get("memory_id") or ref.get("id"), "source_agent": ref.get("source_agent") or row.get("source_agent")})
    return evidence


def score_case(case, response, fixture_rows, resolved_scenes=None):
    expected = {item + ":0" for item in case["expected"]}
    returned = {row["id"] for row in response.get("memories", [])}
    refs = response_evidence(response)
    evidence_ids = {ref.get("id") for ref in refs}
    scene_refs = {(ref.get("source_agent"), ref.get("scene_id")) for ref in response.get("scene_refs", [])}
    retained = set()
    for ref in refs:
        ident = ref.get("id")
        if ident not in expected:
            continue
        source = fixture_rows[ident]
        if (ref.get("scene_id") == source["scene_id"]
                and ref.get("source_agent") == source["source_agent"]
                and set(source["source_event_ids"]) <= set(ref.get("source_event_ids") or
                    (resolved_scenes or {}).get((ref.get("source_agent"), ref.get("scene_id")), {}).get("source_event_ids", []))
                and (source["source_agent"], source["scene_id"]) in scene_refs
                and (not resolved_scenes or not source.get("text") or source["text"] in
                     (resolved_scenes.get((source["source_agent"], source["scene_id"]), {}).get("extraction_user_text") or ""))):
            retained.add(ident)
    true_positive = len(expected & evidence_ids)
    false_positive = len(evidence_ids - expected)
    false_negative = len(expected - evidence_ids)
    return {
        "true_positive": true_positive, "false_positive": false_positive,
        "false_negative": false_negative, "expected": sorted(expected),
        "returned": sorted(returned), "evidence_ids": sorted(str(i) for i in evidence_ids),
        "evidence_retained": len(retained), "evidence_expected": len(expected),
        "forbidden_returned": sorted(returned & {item + ":0" for item in case.get("forbidden", [])}),
        "display_precision": len(returned & expected) / len(returned) if returned else 1.0,
        "abstained": not returned,
        "pass": not false_positive and not false_negative and len(retained) == len(expected),
    }


def worker(args):
    # The caller supplies a fresh writable home. Import targets only after paths
    # and network guards are installed, including during import/bootstrap.
    snapshot = Path(args.worker_root).resolve()
    home = Path(os.environ["TITAN_SHARED_HOME"]).resolve()
    if home == snapshot or home in snapshot.parents or snapshot in home.parents:
        raise RuntimeError("worker data home must be separate from snapshot")
    source_before = runtime_fingerprint(snapshot)
    fixture = json.loads(Path(args.fixtures).read_text())
    errors = validate_fixture(fixture)
    if errors:
        raise ValueError(errors)
    import socket
    from unittest.mock import patch
    original_connect = socket.socket.connect
    parsed_url = urlparse(args.local_embedding_url) if args.local_embedding_url else None
    allowed_port = (parsed_url.port or 80) if parsed_url else None
    network_attempts = []

    def guarded_connect(sock, address):
        if parsed_url and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"} and address[1] == allowed_port:
            return original_connect(sock, address)
        network_attempts.append(str(address))
        raise RuntimeError("evaluation blocked network access")

    socket.socket.connect = guarded_connect
    sys.path.insert(0, str(snapshot))
    import yaml
    settings = yaml.safe_load((snapshot / "config/settings.yaml").read_text())
    if args.ablation != "default":
        settings.setdefault("step2", {})["enabled"] = args.ablation == "on"
    settings_path = home / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(settings))
    os.environ["TITAN_SETTINGS_PATH"] = str(settings_path)
    embedding_path = home / "embedding.yaml"
    embedding_path.write_text(yaml.safe_dump({"current": "ollama", "ollama": {
        "base_url": args.local_embedding_url or "http://127.0.0.1:1", "model": args.embedding_model}}))
    os.environ["TITAN_EMBEDDING_CONFIG_PATH"] = str(embedding_path)
    import_started = time.perf_counter()
    import numpy as np
    from app.embedding import embedder
    from app.save_pipeline import pipeline
    from app.storage.memories import SqliteMemoryRepository
    from app.storage.scenes import SqliteSceneRepository
    bootstrap_import_ms = (time.perf_counter() - import_started) * 1000
    vocabulary = sorted({tag for row in fixture["memories"] + fixture["queries"]
                         for tag in row.get("topics", []) + row.get("entities", [])})
    indexes = {tag: index for index, tag in enumerate(vocabulary)}
    by_text = {row.get("text", row.get("query")): row for row in fixture["memories"] + fixture["queries"]}
    provider_calls = []

    def synthetic_vector(text):
        # Frozen tags intentionally encode a controlled semantic fixture, with
        # tiny lexical tie-break dimensions. This is not a learned embedding.
        row = by_text.get(text)
        vector = np.zeros(len(indexes) + 32, dtype=np.float32)
        if row:
            for topic in row.get("topics", []):
                vector[indexes[topic]] = 1 if topic in {"aurora", "boreal", "preference"} else 3
            for entity in row.get("entities", []):
                vector[indexes[entity]] = .7
        else:
            for tag in vocabulary:
                if tag in text.lower():
                    vector[indexes[tag]] = 1 if tag in {"aurora", "boreal", "preference"} else 3
        for token in re.findall(r"\w+", text.lower()):
            slot = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 32
            vector[len(indexes) + slot] += .015
        if not np.linalg.norm(vector):
            vector[-1] = 1
        return vector

    original_ollama = embedder.ollama_embed

    def provider(texts, model, base_url):
        provider_calls.append({"inputs": len(texts), "bytes": sum(len(t.encode()) for t in texts)})
        return original_ollama(texts, model, base_url) if parsed_url else [synthetic_vector(t) for t in texts]

    def reject_openai(*_args, **_kwargs):
        raise RuntimeError("remote embedding provider is forbidden in this benchmark")

    seed_started = time.perf_counter()
    fixture_rows = {}
    seed_calls = 0
    with patch.object(embedder, "ollama_embed", side_effect=provider), patch.object(embedder, "openai_embed", side_effect=reject_openai):
        seed_vectors = embedder.embed([row["text"] for row in fixture["memories"]])
        seed_calls = len(provider_calls)
        seed_fingerprint = hashlib.sha256(b"".join(np.asarray(vector, dtype=np.float32).tobytes() for vector in seed_vectors)).hexdigest()
        groups = {"eval": [], "peer": []}
        for turn, (source, vector) in enumerate(zip(fixture["memories"], seed_vectors), start=1):
            agent = "eval" if turn <= 8 else "peer"
            row = {"id": source["id"] + ":0", "text": source["text"], "scene_id": source["scene_id"],
                   "ts": source["ts"], "session_id": "fixture", "turn": turn, "stream": "learnings",
                   "type": "fact", "source_type": "user", "source_reliability": .95,
                   "verification_status": "unverified", "fallback_generated": False,
                   "speaker_focus": "user", "memory_kind": "user_fact",
                   "provenance": {"user": source["text"], "assistant": "Recorded."},
                   "source_event_ids": ["event-" + source["scene_id"]], "embedding": vector.tolist()}
            groups[agent].append(row)
            fixture_rows[row["id"]] = {**row, "source_agent": agent}
        for agent, rows in groups.items():
            path = home / "agents" / agent / "out/memories/memory_store.db"
            repo = SqliteMemoryRepository(path)
            repo.append_memories(rows)
            scenes = SqliteSceneRepository(path)
            unique_scenes = {}
            for row in rows:
                sid = row["scene_id"]
                if sid not in unique_scenes:
                    unique_scenes[sid] = {"scene_id": sid, "session_id": "fixture", "turn": row["turn"],
                        "kind": "message_exchange", "ts": row["ts"], "source_event_ids": row["source_event_ids"],
                        "extraction_user_text": row["text"], "extraction_assistant_text": "Recorded.",
                        "messages": [{"role": "user", "content": row["text"]}]}
                else:
                    unique_scenes[sid]["extraction_user_text"] += "\n" + row["text"]
                    unique_scenes[sid]["messages"][0]["content"] += "\n" + row["text"]
            scenes.append_scenes(list(unique_scenes.values()))
        seed_ms = (time.perf_counter() - seed_started) * 1000
        provider_calls.clear()
        cases = []
        steady_times, first_times = [], []
        response_bytes = []
        for case in fixture["queries"]:
            responses = []
            timings = []
            calls_before = len(provider_calls)
            schema_errors = []
            for repeat in range(args.repeats + 1):
                started = time.perf_counter()
                response = pipeline.retrieve_memory_brief(query=case["query"], limit=case["top_k"],
                    max_items=case["top_k"], max_chars=6000, sources=["eval", "peer"], detail="full")
                timings.append((time.perf_counter() - started) * 1000)
                schema_errors.extend(validate_response(response))
                responses.append(response)
                response_bytes.append(len(json.dumps(response, sort_keys=True).encode()))
            stable = all(response == responses[0] for response in responses[1:])
            first_times.append(timings[0])
            steady_times.extend(timings[1:])
            evidence_started = time.perf_counter()
            resolved_scenes = {}
            for ref in responses[0]["scene_refs"]:
                key = (ref.get("source_agent"), ref.get("scene_id"))
                resolved = pipeline.get_scene_context(ref["scene_id"], source_agent=ref.get("source_agent"))
                resolved_scenes[key] = resolved.get("scene") or {}
            evidence_resolution_ms = (time.perf_counter() - evidence_started) * 1000
            scored = score_case(case, responses[0], fixture_rows, resolved_scenes)
            scored.update({"id": case["id"], "query": case["query"], "schema_errors": sorted(set(schema_errors)),
                "repeat_stable": stable, "first_ms": timings[0], "steady": summarize(timings[1:]),
                "embedding_calls": len(provider_calls) - calls_before,
                "response_bytes": len(json.dumps(responses[0], sort_keys=True).encode()),
                "response": responses[0], "evidence_resolution_ms": evidence_resolution_ms,
                "brief": responses[0]["brief"], "scene_refs": responses[0]["scene_refs"]})
            scored["pass"] = scored["pass"] and not schema_errors and stable
            cases.append(scored)
        retrieval_provider_calls = list(provider_calls)
        # Capture admission is exercised through the actual public save pipeline,
        # while extraction responses are scripted to remove model variability.
        capture = []
        for turn, case in enumerate(fixture["capture_cases"], start=100):
            extraction_calls = []

            class ScriptedExtractor:
                def chat(self, *_args, **_kwargs):
                    extraction_calls.append(1)
                    return json.dumps({"memories": [{"text": case["goal"], "type": "fact", "source": "user", "stream": "learnings"}]})

            with patch.object(pipeline, "get_extraction_adapter", return_value=ScriptedExtractor()):
                started = time.perf_counter()
                outcome = pipeline.run_memory_pipeline_outcome("capture", turn, case["goal"], case["outcome"], source_event_ids=[case["id"]])
                capture.append({"id": case["id"], "expected_extract": case["should_extract"],
                    "attempted_extraction": bool(extraction_calls), "records": len(outcome["records"]),
                    "skip_reason": outcome.get("skip_reason"), "ms": (time.perf_counter() - started) * 1000,
                    "pass": bool(extraction_calls) == case["should_extract"] and bool(outcome["records"]) == case["should_extract"]})
        extraction_schema = []
        for name, payload in [("invalid-json", "not-json"), ("missing-list", '{}'),
                              ("non-list", '{"memories": "wrong"}'),
                              ("non-text", '{"memories":[{"text":42}]}')]:
            class InvalidExtractor:
                def chat(self, *_args, **_kwargs):
                    return payload
            rejected = False
            error_type = None
            try:
                with patch.object(pipeline, "get_extraction_adapter", return_value=InvalidExtractor()):
                    pipeline.run_memory_pipeline_outcome("invalid-schema", 1,
                        "Remember that Aurora uses PostgreSQL for persistent storage.", "Recorded.")
            except Exception as error:
                error_type = type(error).__name__
                rejected = error_type == "ExtractionError"
            extraction_schema.append({"id": name, "rejected": rejected,
                "error_type": error_type, "pass": rejected})
    tp = sum(c["true_positive"] for c in cases)
    fp = sum(c["false_positive"] for c in cases)
    fn = sum(c["false_negative"] for c in cases)
    expected_evidence = sum(c["evidence_expected"] for c in cases)
    abstention_cases = [c for c in cases if not c["expected"]]
    db_bytes = sum(p.stat().st_size for p in home.rglob("*.db"))
    return {
        "snapshot": str(snapshot), "ablation": args.ablation,
        "runtime_source_sha256": source_before,
        "configured_step2": bool(settings.get("step2", {}).get("enabled")),
        "fixture_sha256": hashlib.sha256(Path(args.fixtures).read_bytes()).hexdigest(),
        "embedding_mode": "explicit_local_provider" if parsed_url else "synthetic_controlled_fixture",
        "seed_embedding_sha256": seed_fingerprint,
        "embedding_model": args.embedding_model,
        "isolation": {"fresh_titan_home": True, "source_unchanged_during_run": source_before == runtime_fingerprint(snapshot),
                      "blocked_network_attempts": network_attempts,
                      "bytecode_disabled": bool(os.environ.get("PYTHONDONTWRITEBYTECODE"))},
        "metrics": {"true_positive": tp, "false_positive": fp, "false_negative": fn,
            "precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 1.0,
            "abstention_accuracy": sum(c["abstained"] for c in abstention_cases) / len(abstention_cases) if abstention_cases else None,
            "evidence_retention": sum(c["evidence_retained"] for c in cases) / expected_evidence,
            "query_passes": sum(c["pass"] for c in cases), "query_count": len(cases),
            "capture_passes": sum(c["pass"] for c in capture), "capture_count": len(capture),
            "schema_passes": sum(c["pass"] for c in extraction_schema), "schema_count": len(extraction_schema),
            "response_schema_errors": sum(bool(c["schema_errors"]) for c in cases),
            "embedding_provider_calls": len(retrieval_provider_calls),
            "embedding_provider_inputs": sum(c["inputs"] for c in retrieval_provider_calls),
            "capture_embedding_provider_calls": len(provider_calls) - len(retrieval_provider_calls),
            "total_embedding_provider_calls_including_seed": seed_calls + len(provider_calls),
            "embedding_input_bytes": sum(c["bytes"] for c in retrieval_provider_calls),
            "serialized_response_bytes": sum(response_bytes), "sqlite_bytes_after_capture": db_bytes,
            "bootstrap_import_ms": bootstrap_import_ms, "seed_ms": seed_ms, "seed_provider_calls": seed_calls,
            "first_query_per_case": summarize(first_times), "steady_query": summarize(steady_times)},
        "cases": cases, "capture_cases": capture, "extraction_schema_cases": extraction_schema,
    }


def run_pair(args):
    fixture_sha = hashlib.sha256(Path(args.fixtures).read_bytes()).hexdigest()
    if fixture_sha != FROZEN_SHA256:
        raise ValueError("frozen evaluation fixture changed; review and deliberately version the benchmark")
    targets = [("baseline", args.baseline), ("experiment", args.experiment)]
    results = []
    for label, path in targets:
        if not path:
            continue
        path = str(Path(path).resolve())
        if not (Path(path) / "app/save_pipeline/pipeline.py").exists():
            raise ValueError(f"not a Titan checkout: {path}")
        for ablation in args.ablations.split(","):
            with tempfile.TemporaryDirectory(prefix="titan-lean-eval-") as temporary:
                home = Path(temporary)
                base = home / "agents/eval"
                base.mkdir(parents=True)
                # Do not inherit keys, provider paths, or a live Titan namespace.
                env = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "SYSTEMROOT", "DYLD_LIBRARY_PATH", "LANG", "LC_ALL", "VIRTUAL_ENV"}}
                env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                    "TITAN_HOME": str(base), "TITAN_BASE_DIR": str(base), "TITAN_SHARED_HOME": str(home),
                    "TITAN_AGENT_NAME": "eval", "TITAN_MEMORY_BACKEND": "sqlite",
                    "TITAN_MEMORY_READ_FALLBACK": "none", "TITAN_MEMORY_DB_PATH": str(base / "out/memories/memory_store.db")})
                command = [sys.executable, str(Path(__file__).resolve()), "--worker-root", path,
                    "--fixtures", str(Path(args.fixtures).resolve()), "--ablation", ablation,
                    "--repeats", str(args.repeats), "--embedding-model", args.embedding_model]
                if args.local_embedding_url:
                    command += ["--local-embedding-url", args.local_embedding_url]
                started = time.perf_counter()
                completed = subprocess.run(command, cwd=temporary, env=env, text=True,
                    capture_output=True, timeout=args.timeout)
                if completed.returncode:
                    results.append({"label": label, "snapshot": path, "ablation": ablation,
                        "error": completed.stderr[-12000:], "returncode": completed.returncode})
                else:
                    result = json.loads(completed.stdout)
                    result.update({"label": label, "subprocess_wall_ms": (time.perf_counter() - started) * 1000})
                    results.append(result)
    report = {"benchmark_schema_version": 1, "fixture_sha256": fixture_sha,
        "notice": "Synthetic controlled behavior fixtures, not production retrieval quality. Timing is a small local sample, not a load benchmark.",
        "repeats_after_first": args.repeats, "results": results}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    for result in results:
        print(json.dumps({"label": result["label"], "ablation": result["ablation"],
            "metrics": result.get("metrics"), "error": result.get("error")}, sort_keys=True))
    print(f"Report: {output.resolve()}")
    # Quality failures are visible and optionally enforceable; baseline failures
    # must not prevent obtaining the paired experiment measurements.
    if any("error" in result for result in results):
        return 2
    if args.require_all_pass and any(
        r["isolation"]["blocked_network_attempts"] or not r["isolation"]["source_unchanged_during_run"] or
        r["metrics"]["query_passes"] != r["metrics"]["query_count"] or
        r["metrics"]["capture_passes"] != r["metrics"]["capture_count"] or
        r["metrics"]["schema_passes"] != r["metrics"]["schema_count"] for r in results):
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=str(ROOT.parent / "titan-baseline"))
    parser.add_argument("--experiment", default=str(ROOT))
    parser.add_argument("--fixtures", default=str(FIXTURE))
    parser.add_argument("--output", default="/tmp/titan-lean-evaluation.json")
    parser.add_argument("--ablations", default="default,on,off")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--require-all-pass", action="store_true")
    parser.add_argument("--embedding", choices=["synthetic", "local"], default="synthetic", help="local is an explicit opt-in to loopback Ollama")
    parser.add_argument("--local-embedding-url", help="Explicit opt-in to local Ollama embeddings; only literal loopback URLs allowed")
    parser.add_argument("--embedding-model", default="nomic-embed-text:v1.5")
    parser.add_argument("--worker-root", help=argparse.SUPPRESS)
    parser.add_argument("--ablation", choices=["default", "on", "off"], default="default", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or any(a not in {"default", "on", "off"} for a in args.ablations.split(",")):
        parser.error("repeats must be positive and ablations must be default,on,off")
    if args.embedding == "local" and not args.local_embedding_url:
        args.local_embedding_url = "http://127.0.0.1:11434"
    if args.local_embedding_url:
        parsed = urlparse(args.local_embedding_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} or parsed.username or parsed.password:
            parser.error("local provider opt-in requires an http URL with a literal loopback host and no credentials")
    if args.worker_root:
        print(json.dumps(worker(args), sort_keys=True))
        return 0
    return run_pair(args)


if __name__ == "__main__":
    raise SystemExit(main())
