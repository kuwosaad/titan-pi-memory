---
name: titan-recent
description: Show recent Titan memories for the Grok agent. Use when the user runs /titan-recent, asks what happened lately, or wants the latest captures.
---

# Titan Recent

Browse recent memories in the Grok namespace (`~/.titan/agents/grok`).

## Steps

1. Run `titan-grok recent`. MCP equivalent: `titan-memory__get_recent_memories`.
2. Summarize the newest useful items with timestamps when present.
3. Offer to expand a specific scene with `get_scene_context` or deepen with `query_memories`.

## Rules

- If the list is empty, check that MCP is connected and passive capture/hooks are writing traces.
- Do not invent recent work that is not in the returned memories.
