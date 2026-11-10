# Existing-memory dedup: save-pipeline integration

## Finding

An offline corpus scan should remain a separate, read-only maintenance workflow. Do not put corpus-wide semantic matching into the ordinary save path. The current save pipeline has no active existing-memory duplicate scan: `run_memory_pipeline_outcome` accepts optional `existing_text_hashes` (`app/save_pipeline/pipeline.py:131-141`) and filters against it (`:164-175`), but repository search finds no caller passing it. This is dormant optional behavior, not online dedup.

Current dedup layers are narrower:

- Extraction sanitization removes exact case-insensitive duplicate strings within one extraction batch (`app/save_pipeline/extraction/extractor.py:280-330`); the prompt also asks the extractor to avoid paraphrase duplicates.
- Trace ingestion is idempotent on `(session_id, event_id)` (`app/storage/traces.py:141`, `:615-677`), preventing replay of the same source event from starting a second save.
- Memory append is idempotent only for the same memory ID and same immutable derivation. SQLite serializes the check/insert in `BEGIN IMMEDIATE` and enforces the ID primary key (`app/storage/memories.py:603-664`, `:391-424`); JSON append similarly compares same-ID rows while preserving historical duplicate rows (`:261-296`). Neither detects equal text under different IDs.
- Retry recovery skips scenes already represented by a memory in that session (`app/save_pipeline/pipeline.py:810-845`), but this is scene-level retry idempotency, not corpus duplicate detection.

The optional hash filter is unsuitable as the planned feature: it hashes only `text.strip().lower()` (`pipeline.py:127-129`), has no durable index or concurrency boundary, and would discard the newly extracted record without preserving its new scene/evidence association. Even if wired to a snapshot of existing hashes, concurrent saves could both pass the check. More importantly, same text may be supported by distinct evidence or have different temporal/source meaning.

## Lineage and race constraints

Each saved memory receives a scene ID plus a source-event subset resolved from extraction evidence refs (`pipeline.py:204-230`, `:1654-1680`). Scenes separately preserve raw interaction evidence. Reusing a prior memory row on text match would lose the new derivation lineage unless a many-to-many evidence relationship is added; unioning event IDs into its existing `source_event_ids` would falsely suggest those events derived the original memory. Keep each saved record and its lineage intact during initial scan/benchmark work.

Memory IDs encode session/turn/index and same-ID append has a transactional boundary, but no unique constraint exists on canonical text, text hash, or semantic equivalence. Adding a pre-append query creates a check-then-insert race unless it is performed inside the same write transaction with a defined concurrency-safe key. A semantic/model decision should not be converted into that key: it is uncertain and may vary across model versions.

The `MemoryStore` contract exposes append and reads only, with no update, delete, alias, or duplicate-evidence operation (`app/storage/repository.py:19-45`). Therefore cross-ID consolidation cannot safely be applied today. IDs may be externally referenced; graph state (`h`, `tau`, incoming/outgoing weights) is mutable and not part of immutable derivation identity. No destructive dedup should be routed through `append_memories` or retrieval.

## Minimal staged recommendation

1. **Keep discovery offline and read-only.** Add a standalone command/module that opens one explicitly identified namespace and emits candidate pairs/groups plus method, scores, record IDs, source agent, type/stream, scene IDs, and source-event IDs. Start with exact normalized hashes and lexical candidates; add semantic embeddings/ANN only as candidate generation. Treat model outputs as review signals, never proof or deletion instructions. Keep pair evidence and do not treat connected components as duplicate families without pairwise checks.
2. **Measure before policy.** Benchmark candidate recall and false-positive classes on a labeled sample (copies, edits, paraphrases, related facts, contradictions, changed date/number/entity). Report potential logical bytes and scratch-copy compacted bytes separately. No live store size or dedup prevalence was measured in this audit.
3. **Review-only semantic results.** Produce a stable plan artifact and human-readable evidence comparison. The existing storage-safety report (`docs/research/bulk-dedup-storage-safety.md`) specifies snapshots, fingerprints, receipts, rollback, exact-duplicate handling, and why cross-ID apply is currently unsupported. Reuse that plan rather than adding another save-time mechanism.
4. **Only later design apply contracts.** Before cross-ID deletion, add durable aliases/redirects, multi-source provenance/evidence, reference/graph-weight migration, update/delete APIs, and verified rollback. Keep those outside ordinary ingestion. If a future save-time optimization is wanted, first define whether repeated claims should create a new evidence-bearing memory or attach evidence to an existing canonical claim; then implement that behavior transactionally and test concurrent saves. Do not infer the policy from the dormant text-hash parameter.

## Exact integration anchors

- Save sequence and dormant optional hash filter: `app/save_pipeline/pipeline.py:131-238`.
- Within-extraction exact dedup: `app/save_pipeline/extraction/extractor.py:280-330`.
- Truthful per-memory evidence resolution: `app/save_pipeline/pipeline.py:1654-1680`.
- Same-ID JSON append semantics: `app/storage/memories.py:261-296`.
- Same-ID SQLite transactional append semantics: `app/storage/memories.py:603-664`.
- Append/read-only repository boundary: `app/storage/repository.py:19-45`.
- Existing safe scan/apply/rollback design and limitations: `docs/research/bulk-dedup-storage-safety.md`, especially “Cross-ID semantic consolidation” and “Minimal future seam”.
