"""Small frozen Laya-native entailment experiment; never mutates live Titan data."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

from jev_duplicate_prototype import read_json, write_json


# Exact question/criteria from Laya's published XNLI benchmark, not a chat prompt.
QUESTION = {
    "type": "choice",
    "instructions": "What is the relationship between `premise` and `hypothesis`?",
    "criteria": {
        "entailment": "the premise implies the hypothesis is true",
        "neutral": "the premise neither implies nor contradicts the hypothesis",
        "contradiction": "the premise implies the hypothesis is false",
    },
}
SOURCE = "https://github.com/NandhaKishorM/laya/blob/main/research/scripts/build_benchmark_nb.py"

# Authored and frozen before inference. These are sanity checks, not a benchmark.
CONTROLS = [
    ("exact", "Titan stores memories in SQLite.", "Titan stores memories in SQLite.", True),
    ("paraphrase", "The user prefers concise answers.", "The user likes responses that are brief.", True),
    ("subsumption", "Titan stores memories in a local SQLite database.", "Titan stores memories in SQLite.", True),
    ("subsumption_reverse", "The team chose Python.", "The team chose Python for its memory service on Monday.", True),
    ("partial_overlap", "The team chose SQLite on Monday.", "The team chose SQLite for its local memory service.", True),
    ("number", "The retry limit is 3 attempts.", "The retry limit is 5 attempts.", False),
    ("negation", "The user wants automatic memory merging enabled.", "The user does not want automatic memory merging enabled.", False),
    ("planned_done", "The migration is planned for Friday.", "The migration finished on Friday.", False),
    ("entity", "Mira approved the release.", "Omar approved the release.", False),
    ("steps", "The pipeline extracts memories from a conversation.", "The pipeline embeds the extracted memories.", False),
    ("topic", "Titan stores memories in SQLite.", "Titan ranks memories using relevance scores.", False),
    ("changed_decision", "On Monday the team chose SQLite.", "On Tuesday the team replaced SQLite with PostgreSQL.", False),
    ("instruction", "The backup completed successfully.", "When writing a summary, say the backup completed successfully.", False),
    ("forecast_measurement", "The forecast peak distance is 253000 miles.", "The measured distance is 249000 miles.", False),
]


def live_fingerprints():
    """Check only databases explicitly selected for this benchmark run."""
    selected = os.environ.get("TITAN_BENCH_VERIFY_DBS", "")
    if not selected:
        return None
    rows = []
    paths = sorted({Path(value).expanduser().resolve(strict=True)
                    for value in selected.split(os.pathsep) if value})
    for path in paths:
        digest, n = hashlib.sha256(), 0
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            for row in conn.execute("SELECT * FROM memories ORDER BY id"):
                digest.update(json.dumps(row, ensure_ascii=False, default=lambda v: v.hex()).encode())
                n += 1
        rows.append({"database": f"db-{len(rows) + 1}", "records": n, "sha256": digest.hexdigest()})
    return rows


def verify_live_fingerprints(before_path: Path):
    before = read_json(before_path)
    if before is None:
        return None
    current = live_fingerprints()
    if current is None:
        return None
    # Historical reports included local paths; compare their content without
    # requiring those paths to appear in newly generated reports.
    before = [{"database": f"db-{i + 1}", "records": row["records"],
               "sha256": row["sha256"]} for i, row in enumerate(before)]
    return current == before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    baseline = run.parent / "jev-save-prototype"
    shutil.copyfile(baseline / "output.json", run / "output.json")
    records = read_json(run / "output.json")["records"]
    pairs = read_json(baseline / "jev_report.json")["pairs"]
    cases = [{**p, "suite": "nasa", "left": records[p["a"]]["text"],
              "right": records[p["b"]]["text"]} for p in pairs]
    cases += [{"pair_id": f"c{i:02}", "suite": "control", "kind": kind,
               "left": a, "right": b, "expected_candidate": gold}
              for i, (kind, a, b, gold) in enumerate(CONTROLS)]
    manifest = {
        "question": QUESTION, "question_source": SOURCE,
        "input_form": "Only original texts as premise and hypothesis, run in both directions",
        "primary_policy": "Review if either direction entails and neither contradicts. Otherwise keep both.",
        "secondary_policy": "Report mutual entailment counts only; do not choose policy after seeing outputs.",
        "review": "No automatic merges; review every candidate against source and preserve unique details.",
        "nasa_positive_pair_ids": ["p004", "p008", "p078"],
        "nasa_gold_note": "Previously reviewed, not held out; all three belong to one group.",
        "cases": cases, "model_revision": args.model_path.name,
        "baseline_sha256": hashlib.sha256((run / "output.json").read_bytes()).hexdigest(),
        "fixed_before_inference": True,
    }
    write_json(run / "manifest.json", manifest)
    write_json(run / "live_before.json", live_fingerprints())
    os.environ.update({"HF_HOME": str(run / "hf-cache"), "USE_TF": "0",
                       "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false"})
    import torch
    import laya
    from laya.common import build_sequence, render_options, serialize_state

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    started = time.perf_counter()
    agent = laya.load(str(args.model_path.resolve()), device="cpu")
    metadata = {"load_ms": round((time.perf_counter() - started) * 1000, 1),
                "sdk_version": importlib.metadata.version("laya"), "torch_version": torch.__version__,
                "model_revision": args.model_path.name, "device": "cpu", "threads": 4,
                "config": dict(agent.cfg)}
    internal = agent._to_internal(QUESTION)
    head = len(agent.tok("choice question: " + internal["ins"], add_special_tokens=False)["input_ids"])
    options = [len(agent.tok(" " + opt, add_special_tokens=False)["input_ids"]) for opt in render_options(internal)]
    assert max(options) <= 48
    head += sum(n + 1 for n in options)
    assert head <= agent.cfg["head_max_len"]
    prepared, audit = [], []
    for case in cases:
        directions = []
        for left, right in [("left", "right"), ("right", "left")]:
            state = {"premise": case[left], "hypothesis": case[right]}
            needed = head + len(agent.tok(serialize_state(state), add_special_tokens=False)["input_ids"]) + 4
            seq, markers = build_sequence(agent.tok, state, internal, agent.cfg["max_len"], agent.cfg["head_max_len"])
            assert len(seq) == needed and len(markers) == 3, (case["pair_id"], needed)
            directions.append(state)
            audit.append({"pair_id": case["pair_id"], "direction": left, "tokens": needed})
        prepared.append((case, directions))
    metadata.update({"token_audit": audit, "all_inputs_untruncated": True,
                     "manifest_sha256": hashlib.sha256((run / "manifest.json").read_bytes()).hexdigest()})
    agent.predict(prepared[0][1][0], {"relation": QUESTION})
    results = []
    for case, states in prepared:
        answers, elapsed = [], 0
        for state in states:
            started = time.perf_counter()
            answers.append(agent.predict(state, {"relation": QUESTION})["answers"]["relation"])
            elapsed += (time.perf_counter() - started) * 1000
        choices = [a["choice"] for a in answers]
        results.append({"pair_id": case["pair_id"], "suite": case["suite"], "answers": answers,
                        "candidate": "entailment" in choices and "contradiction" not in choices,
                        "mutual_entailment": choices == ["entailment", "entailment"],
                        "elapsed_ms": round(elapsed, 1)})
        if len(results) % 15 == 0 or len(results) == len(prepared):
            write_json(run / "laya_report.json", {"metadata": metadata, "results": results})
            print(f"{len(results)}/{len(prepared)} pairs; {sum(r['candidate'] for r in results)} candidates", flush=True)
    summary = {}
    for suite in ["nasa", "control"]:
        subset = [r for r in results if r["suite"] == suite]
        gold = set(manifest["nasa_positive_pair_ids"]) if suite == "nasa" else {
            c["pair_id"] for c in cases if c.get("expected_candidate")}
        flagged = {r["pair_id"] for r in subset if r["candidate"]}
        summary[suite] = {"pairs": len(subset), "candidate_ids": sorted(flagged),
                          "true_positives": sorted(flagged & gold), "false_positives": sorted(flagged - gold),
                          "missed_positives": sorted(gold - flagged),
                          "mutual_entailment_ids": [r["pair_id"] for r in subset if r["mutual_entailment"]],
                          "elapsed_ms": round(sum(r["elapsed_ms"] for r in subset), 1)}
    summary["live_unchanged_after_inference"] = verify_live_fingerprints(run / "live_before.json")
    write_json(run / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
