# Larger Jev/Laya save-screening comparison — 2026-09-22

**Jev performed better on this larger sample of actual Titan memories.** It found 23 of 27 clear duplicate relationships; Laya found 10. After source-based review, Jev reduced 71 records to 55 and Laya reduced them to 61. Both reviewed outputs were embedded as needed, saved and verified in isolated stores.

Laya produced fewer suggestions, but its precision was slightly lower as well as its recall being much lower. The earlier NASA result did not generalize well to these project conversations.

## Sample and frozen rules

Read-only snapshots of the four most recent OpenCode sessions containing 12–24 records were selected by `MAX(ts)`, before inspecting model results. These sessions were not in the previous Pi or NASA comparisons. All records and all within-session pairs were retained: 71 memories, four conversations, 626 comparisons. No cross-conversation pairs or similarity prefilter were used.

These are actual previously saved Titan extraction outputs, not synthetic examples. Extraction was not rerun; none of the selected records was marked as deterministic fallback. Nine existing records lacked vectors, so missing vectors were generated only when saving scratch results.

Two Luna xhigh reviewers drafted disjoint batches of labels. The primary orchestrator checked the texts and provenance and corrected several draft grouping/index mistakes before either model ran. Frozen labels identify 12 disjoint duplicate groups, yielding 27 positive pair relationships. Thirteen borderline pairs are reported separately; the other 586 pairs are negative. The original drafts, adjudicated labels and their hashes remain available for audit.

Same core facts/events may be consolidated with additional details preserved. Related facts, requests versus commitments/completions, findings versus policies, and changed scope are kept separate. Several source/extraction issues were recorded rather than silently corrected—for example, a clone plan extracted as completed and a remote-ref inventory counting `origin/HEAD` as a branch.

The existing model-specific configurations were fixed:

- **Jev:** previous short same/different/unclear question; forward same or unclear. Free `jev-1.13-free`, 15 pairs per hosted batch, original texts only.
- **Laya:** previous native premise/hypothesis relationship question, both directions; forward any entailment unless either direction contradicts. Same pinned English checkpoint, SDK 0.3.5, CPU with four threads.

No prompt variants, threshold fitting, training or post-result relabeling were used. Gold labels and provenance were never passed to either screener.

## Results

| Measurement | Jev | Laya |
|---|---:|---:|
| Clear duplicate relationships caught | **23/27 (85.2%)** | 10/27 (37.0%) |
| Clear duplicate relationships missed | **4** | 17 |
| Total pairs sent for review | 52 | **25** |
| True duplicate suggestions | **23** | 10 |
| Clear false suggestions | 23 | **12** |
| Borderline suggestions, scored separately | 6 | 3 |
| Precision, excluding borderline cases | **50.0%** | 45.5% |
| Unique memories involved in review | 40 | **32** |
| Duplicate groups fully connected by correct suggestions | **10/12** | 6/12 |
| Additional groups partly reached | 1 | 2 |
| Final saved memories after review | **55** | 61 |
| Reduction from 71 | **16 (22.5%)** | 10 (14.1%) |
| Screening time in this setup | **58.55 s** | 145.05 s |

There were no inference failures or retries. Jev completed 43 hosted batches, returning 52 same and 574 different results, with no unclear results. Laya completed 1,252 directional calls without truncation; its largest input was 196 tokens. Its 24.01-second model load and unscored warmup are excluded from screening time. This is an operational timing comparison, not a hardware-controlled model-speed benchmark.

| Conversation | Memories / pairs | Jev positive pairs found | Laya positive pairs found | Saved after Jev / Laya review |
|---|---:|---:|---:|---:|
| Branch merge discussion | 20 / 190 | 3/3 | 3/3 | 17 / 17 |
| Design-guide discussion | 23 / 253 | 4/4 | 2/4 | 19 / 21 |
| Circular-import investigation | 15 / 105 | 12/13 | 3/13 | 9 / 12 |
| Skill review and installation | 13 / 78 | 4/7 | 2/7 | 10 / 11 |

## What the errors mean

