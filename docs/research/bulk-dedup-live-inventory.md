# Read-only Titan memory inventory

The inventory method below collects aggregate counts without returning memory
text or identifiers. Open each selected SQLite store in read-only mode, enable
`PRAGMA query_only`, and use a read transaction. Keep the resulting counts
private: even aggregate observations can reveal information about a user's store.

Exact and normalized text groups identify candidates, not safe deletions. Rows
with matching text can differ in evidence, embeddings, weights, and references.
Logical payload sizes do not include database and index overhead and do not
predict reclaimable disk space. A compacted copy is needed to measure reduction.
Legacy weight blobs may warrant separate investigation, but retiring them
requires establishing their behavior and preserving storage contracts.

## Reproducible aggregate-only outline

Enumerate only `~/.titan/agents/*/out/memories/memory_store.db`. For each
existing file, open `file:<URL-encoded-absolute-path>?mode=ro` with
`sqlite3.connect(..., uri=True)`, set `PRAGMA query_only=ON`, and begin a read
transaction. Inspect `PRAGMA table_info(memories)` only to accommodate older
schemas. Run aggregates, never selecting row values:

```sql
SELECT COUNT(*),
       COALESCE(SUM(length(CAST(text AS BLOB))), 0),
       COALESCE(SUM(length(embedding_blob)), 0)
FROM memories;

SELECT COALESCE(SUM(n - 1), 0)
FROM (SELECT COUNT(*) AS n FROM memories GROUP BY text HAVING COUNT(*) > 1);

-- Aggregate surplus logical payload under either key expression, with one
-- arbitrary keeper per group and without returning row text or identifiers.
WITH ranked AS (
  SELECT length(CAST(text AS BLOB)) AS text_bytes,
         COALESCE(length(embedding_blob), 0) AS embedding_bytes,
         row_number() OVER (PARTITION BY text ORDER BY rowid) AS rn
  FROM memories
)
SELECT SUM(CASE WHEN rn > 1 THEN 1 ELSE 0 END) AS surplus_rows,
       SUM(CASE WHEN rn > 1 THEN text_bytes + embedding_bytes ELSE 0 END)
         AS surplus_text_embedding_bytes
FROM ranked;

-- Repeat with PARTITION BY lower(trim(text)) for the normalized definition.

-- Only when this column exists:
SELECT COALESCE(SUM(length(outgoing_weights)), 0) FROM memories;
```

Also read `PRAGMA page_size`, `page_count`, and `freelist_count`, then obtain
main-file bytes from filesystem metadata. Close with rollback. Do not log
exception messages from SQL that could expose query values; report only an
aggregate or generic inaccessible status. Duplicate surplus is exact SQLite
text equality within one namespace, not semantic similarity or a cross-agent
match. Byte counts measure logical column payload sizes, not database/index
overhead or guaranteed reclaimable space.
