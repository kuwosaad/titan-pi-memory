---
name: titan-query
description: Semantic recall from Titan Memory for Grok. Use when the user runs /titan-query, asks what Titan remembers, searches prior decisions, or wants memory about a topic.
---

# Titan Query

Search Titan Memory for the Grok agent namespace (`~/.titan/agents/grok`).

Prefer the Pi-parity CLI. It works in every Grok session, including ones that started before MCP attached.

## Steps

1. Run `titan-grok query "<question>"`.
2. Date range: `titan-grok query "" --from 2026-08-13 --to 2026-08-14` (empty query = all memories in the bracket).
3. If a hit has `[scene: ...]`, run `titan-grok scene <scene_id>`.
4. If MCP is connected, `titan-memory__query_memories` is an equivalent.
5. Verify repo facts when the answer depends on current code.

## Rules

- Default to the Grok namespace only.
- Memories are pointers; do not invent missing detail.
- If nothing useful returns, say so and suggest `/titan-recent` or a broader query.