Jev often confused repeated subject matter with repeated information: a concrete stash action with a general stash policy, an investigation request with its finding, or an installation request with completion. Review rejected these suggestions.

Laya also confused some lifecycle states. Its smaller candidate list did not consistently contain better matches. It missed the repeated design-guide definition with complementary styling details, the completed repository move, and much of the repeated import-cycle description. This fits the known limitation of directional entailment: each memory can add facts absent from the other, even when a reviewer could safely consolidate the repeated core information.

Both models missed the complementary pre-install inspection workflow pair. Jev reached only two of three records describing a completed skill installation; Laya reached none. We merged only the candidate-connected subsets that passed review, rather than adding missed memories from the gold labels. The best possible result under all frozen clear groups would have been 53 records.

Candidate links were never treated as automatic permission to merge. Negative and borderline suggestions were retained separately. Every proposed group or partial group received explicit text review.

## Save and factual verification

Eight separate scratch SQLite stores were created, one per conversation/model. All 71 originals are represented exactly once in each model's resulting partition. Unmerged texts and available vectors remain unchanged. Changed texts and any missing vectors were embedded; final stores contain 13 newly embedded texts for Jev and 12 for Laya. Those counts include repair of missing baseline vectors and are not a measure of embedding savings.

The save helper was narrowly updated to preserve both sides of source provenance, record source-qualified lineage, accept isolated session names, and embed unchanged records that lacked vectors. Source-event unions, source identities, both provenance sides, final wording and 768-dimensional vectors were verified. The scratch records use representative classification fields; full original metadata remains in the snapshots and lineage artifacts. A production metadata merge policy was not implemented.

A separate Luna audit checked the 12 full-group merged texts without reading model outputs. It found no material factual loss and requested one precision improvement: explicitly state the reverse import edge in the detailed cycle description. That wording was improved in the final Jev review, re-embedded and saved again; the previous scratch version was retained. The primary orchestrator separately checked the three partial-group rewrites. All final saves passed verification after the amendment. No additional factual loss was found relative to the selected baseline memories; this does not establish that every original extraction was correct.

All three live memory stores (3,120 rows) retained identical read-only fingerprints. Canonical main remains clean. Prototype scripts compile; all eight final scratch stores passed readback and lineage checks. No production files or real memories were changed.

## Interpretation and limits

**Keep Jev as the leading candidate for this workflow.** On this sample it recovered more useful duplication, produced at least comparable suggestion quality, and ran faster in our setup. Laya's tailored question remains better than its first NASA configuration, but it is not matching Jev across these real conversations.

Both still need the intelligent reviewer: about half their non-borderline suggestions were false matches. Reviewer API cost and latency were not measured; review was performed by the orchestrator against predeclared judgments. No retrieval-quality test or full new extraction-to-pre-embedding integration was run. Fewer saved records do not by themselves establish lower total compute or SQLite disk usage.

This is four conversations and 12 duplicate groups, not 626 independent positive examples. Ten positive relationships come from one five-record group. Labels are source-reviewed judgments with explicit ambiguity handling, not a large human-labeled benchmark. The sample is limited to historical technical conversations from one Titan adapter; it does not establish performance on every kind of memory or adversarial input.

## Artifacts and reproduction

Private snapshots, provenance and outputs remain under ignored `.bench/jev-laya-expanded/`. Key files are `manifest.json`, `comparison.json`, `verification.json`, `final_fact_audit.json`, `fact_audit_resolution.json`, the two model reports, and each `batch*/{jev,laya}/reviewed.json` and `reviewed_saved.json`. Do not commit this directory.

`tools/benchmarks/jev_laya_expanded.py` provides freeze, model-run, evaluation and verification phases; `tools/benchmarks/jev_save_review.py` persists explicitly approved results. Run inference only in a fresh prepared directory; it refuses to overwrite an existing model report. Verification can be repeated safely:

```bash
python3 tools/benchmarks/jev_laya_expanded.py verify --run-dir .bench/jev-laya-expanded
```

Previous experiment: [Laya-specific entailment test](laya-entailment-experiment.md).
