# Finding duplicates in stored Titan memories

**Decision, 2026-09-24:** Build a read-only maintenance scan and review loop first. Do not let Jev, Laya, text similarity, or a connected-component cluster delete memories. The current storage contract cannot preserve retired memory IDs or multiple sources of evidence on one surviving row. The model experiments also do not support automatic deletion.

This work treats “Java” as the experimental **Jev** decision model. “Jest” may refer to **Laya**, the local entailment model in Titan's earlier dedup experiments; that interpretation remains unconfirmed. Neither name changes the storage safety requirement.

## What exists today

The merged save path filters duplicates within an extraction batch, deduplicates incoming trace events by `(session_id, event_id)`, and makes append idempotent for the *same memory ID*. Its optional `existing_text_hashes` parameter has no caller, so it does not currently compare a new memory with all stored memories. Distinct memory IDs can therefore contain repeated text. An existing-memory sweep belongs in an offline maintenance path, not inside every save. See [save integration](bulk-dedup-save-integration.md).

“Duplicate” needs an action-based definition: can one record disappear while every useful claim, its date, subject, source, scene, event lineage, and ID reference remain available? Equal or similar wording alone does not answer that. A more detailed memory can contain the old claim *and* a new one. Changed values and direct user statements versus agent inference need separate treatment.

## Three architectures considered

| Path | Discovery | Strength | Limit |
| --- | --- | --- | --- |
| Exact inventory | Hash normalized text per agent namespace | Fast, reproducible, works on large stores | Misses paraphrases; equal text can have different evidence |
| Hybrid candidate loop **(recommended next)** | Exact hash plus bounded top-k neighbors from existing embeddings, with lexical fallback for missing vectors; then optional Jev/Laya and human review | Finds paraphrases without comparing every pair | Candidates can include changed or opposing facts; model quality and candidate recall must be measured |
| Automatic consolidation | Classify groups, keep one row, delete others, compact database | Would actually reclaim space | Unsafe with current model accuracy and missing alias/evidence/update-delete storage contracts |

The hybrid path is a *discovery* architecture. Exact hashing is linear in the number of memories. Vector-neighbor search should cap candidate edges per record; a production approximate index such as [HNSW](https://arxiv.org/abs/1603.09320) may be needed at large scale. [MinHash/LSH](https://cs.brown.edu/courses/cs253/papers/nearduplicate.pdf) can help with edited copies but only proposes candidates. Do not hard-partition by scene or source: a duplicate may cross both. Keep namespace identity and all provenance fields for final review. See [candidate experiments](bulk-dedup-candidates.md) and [primary-source research](bulk-dedup-primary-sources.md).

## The loop

1. **Snapshot and count.** The operator names one agent namespace. Open its SQLite database read-only, use a consistent snapshot, count rows and sizes, and report exact-text groups. Keep private text out of default output. The first runnable slice is `tools/benchmarks/bulk_dedup_inventory.py`; it does not change stored memories. JSON stores need their own scanner.
2. **Find bounded candidates.** Add exact hash matches, a capped number of nearest existing embedding neighbors, and a lexical fallback for missing/incompatible vectors. Record which path found each pair. Never send all possible pairs to a model: 10,000 records have almost 50 million unordered pairs.
3. **Judge pairs, not clusters.** Compare full text, dates, numbers, negation, subject, status, source, scene, and event lineage. Jev can be an optional *review signal* only after a blinded, corpus-specific evaluation. Laya can be tested locally if its checkpoint is installed. Use `duplicate / related-but-keep / changed-or-conflicting / unclear`; keep both whenever unsure. A chain of individually similar pairs is not proof that every member is interchangeable.
4. **Review and measure.** Present the original records and evidence together. Record decisions with algorithm/model versions, scores, and reasons. Measure candidate recall separately from final duplicate precision, dangerous false merges, group purity, reviewer time, model calls, and cost. Use held-out real examples with known answers and difficult negative pairs before choosing thresholds.
5. **Consolidate only after storage support exists.** Future apply needs durable retired-ID aliases, a many-to-one evidence bundle, reference updates, explicit update/delete APIs, a reviewed plan, a consistent backup, scratch-copy verification, and rollback. Compare compacted scratch database bytes before promising savings. The current command should export a review report, not an apply plan. See [storage safety](bulk-dedup-storage-safety.md) and [CLI workflow](bulk-dedup-cli-workflow.md).

## Experiments and what they say

| Experiment | Result | Interpretation |
| --- | --- | --- |
| 28 hand-labeled synthetic pairs, exact normalized text | 4 true duplicates, 0 false merges, 6 missed duplicates | Safe narrow starting signal on this fixture; misses paraphrases and same-event copies. [Fixture and evaluator](bulk-dedup-evaluation.md) |
| Jev, all 28 blinded pairs in one request | 10 true positives, **18 false positives**, 0 true negatives | It called every pair a duplicate. [Jev probe](bulk-dedup-jev.md) |
| Jev, same pairs in four requests of seven | 9 true positives, **12 false positives**, 6 true negatives, 1 missed duplicate | Three batches called every pair duplicate; one called every pair different. Batch context materially changed answers. [Jev probe](bulk-dedup-jev.md) |
| Jev, all 28 pairs in separate requests | 10 true positives, **3 false positives**, 15 true negatives, 0 missed duplicates; 35.94 s total | Better than batching on this fixture, but it wrongly merged all three provenance-sensitive negatives. [Isolated probe](bulk-dedup-jev-calibration.md) |
| Laya prior 71-memory frozen technical sample | 10/27 duplicate links found; 45.5% precision excluding borderline | Local entailment is another candidate signal, not a deletion judge. A new isolated inference run was unavailable because the checkpoint and dependencies are absent here. [Laya review](bulk-dedup-laya.md) |
| 1,000 synthetic records, exhaustive vector top-5 | 120/120 planted duplicate pairs found among 3,139 candidates | Good on deliberately easy vector clusters; does not establish recall on real Titan memories. Exhaustive search took about 10 seconds, so scale needs an index. [Candidate probe](bulk-dedup-candidates.md) |
| Synthetic SQLite delete and weight-null probes | Database file did not shrink on change; it shrank after `VACUUM` | Logical deletion and disk reclamation are different. Synthetic amounts are not real-store estimates. [Space probe](bulk-dedup-storage-safety.md); [SQLite VACUUM](https://www.sqlite.org/lang_vacuum.html) |

The first Jev run in this work appeared perfect because fixture IDs such as `exact-01` leaked the answer class into the request. That result was discarded. The table uses blinded reruns only. The hand-written fixture is useful for exposing failure modes, not for estimating production accuracy. The historical Jev branch likewise used small technical samples and required a separate reviewer; it did not establish safe automatic cleanup.

## Interpreting space estimates

A read-only inventory can identify exact-text candidates and the space used by
legacy weight blobs. Those observations do not establish safe deletions or
reclaimable disk space. Keep inventory results private and measure a compacted
copy before estimating savings. See the [inventory method](bulk-dedup-live-inventory.md).

## Next implementation slice

Keep the existing read-only SQLite inventory runnable and private by default. Then add a bounded candidate-export command, label a representative set of real pairs locally, and compare exact, lexical, vector, isolated Jev, and (if installed) Laya on the *same* gold set. Set a review threshold only after measuring dangerous false merges and candidate recall. Design aliases and evidence bundles before implementing any deletion command.
