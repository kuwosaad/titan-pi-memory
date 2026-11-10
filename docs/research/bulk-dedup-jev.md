# Offline duplicate-finder probe for existing Titan memories

**Recommendation: start with a read-only exact-text inventory and review report. Do not auto-merge or delete memories.** Exact normalized text is a high-confidence narrow signal; simple lexical overlap misses paraphrases and confuses contradictions, negation, and changed values. The small synthetic probe below is a sanity check, not a population-level accuracy estimate.

## Scope and safety

This probe uses only 12 hand-authored synthetic pairs. It does not import Titan, locate a store, read any user memory, make network/model calls, or write result artifacts. Run it with:

```bash
python3 tools/benchmarks/bulk_dedup_jev_probe.py
```

The current save pipeline has an optional `existing_text_hashes` filter before embedding (`app/save_pipeline/pipeline.py`, `run_memory_pipeline_outcome`), but no caller supplies it today. It is therefore not an active comparison against stored memories. Storage separately treats immutable record identity as ID plus canonical payload; it explicitly leaves legacy rows with different IDs alone (`app/storage/memories.py`, `_same_memory` and the legacy duplicate handling around line 266). A bulk tool should follow that preservation stance and emit candidates without changing storage.

## Method and observed results

The probe compares two offline signals: case/punctuation/whitespace-normalized exact equality, and Jaccard overlap of unique whitespace tokens at a fixed 0.60 threshold. The hand-authored labels call only exact duplicates and two paraphrases “same”; changed qualification, contradiction, negation, numeric value, endpoint, lifecycle/status, or subject are different. This is deliberately small and includes adversarial near-matches.

| Signal | Precision | Recall | Interpretation |
|---|---:|---:|---|
| Normalized exact equality | 100% (2/2) | 50% (2/4) | Good candidate for human-confirmed duplicate review; misses paraphrases. |
| Token overlap ≥ 0.60 | 25% (2/8) | 50% (2/4) | Too noisy for dedup decisions; flagged contradiction, negation, number/status changes, and different people. |

The script's measured local runtime was about 0.1 ms for 12 pairs on this run, with no network, model, or vector cost. Runtime is illustrative only; pairwise comparison scales quadratically if every pair is compared. For a bulk corpus, normalize and hash each text in one pass for exact duplicates, then compare only targeted candidate subsets if a later review workflow is justified.

### Jev structured decision run

**Correction:** the first Jev request was contaminated. It used fixture IDs such as `exact-01` and `comp-01` as question/state IDs, which disclosed the class. Its apparent 20/20 result is invalid and must not be cited as model accuracy.

The corrected request sent all 28 repository-authored synthetic pairs in one batch to `jev-1.13-free` at OpenCode Zen's documented System One endpoint. It assigned opaque `pair_01`…`pair_28` IDs and randomized pair order with fixed seed 4281. Fixture IDs, labels, and notes stayed local and were used only for scoring. The request included only each pair's text and, when present, `source`, `agent`, and `event_id` fields needed for provenance cases. The question asked whether one duplicate memory could be removed while preserving useful meaning, details, and provenance. Choices were `same`, `different` (including meaningful added detail or changed facts), and `unclear` (preserve both).

| Corrected result on all 28 pairs | Count |
|---|---:|
| True positive | 10 |
| False positive | 18 |
| True negative | 0 |
| False negative | 0 |
| `unclear` | 0 |
| Precision / recall for `same` | 35.7% / 100% |
| Requests / pairs | 1 / 28 |
| Observed latency | 1.43 s |

Jev chose `same` for every pair. The 18 false positives cover complementary detail, changed quantities/status, negation, independent similar facts, differing provenance and subjects. The obvious failure mode is a one-class response despite explicit criteria. Therefore these results provide no basis for Jev as an automatic deduplication judge; at most, reconsider its use as a broad candidate screen only after further blinded evaluation. The request and response validation succeeded. Token usage and billing amount were not retained, so cost is unmeasured. OpenCode documents the `jev-1.13-free` model and typed choice questions at [OpenCode Zen](https://opencode.ai/docs/en/zen/).

### Controlled batch-size check

To test whether that one-class behavior came from the large batch, the same 28 blinded pairs were sent in four separate calls of seven. Prompt text and choices were identical; each call used opaque IDs, the same fixed-seed shuffled order, and only text plus relevant source/agent/event metadata. No fixture labels, IDs, or notes were included in any request.

| Four batches of seven | Count |
|---|---:|
| True positive | 9 |
| False positive | 12 |
| True negative | 6 |
| False negative | 1 |
| `unclear` | 0 |
| Precision / recall for `same` | 42.9% / 90% |
| Requests / pairs | 4 / 28 |
| Per-call latency | 1.32, 1.33, 1.26, 1.34 s |
| Total latency | 5.25 s |

Batch outcomes varied sharply: the first two batches chose `same` for all 14 pairs, the third chose `different` for all seven, and the fourth chose `same` for all seven. The all-different batch contained one true duplicate (exact-01); the other three batches generated all 12 false positives. The first two calls' `same` scores were mostly 0.52–0.69; the third call's `different` scores were 0.26–0.31 on its six non-duplicate pairs and 0.27 for the missed exact pair's `same` probability; the final batch's `same` scores were 0.75–0.81. This indicates strong batch/context instability, not merely collapse on one 28-pair request. No request or validation failures occurred. Cost/token usage was not captured.

## Relevance of the prior Jev experiment

The historical work is on `origin/experiment/jev-retrieval-dedup`, commit `22b05d1a50ab80bc9430f4e2b23d8b41611569ff` (based on `bbb454b213db7bcdb987c1abc3557ab79517c3de`). Its retrieval report, `docs/research/jev-retrieval-prototype.md`, found that the judge called 22/80 pairs `same`; a separate blind AI review agreed on only 10 of those 22. At a selected 0.8 probability threshold, 8/10 accepted pairs were judged same by that reviewer. This was not calibrated or held-out validation, and the report found brief regressions and no demonstrated end-to-end retrieval improvement. Its conclusion was to keep the integration experimental.

The companion `docs/research/jev-save-prototype.md` describes a different use: Jev only screened possible overlap in a constructed public-text example; a separate review approved one clear duplicate group, reducing 18 extracted memories to 16. It explicitly says this single example does not establish general accuracy or savings. Those findings support a candidate-and-review workflow, not automatic cleanup. The historic scripts also depended on an authorized hosted endpoint; they are not offline tools.

## Reuse recommendation

1. For an offline first pass, inventory normalized exact-text collisions with source/agent identity, IDs, timestamps, and full original texts in the report. Keep every record untouched. Verify normalization does not erase meaningful distinctions; begin with only case, whitespace, and punctuation normalization.
2. Keep near-match detection as a separately labeled review queue. Similarity is evidence that two memories share words, not evidence they are interchangeable. Never suppress a candidate merely because another candidate overlaps with it.
3. If adding semantic screening later, reproduce the historical Jev protocol only against an exported, explicitly selected snapshot and require an independent human decision before any merge. Preserve provenance and all details in the merged text, and verify a fresh isolated store before considering a migration.
4. Before touching a real corpus, measure false positives on a representative, manually labeled sample that includes negation, changed quantities, scope, attribution, plans versus completion, and supersession. The present toy probe is not sufficient to set production thresholds.

## Limitations

The labels are synthetic and intentionally curated, not human-reviewed examples from Titan. Token Jaccard treats words as unordered and has no understanding of entities, dates, lifecycle, or entailment. Exact normalization may still equate punctuation changes that matter in some domains; keep original texts and inspect exact matches. No live-store path, user memories, or mutation behavior was tested.
