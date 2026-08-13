---
name: titan-save
description: Save a durable decision or outcome into Titan Memory for Grok. Use when the user runs /titan-save, says remember this, or asks to store a decision.
---

# Titan Save

Persist a structured recap into the Grok agent namespace (`~/.titan/agents/grok`).

## Steps

1. Collect a short `goal`, key `thoughts` / decisions, and the `outcome` from the user or the current turn.
2. Call `store_trace_packet` via `use_tool` with `titan-memory__store_trace_packet` (or the listed `titan-memory` tool).
3. Include only durable context worth remembering. Redact secrets, tokens, and credentials.
4. Confirm what was stored in plain language.

## Rules

- Prefer one clear packet over many noisy events.
- Do not store secrets or raw credentials.
- If the user only wants a tiny note, still use goal/thoughts/outcome shape.
