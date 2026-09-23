"""Frozen comparison over four read-only Titan snapshots; scratch artifacts only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import os
from pathlib import Path
import time

from jev_duplicate_prototype import judge_pairs, read_json, write_json
from jev_save_review import SIMPLE_INSTRUCTIONS, SIMPLE_OPTIONS
from laya_entailment_probe import QUESTION, live_fingerprints


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(root):
    assert not (root / "manifest.json").exists(), "Do not overwrite a frozen experiment"
    batches = []
    for b in range(4):
        directory = root / f"batch{b}"
        records = read_json(directory / "output.json")["records"]
        gold = read_json(directory / "gold_review.json")
        members = [i for g in gold["duplicate_groups"] for i in g["members"]]
        assert len(members) == len(set(members)) and all(0 <= i < len(records) for i in members)
        positives = {tuple(sorted(pair)) for g in gold["duplicate_groups"]
                     for pair in itertools.combinations(g["members"], 2)}
        ambiguous = {tuple(sorted(p["members"])) for p in gold.get("ambiguous_pairs", [])}
        assert not positives & ambiguous
        pairs = [{"pair_id": f"b{b}p{i:03}", "a": a, "b": c,
                  "gold": "ambiguous" if (a, c) in ambiguous else "same" if (a, c) in positives else "different"}
                 for i, (a, c) in enumerate(itertools.combinations(range(len(records)), 2))]
        assert len({r['session_id'] for r in records}) == 1
        batches.append({"batch": b, "memories": len(records), "pairs": pairs,
                        "baseline_sha256": digest(directory / "output.json"),
                        "gold_sha256": digest(directory / "gold_review.json")})
    write_json(root / "manifest.json", {
        "selection": "Four latest OpenCode sessions with 12–24 memories, by MAX(ts); all records, all within-session pairs. Not used in prior Pi/NASA tests.",
        "extraction": "Previously saved actual Titan records, no re-extraction; no deterministic fallback flags set.",
        "jev_question": SIMPLE_INSTRUCTIONS, "jev_options": SIMPLE_OPTIONS,
        "jev_policy": "Forward same and unclear, keep different. No threshold fitting.",
        "laya_question": QUESTION,
        "laya_policy": "Two directions; forward if any entailment and no contradiction. Otherwise keep both.",
        "review_policy": "Use predeclared source-reviewed groups; reject negative/ambiguous edges. Review connected candidate subsets explicitly; preserve other records unchanged.",
        "batches": batches, "frozen_before_inference": True,
    })


def inputs(root):
    manifest = read_json(root / "manifest.json")
    assert manifest["jev_question"] == SIMPLE_INSTRUCTIONS and manifest["jev_options"] == SIMPLE_OPTIONS
    assert manifest["laya_question"] == QUESTION
    for batch in manifest["batches"]:
        directory = root / f"batch{batch['batch']}"
        assert digest(directory / "output.json") == batch["baseline_sha256"]
        assert digest(directory / "gold_review.json") == batch["gold_sha256"]
    return manifest


def run_jev(root):
    manifest = inputs(root)
    total = sum(len(b["pairs"]) for b in manifest["batches"])
    target = root / "jev_report.json"
    assert not target.exists(), "Do not overwrite inference"
    calls, answers, errors = [], {}, []
    started_all = time.perf_counter()
    for batch in manifest["batches"]:
        records = read_json(root / f"batch{batch['batch']}" / "output.json")["records"]
        for start in range(0, len(batch["pairs"]), 15):
            pairs = batch["pairs"][start:start + 15]
            # Gold labels are never passed to the model.
            public_pairs = [{k: p[k] for k in ["pair_id", "a", "b"]} for p in pairs]
            for attempt in range(2):
                try:
                    result = judge_pairs(records, public_pairs, instructions=SIMPLE_INSTRUCTIONS,
                                         options=SIMPLE_OPTIONS, text_only=True)
                    break
                except Exception as exc:
                    errors.append({"batch": batch["batch"], "offset": start, "attempt": attempt + 1,
                                   "error_type": type(exc).__name__})
                    if attempt:
                        write_json(root / "jev_failure.json", {"calls": calls, "answers": answers, "errors": errors})
                        raise RuntimeError("Jev failed after one retry; see sanitized failure artifact") from None
            calls.append(result)
            answers.update(result["answers"])
            write_json(target, {"manifest_sha256": digest(root / "manifest.json"), "calls": calls,
                                "answers": answers, "errors": errors,
                                "elapsed_ms": round(sum(c["elapsed_ms"] for c in calls), 1),
                                "wall_ms": round((time.perf_counter() - started_all) * 1000, 1)})
            print(f"Jev {len(answers)}/{total} pairs", flush=True)


def run_laya(root, model_path):
    manifest = inputs(root)
    target = root / "laya_report.json"
    assert not target.exists(), "Do not overwrite inference"
    os.environ.update({"HF_HOME": str(root / "hf-cache"), "USE_TF": "0",
                       "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false"})
    import torch
    import laya
    from laya.common import build_sequence, render_options, serialize_state

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    started = time.perf_counter()
    agent = laya.load(str(model_path.resolve()), device="cpu")
    metadata = {"load_ms": round((time.perf_counter() - started) * 1000, 1),
                "revision": model_path.name, "sdk_version": importlib.metadata.version("laya"),
                "torch_version": torch.__version__, "device": "cpu", "threads": 4,
                "config": dict(agent.cfg)}
    internal = agent._to_internal(QUESTION)
    lengths = [len(agent.tok(" " + opt, add_special_tokens=False)["input_ids"]) for opt in render_options(internal)]
    assert max(lengths) <= 48
    head = len(agent.tok("choice question: " + internal["ins"], add_special_tokens=False)["input_ids"]) + sum(n + 1 for n in lengths)
    assert head <= agent.cfg["head_max_len"]
    prepared, audit = [], []
    for batch in manifest["batches"]:
        records = read_json(root / f"batch{batch['batch']}" / "output.json")["records"]
        for p in batch["pairs"]:
            states = []
            for a, b in [(p["a"], p["b"]), (p["b"], p["a"])]:
                state = {"premise": records[a]["text"], "hypothesis": records[b]["text"]}
                needed = head + len(agent.tok(serialize_state(state), add_special_tokens=False)["input_ids"]) + 4
                seq, markers = build_sequence(agent.tok, state, internal, agent.cfg["max_len"], agent.cfg["head_max_len"])
                assert len(seq) == needed and len(markers) == 3, (p["pair_id"], needed)
                audit.append(needed)
                states.append(state)
            prepared.append((p["pair_id"], states))
    metadata.update({"all_inputs_untruncated": True, "max_input_tokens": max(audit), "directional_calls": len(audit)})
    agent.predict(prepared[0][1][0], {"relation": QUESTION})
    results = []
    for qid, states in prepared:
        started = time.perf_counter()
        answers = [agent.predict(state, {"relation": QUESTION})["answers"]["relation"] for state in states]
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        choices = [a["choice"] for a in answers]
        results.append({"pair_id": qid, "answers": answers, "elapsed_ms": elapsed,
                        "candidate": "entailment" in choices and "contradiction" not in choices})
        if len(results) % 25 == 0 or len(results) == len(prepared):
            write_json(target, {"manifest_sha256": digest(root / "manifest.json"), "metadata": metadata,
                                "results": results, "elapsed_ms": round(sum(r["elapsed_ms"] for r in results), 1)})
            print(f"Laya {len(results)}/{len(prepared)} pairs", flush=True)


def evaluate(root):
    """Score frozen labels and prepare proposals for explicit final review."""
    manifest = inputs(root)
    jev, laya = read_json(root / "jev_report.json"), read_json(root / "laya_report.json")
    expected = {p["pair_id"] for b in manifest["batches"] for p in b["pairs"]}
    assert set(jev["answers"]) == expected
    assert len(laya["results"]) == len(expected) and {r["pair_id"] for r in laya["results"]} == expected
    assert jev["manifest_sha256"] == laya["manifest_sha256"] == digest(root / "manifest.json")
    flags = {"jev": {qid for qid, a in jev["answers"].items() if a["choice"] in {"same", "unclear"}},
             "laya": {r["pair_id"] for r in laya["results"] if r["candidate"]}}
    summary = {}
    for model, flagged in flags.items():
        batches = []
        for batch in manifest["batches"]:
            b = batch["batch"]
            directory = root / f"batch{b}"
            records = read_json(directory / "output.json")["records"]
            gold = read_json(directory / "gold_review.json")
            candidates = [p for p in batch["pairs"] if p["pair_id"] in flagged]
            positive_edges = {frozenset((p["a"], p["b"])) for p in candidates if p["gold"] == "same"}
            groups, grouped, full_groups, partial_groups = [], set(), 0, 0
            for g in gold["duplicate_groups"]:
                remaining, components = set(g["members"]), []
                while remaining:
                    component = {min(remaining)}
                    while True:
                        expanded = component | {i for i in remaining if any(frozenset((i, j)) in positive_edges for j in component)}
                        if expanded == component: break
                        component = expanded
                    remaining -= component
                    components.append(sorted(component))
                if len(components) == 1: full_groups += 1
                elif any(len(c) > 1 for c in components): partial_groups += 1
                for component in components:
                    if len(component) < 2: continue
                    complete = set(component) == set(g["members"])
                    groups.append({"members": component, "text": g["merged_text"] if complete else None,
                                   "reason": g["reason"], "gold_group": g["members"],
                                   "anchors": g["anchors"] if complete else [],
                                   "needs_subset_wording": not complete})
                    grouped.update(component)
            groups += [{"members": [i], "text": record["text"], "reason": "Retained unchanged"}
                       for i, record in enumerate(records) if i not in grouped]
            groups.sort(key=lambda g: min(g["members"]))
            target = directory / model
            target.mkdir(exist_ok=False)
            for g in groups:
                g["source_memory_ids"] = [records[i]["id"] for i in g["members"]]
            write_json(target / "proposed_review.json", {
                "session_id": f"expanded-{model}-batch{b}-reviewed", "before": len(records), "after": len(groups),
                "groups": groups, "candidate_reviews": candidates,
                "reviewer": "Proposal from frozen source labels; requires orchestrator verification before saving",
            })
            (target / "output.json").write_bytes((directory / "output.json").read_bytes())
            positive_count = sum(p["gold"] == "same" for p in batch["pairs"])
            tp = sum(p["gold"] == "same" for p in candidates)
            fp = sum(p["gold"] == "different" for p in candidates)
            ambiguous = sum(p["gold"] == "ambiguous" for p in candidates)
            batches.append({"batch": b, "memories": len(records), "pairs": len(batch["pairs"]),
                            "positive_pairs": positive_count, "ambiguous_pairs": sum(p["gold"] == "ambiguous" for p in batch["pairs"]),
                            "review_pairs": len(candidates), "review_unique_memories": len({i for p in candidates for i in (p["a"], p["b"])}),
                            "true_positives": tp, "false_positives": fp, "ambiguous_flags": ambiguous,
                            "false_negatives": positive_count - tp, "gold_groups": len(gold["duplicate_groups"]),
                            "fully_reached_groups": full_groups, "partly_reached_groups": partial_groups,
                            "proposed_after": len(groups), "needs_subset_wording": sum(g.get("needs_subset_wording", False) for g in groups),
                            "missed_positive_ids": [p["pair_id"] for p in batch["pairs"] if p["gold"] == "same" and p["pair_id"] not in flagged],
                            "false_positive_ids": [p["pair_id"] for p in candidates if p["gold"] == "different"]})
        keys = [k for k, v in batches[0].items() if isinstance(v, int) and k != "batch"]
        total = {k: sum(b[k] for b in batches) for k in keys}
        total["precision_excluding_ambiguous"] = total["true_positives"] / max(1, total["true_positives"] + total["false_positives"])
        total["recall"] = total["true_positives"] / max(1, total["positive_pairs"])
        total["screening_ms"] = (jev if model == "jev" else laya)["elapsed_ms"]
        summary[model] = {"totals": total, "batches": batches}
    write_json(root / "comparison.json", summary)
    print(json.dumps({m: d["totals"] for m, d in summary.items()}, indent=2))


def verify(root):
    manifest = inputs(root)
    summary = read_json(root / "comparison.json")
    checks = []
    for batch in manifest["batches"]:
        b = batch["batch"]
        records = read_json(root / f"batch{b}" / "output.json")["records"]
        gold_pairs = {frozenset((p["a"], p["b"])) for p in batch["pairs"] if p["gold"] == "same"}
        for model in ["jev", "laya"]:
            directory = root / f"batch{b}" / model
            review, saved = read_json(directory / "reviewed.json"), read_json(directory / "reviewed_saved.json")
            assert review.get("approved_by_orchestrator") is True
            members = [i for g in review["groups"] for i in g["members"]]
            assert sorted(members) == list(range(len(records)))
            assert len(saved["records"]) == saved["count"] == review["after"]
            assert saved["count"] == summary[model]["batches"][b]["proposed_after"]
            by_sources = {tuple(g["source_memory_ids"]): g for g in review["groups"]}
            by_id = {m["id"]: m for m in saved["records"]}
            for lineage in saved["lineage"]:
                group = by_sources[tuple(lineage["source_memory_ids"])]
                originals = [records[i] for i in group["members"]]
                record = by_id[lineage["id"]]
                assert record["text"] == group["text"]
                assert record["session_id"] == review["session_id"]
                assert record["source_event_ids"] == list(dict.fromkeys(e for m in originals for e in m["source_event_ids"]))
                assert lineage["source_qualified_ids"] == [{"agent": m["source_agent"], "id": m["id"]} for m in originals]
                for side in ["user", "assistant"]:
                    assert record["provenance"][side] == "\n\n".join(dict.fromkeys(m["provenance"][side] for m in originals))
                assert len(record["embedding"]) == 768
                if len(originals) == 1:
                    assert record["text"] == originals[0]["text"]
                    if originals[0]["embedding"]: assert record["embedding"] == originals[0]["embedding"]
                else:
                    assert all(frozenset(pair) in gold_pairs for pair in itertools.combinations(group["members"], 2))
            checks.append({"batch": b, "model": model, "saved": saved["count"],
                           "newly_embedded": saved["newly_embedded"], "partition_text_vectors_provenance_lineage_verified": True})
    assert live_fingerprints() == read_json(root / "live_before.json")
    write_json(root / "verification.json", {"checks": checks, "live_stores_unchanged": True,
                                            "frozen_inputs_labels_unchanged": True})
    print("Verified all eight scratch stores, every original's lineage and both provenance sides; live stores unchanged.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["freeze", "jev", "laya", "evaluate", "verify"])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()
    if args.phase == "freeze": freeze(args.run_dir)
    elif args.phase == "jev": run_jev(args.run_dir)
    elif args.phase == "evaluate": evaluate(args.run_dir)
    elif args.phase == "verify": verify(args.run_dir)
    else:
        if not args.model_path: parser.error("--model-path required for Laya")
        run_laya(args.run_dir, args.model_path)


if __name__ == "__main__":
    main()
