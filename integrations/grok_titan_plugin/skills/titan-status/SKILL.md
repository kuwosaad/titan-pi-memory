---
name: titan-status
description: Check Titan Memory health for the Grok agent. Use when the user runs /titan-status, asks if Titan is working, or wants a doctor report.
---

# Titan Status

Run Titan doctor for the Grok namespace.

## Steps

1. Call `doctor` via `use_tool` with `titan-memory__doctor` (or the listed `titan-memory` tool).
2. Report agent name/namespace, MCP tool visibility, trace directory status, memory/provider signals, and any missing keys.
3. If something fails, point to the next concrete check (plugin enablement, `/mcps`, hooks, `~/.titan/agents/grok/.env`).

## Expected home

```text
~/.titan/agents/grok/
```
