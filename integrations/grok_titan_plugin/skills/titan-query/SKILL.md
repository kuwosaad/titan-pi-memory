---
name: titan-query
description: Semantic recall from Titan Memory for Grok. Use when the user runs /titan-query, asks what Titan remembers, searches prior decisions, or wants memory about a topic.
---

# Titan Query

Search Titan Memory for the Grok agent namespace (`~/.titan/agents/grok`).

## Steps

1. Call `query_memories` via `use_tool` with qualified name `titan-memory__query_memories` (or the listed `titan-memory` tool).
2. Pass the user's question as `query`.
3. If the user names a date or range, also pass `date_from` and/or `date_to` (ISO 8601) when the tool supports them.
4. Summarize the strongest matches. Expand important `scene_id` values with `get_scene_context` before treating a memory as solid.
5. Verify repo facts when the answer depends on current code. Label what came from memory vs verification.

## Rules

- Default to the Grok namespace only.
- Memories are pointers; do not invent missing detail.
- If nothing useful returns, say so and suggest `/titan-recent` or a broader query.
