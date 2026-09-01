---
name: titan-save
description: Save a durable decision or outcome into Titan Memory for Grok. Use when the user runs /titan-save, says remember this, or asks to store a decision.
---

# Titan Save

Persist a structured recap into the Grok agent namespace (`~/.titan/agents/grok`).

## Steps

1. Collect a short `goal`, key `thoughts` / decisions, and the `outcome`.
2. Run `titan-grok save --goal "..." --thoughts "..." --outcome "..."`.
3. MCP equivalent: `titan-memory__store_trace_packet`.
4. Redact secrets. Confirm what was stored.

## Rules

- Prefer one clear packet over many noisy events.
- Do not store secrets or raw credentials.
- If the user only wants a tiny note, still use goal/thoughts/outcome shape.
