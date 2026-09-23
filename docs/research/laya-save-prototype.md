# Laya versus Jev: the same save experiment — 2026-09-22

Follow-up: [a shorter-prompt experiment](laya-simple-prompt-experiment.md) recovered the known duplicate group, with many more uncertain pairs. The results below describe the original detailed-prompt run.

**Laya did not match Jev on this sample.** It missed the known duplicate event and flagged two unrelated pairs. After reviewing those flags, its end-to-end result remained 18 memories. Jev's conservative result was 16.

## Completed loop

- [x] Freeze the previous real Titan extraction output: 18 memories from the same 686-word NASA reading session, plus the original source and blind review.
- [x] Install Laya into a separate ignored virtual environment and download the official English checkpoint.
- [x] Screen exactly the same 153 pairs using the same candidate-screening instructions, option descriptions and per-memory text/metadata.
- [x] Verify that both complete memories, instructions and all options fit into every Laya input.
- [x] Review all Laya flags against the source and the previously established independent judgments.
- [x] Save the reviewed result to a new scratch SQLite database and verify every text, vector, provenance field and source-event list by reading it back.
- [x] Verify frozen inputs and all live memory-row fingerprints remained unchanged.

## Observed comparison

| Measurement | Jev | Laya |
|---|---:|---:|
| Starting memories | 18 | 18 |
| Pairs screened | 153 | 153 |
| Flagged pairs | 7 | 2 |
| Known duplicate pair relationships found | 3 of 3 | 0 of 3 |
| Confirmed duplicate groups found | 1 | 0 |
| Final saved memories after review | **16** | **18** |
| Measured screening time | 15.15 s | 39.62 s |

The three known relationships belong to a single group of three memories describing the same record-breaking event. This is one positive group, not a statistically meaningful accuracy or recall estimate. Overall classification accuracy would also be misleading because almost all 153 pairs are distinct.

Laya's two suggestions were:

1. **Farthest-distance forecast versus pending crater-name submissions.** Both discuss future matters, but they are different facts.
2. **Completed launch versus scheduled splashdown.** These are distinct events, dates and lifecycle states.

The reviewer rejected both. The measured and predicted distances, corrected values, and other details therefore remain unchanged. The original duplicate event also remains: a downstream reviewer cannot remove a duplicate that the screening stage never forwards under this workflow.

For the genuine duplicate pairs, Laya chose `different`, with reported probabilities approximately 0.55, 0.78 and 0.75. Its two incorrect flags had low confidence. No threshold was fitted to this sample; lowering a threshold after seeing these answers would be a separate experiment.

## Model and execution details

The tested model is the [official English Laya checkpoint](https://huggingface.co/convaiinnovations/laya), revision `1c5edc17a7acd8701df6fc341c0d179f1c62c982`, using `laya==0.3.5`, PyTorch `2.14.0` and Transformers `5.17.0`.

It ran locally on an Apple arm64 CPU with four PyTorch threads. There was no fine-tuning, calibration, variant selection based on the results, or paid inference endpoint.

The [SDK](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py) shares one state across questions in a call. Each Laya call therefore received one pair and its question. Sending Jev's entire 15-pair state into Laya's short context would risk truncating the later pairs. The relevant pair contents and question wording were preserved; irrelevant neighboring pairs were not included.

The original token limits, 512 total and 192 for the question/options, were sufficient. The largest full input was 460 tokens; the largest question/options section was 149. Every sequence was audited before scoring. No input or option truncation occurred, and no limit increase was needed.

The 39.62-second total covers 153 sequential, warmed local calls; the median was 254 ms per pair. Model download took 128 seconds, loading took 27 seconds, and an unscored warmup took 277 ms. These setup costs are excluded from screening time.

Jev's earlier result used 11 hosted requests containing up to 15 pairs each. CPU versus hosted hardware and different batching make these **observed deployment timings**, not a hardware-controlled model-speed benchmark. The quality failure is the decisive result here.

## Isolation and limits

The extraction baseline comes from the previous real core save-pipeline run. Reusing its frozen output controls the comparison: this run tests Laya screening → review → reviewed persistence. It does not rerun extraction, alter the saved inputs, or insert Laya into production.

Since no merge passed review, all 18 existing embeddings were retained without regeneration. The new scratch database preserves all original text and supporting evidence. Original extraction omissions remain unchanged; no new information loss was introduced by review.

The model's general capabilities are not established by one article. This does establish that the tested English checkpoint, with these instructions, is not a working replacement for Jev in our proposed workflow. Additional prompts or fine-tuning could be separate experiments; they were not used to improve these scores after the fact.

No app/configuration files, live memory rows or installed Titan runtime were changed. The script compiled, all 153 actual model calls completed, and saved-record checks passed. The broad application suite was not run because application behavior was not changed.

## Artifacts and reproduction

- Runner: [laya_save_probe.py](../../tools/benchmarks/laya_save_probe.py)
- Previous comparison baseline: [Jev save experiment](jev-save-prototype.md)
- Local ignored artifacts: `.bench/laya-save-prototype/`, including `frozen_inputs.json`, `metadata.json`, `laya_report.json`, `reviewed.json`, `reviewed_saved.json`, `comparison.json`, and `reviewed-store/memory_store.db`.

With the prepared inputs and isolated environment:

```bash
.bench/laya-save-prototype/venv/bin/python tools/benchmarks/laya_save_probe.py \
  --run-dir .bench/laya-save-prototype
```

This reruns inference and overwrites the screening report. Review new results before saving another reviewed store; the persistence helper refuses to overwrite an existing store.

**Decision:** continue evaluating Jev as the candidate screener. Do not switch this workflow to the tested Laya checkpoint based on the current evidence.
