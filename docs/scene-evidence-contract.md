# Scene evidence contract

Titan treats a memory as an address and a scene as its evidence.

## Evidence

Version-1 scenes store the sanitized, normalized event payload Titan admitted
after adapter bounds and secret redaction. Events are ordered by ledger
sequence, deduplicated by `(session_id, event_id)`, and retained in the scene
so the scene remains readable after the temporary event ledger is pruned.

`complete` means every covered event is present and message/tool provenance
points to source event IDs in the scene. `partial` means Titan cannot prove
that claim. Known missing IDs are listed in `missing_source_event_ids`; the
list may be empty when a legacy scene has no provable lineage. Legacy scenes
remain version 0 and partial.

## Retrieval

Memory search returns `scene_refs` only: the scene ID, evidence status,
evidence version, and missing IDs. The full scene is fetched explicitly with
`get_scene_context` (or the scene HTTP/MCP equivalent).

## Checkpoints

`checkpoints.json` records events that have been classified and whose pending
evidence reference is durable. `scene_checkpoints.json` records the highest
contiguous event sequence whose evidence is durably stored or explicitly
marked missing. Only the latter checkpoint permits ledger pruning.

`event_index.json` is an append-only seen-event registry. Pruning removes
temporary event payloads but never removes index entries, so a previously
admitted event remains a duplicate forever.
