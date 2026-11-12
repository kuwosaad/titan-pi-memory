# Offline stored-memory duplicate evaluation

This fixture evaluates pairwise duplicate decisions without opening Titan stores,
calling an embedding provider, or writing memory data. It draws its regression
motifs from the repository's retrieval regression coverage: near-duplicate
collapse across scenes, preservation of opposing decisions, and source-qualified
memory identity. The remaining pairs are synthetic boundary and adversarial cases.

Run the conservative exact-text baseline:

```sh
python3 tools/benchmarks/bulk_dedup_eval.py
```

To score a detector, provide a JSON object mapping every fixture ID to a boolean
(`true` means merge as duplicate):

```sh
python3 tools/benchmarks/bulk_dedup_eval.py --predictions /path/to/predictions.json --json
```

The positive class comprises exact duplicates, paraphrase duplicates, and
same-event duplicates. Complementary detail, contradiction/change over time,
independent similar facts, and provenance-sensitive pairs default to separate.
Provenance cases may override that default with `expected`; they test explicit
provenance handling such as same-event corroboration while keeping the source
identity visible. Metrics report duplicate precision and false-merge rate, as
well as recall and per-label counts. False merges are the priority error because
they can erase distinct facts or evidence origin.

## Current fixture results

The included baseline merges only normalized exact text. It has perfect precision
on this fixture but low recall: it is a safety floor, not a semantic duplicate
detector. Its provenance exception permits exact text from different source
channels only when an explicit shared event ID exists, while differing agent
namespaces remain separate.

## Limits

This is a small hand-labeled benchmark, not a representative corpus or a
statistical estimate of production error rates. The pairs are English and
single-claim oriented; real memories can contain several claims, missing dates,
uncertain provenance, source reliability differences, and aliases. It tests
pairwise decisions only, not transitive clustering, representative selection,
merge summarization, graph lineage, or retrieval ranking. Provenance policy is
deliberately conservative and may need a different expected decision for a
specific product contract. A detector should be evaluated on an expanded,
blindly labeled corpus before thresholds are chosen.
