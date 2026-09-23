# Jev retrieval prototype — 2026-09-22

**Result: useful duplicate judgments, but no demonstrated end-to-end improvement. Do not enable this version in normal retrieval.** Some detail-sensitive pairs were incorrectly treated as interchangeable, and changing candidates before ranking sometimes displaced relevant facts from the final brief.

Branch: `experiment/jev-retrieval-dedup`, based on `bbb454b213db7bcdb987c1abc3557ab79517c3de`. The experiment made no installation or production changes.

## Completed checklist

- [x] Create an isolated branch/worktree; leave canonical main untouched.
- [x] Read 368 Pi memories through read-only SQLite and back up the database to a scratch copy.
- [x] Select 80 real memory pairs, including paraphrases, partial overlap, numbers/version changes, negations and ordering differences.
- [x] Label before calling Jev; obtain a separate blind review from one Luna agent using xhigh reasoning.
- [x] Call OpenCode Zen `jev-1.13-free` with `same / different / unclear`.
- [x] Replay 15 actual historical requests as retrieval queries against the scratch database.
- [x] Inspect collapsed pairs, final results, brief regressions and added latency.
- [x] Verify all 368 live memory texts, IDs, selected metadata and embeddings still match the export.

## Pair results

Jev returned `same` for 22 pairs, `different` for 58, and never selected `unclear`.

The primary labels were too permissive about added detail: they marked 25 pairs same; the blind reviewer marked only 12 same. Both label sets are AI judgments, not human ground truth. A single accuracy percentage would obscure this disagreement.

| Blind review | Jev same | Jev different |
|---|---:|---:|
| Same | 10 | 2 |
| Different | 12 | 55 |
| Unclear | 0 | 1 |

For the query replay, require Jev's reported `same` probability to be at least 0.8. This accepts ten of the original 80 pairs; eight are same according to the blind review and two are different. The disputed examples are “work on the repository and keep it updated” versus “keep it updated,” and “make changes then sync” versus “build features then sync.” Those qualifications can matter under our strict rule that either memory must preserve the other's meaning.

The 0.8 cutoff was chosen after inspecting the exploratory pair run. It is not calibrated or held-out validation. In addition, a pair's probability changed from 0.64 to 0.84 between the pair batch and a query batch; the cutoff does not establish stable correctness.

## Retrieval replay

Both arms used the same top 20 candidates at the existing near-duplicate grouping seam. Baseline used Titan's existing grouping. The experimental arm used embedding cosine >=0.85 to identify same-session pairs, capped at 40 pairs in one Jev request (observed maximum 36). It collapsed a group only when every member pair passed the rule, retaining the first candidate as representative.

The experiment replaced that grouping function only inside the script. It also prevented the later cosine redundancy filter from suppressing pairs Jev kept separate. Normal reranking, scene/event selection and brief construction continued afterward. This tests an integration change, not the judge alone. It is a matched 20-candidate comparison, not an unmodified full production baseline.

| Measurement | Observed |
|---|---:|
| Historical requests replayed | 15 |
| Candidate slots inspected | 300 |
| Slots collapsed by Jev | 18, across 11 queries |
| Final returned hits, summed across queries | 142 baseline → 190 experiment |
| Median added latency | 1.45 seconds |
| Maximum added latency | 2.25 seconds |

The 18 collapses include repeated groups across queries; they are not 18 unique memories or an improvement relative to baseline. Titan already suppresses duplicates. The experimental behavior retained more related memories overall, and repetition remained in some final briefs.

One clear regression: for a request about inaccurate dashboard data, the baseline brief included the dashboard-versus-graph mismatch. The experimental brief omitted it as other results consumed the 700-character budget. Another replay produced two near-identical skill-menu workflow lines. A collapsed “managed alongside” / “updated alongside” pair was also disputed by the blind review. Therefore neither fact preservation nor lower visible repetition was demonstrated reliably.

Query embeddings were warmed once and reused in both timed arms. Latency excludes embedding/model startup. These are selected historical requests replayed against one snapshot, not logged production retrievals or a temporally faithful reconstruction. No downstream answer-generation quality was tested.

## Files and reproduction

The standalone script is [jev_duplicate_prototype.py](../../tools/benchmarks/jev_duplicate_prototype.py). With the existing private inputs in this worktree:

```bash
python3 tools/benchmarks/jev_duplicate_prototype.py pairs --run-dir .bench/jev-duplicate-prototype
python3 tools/benchmarks/jev_duplicate_prototype.py queries --run-dir .bench/jev-duplicate-prototype
```

These commands make fresh authenticated free-model calls and overwrite local result files. They require the existing OpenCode credential and the repository's local embedding service/dependencies. The script consumes prepared inputs; it does not export a live database automatically.

Private memories, vectors, pair labels, query text, responses and the SQLite copy remain in ignored `.bench/jev-duplicate-prototype/`. They are not source artifacts. The experiment sends selected memory text and metadata to the authorized hosted Jev endpoint; embeddings stay local. There is no paid-model fallback.

Verification: both commands completed successfully; grouping checks passed for non-transitive pairs, missing answers, low probabilities and unclear decisions. Canonical main is clean. No application code changed, and the full application suite was not run. The live-record comparison does not claim byte-for-byte database or sidecar identity.

## Smallest next step

Keep this as an experiment. If continuing, test duplicate suppression on already-ranked results while preserving their order, and settle the few disputed detail-sensitive examples first. That would separate judge usefulness from reranking effects. Larger benchmarks and production infrastructure remain deferred.
