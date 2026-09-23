# Short-prompt comparison — 2026-09-22

**Simplifying the input helped Laya find the known duplicates.** Both models now produced 16 memories after review, starting from the same 18. Laya remained much less decisive about other pairs, creating more review work under the existing policy.

## One fixed change, declared before scoring

Question:

> In {pair_id}, do a and b describe the same fact or event, even if one adds details?

Options:

- `same`: Same fact or event.
- `different`: Different facts or events.
- `unclear`: Not enough information.

Each pair contained only its two original memory texts. Timestamps, source type and session metadata were omitted. This changes both prompt length and input format, so the improvement cannot be attributed to instruction wording alone.

The 18 memories, 153 pair identities, source evidence, Laya checkpoint/version, and conservative review judgments were frozen. We tested one prompt variant, with no fine-tuning or threshold fitting. Both models received the same short instructions and option descriptions.

## Results

| Measurement | Laya, previous detailed prompt | Laya, short prompt | Jev, short prompt |
|---|---:|---:|---:|
| `same` | 2 | 14 | 7 |
| `unclear` | 0 | 114 | 0 |
| `different` | 151 | 25 | 146 |
| Pairs sent to review | 2 | **128** | **7** |
| Known duplicate relationships found | 0/3 | **3/3** | **3/3** |
| Final memories after review | 18 | **16** | **16** |
| Screening time | 39.62 s | 19.74 s | 15.33 s |

The three positive relationships are all within one group of three memories describing the same event. This is one known duplicate group, not three independent successes or a reliable general accuracy estimate.

All three genuine relationships received `same` from Laya. Its other 11 `same` suggestions concerned distinct claims. Examples include a completed launch versus departure from Earth orbit, and a measured record-breaking event versus an editorial update to a future-distance forecast. Review kept these separate.

The existing policy forwards both `same` and `unclear`. That explains the 128 candidate reviews: 14 positive predictions plus 114 abstentions. These counts indicate candidate workload, not measured reviewer token costs or API latency. Review used the same previously established source judgments; a new review API was not benchmarked.

If uncertain pairs were simply retained without review, Laya would forward only 14 pairs and would still catch this sample's known group. That is an informative counterfactual, not a separately validated policy. We did not silently switch rules after seeing the results. Jev forwarded seven pairs under the existing rule.

## End-to-end verification

- Both screening runs completed all 153 comparisons.
- Laya reused exactly the previous pinned English checkpoint on CPU with four threads. Full input/option token audits passed without truncation.
- The approved three-memory group used the existing reviewed wording, retaining all details and evidence. Every other memory remained unchanged.
- Both reduced sets were embedded as needed, saved to separate scratch SQLite stores and read back successfully: 16 records each, one newly embedded merged text per store.
- Final texts, provenance and source-event lists matched the prior conservative Jev result. Frozen input hashes and live memory-row fingerprints remained unchanged.
- The modified prototype scripts compiled. No application/configuration files, installed Titan behavior or real memories were changed.

Laya timings are warmed sequential local CPU inference; Jev timings are 11 hosted batches. This is not a hardware-controlled speed comparison. Shorter inputs also reduce Laya's computation. Prior extraction omissions remain; no additional loss was found in the reviewed results.

## Interpretation

Our earlier failure did partly depend on how we asked the question. Laya should not be dismissed as unable to recognize these duplicates. However, matching the final memory count does not mean matching screening efficiency: the reviewer handled far more candidates under our current policy.

Jev remains the more selective screener on this sample. A Laya policy that keeps uncertain pairs unchanged is a plausible separate experiment, using fresh examples before relying on it. No training project or production integration was started.

## Reproduction

Prepared data and results are in ignored `.bench/simple-duplicate-prototype/`. `manifest.json` records the predeclared prompt and frozen hashes; `comparison.json` records counts and verification. The `laya/` and `jev/` subdirectories contain reviewed outputs and separate scratch stores.

```bash
python3 tools/benchmarks/jev_save_review.py \
  --run-dir .bench/simple-duplicate-prototype --simple

.bench/laya-save-prototype/venv/bin/python tools/benchmarks/laya_save_probe.py \
  --run-dir .bench/simple-duplicate-prototype --simple \
  --model-path .bench/laya-save-prototype/hf-cache/hub/models--convaiinnovations--laya/snapshots/1c5edc17a7acd8701df6fc341c0d179f1c62c982
```

These rerun inference and overwrite screening reports. Existing reviewed stores are preserved. Previous results: [detailed-prompt Laya comparison](laya-save-prototype.md), [original Jev save experiment](jev-save-prototype.md).
