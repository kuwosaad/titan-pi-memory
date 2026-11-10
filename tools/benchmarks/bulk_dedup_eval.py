#!/usr/bin/env python3
"""Score pairwise stored-memory duplicate predictions without accessing Titan stores."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = ROOT / "tests/fixtures/bulk_dedup_cases.json"
DUPLICATE_LABELS = {"exact_duplicate", "paraphrase_duplicate", "same_event_duplicate"}
VALID_LABELS = DUPLICATE_LABELS | {
    "complementary_detail", "contradiction_change_over_time",
    "independent_similar_facts", "provenance_sensitive",
}


def text_of(value: Any) -> str:
    return value if isinstance(value, str) else str(value.get("text", ""))


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", text_of(value).casefold()).strip()


def default_prediction(case: dict[str, Any]) -> bool:
    """Conservative baseline: exact normalized text, except provenance constraints."""
    a, b = case["a"], case["b"]
    if normalized(a) != normalized(b) or not normalized(a):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        # Explicitly distinct agent identities imply namespace isolation.
        if a.get("agent") and b.get("agent") and a["agent"] != b["agent"]:
            return False
        # Conflicting evidence source classes cannot be silently collapsed.
        if a.get("source") and b.get("source") and a["source"] != b["source"]:
            return a.get("event_id") is not None and a.get("event_id") == b.get("event_id")
    return True


def expected_prediction(case: dict[str, Any]) -> bool:
    if "expected" in case:
        return case["expected"] == "duplicate"
    return case["label"] in DUPLICATE_LABELS


def metrics(cases: list[dict[str, Any]], predictions: dict[str, bool]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    by_label: dict[str, Counter[str]] = {}
    errors = []
    for case in cases:
        actual = expected_prediction(case)
        predicted = predictions[case["id"]]
        bucket = by_label.setdefault(case["label"], Counter())
        bucket["predicted_duplicate" if predicted else "predicted_separate"] += 1
        if actual and predicted:
            tp += 1
        elif predicted:
            fp += 1
            errors.append({"id": case["id"], "error": "false_merge", "label": case["label"]})
        elif actual:
            fn += 1
            errors.append({"id": case["id"], "error": "missed_duplicate", "label": case["label"]})
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else None
    return {
        "cases": len(cases), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "duplicate_precision": precision,
        "false_merge_rate_on_separate": fp / (fp + tn) if fp + tn else None,
        "duplicate_recall": tp / (tp + fn) if tp + fn else None,
        "dangerous_false_merges": sum(1 for item in errors if item["error"] == "false_merge"),
        "per_label": {key: dict(sorted(value.items())) for key, value in sorted(by_label.items())},
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--predictions", type=Path, help="JSON object mapping case IDs to true/false duplicate predictions")
    parser.add_argument("--json", action="store_true", help="Emit full metrics JSON")
    args = parser.parse_args()
    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload["cases"]
    for case in cases:
        if case.get("label") not in VALID_LABELS:
            parser.error(f"unknown label for {case.get('id')}: {case.get('label')}")
    if args.predictions:
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        missing = {case["id"] for case in cases} - predictions.keys()
        if missing:
            parser.error(f"missing predictions: {', '.join(sorted(missing))}")
        predictions = {case["id"]: bool(predictions[case["id"]]) for case in cases}
    else:
        predictions = {case["id"]: default_prediction(case) for case in cases}
    result = metrics(cases, predictions)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        precision = "n/a" if result["duplicate_precision"] is None else f"{result['duplicate_precision']:.3f}"
        fmr = "n/a" if result["false_merge_rate_on_separate"] is None else f"{result['false_merge_rate_on_separate']:.3f}"
        print(f"cases={result['cases']} tp={result['tp']} fp={result['fp']} tn={result['tn']} fn={result['fn']}")
        print(f"duplicate_precision={precision} false_merge_rate_on_separate={fmr} dangerous_false_merges={result['dangerous_false_merges']}")
        for label, counts in result["per_label"].items():
            print(f"{label}: {counts}")
        if result["errors"]:
            print("errors:")
            for error in result["errors"]:
                print(f"  {error['id']}: {error['error']} ({error['label']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
