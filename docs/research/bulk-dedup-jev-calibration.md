# Jev isolated-pair calibration

**Date:** 2026-09-24  
**Model:** `jev-1.13-free`  
**Probe:** `tools/benchmarks/bulk_dedup_jev_isolated_probe.py`

## Method

The initial 10-pair pilot used an easy balanced subset and a shorter prompt; it is superseded by the full-fixture run below and is not directly comparable with batch results. The full run sent 28 independent API requests, one pair per request, covering all synthetic fixture pairs. It used the same question, prompt, and choices as the batched probe. Each request used an opaque state/question ID. Fixture identifiers, labels, and notes remained local for scoring; only text and, where present, `source`, `agent`, and `event_id` metadata were sent. No live memories were used.

## Result

The full run matched all 10 positive examples and rejected 15 of 18 negatives. No `unclear` predictions occurred.

| Gold | Predicted same | Predicted different | Predicted unclear |
|---|---:|---:|---:|
| Same (10) | 10 | 0 | 0 |
| Different (18) | 3 | 15 | 0 |

Positive precision: **0.769** · Positive recall: **1.000** · Accuracy: **0.893** · Negative rejection: **0.833**.

All three false positives were provenance-sensitive negatives: the model merged identical text across distinct agent or evidence provenance. The other 15 negatives, spanning complementary details, contradictions/status changes, and independent similar facts, were classified `different`. All exact, paraphrase, and same-event duplicate positives were classified `same`.

| Category | Gold same | Gold different predicted same | Gold different predicted different |
|---|---:|---:|---:|
| Exact duplicate | 3/3 | — | — |
| Paraphrase duplicate | 4/4 | — | — |
| Same-event duplicate | 2/2 | — | — |
| Provenance-sensitive | 1/1 | 3/3 | 0/3 |
| Complementary detail | — | 0/4 | 4/4 |
| Contradiction/change over time | — | 0/5 | 5/5 |
| Independent similar facts | — | 0/6 | 6/6 |

The 28 call latencies ranged from 1.18 to 1.48 seconds (35.94 seconds total; 1.28 seconds mean). Same probabilities ranged from 0.68 to 1.00 on positives; the three false positives had probabilities 0.70, 0.76, and 0.77.

## Comparison with batched runs

The runs use the same 28 fixture pairs and gold labels. The full isolated run and both batch runs used the batched prompt/criteria; the 10-pair pilot above did not. The 28-pair batch, four batches of seven, and one-pair requests therefore compare request grouping under a shared prompt. One-pair requests still differ in context and opaque IDs, so results are evidence of instability, not a controlled estimate of a causal batch-size effect.

| Request grouping | Calls | TP | FP | TN | FN | Precision | Recall | Accuracy | Total observed latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| One batch of 28 | 1 | 10 | 18 | 0 | 0 | 35.7% | 100% | 35.7% | 1.43 s |
| Four batches of 7 | 4 | 9 | 12 | 6 | 1 | 42.9% | 90% | 53.6% | 5.25 s |
| One pair per request | 28 | 10 | 3 | 15 | 0 | 76.9% | 100% | 89.3% | 35.94 s |

The batched run rejected no negatives; splitting into four batches rejected six, while isolated requests rejected 15. The 28-pair isolated result is much stronger on this fixture, but provenance still produced three false merges, which makes the model unsuitable for automatic deduplication. Token use and billing were not captured for any run.

## Limits

Despite using every pair in this small fixture, this remains a curated synthetic evaluation, not an estimate of production performance. It does not test realistic class prevalence, representative memory text, repeatability, or robustness to different phrasings. The category counts are small, especially for provenance. The request grouping results vary materially and require replication before drawing conclusions about model behavior.
