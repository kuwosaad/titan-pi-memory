# Jev save experiment — 2026-09-22

**The small loop worked: 18 extracted memories became 16 after Jev screening and LLM review. No additional information loss was found relative to the extracted memories.** This is one public-text example, not evidence of general accuracy, faster retrieval, or lower total cost.

## What actually ran

- [x] Find public source text: NASA's [Artemis II record release](https://www.nasa.gov/news-release/nasas-artemis-ii-crew-eclipses-record-for-farthest-human-spaceflight/).
- [x] Use 686 source words in three natural sections: summary/correction, record/background, and flyby/return. Add a short reading-session request and acknowledgement. The source prose is real; the conversation wrapper is constructed.
- [x] Run Titan's existing `run_memory_pipeline_outcome` for each section, using configured OpenCode Go `deepseek-v4-flash` extraction and local Ollama embeddings. Persist and read back the baseline in scratch SQLite.
- [x] Ask Jev to screen every pair for potential duplication or redundant overlap. It flags candidates; it does not authorize merges.
- [x] Have the primary orchestrator review the flags against the source. Obtain a separate Luna duplicate review that cannot see Jev's choices, plus an independent source-fact check.
- [x] Merge only the agreed clear duplicate group, generate its new embedding, save a separate reduced scratch database, and verify text, vectors, provenance and source-event references by reading it back.
- [x] Verify that all 3,120 memory rows across the three live stores still match their pre-experiment logical fingerprints.

Five Luna agents assisted with source selection, the isolated runner, storage isolation, blind duplicate review and factual coverage. No application/configuration files, installed runtime, or real memories were changed.

## Results

| Measurement | Observed |
|---|---:|
| Saved baseline memories | 18: 4 + 5 + 9 across three exchanges |
| Pairs screened | 153 |
| Jev flags | 7 |
| Independently agreed clear duplicate groups | 1, containing 3 memories |
| Final memories | **16**, a reduction of 2 (11.1%) |
| Jev screening time | 15.15 seconds across 11 serial requests |
| Additional information loss found after merging | None in this sample |

All three pair relationships inside the clear duplicate group were flagged. The other flags were retained as separate memories. There were no clear duplicate groups identified by the independent reviewer that Jev missed; this is just one observed group, not a recall estimate.

The three merged memories described the same record-breaking event. One contained crew identities and elapsed mission time; another added the measured distance and clock time; the third repeated the achievement. The final wording retains all those details. Picking one original and discarding the others would have lost information.

Two flags confused a forecast with the instruction to record forecasts accurately. Another involved a corrected number versus a planned flyby that reused that number but included additional observations. These illustrate why review is necessary even when Jev is only screening.

An initial broader review also combined the farthest-distance forecast with the note that NASA updated that forecast, yielding 15 memories without detected factual loss. The blind reviewer classified this as optional consolidation, not clear duplicate removal. That variant is retained separately; **the reported deduplication result is 18 → 16**, not 18 → 15.

## What was preserved—and what was already missing

The independent review checked 12 important distinctions established before reading the output, including measured versus forecast distances, old versus corrected values, dates, crew identities, and proposed versus completed actions. These remain represented. Every baseline memory maps to one final memory, with the original evidence retained.

The original extractor omitted some article details, including picture-taking/camera details and broader program goals. Minor background details were also absent. Deduplication neither caused nor repaired those omissions. One of the 18 baseline memories records the synthetic wrapper's forecast-handling instruction; it was retained unchanged and does not count as a NASA source fact.

## Limitations and failures encountered

- This exercised the real core extraction → embedding → persistence path, not the event/spool intake and scene assembler. The three exchanges share a session, and duplicates were found across exchanges. It does not establish that a single extraction batch commonly contains duplicates.
- The baseline was saved first, then reviewed output was saved to another store. This demonstrates the proposed decisions and resulting records, not an installed pre-save hook or embedding-cost savings.
- OpenCode Go initially rejected the stock request without a routing session header (`MissingSessionID`). The experiment adds `x-opencode-session` locally; production code remains unchanged.
- One extraction request timed out. Only the failed third exchange was retried, preserving the nine records already saved. Successful extraction/save calls took approximately 26, 85 and 10 seconds; these are not stable latency measurements.
- Both normal fallback and the extractor's internal invalid-JSON fallback were disabled. The latter was patched to raise inside the probe so fabricated fallback output could not pass as real model extraction.
- Jev's `same` label meant “send this candidate to review,” including partial overlap. `unclear` would also go to review. No probability cutoff was used.
- The stronger-model review was performed by the orchestrator, with independent agent checks. We did not benchmark a separate review API's latency or compare against sending all 18 memories directly to one LLM. Net efficiency remains unproven.
- No retrieval-quality improvement was tested. No production integration or broad application test suite was run.

## Reproduction and artifacts

Code:

- [Core save runner](../../tools/benchmarks/jev_save_prototype.py)
- [Jev screening and reviewed persistence](../../tools/benchmarks/jev_save_review.py)
- [Shared Jev transport](../../tools/benchmarks/jev_duplicate_prototype.py)

Local inputs and results remain under ignored `.bench/jev-save-prototype/`: `source.json`, `input.json`, `output.json`, `jev_report.json`, `blind_duplicate_review.json`, `fact_check.json`, and `verification.json`.

**Final conservative output:** `final/reviewed.json` and `final/reviewed_saved.json`, with 16 records in `final/reviewed-store/memory_store.db`. The root `reviewed.json`/`reviewed_saved.json` preserve the earlier optional 15-memory consolidation for comparison. Original baseline rows remain intact.

Using the prepared inputs, these commands make fresh extraction/Jev calls and replace the corresponding JSON results:

```bash
python3 tools/benchmarks/jev_save_prototype.py
python3 tools/benchmarks/jev_save_review.py --run-dir .bench/jev-save-prototype
```

Review the new output before creating a new `reviewed.json`; old review indices must not be reused after re-extraction. `--save-reviewed` persists an approved review in a fresh scratch store and refuses to overwrite an existing store.

The next useful question is whether this pattern also appears in a few real conversations, and whether Jev screening helps enough to justify an extra call. This experiment supports that next small test, not automatic merging of existing memories.
