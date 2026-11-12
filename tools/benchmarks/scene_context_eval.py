#!/usr/bin/env python3
"""Frozen four-arm scene packet experiment, isolated/offline unless --embedding local.

Run: PYTHONDONTWRITEBYTECODE=1 python3 tools/benchmarks/scene_context_eval.py \
 --embedding local --output ../scene-context-evaluation.json
This evaluates common full-metadata packets, not the native compact brief.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/scene_context_cases.json"
FROZEN_SHA256 = "7eaa9154928fc9660b5623ac8e34f683f80ddfa8604282df058e810cc3e4ee91"
ARMS = ("flat", "step2", "expansion_only", "contextual_selection", "contextual_packet_embeddings")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_bundle_fingerprint():
    paths = list((ROOT / "app").rglob("*.py")) + list((ROOT / "config").glob("*.yaml")) + [Path(__file__).resolve()]
    value = hashlib.sha256()
    for path in sorted(paths):
        value.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return value.hexdigest()


def identity(memory):
    return str(memory.get("source_agent") or ""), str(memory.get("id") or "")


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    at = (len(ordered) - 1) * fraction
    lo = int(at)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (at - lo)


def timings(values):
    return {"n": len(values), "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95)}


def validate_fixture(fixture):
    errors = []
    for case in fixture.get("cases", []):
        rows = case["memories"]
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)):
            errors.append(case["id"] + ": duplicate fixture ID")
        for option in case["required_sets"]:
            if not set(option) <= set(ids):
                errors.append(case["id"] + ": unknown expected ID")
        if not set(case["acceptable_ids"]) <= set(ids):
            errors.append(case["id"] + ": unknown acceptable ID")
    if not fixture.get("cases"):
        errors.append("missing cases")
    return errors


def rendered_records(result):
    records = []
    for line in result.get("rendered", "").splitlines():
        packet = json.loads(line)
        records.extend(packet["records"])
    return records


def score_result(case, result, stored_rows, parameters, resolved_scenes=None):
    """Credit actual rendered claim text, never a scene/duplicate pointer alone."""
    records = rendered_records(result)
    visible, provenance, invalid = set(), set(), []
    for record in records:
        memory = record.get("memory") or {}
        key = identity(memory)
        original = stored_rows.get(key)
        if original and memory.get("text") == original["text"]:
            visible.add(memory["id"])
            if (memory.get("scene_id") == original["scene_id"]
                    and set(memory.get("source_event_ids") or []) == set(original["source_event_ids"])
                    and (resolved_scenes is None or (
                        original["text"] in (resolved_scenes.get((key[0], original["scene_id"]), {}).get("extraction_user_text") or "")
                        and set(original["source_event_ids"]) <= set(resolved_scenes.get((key[0], original["scene_id"]), {}).get("source_event_ids") or [])))):
                provenance.add(memory["id"])
        else:
            invalid.append(list(key))
    options = [set(option) for option in case["required_sets"]]
    best = min(options, key=lambda option: (len(option - visible), len(option), sorted(option)))
    unknown = all(not option for option in options)
    complete = not records if unknown else any(option <= visible and option <= provenance for option in options)
    bad_ids = visible - set(case["acceptable_ids"])
    context_ids = {r["memory"]["id"] for r in records if r.get("role") == "scene_context"}
    lengths_ok = len(records) <= parameters["top_k"] and len(result.get("rendered", "")) <= parameters["max_chars"]
    duplicates = len(records) - len({identity(r["memory"]) for r in records})
    refs = [ref for r in records for ref in r["memory"].get("evidence_refs") or []]
    scope_violations = []
    filters = case.get("filters", {})
    for record in records:
        memory = record["memory"]
        if (memory.get("source_agent") not in filters.get("sources", [memory.get("source_agent")])
                or (filters.get("date_from") and str(memory.get("ts") or "") < filters["date_from"])
                or (filters.get("date_to") and str(memory.get("ts") or "") > filters["date_to"])
                or (filters.get("mode", "both") != "both" and memory.get("stream") != filters["mode"])
                or (filters.get("memory_types") and memory.get("type") not in filters["memory_types"])
                or float(memory.get("source_reliability", 0)) < filters.get("min_reliability", .4)):
            scope_violations.append(memory["id"])
    complete = complete and not scope_violations
    consistency_errors = []
    if result.get("stats", {}).get("rendered_chars", len(result.get("rendered", ""))) != len(result.get("rendered", "")):
        consistency_errors.append("reported characters differ from rendered characters")
    if result.get("selected_ids") is not None and [tuple(i) for i in result["selected_ids"]] != [identity(r["memory"]) for r in records]:
        consistency_errors.append("selected IDs differ from rendered records")
    return {"complete_required_evidence": bool(complete and lengths_ok and not invalid and not consistency_errors),
        "clean_complete": bool(complete and not bad_ids and lengths_ok and not invalid and not consistency_errors),
        "required_best_set": sorted(best), "missing_required_ids": sorted(best - visible),
        "visible_ids": sorted(visible), "irrelevant_ids": sorted(bad_ids),
        "irrelevant_context_ids": sorted(context_ids - set(case["acceptable_ids"])),
        "provenance_valid_ids": sorted(provenance), "invalid_records": invalid,
        "provenance_retained": len(visible & provenance), "provenance_expected": len(visible),
        "forbidden_ids": sorted(visible & set(case.get("forbidden_ids", []))),
        "unknown_answer_case": unknown, "abstained": not records,
        "rendered_chars": len(result.get("rendered", "")), "record_count": len(records),
        "duplicate_record_identities": duplicates, "alternate_evidence_ref_count": len(refs),
        "budget_ok": lengths_ok, "scope_violations": scope_violations, "consistency_errors": consistency_errors,
        "wrong_project_ids": sorted(bad_ids) if case["id"] == "wrong-project-neighbor" else [],
    }


def native_order_result(seeds, build, arguments):
    """Same renderer/budgets, retaining public seed order; diagnostic only."""
    packets, parts, hits = [], [], []
    for seed in seeds:
        single = build([seed], [], **arguments, arm="expansion_only")
        if not single["rendered"]:
            continue
        cost = len(single["rendered"]) + bool(parts)
        if len(hits) + len(single["hits"]) > arguments["top_k"] or sum(len(p) for p in parts) + max(0, len(parts)-1) + cost > arguments["max_chars"]:
            continue
        parts.append(single["rendered"])
        packets.extend(single["packets"])
        hits.extend(single["hits"])
    return {"rendered": "\n".join(parts), "packets": packets, "hits": hits}


def worker(args):
    home = Path(os.environ["TITAN_HOME"])
    source_path = ROOT / "app/retrieval_pipeline/scene_context_experiment.py"
    source_before = digest(source_path)
    bundle_before = source_bundle_fingerprint()
    harness_before = digest(__file__)
    fixture = json.loads(FIXTURE.read_text())
    parameters = fixture["parameters"]
    original_connect = socket.socket.connect
    network_attempts = []

    def connect(sock, address):
        if args.embedding == "local" and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 11434:
            return original_connect(sock, address)
        network_attempts.append(str(address))
        raise RuntimeError("scene evaluator blocked network access")
    socket.socket.connect = connect
    sys.path.insert(0, str(ROOT))
    import numpy as np
    import yaml
    from unittest.mock import patch
    from app.embedding import embedder
    from app.retrieval_pipeline import config, retriever
    from app.retrieval_pipeline.federated import FederatedRecall
    from app.retrieval_pipeline.scene_context_experiment import build_scene_context_result
    from app.storage.memories import SqliteMemoryRepository
    from app.storage.scenes import SqliteSceneRepository
    from app.storage.repository import CandidateFilters
    settings = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
    setting_path = home / "settings.yaml"
    setting_path.write_text(yaml.safe_dump(settings))
    os.environ["TITAN_SETTINGS_PATH"] = str(setting_path)
    embedding_path = home / "embedding.yaml"
    embedding_path.write_text(yaml.safe_dump({"current": "ollama", "ollama": {
        "base_url": "http://127.0.0.1:11434", "model": "nomic-embed-text:v1.5"}}))
    os.environ["TITAN_EMBEDDING_CONFIG_PATH"] = str(embedding_path)
    calls, observed_vectors = [], {}
    real_provider = embedder.ollama_embed

    def provider(texts, model, base_url):
        started = time.perf_counter()
        if args.embedding == "local":
            vectors = real_provider(texts, model, base_url)
        else:
            # Label-blind bag-of-words vectors for offline harness smoke only.
            vectors = []
            for text in texts:
                vector = np.zeros(256, dtype=np.float32)
                for token in re.findall(r"\w+", text.lower()):
                    if token not in {"a", "an", "the", "of", "to", "its", "for", "is", "and"}:
                        slot = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % len(vector)
                        vector[slot] += 1
                vectors.append(vector)
        for text, vector in zip(texts, vectors):
            observed_vectors[text] = vector.copy()
        calls.append({"texts": len(texts), "bytes": sum(len(t.encode()) for t in texts),
                      "ms": (time.perf_counter() - started) * 1000})
        return vectors

    results = []
    bootstrap = []
    all_fingerprint = hashlib.sha256()
    with patch.object(embedder, "ollama_embed", side_effect=provider):
        for case in fixture["cases"]:
            started = time.perf_counter()
            call_start = len(calls)
            source_rows = case["memories"]
            vectors = embedder.embed([row["text"] for row in source_rows])
            repositories, scene_repositories, stored_rows, vector_map = {}, {}, {}, {}
            for turn, (source, vector) in enumerate(zip(source_rows, vectors), 1):
                row = {**source, "turn": turn, "source_type": "user", "verification_status": "unverified",
                    "fallback_generated": False, "speaker_focus": "user", "memory_kind": "user_fact",
                    "source_event_ids": ["event-" + source["scene_id"]],
                    "provenance": {"user": source["text"], "assistant": "Recorded."}, "embedding": vector.tolist()}
                key = identity(row)
                stored_rows[key] = row
                vector_map[key] = vector.tolist()
                all_fingerprint.update(np.asarray(vector, dtype=np.float32).tobytes())
            agents = sorted({row["source_agent"] for row in source_rows})
            for agent in agents:
                db = home / case["id"] / "agents" / agent / "out/memories/memory_store.db"
                repo = SqliteMemoryRepository(db)
                rows = [row for row in stored_rows.values() if row["source_agent"] == agent]
                repo.append_memories(rows)
                scene_repo = SqliteSceneRepository(db)
                scenes = {}
                for row in rows:
                    if row["scene_id"] not in scenes:
                        scenes[row["scene_id"]] = {"scene_id": row["scene_id"], "session_id": row["session_id"],
                            "turn": row["turn"], "kind": "message_exchange", "ts": row["ts"],
                            "source_event_ids": row["source_event_ids"], "extraction_user_text": "",
                            "extraction_assistant_text": "Recorded.", "messages": []}
                    scene = scenes[row["scene_id"]]
                    scene["extraction_user_text"] += row["text"] + "\n"
                    scene["messages"].append({"role": "user", "content": row["text"]})
                scene_repo.append_scenes(list(scenes.values()))
                repositories[agent] = SqliteMemoryRepository(db, initialize=False)
                scene_repositories[agent] = SqliteSceneRepository(db, initialize=False)
            bootstrap.append({"case": case["id"], "ms": (time.perf_counter()-started)*1000,
                              "embedding_calls": len(calls)-call_start})
            recall = FederatedRecall(active_agent="eval", memory_repositories=repositories,
                scene_repositories=scene_repositories, include_active_buffer=False)
            filters = case["filters"]
            for arm in ARMS:
                arm_settings = copy.deepcopy(settings)
                arm_settings.setdefault("step2", {})["enabled"] = arm == "step2"
                runs, elapsed, native_diagnostic = [], [], None
                calls_before = len(calls)
                for repeat in range(parameters["repeats"] + 1):
                    started = time.perf_counter()
                    with patch.object(config, "load_settings", return_value=arm_settings):
                        hits = recall.query_hits(case["query"], limit=parameters["seed_pool_k"],
                            sources=filters["sources"], mode=filters.get("mode", "both"),
                            date_from=filters.get("date_from"), date_to=filters.get("date_to"),
                            memory_types=filters.get("memory_types"), min_reliability=filters.get("min_reliability"))
                        seeds = hits[:parameters["top_k"]]
                        siblings = []
                        read_count = 0
                        if arm in {"expansion_only", "contextual_selection", "contextual_packet_embeddings"}:
                            candidate_filters = CandidateFilters(recency_days=settings.get("retrieval_recency_days"),
                                session_id=None, session_bias=False, memory_types=filters.get("memory_types"),
                                mode=filters.get("mode", "both"),
                                min_reliability=filters.get("min_reliability", settings.get("retrieval", {}).get("min_reliability", .4)),
                                date_from=filters.get("date_from"), date_to=filters.get("date_to"))
                            for agent in filters["sources"]:
                                rows = repositories[agent].query_candidates(candidate_filters)
                                read_count += len(rows)
                                siblings.extend({**row, "source_agent": agent}
                                    for row in retriever.apply_hidden_metadata_filter(rows))
                        arguments = {"query_vector": observed_vectors.get(case["query"]), "vectors": vector_map,
                            "top_k": parameters["top_k"], "max_chars": parameters["max_chars"],
                            "relationship_bonus_cap": parameters["relationship_bonus_cap"]}
                        packet_embedding_inputs = []
                        contextual_vectors = None
                        if arm == "contextual_packet_embeddings":
                            # Diagnostic materialization exposes the SAME bounded full
                            # packets; it is not returned evidence and has no labels.
                            prepared = build_scene_context_result(seeds, siblings,
                                **{**arguments, "top_k": 9, "max_chars": 100000}, arm="expansion_only")
                            embedding_keys = []
                            for packet in prepared["packets"]:
                                if len(packet["records"]) > 1:
                                    embedding_keys.append(tuple(packet["seed_id"]))
                                    packet_embedding_inputs.append("\n".join(record["memory"]["text"] for record in packet["records"]))
                            if packet_embedding_inputs:
                                packet_vectors = embedder.embed(packet_embedding_inputs)
                                contextual_vectors = {key: vector.tolist() for key, vector in zip(embedding_keys, packet_vectors)}
                        result = build_scene_context_result(seeds, siblings, **arguments,
                            contextual_vectors=contextual_vectors,
                            arm="contextual_selection" if arm in {"contextual_selection", "contextual_packet_embeddings"} else "expansion_only")
                        result["contextual_packet_embedding_inputs"] = packet_embedding_inputs
                    elapsed.append((time.perf_counter()-started)*1000)
                    result["scope_filtered_store_read_count"] = read_count
                    result["returned_pool_ids"] = [list(identity(h["memory"])) for h in hits]
                    result["public_seed_order"] = [list(identity(h["memory"])) for h in seeds]
                    result["public_seed_scores"] = [{"id": list(identity(h["memory"])),
                        "score": h.get("score"), "selection_score": h.get("selection_score"),
                        "step2_delta": h.get("step2_delta")} for h in seeds]
                    if repeat == 0 and arm in {"flat", "step2"}:
                        native_diagnostic = native_order_result(seeds, build_scene_context_result, arguments)
                    runs.append(result)
                result = runs[0]
                audit_started = time.perf_counter()
                resolved_scenes = {}
                for record in rendered_records(result):
                    memory = record["memory"]
                    scene_key = (memory["source_agent"], memory["scene_id"])
                    if scene_key not in resolved_scenes:
                        resolved_scenes[scene_key] = scene_repositories[scene_key[0]].get_scene(scene_key[1]) or {}
                scored = score_result(case, result, stored_rows, parameters, resolved_scenes)
                scored["durable_scene_audit_ms"] = (time.perf_counter()-audit_started)*1000
                scored["durable_scene_audit_count"] = len(resolved_scenes)
                expected = set().union(*map(set, case["required_sets"]))
                pool_ids = {key[1] for key in result["returned_pool_ids"]}
                scoped_ids = {row["id"] for row in source_rows if row["source_agent"] in filters["sources"]}
                scored.update({"case": case["id"], "query": case["query"], "arm": arm,
                    "required_ids_outside_returned34_pool": sorted(expected-pool_ids),
                    "context_ids_outside_returned34_pool": sorted(key[1] for key in result["context_ids"] if key[1] not in pool_ids),
                    "corpus_records": len(source_rows), "scope_source_records": len(scoped_ids),
                    "returned_pool_count": len(result["returned_pool_ids"]),
                    "first_ms": elapsed[0], "steady": timings(elapsed[1:]), "steady_samples_ms": elapsed[1:],
                    "embedding_calls": len(calls)-calls_before,
                    "embedding_input_texts": sum(c["texts"] for c in calls[calls_before:]),
                    "repeat_identical": all(run == runs[0] for run in runs[1:]), "result": result})
                if native_diagnostic is not None:
                    scored["native_order_diagnostic"] = {"score": score_result(case, native_diagnostic, stored_rows, parameters),
                        "rendered": native_diagnostic["rendered"],
                        "order": [list(identity(h["memory"])) for h in native_diagnostic["hits"]],
                        "common_selector_reordered": [identity(h["memory"]) for h in native_diagnostic["hits"]] != [identity(h["memory"]) for h in result["hits"]]}
                results.append(scored)
    summaries = {}
    for arm in ARMS:
        cases = [c for c in results if c["arm"] == arm]
        unknown = [c for c in cases if c["unknown_answer_case"]]
        summaries[arm] = {"cases": len(cases), "complete_evidence_cases": sum(c["complete_required_evidence"] for c in cases),
            "clean_complete_cases": sum(c["clean_complete"] for c in cases),
            "irrelevant_records": sum(len(c["irrelevant_ids"]) for c in cases),
            "irrelevant_context_records": sum(len(c["irrelevant_context_ids"]) for c in cases),
            "forbidden_records": sum(len(c["forbidden_ids"]) for c in cases),
            "unknown_abstentions": sum(c["abstained"] for c in unknown), "unknown_cases": len(unknown),
            "rendered_chars": sum(c["rendered_chars"] for c in cases),
            "embedding_calls": sum(c["embedding_calls"] for c in cases),
            "embedding_input_texts": sum(c["embedding_input_texts"] for c in cases),
            "provenance_retained": sum(c["provenance_retained"] for c in cases),
            "provenance_expected": sum(c["provenance_expected"] for c in cases),
            "budget_failures": sum(not c["budget_ok"] for c in cases),
            "invalid_records": sum(len(c["invalid_records"]) for c in cases),
            "scope_violations": sum(len(c["scope_violations"]) for c in cases),
            "consistency_errors": sum(len(c["consistency_errors"]) for c in cases),
            "wrong_project_records": sum(len(c["wrong_project_ids"]) for c in cases),
            "duplicate_record_identities": sum(c["duplicate_record_identities"] for c in cases),
            "durable_scene_audit_ms": sum(c["durable_scene_audit_ms"] for c in cases),
            "steady_request": timings([value for c in cases for value in c["steady_samples_ms"]]),
            "unstable_cases": sum(not c["repeat_identical"] for c in cases),
            "steady_case_p50": timings([c["steady"]["p50_ms"] for c in cases])}
    paired = []
    for case in fixture["cases"]:
        by_arm = {c["arm"]: c for c in results if c["case"] == case["id"]}
        expansion, contextual = by_arm["expansion_only"], by_arm["contextual_selection"]
        paired.append({"case": case["id"],
            "same_public_seeds": expansion["result"]["public_seed_order"] == contextual["result"]["public_seed_order"],
            "same_inspected_siblings": expansion["result"]["inspected_sibling_ids"] == contextual["result"]["inspected_sibling_ids"],
            "contextual_complete_gain": int(contextual["complete_required_evidence"])-int(expansion["complete_required_evidence"]),
            "contextual_irrelevant_delta": len(contextual["irrelevant_ids"])-len(expansion["irrelevant_ids"]),
            "output_changed": contextual["visible_ids"] != expansion["visible_ids"]})
    gates = {}
    for contextual_arm in ("contextual_selection", "contextual_packet_embeddings"):
        contextual = summaries[contextual_arm]
        comparison_arms = ("flat", "step2", "expansion_only")
        complete_gain = all(contextual["complete_evidence_cases"] > summaries[arm]["complete_evidence_cases"] for arm in comparison_arms)
        valid_outputs = all(contextual[name] == 0 for name in ("budget_failures", "invalid_records", "scope_violations", "consistency_errors", "duplicate_record_identities", "unstable_cases")) and contextual["provenance_retained"] == contextual["provenance_expected"]
        regressions = []
        for case in fixture["cases"]:
            by_arm = {c["arm"]: c for c in results if c["case"] == case["id"]}
            current = by_arm[contextual_arm]
            for other_arm in comparison_arms:
                other = by_arm[other_arm]
                if len(current["wrong_project_ids"]) > len(other["wrong_project_ids"]):
                    regressions.append({"case": case["id"], "against": other_arm, "kind": "wrong_project"})
                if current["unknown_answer_case"] and not current["abstained"] and other["abstained"]:
                    regressions.append({"case": case["id"], "against": other_arm, "kind": "no_answer"})
        candidates_equal = all(p["same_public_seeds"] and p["same_inspected_siblings"] for p in paired)
        for case in fixture["cases"]:
            by_arm = {c["arm"]: c for c in results if c["case"] == case["id"]}
            candidates_equal = candidates_equal and by_arm[contextual_arm]["result"]["public_seed_order"] == by_arm["expansion_only"]["result"]["public_seed_order"] and by_arm[contextual_arm]["result"]["inspected_sibling_ids"] == by_arm["expansion_only"]["result"]["inspected_sibling_ids"]
        gates[contextual_arm] = {"accepted": bool(complete_gain and valid_outputs and not regressions and candidates_equal and all(contextual["irrelevant_records"] <= summaries[arm]["irrelevant_records"] for arm in comparison_arms)),
            "no_total_irrelevant_regression": all(contextual["irrelevant_records"] <= summaries[arm]["irrelevant_records"] for arm in comparison_arms),
            "complete_gain_over_all_three_baselines": complete_gain, "valid_provenance_budgets_schema_scope": valid_outputs,
            "wrong_project_or_no_answer_regressions": regressions, "same_candidates_as_expansion": candidates_equal}
    return {"fixture_sha256": digest(FIXTURE), "algorithm_sha256": source_before,
        "algorithm_unchanged": source_before == digest(source_path),
        "source_bundle_sha256": bundle_before, "source_bundle_unchanged": bundle_before == source_bundle_fingerprint(),
        "harness_sha256": harness_before, "harness_unchanged": harness_before == digest(__file__),
        "embedding_mode": args.embedding,
        "seed_vectors_sha256": all_fingerprint.hexdigest(), "parameters": parameters,
        "experiment_version": "supplemental_five_arm_v2_original_four_arm_report_preserved",
        "algorithm": {"seed_choice": "first3 public query_hits(limit34), preserving returned order",
            "flat_note": "flat and Step2 use common score-priority packet selector; native-order diagnostics included",
            "scope_note": "sources and repository date/type/stream/reliability are strict; no session/project permission claim",
            "hop": 1, "source_scene_cap": 3, "new_siblings_per_scene": 2,
            "sibling_order": "query-sibling cosine times seed-sibling cosine descending",
            "cosine_gates": {"query_sibling": .25, "seed_sibling": .25},
            "context_score": "base + min(.1, .1 * max(query-sibling cosine * seed-sibling cosine)); no contextual_vectors supplied",
            "packet_embedding_recipe": "frozen before initial output inspection: materialize same packets using top_k9/max_chars100000; one batch up to3 expanded-only newlinejoined seed+siblings; final selection3/2400; preparationCPU/provider included; diagnosticmaterialization not evidence",
            "packet_embedding_score": "base + min(.1, max(.1*MAXassociation, cosine(query,packet_embedding)-cosine(query,seed_embedding),0)); no contextual sibling rescue",
            "selector": "descending packet score; admitted-order ties; full first; seed-only fallback only if full infeasible; total records3/chars2400",
            "cost_note": "full public-metadata JSON renderer, not native compact brief; store read scans all filtered rows; only selected hop bounded",
            "internal_pool_note": "returned34 is observable output pool; internal multi-aspect pool may60; no internal-out-of-pool claim"},
        "isolation": {"fresh_titan_home": True, "blocked_network_attempts": network_attempts,
            "bytecode_disabled": bool(os.environ.get("PYTHONDONTWRITEBYTECODE")), "stores_read_only_during_retrieval": True},
        "integrity": {"frozen_fixture_matches": digest(FIXTURE) == FROZEN_SHA256,
            "algorithm_source_unchanged": source_before == digest(source_path),
            "source_bundle_unchanged": bundle_before == source_bundle_fingerprint(),
            "harness_unchanged": harness_before == digest(__file__),
            "no_blocked_network_attempts": not network_attempts,
            "all_outputs_valid": all(all(summary[field] == 0 for field in ("budget_failures", "invalid_records", "scope_violations", "consistency_errors", "duplicate_record_identities", "unstable_cases")) and summary["provenance_retained"] == summary["provenance_expected"] for summary in summaries.values()),
            "all_context_arms_same_candidates": all(gate["same_candidates_as_expansion"] for gate in gates.values())},
        "measurement_revision_note": "Compared with preserved original4 report: adds durable SQLite scene/text/event audit, actual39request quantiles, provenance/budget/scope/duplicate/stability/irrelevance hard gates, and broader source fingerprints. No algorithm/threshold/case-label changes.",
        "bootstrap": bootstrap, "summaries": summaries, "paired_contextual_vs_expansion": paired,
        "paired_packet_embeddings_vs_expansion": [{"case": case["id"],
            "complete_gain": int(next(c for c in results if c["case"]==case["id"] and c["arm"]=="contextual_packet_embeddings")["complete_required_evidence"])-int(next(c for c in results if c["case"]==case["id"] and c["arm"]=="expansion_only")["complete_required_evidence"]),
            "visible_output_changed": next(c for c in results if c["case"]==case["id"] and c["arm"]=="contextual_packet_embeddings")["visible_ids"] != next(c for c in results if c["case"]==case["id"] and c["arm"]=="expansion_only")["visible_ids"],
            "same_inspected_siblings": next(c for c in results if c["case"]==case["id"] and c["arm"]=="contextual_packet_embeddings")["result"]["inspected_sibling_ids"] == next(c for c in results if c["case"]==case["id"] and c["arm"]=="expansion_only")["result"]["inspected_sibling_ids"]
        } for case in fixture["cases"]],
        "contextual_acceptance_gates": gates, "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding", choices=["synthetic", "local"], default="synthetic")
    parser.add_argument("--output", default=str(ROOT.parent / "scene-context-evaluation.json"))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if digest(FIXTURE) != FROZEN_SHA256:
        parser.error("frozen fixture changed")
    errors = validate_fixture(json.loads(FIXTURE.read_text()))
    if errors:
        parser.error(str(errors))
    if args.worker:
        print(json.dumps(worker(args), sort_keys=True))
        return 0
    with tempfile.TemporaryDirectory(prefix="titan-scene-eval-") as temporary:
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "VIRTUAL_ENV"}}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "TITAN_HOME": temporary,
            "TITAN_BASE_DIR": temporary, "TITAN_SHARED_HOME": temporary, "TITAN_AGENT_NAME": "eval",
            "TITAN_MEMORY_BACKEND": "sqlite", "TITAN_MEMORY_READ_FALLBACK": "none"})
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--embedding", args.embedding]
        completed = subprocess.run(command, env=env, cwd=temporary, text=True, capture_output=True, timeout=300)
        if completed.returncode:
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
        report = json.loads(completed.stdout)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"summaries": report["summaries"], "contextual_acceptance_gates": report["contextual_acceptance_gates"]}, indent=2))
    print(f"Report: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
