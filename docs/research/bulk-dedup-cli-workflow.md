# Bulk memory deduplication CLI workflow

## Near-term recommendation: read-only inventory and review

The first runnable maintenance slice should be a standalone inventory command
that scans one explicitly selected SQLite database in read-only mode, reports
aggregate duplicate candidates, and optionally lists IDs for human review. It
must not create a keeper, deletion plan, or apply action. Current Titan has no
retired-ID alias contract or multi-source evidence model, so cross-ID
consolidation is unsafe. The proposed inventory utility is documented and
implemented in `tools/benchmarks/bulk_dedup_inventory.py`.

The main `titan` CLI already uses dry-run-by-default `--apply` commands for
scene backfill and Codex pending recovery (`tools/cli/titan.py:2230-2248,
3454-3461, 3481-3490`). That convention is useful context, but it does not make
memory deletion safe. Any future mutation path is gated on alias/evidence and
storage update/delete contracts, a verified backup, and scratch-copy
verification.

## User workflow

```console
$ python tools/benchmarks/bulk_dedup_inventory.py --db /path/to/codex/memory_store.db
{
  "backend": "sqlite",
  "database": "/path/to/codex/memory_store.db",
  "rows_scanned": 73105,
  "exact_text_duplicate_groups": 142,
  "duplicate_excess_rows": 193,
  "duplicate_group_text_bytes": 62418,
  "duplicate_group_vector_bytes": 786432,
  "page_size": 4096,
  "page_count": 18302,
  "freelist_count": 17,
  "note": "Candidate payload bytes are inventory statistics, not reclaimable-space estimates. No changes made."
}

$ python tools/benchmarks/bulk_dedup_inventory.py --db /path/to/codex/memory_store.db --groups
{
  "...": "aggregate fields omitted here",
  "groups": [
    {"normalized_text_sha256": "...", "count": 3, "memory_ids": ["mem_a12", "mem_f09", "mem_6bc"], "group_text_bytes": 246, "group_vector_bytes": 16384}
  ],
  "note": "Review candidate members and provenance. This command never deletes or rewrites rows."
}
```

Numbers are illustrative. By default JSON contains aggregates only: no memory
text and no IDs. `--groups` explicitly opts into ID output for review; it still
never emits text, and the user can fetch individual records through existing
inspection tools. Candidate byte totals describe logical text/vector payload
summed across all candidate rows in duplicate groups. They are not
estimates of excess bytes or disk space reclaimable by deletion or compaction.
SQLite deletions do not necessarily shrink the file, and cross-ID candidates
are not currently safe to remove.

This initial utility can be followed by a resumable scan/review/export command
that persists checkpoints and reviewer notes outside the memory database. Such
review artifacts remain advisory: there is no `keep`, `plan`, or `apply`
command in the near-term design.

## Inventory data flow and bounds

1. Require an explicit `--db` path. Open its resolved URI using SQLite
   `mode=ro`, set `PRAGMA query_only=ON`, and fail if it is absent or lacks the
   expected `memories` table. Begin one read transaction so counts, page
   observations, and scanned rows share a consistent SQLite snapshot. Do not
   initialize schema or resolve federation.
   The operator is responsible for selecting one namespace database; report
   the exact resolved path.
2. Read `id`, `text`, and `length(embedding_blob)` in fixed-size `fetchmany()`
   batches. Normalize candidate text by Unicode `casefold()` plus collapsing
   whitespace, hash it, and insert only the digest and byte counts into a
   temporary SQLite scratch database. This bounds process memory by batch size
   and avoids storing normalized memory text even in aggregate mode. Store IDs
   in scratch only when `--groups` was requested. Never send text to hosted
   models or embeddings.
3. Aggregate duplicate group count, excess-row count, total UTF-8 text bytes
   across candidate groups, and total stored vector blob bytes across those
   groups (measured with SQL `length(embedding_blob)`).
   Read `page_size`, `page_count`, and `freelist_count` as database observations.
   Default JSON must not include text or memory IDs. `--groups` can return group
   hashes and member IDs for review.
4. Do not write to the source DB, its WAL, or its schema. The digest-only
   scratch database is a temporary inventory artifact, not a source mutation.
   The utility does not modify, checkpoint, truncate, or vacuum a WAL/database.

