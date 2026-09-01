---
name: titan-status
description: Check Titan Memory health for the Grok agent. Use when the user runs /titan-status, asks if Titan is working, or wants a doctor report.
---

# Titan Status

Run Titan doctor for the Grok namespace.

## Steps

1. Run `titan-grok doctor`.
2. MCP equivalent: `titan-memory__doctor`.
3. Report workspace, spool/traces, memory count, and config. Ignore OpenCode-centric `titan doctor --agent grok` "plugin missing" noise.
4. If something fails, check `~/.titan/agents/grok/.env`, plugin enablement, and `/mcps`.

## Expected home

```text
~/.titan/agents/grok/
```
