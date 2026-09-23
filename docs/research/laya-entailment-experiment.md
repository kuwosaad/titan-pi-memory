# Laya-specific comparison experiment — 2026-09-22

**Using Laya's published inference format reduced the NASA candidate list to three pairs.** Review accepted two relationships belonging to the same duplicate group and rejected one unrelated pair. The final 16 memories match the prior conservative result. This is promising for selective screening, but it missed partial overlap and doubled local inference work.

## What changed and why

Laya's own XNLI evaluation represents two statements as `premise` and `hypothesis`, then classifies their relationship as entailment, neutral, or contradiction. We reused that question and criteria exactly. This is evidence about its published evaluation interface, not proof that our memories match its training distribution. [Official benchmark code](https://github.com/NandhaKishorM/laya/blob/main/research/scripts/build_benchmark_nb.py).

Plainly: does statement A establish statement B? We ask again with the statements reversed. Original memory texts are unchanged, without pair wrappers or orchestration instructions.

Before inference, we froze the following policy: send a pair to review if either direction returns entailment and neither returns contradiction. Otherwise retain both memories. Mutual entailment is reported separately, not chosen after seeing results. No confidence threshold, tuning, model switch, or automatic merge was used.

The test reused the exact 18 baseline records extracted by the real Titan pipeline, all 153 pair identities, and the pinned English Laya checkpoint. Fourteen new synthetic checks and their candidate labels were written before inference. These are small author-created sanity checks, not an independent benchmark.

## Results on the frozen NASA memories

| Measurement | Laya, previous short prompt | Laya, directional entailment | Jev, previous short prompt |
|---|---:|---:|---:|
| Pairs sent to review | 128 | **3** | 7 |
| Unique memories involved | 18 | **5** | 10 |
| Known duplicate pair relationships found | 3/3 | **2/3** | 3/3 |
| Known duplicate groups reached | 1/1 | **1/1** | 1/1 |
| Memories after conservative review | 16 | **16** | 16 |
| Screening time | 19.74 s | **40.35 s** | 15.33 s |

Jev's results are reused from the previous run. Local timings exclude model loading and warmup; Laya ran on CPU with four threads. Jev used hosted batches, so these are not hardware-controlled comparisons. This Laya run loaded the checkpoint in 23.20 seconds and made 306 directional calls for NASA.

The previous policy forwarded 114 `unclear` results as well as 14 `same` results. Changing that policy alone would have reduced its review list to 14, retaining all three positives. The new format therefore improves selectivity beyond removing uncertainty escalation, but at a recall cost. We changed both formulation and routing policy; the whole 128-to-3 improvement cannot be attributed to wording alone.

### Review of every current candidate

- **p008, memories 0/9:** same April 6 record-breaking event. Review accepted.
- **p078, memories 5/9:** the detailed measured-distance account and the summary describe that same event. Review accepted.
- **p113, memories 8/14:** April 2 orbital escape versus scheduled April 10 splashdown. Review rejected; both retained.

The reviewer explicitly examined memories 0/5/9 together against NASA paragraphs p1/p3 and preserved all their details. Grouping was not automatic transitive merging. The resulting text retains the six-day mission timing, crew identities/agencies, Apollo 13 comparison, measured distance and clock time.

**The missed relationship matters:** memories 0 and 5 each add details absent from the other. Laya returned neutral in both directions. Both reached review through the shorter memory 9; without that bridge, this duplicate pair would be missed. Requiring entailment in both directions would have flagged zero NASA pairs.

## Fresh checks

Candidate routing matched the predeclared expectation in **12/14** checks: four of five positives found, eight of nine negatives retained without review. Controls took 2.60 seconds for 28 directional calls.

- Exact repetition, a paraphrase, and two cases where one statement contains the other were flagged.
- Changed numbers, negation, planned/completed state, different entities, distinct pipeline steps, shared topic, changed decisions, and forecast/measured values were kept separate.
- **Miss:** two statements about choosing SQLite, each with a different extra detail, were treated as neutral. This reproduces the NASA partial-overlap limitation.
- **False flag:** an instruction to say a backup succeeded was treated as supporting a statement that it actually succeeded. A reviewer must distinguish instructions from evidence.

These are routing results, not 12/14 accuracy at logical inference. Some retained pairs were labeled contradiction even though both could hold—for example, different people approving a release, or decisions changing over time. The safe retention outcome does not validate those relationship labels.

## Save verification and scope

The frozen extraction was reused to isolate screening behavior. Current Laya inference, source review, merge, changed-text embedding, SQLite save and readback all ran. One merged text was newly embedded; the other 15 original vectors were reused. This does not benchmark a complete new pre-embedding extraction run or an automated reviewer API.

Verified:

- All 167 pairs completed in both directions; all inputs fit the default budget (maximum 249 tokens).
- Manifest and baseline hashes remained unchanged.
- Reviewed groups partition all 18 originals exactly once; all 15 singleton texts remain unchanged.
- Final wording matches the previously fact-checked conservative 16-memory result; prior extraction omissions remain, with no additional merge loss.
- Saved texts, vectors, source-event lists and provenance match SQLite readback.
- All three live stores, containing 3,120 rows, have unchanged read-only fingerprints. Canonical main remains clean.
- New script compiles. No production integration.

## Decision

Treat Laya as a selective redundancy screener using this task-specific format, with a strong reviewer preserving details. This experiment supports that direction; it does not establish that Laya beats Jev or saves total computation. Review volume fell, local inference time increased, and reviewer cost was not measured.

The next useful small test is a fresh real conversation containing duplicates with different extra details. That tests the known weakness without relying on a short summary memory to connect them. Broad partial-overlap detection remains unproven.

## Reproduction and artifacts

Script: `tools/benchmarks/laya_entailment_probe.py`. Use a **new** run directory; the runner refuses to overwrite existing results:

```bash
.bench/laya-save-prototype/venv/bin/python tools/benchmarks/laya_entailment_probe.py \
  --run-dir .bench/laya-entailment-repeat \
  --model-path .bench/laya-save-prototype/hf-cache/hub/models--convaiinnovations--laya/snapshots/1c5edc17a7acd8701df6fc341c0d179f1c62c982
```

Ignored `.bench/laya-entailment-prototype/` contains `manifest.json` (question, policy, frozen cases and labels), `laya_report.json` (every directional answer), `summary.json`, `reviewed.json`, `reviewed_saved.json`, `verification.json`, and the separate `reviewed-store/`.

Previous comparison: [short-prompt experiment](laya-simple-prompt-experiment.md).