The current storage contract exposes `load_all_memories()` but no ordered batch
cursor (`app/storage/repository.py:19-44`); its wrapper warns that loading the
whole store consumes RAM (`app/storage/memories.py:930-934`). SQLite
`load_all_memories()` fetches every row and decodes embeddings
(`app/storage/memories.py:666-670`), while JSON reads the whole document
(`app/storage/memories.py:241-255`). The standalone SQLite inventory avoids
those helpers. JSON inventory needs a separate streaming/offset design; do not
silently load large JSON stores into memory under a batch-shaped interface.

## Candidate interpretation and provenance

Candidate normalization is only a way to find matching text. It is not
canonical memory identity or proof of duplicate meaning. The current immutable
identity includes ID, text, type, stream, session/turn, scene, source type and
reliability, verification/fallback, source event IDs, provenance, speaker
focus, kind, and embedding (`app/storage/memories.py:159-206`). `source_agent`
is not in that tuple but still must be retained as provenance and never crossed
between namespaces. Different IDs may have distinct meaning, evidence, or
external references even when normalized text matches.

The storage safety review found SQLite memory IDs are primary keys, so distinct
rows with the same ID cannot exist in a normally initialized memory table.
Historical JSON can contain duplicate IDs, including rows with differing
mutable LNN state; JSON append deliberately preserves those records
(`app/storage/memories.py:261-296`). This standalone command handles SQLite
only and reports normalized-text candidates across IDs without declaring them
safe to merge. Do not use an ID collision or exact text alone as removal
eligibility.

## Coexistence with the merged save pipeline

This inventory is offline observation, not another save-time deduper. The
current event-first pipeline deduplicates incoming events by
`(session_id, event_id)`, extracts memories, embeds them, and persists source
event lineage (`app/save_pipeline/pipeline.py:5-12`). Its extraction path can
optionally filter exact text hashes supplied through `existing_text_hashes`
(`app/save_pipeline/pipeline.py:136-175`), while storage append enforces
immutable-ID idempotency/collision behavior (`app/storage/memories.py:261-296,
603-655`). Keep these responsibilities intact: the inventory does not touch
event ledgers/checkpoints, scenes, extraction, embedding, or the online save
dedup policy.

## Why no apply yet

The current `MemoryStore` contract has no update/delete/alias operation
(`app/storage/repository.py:19-43`). Retrieval, graph weights, and external
consumers may refer to memory IDs; there is no alias table or general reference
rewrite seam. The current model also cannot safely represent multiple source
scenes and provenance in one consolidated memory. Therefore cross-ID groups
must remain report-only.

A future migration needs, at minimum:

- durable retired-ID aliases and updates to every internal/external reference;
- multi-source evidence/provenance that preserves each member's scene, session,
  source event IDs, agent, and attribution;
- explicit repository update/delete operations that preserve FTS triggers and
  immutable identity rules;
- a consistent, verified backup and lossless receipt;
- a reviewed exact plan, revalidated under a write lock;
- scratch-copy application and verification of records, lineage, FTS, database
  integrity, and measured compacted size before any live apply;
- rollback that refuses to overwrite later writes.

Until then, the usable command is scan/review/export only. Do not advertise
candidate payload bytes as reclaimable space. Any future space accounting must
compare a consistent backup with a proposed scratch rewrite/compaction; logical
row deletion, SQLite free pages, WAL state, and actual file shrink are separate
measurements.

## Code anchors

- SQLite readonly URI support: `app/storage/sqlite.py:37-53`.
- Runtime namespace/path resolution: `app/runtime/context.py:140-190`.
- Memory table schema and FTS triggers: `app/storage/memories.py:391-445`.
- Full-load warning and adapters: `app/storage/memories.py:241-296, 666-670,
  930-948`.
- Repository contract: `app/storage/repository.py:19-43`.
- CLI dry-run/apply precedent: `tools/cli/titan.py:2230-2248, 3454-3461,
  3481-3490, 3501-3507`.
- Event-first save and optional exact text hash filter:
  `app/save_pipeline/pipeline.py:5-12, 136-175, 1988-2004`.
- Full mutation constraints: `docs/research/bulk-dedup-storage-safety.md`.
