"""Offline synthetic Laya duplicate-screen probe; never opens Titan stores."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

QUESTION = "Does a and b describe the same fact or event, even if one adds details?"
CASES = [
    ("duplicate", "The Atlas team chose SQLite for local memory storage.", "SQLite was selected by the Atlas team to store memories locally.", True),
    ("entailment", "The Atlas team chose SQLite for local memory storage.", "The Atlas team chose SQLite for local memory storage because it needs offline reads.", False),
    ("contradiction", "Atlas uses SQLite for local memory storage.", "Atlas does not use SQLite for local memory storage.", False),
    ("superseded", "Atlas uses SQLite for local memory storage.", "Atlas replaced SQLite with Postgres for local memory storage.", False),
    ("distinct", "Atlas uses SQLite for local memory storage.", "The Atlas CLI prints colored status labels.", False),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True, help="Already-downloaded pinned Laya checkpoint")
    parser.add_argument("--output", type=Path, help="Optional JSON result path")
    args = parser.parse_args()
    if not args.model_path.exists():
        parser.error(f"checkpoint not found: {args.model_path}; this probe does not download models")
    try:
        import laya
        import torch
    except ImportError as exc:
        parser.error(f"Laya runtime unavailable ({exc}); install the pinned probe environment first")

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    agent = laya.load(str(args.model_path.resolve()), device="cpu")
    results = []
    for kind, left, right, gold in CASES:
        choices = [agent.predict({"premise": a, "hypothesis": b}, {"relation": QUESTION})["answers"]["relation"]["choice"]
                   for a, b in ((left, right), (right, left))]
        candidate = "entailment" in choices and "contradiction" not in choices
        results.append({"kind": kind, "gold_duplicate": gold, "directions": choices, "review_candidate": candidate})
    tp = sum(r["gold_duplicate"] and r["review_candidate"] for r in results)
    fp = sum(not r["gold_duplicate"] and r["review_candidate"] for r in results)
    fn = sum(r["gold_duplicate"] and not r["review_candidate"] for r in results)
    report = {"model": str(args.model_path), "device": "cpu", "question": QUESTION, "results": results,
              "precision": tp / (tp + fp) if tp + fp else None,
              "recall": tp / (tp + fn) if tp + fn else None,
              "warning": "Five author-written cases are a smoke probe, not a quality estimate."}
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
