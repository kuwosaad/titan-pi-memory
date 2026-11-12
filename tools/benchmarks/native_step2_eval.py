#!/usr/bin/env python3
"""Native shipped Step2 on/off: original and lean snapshots, no packet selector.

PYTHONDONTWRITEBYTECODE=1 python3 tools/benchmarks/native_step2_eval.py \
  --output ../native-step2-evaluation.json
Only fictional fixtures and literal-loopback Ollama embeddings are used.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/native_step2_cases.json"
# Supplied independently by the fixture owner before any outcomes.
FROZEN_SHA256 = "05caa7f10e09d128bb64a9baf59a57f7077a7dc5261fb40e7eaf8476fa078e3f"
_PUBLIC = ("id", "text", "source_agent", "scene_id", "source_event_ids", "ts", "stream", "type", "source_reliability", "verification_status", "evidence_refs")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def helpers():
    spec = importlib.util.spec_from_file_location("_native_step2_helpers", ROOT / "tools/benchmarks/lean_memory_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qualified(memory):
    return f"{memory.get('source_agent')}:{memory.get('fixture_id') or memory.get('id')}"


def public_memory(hit, default_source="codex"):
    memory = hit.get("memory") or {}
    return {**{field: copy.deepcopy(memory[field]) for field in _PUBLIC if field in memory},
            "source_agent": hit.get("source_agent") or memory.get("source_agent") or default_source}


def effective_config(settings, enabled):
    result = copy.deepcopy(settings)
    result.setdefault("step2", {})["enabled"] = enabled
    return result


def config_without_switch_sha(settings):
    stripped = copy.deepcopy(settings)
    stripped.setdefault("step2", {}).pop("enabled", None)
    return hashlib.sha256(json.dumps(stripped, sort_keys=True).encode()).hexdigest()


def score_brief(case, response, rendered_memories, durable_scenes, params, max_chars):
    brief = response.get("brief") or ""
    # Included hits come from the native renderer itself. Pointer-only or hidden
    # response rows cannot earn claim credit, even if a tension note quotes them.
    visible = {qualified(row): row for row in rendered_memories if row.get("text") and row["text"] in brief}
    scene_refs = {(ref.get("source_agent"), ref.get("scene_id")) for ref in response.get("scene_refs", [])}
    retained = set()
    for key, row in visible.items():
        scene_key = (row["source_agent"], row.get("scene_id"))
        scene = durable_scenes.get(scene_key) or {}
        if (scene_key in scene_refs and row["text"] in (scene.get("extraction_user_text") or "")
                and set(row.get("source_event_ids") or []) <= set(scene.get("source_event_ids") or [])
                and row.get("source_event_ids")):
            retained.add(key)
    options = [set(option) for option in case["required_sets"]] or [set()]
    unknown = case.get("expect_abstain", all(not option for option in options))
    best = min(options, key=lambda option: (len(option-set(visible)), len(option), sorted(option)))
    complete = not visible if unknown else any(option <= set(visible) and option <= retained for option in options)
    irrelevant = set(visible)-set(case["acceptable_ids"])
    budget_ok = len(brief) <= max_chars and len(visible) <= params["max_items"]
    return {"complete_required_evidence": bool(complete and budget_ok),
        "clean_complete": bool(complete and not irrelevant and budget_ok),
        "visible_ids": sorted(visible), "missing_required_ids": sorted(best-set(visible)),
        "irrelevant_ids": sorted(irrelevant), "provenance_retained_ids": sorted(retained),
        "provenance_expected": len(visible), "rendered_chars": len(brief), "budget_ok": budget_ok,
        "unknown_answer_case": bool(unknown), "abstained": not visible,
        "cluster_header_rendered": "INSIGHT " in brief, "tension_rendered": "TENSION" in brief,
        "later_marker_rendered": "LATER VIEW" in brief, "earlier_marker_rendered": "EARLIER VIEW" in brief}


def stage(hits):
    fields = ("score", "base_score", "final_score", "selection_score", "step2_bonus", "support_score",
              "step2_contradiction_delta", "cluster_id", "cluster_size", "tension_note")
    return [{"id": str((hit.get("memory") or {}).get("id")),
             **{key: hit[key] for key in fields if key in hit}} for hit in hits]


def worker(args):
    target = Path(args.worker_root).resolve()
    data_root = Path(os.environ["TITAN_SHARED_HOME"])
    helper = helpers()
    source_before = helper.runtime_fingerprint(target)
    harness_before = sha(__file__)
    fixture = json.loads(FIXTURE.read_text())
    params = fixture.get("params", fixture.get("parameters"))
    original_connect = socket.socket.connect
    blocked = []

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 11434:
            return original_connect(sock, address)
        blocked.append(str(address))
        raise RuntimeError("native Step2 evaluator blocked non-loopback network")
    socket.socket.connect = connect
    sys.path.insert(0, str(target))
    import numpy as np
    import yaml
    from unittest.mock import patch
    settings = yaml.safe_load((target / "config/settings.yaml").read_text())
    effective = effective_config(settings, args.enabled == "on")
    settings_path = data_root / "native-settings.yaml"
    settings_path.write_text(yaml.safe_dump(effective))
    os.environ["TITAN_SETTINGS_PATH"] = str(settings_path)
    embedding_path = data_root / "embedding.yaml"
    embedding_path.write_text(yaml.safe_dump({"current": "ollama", "ollama": {
        "base_url": "http://127.0.0.1:11434", "model": "nomic-embed-text:v1.5"}}))
    os.environ["TITAN_EMBEDDING_CONFIG_PATH"] = str(embedding_path)
    bootstrap_started = time.perf_counter()
    from app.embedding import embedder
    from app.retrieval_pipeline import retriever
    from app.save_pipeline import pipeline
    from app.storage.memories import SqliteMemoryRepository
    from app.storage.scenes import SqliteSceneRepository
    bootstrap_import_ms = (time.perf_counter()-bootstrap_started)*1000
    calls = []
    real_provider = embedder.ollama_embed

    def provider(texts, model, base_url):
        values = real_provider(texts, model, base_url)
        calls.append({"inputs": len(texts), "bytes": sum(len(text.encode()) for text in texts)})
        return values

    observations, rendered = [], []
    real_rerank = retriever._step2_1_rerank
    real_attention = retriever._build_attention_matrix
    real_select = retriever._select_diverse_hits
    real_notes = pipeline.build_memory_notes

    def observe_attention(*positional, **keywords):
        matrix = real_attention(*positional, **keywords)
        observations.append({"stage": "attention", "shape": list(matrix.shape), "nonzero_edges": int(np.count_nonzero(matrix)),
                             "max_weight": float(np.max(matrix)) if matrix.size else 0.0})
        return matrix

    def observe_rerank(*positional, **keywords):
        before = stage(positional[0])
        values = real_rerank(*positional, **keywords)
        after = stage(values)
        before_scores = {row["id"]: float(row.get("score", 0)) for row in before}
        observations.append({"stage": "step2_rerank", "before": before, "after": after,
            "score_changed_ids": [row["id"] for row in after if abs(float(row.get("score", 0))-before_scores.get(row["id"], 0)) > 1e-9],
            "order_changed": [row["id"] for row in before] != [row["id"] for row in after],
            "clustered_hits": sum(row.get("cluster_size", 0)>1 for row in after)})
        return values

    def observe_select(*positional, **keywords):
        before = stage(positional[0])
        values = real_select(*positional, **keywords)
        observations.append({"stage": "native_selector", "before": before, "after": stage(values)})
        return values

    def observe_notes(*positional, **keywords):
        value = real_notes(*positional, **keywords)
        rendered.extend(public_memory(hit) for hit in keywords.get("included_hits") or [])
        return value

    results, seeds = [], []
    seed_fingerprint = hashlib.sha256()
    with patch.object(embedder, "ollama_embed", side_effect=provider), \
         patch.object(retriever, "_build_attention_matrix", side_effect=observe_attention), \
         patch.object(retriever, "_step2_1_rerank", side_effect=observe_rerank), \
         patch.object(retriever, "_select_diverse_hits", side_effect=observe_select), \
         patch.object(pipeline, "build_memory_notes", side_effect=observe_notes):
        for case in fixture["cases"]:
            case_home = data_root / case["id"]
            active = "codex"
            base = case_home / "agents" / active
            os.environ.update({"TITAN_HOME": str(base), "TITAN_BASE_DIR": str(base), "TITAN_SHARED_HOME": str(case_home),
                "TITAN_MEMORY_DB_PATH": str(base / "out/memories/memory_store.db")})
            start = time.perf_counter()
            vectors = embedder.embed([row["text"] for row in case["memories"]])
            rows = []
            for turn, (source, vector) in enumerate(zip(case["memories"], vectors), 1):
                seed_fingerprint.update(np.asarray(vector, dtype=np.float32).tobytes())
                rows.append({**source, "id": source["id"]+":0", "turn": turn, "source_type": "user", "verification_status": source.get("verification_status", "unverified"),
                    "fallback_generated": False, "speaker_focus": "user", "memory_kind": "user_fact",
                    "source_event_ids": source.get("source_event_ids") or ["event-"+source["scene_id"]],
                    "provenance": {"user": source["text"], "assistant": "Recorded."}, "embedding": vector.tolist()})
            for agent in {row["source_agent"] for row in rows} | {active}:
                db = case_home / "agents" / agent / "out/memories/memory_store.db"
                repo = SqliteMemoryRepository(db)
                agent_rows = [row for row in rows if row["source_agent"] == agent]
                repo.append_memories(agent_rows)
                scene_repo = SqliteSceneRepository(db)
                scenes = {}
                for row in agent_rows:
                    key = row["scene_id"]
                    if key not in scenes:
                        scenes[key] = {"scene_id": key, "session_id": row["session_id"], "turn": row["turn"],
                            "kind": "message_exchange", "ts": row["ts"], "source_event_ids": [],
                            "extraction_user_text": "", "extraction_assistant_text": "Recorded.", "messages": []}
                    scene = scenes[key]
                    scene["source_event_ids"] = list(dict.fromkeys(scene["source_event_ids"]+row["source_event_ids"]))
                    scene["extraction_user_text"] += row["text"]+"\n"
                    scene["messages"].append({"role": "user", "content": row["text"]})
                scene_repo.append_scenes(list(scenes.values()))
            seeds.append({"case": case["id"], "ms": (time.perf_counter()-start)*1000})
            filters = case.get("filters", {})
            for budget, max_chars in (("native700", params["max_chars"]), ("roomy2000", params["diagnostic_max_chars"])):
                responses, sample_ms, repeats_observed, repeats_rendered = [], [], [], []
                before_calls = len(calls)
                for repeat in range(params["repeats"]+1):
                    observations.clear()
                    rendered.clear()
                    start = time.perf_counter()
                    kwargs = {"query": case["query"], "sources": filters.get("sources", sorted({row["source_agent"] for row in rows})),
                        "session_id": filters.get("session_id"), "date_from": filters.get("date_from"),
                        "date_to": filters.get("date_to"), "detail": "compact"}
                    if "mode" in filters:
                        kwargs["mode"] = filters["mode"]
                    if budget == "roomy2000":
                        kwargs["max_chars"] = max_chars
                    response = pipeline.retrieve_memory_brief(**kwargs)
                    sample_ms.append((time.perf_counter()-start)*1000)
                    responses.append(response)
                    repeats_observed.append(copy.deepcopy(observations))
                    repeats_rendered.append(copy.deepcopy(rendered))
                response = responses[0]
                actual_rendered = [{**memory, "fixture_id": memory["id"][:-2]} for memory in repeats_rendered[0]]
                audit_start = time.perf_counter()
                durable_scenes = {}
                for memory in actual_rendered:
                    key = (memory["source_agent"], memory.get("scene_id"))
                    if key not in durable_scenes:
                        durable_scenes[key] = pipeline.get_scene_context(key[1], source_agent=key[0]).get("scene") or {}
                score = score_brief(case, response, actual_rendered, durable_scenes, params, max_chars)
                stages = repeats_observed[0]
                score.update({"case": case["id"], "query": case["query"], "budget": budget,
                    "raw_response": response, "native_rendered_memories": actual_rendered,
                    "native_observations": stages, "first_ms": sample_ms[0], "steady_ms": sample_ms[1:],
                    "steady": helper.summarize(sample_ms[1:]), "scene_audit_ms": (time.perf_counter()-audit_start)*1000,
                    "embedding_calls": len(calls)-before_calls, "embedding_inputs": sum(c["inputs"] for c in calls[before_calls:]),
                    "rerank_called": any(stage["stage"] == "step2_rerank" for stage in stages),
                    "nonzero_attention_edges": sum(stage["nonzero_edges"] for stage in stages if stage["stage"] == "attention"),
                    "rerank_score_changed": any(stage.get("score_changed_ids") for stage in stages),
                    "rerank_order_changed": any(stage.get("order_changed") for stage in stages),
                    "repeat_identical": all(r == responses[0] for r in responses[1:])})
                results.append(score)
    summaries = {}
    for budget in ("native700", "roomy2000"):
        cases = [case for case in results if case["budget"] == budget]
        summaries[budget] = {"cases": len(cases), "complete": sum(c["complete_required_evidence"] for c in cases),
            "clean_complete": sum(c["clean_complete"] for c in cases), "irrelevant_records": sum(len(c["irrelevant_ids"]) for c in cases),
            "unknown_cases": sum(c["unknown_answer_case"] for c in cases), "unknown_abstentions": sum(c["unknown_answer_case"] and c["abstained"] for c in cases),
            "provenance_expected": sum(c["provenance_expected"] for c in cases),
            "provenance_retained": sum(len(c["provenance_retained_ids"]) for c in cases),
            "rerank_called_cases": sum(c["rerank_called"] for c in cases), "attention_nonzero_cases": sum(c["nonzero_attention_edges"]>0 for c in cases),
            "score_changed_cases": sum(c["rerank_score_changed"] for c in cases), "order_changed_cases": sum(c["rerank_order_changed"] for c in cases),
            "rendered_cluster_cases": sum(c["cluster_header_rendered"] for c in cases), "rendered_tension_cases": sum(c["tension_rendered"] for c in cases),
            "rendered_chars": sum(c["rendered_chars"] for c in cases), "budget_failures": sum(not c["budget_ok"] for c in cases),
            "unstable_cases": sum(not c["repeat_identical"] for c in cases), "embedding_calls": sum(c["embedding_calls"] for c in cases),
            "embedding_inputs": sum(c["embedding_inputs"] for c in cases), "steady": helper.summarize([v for c in cases for v in c["steady_ms"]])}
    return {"target": str(target), "enabled": args.enabled == "on", "fixture_sha256": sha(FIXTURE),
        "runtime_sha256": source_before, "runtime_unchanged": source_before == helper.runtime_fingerprint(target),
        "harness_sha256": harness_before, "harness_unchanged": harness_before == sha(__file__),
        "effective_settings": effective, "settings_except_switch_sha256": config_without_switch_sha(effective),
        "seed_vectors_sha256": seed_fingerprint.hexdigest(), "bootstrap_import_ms": bootstrap_import_ms, "seed_setup": seeds,
        "isolation": {"fresh_titan_home": True, "blocked_network_attempts": blocked, "bytecode_disabled": bool(os.environ.get("PYTHONDONTWRITEBYTECODE"))},
        "summaries": summaries, "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", default=str(ROOT.parent / "titan-baseline"))
    parser.add_argument("--lean", default=str(ROOT))
    parser.add_argument("--output", default=str(ROOT.parent / "native-step2-evaluation.json"))
    parser.add_argument("--worker-root", help=argparse.SUPPRESS)
    parser.add_argument("--enabled", choices=["on", "off"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sha(FIXTURE) != FROZEN_SHA256:
        parser.error("fixture digest is not independently frozen")
    if args.worker_root:
        print(json.dumps(worker(args), sort_keys=True))
        return 0
    results = []
    # Opposite ordering per checkout limits simple on-first warmup bias. It is
    # still a sequential small local sample, not a production speed benchmark.
    for label, target, order in (("original", args.original, ("off", "on")), ("lean", args.lean, ("on", "off"))):
        for enabled in order:
            with tempfile.TemporaryDirectory(prefix="native-step2-") as temporary:
                base = Path(temporary) / "agents/codex"
                base.mkdir(parents=True)
                env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "VIRTUAL_ENV", "SYSTEMROOT"}}
                env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "TITAN_HOME": str(base), "TITAN_BASE_DIR": str(base),
                    "TITAN_SHARED_HOME": temporary, "TITAN_AGENT_NAME": "codex", "TITAN_MEMORY_BACKEND": "sqlite", "TITAN_MEMORY_READ_FALLBACK": "none"})
                completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker-root", str(Path(target).resolve()),
                    "--enabled", enabled], cwd=temporary, env=env, text=True, capture_output=True, timeout=300)
                if completed.returncode:
                    print(completed.stderr, file=sys.stderr)
                    return completed.returncode
                result = json.loads(completed.stdout)
                result["label"] = label
                results.append(result)
    pairs = []
    for label in ("original", "lean"):
        off = next(r for r in results if r["label"] == label and not r["enabled"])
        on = next(r for r in results if r["label"] == label and r["enabled"])
        for before in off["cases"]:
            after = next(c for c in on["cases"] if c["case"] == before["case"] and c["budget"] == before["budget"])
            pairs.append({"label": label, "case": before["case"], "budget": before["budget"],
                "complete_delta": int(after["complete_required_evidence"])-int(before["complete_required_evidence"]),
                "clean_delta": int(after["clean_complete"])-int(before["clean_complete"]),
                "irrelevant_delta": len(after["irrelevant_ids"])-len(before["irrelevant_ids"]),
                "visible_ids_changed": before["visible_ids"] != after["visible_ids"],
                "rendered_order_changed": [qualified(m) for m in before["native_rendered_memories"]] != [qualified(m) for m in after["native_rendered_memories"]],
                "brief_changed": before["raw_response"]["brief"] != after["raw_response"]["brief"]})
    integrity = {"frozen_fixture": sha(FIXTURE) == FROZEN_SHA256,
        "settings_pairs_only_switch": all(len({r["settings_except_switch_sha256"] for r in results if r["label"] == label}) == 1 for label in ("original", "lean")),
        "identical_seed_vectors": len({r["seed_vectors_sha256"] for r in results}) == 1,
        "unchanged_sources": all(r["runtime_unchanged"] and r["harness_unchanged"] for r in results),
        "no_network_blocks": all(not r["isolation"]["blocked_network_attempts"] for r in results),
        "all_budgets_stable_provenance": all(s["budget_failures"] == 0 and s["unstable_cases"] == 0 and s["provenance_expected"] == s["provenance_retained"] for r in results for s in r["summaries"].values())}
    report = {"experiment": "native Step2.1 on/off with shipped settings; primary700 diagnostic2000; no packet selector",
        "fixture_sha256": sha(FIXTURE), "integrity": integrity, "results": results, "pairs": pairs,
        "limitations": "Small fictional cases; real warmed loopback embeddings; pure observer wrappers included in timings; opposite pair run ordering; not production speed or corpus quality evidence."}
    Path(args.output).write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({"integrity": integrity, "arms": [{"label":r["label"],"enabled":r["enabled"],"summaries":r["summaries"]} for r in results]}, indent=2))
    print(f"Report: {Path(args.output).resolve()}")
    return 0 if all(integrity.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
