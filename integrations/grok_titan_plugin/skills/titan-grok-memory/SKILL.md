---
name: titan-grok-memory
description: >
  Use Titan Memory from Grok via the titan-grok CLI (Pi's native Titan tools).
  Use when recalling prior work, decisions, or dates; when the user says remember
  this; when checking Titan health or the graph; or for /titan-query /titan-save
  /titan-recent /titan-status /titan-clusters /titan-cortex /titan-graph.
---

# Titan Grok Memory

Titan is Grok's cross-session memory. Namespace: `~/.titan/agents/grok`.

Pi calls native tools. Grok calls `titan-grok`. Do that. Do not wait for MCP.

```bash
titan-grok query "what did we decide about X"
titan-grok query "" --from 2026-08-13 --to 2026-08-14
titan-grok recent
titan-grok scene <scene_id>
titan-grok save --goal "..." --thoughts "..." --outcome "..."
titan-grok doctor
titan-grok clusters
titan-grok cortex 1,2 --question "how do these relate"
titan-grok patterns status
titan-grok graph --open
```

If `titan-grok` is missing from PATH:

```bash
~/.local/bin/titan-grok
# or, from this repo
python3 integrations/grok_titan_plugin/scripts/titan_grok_tools.py
```

## How to work

1. Prior work / decisions → `titan-grok query "..."`
2. Hit has `[scene: id]` → `titan-grok scene <id>`
3. "What happened on DATE?" → `titan-grok query "" --from DATE --to DATE+1d`
4. "When did we first talk about X?" → query X, take earliest ts, then query X in a ±1 day bracket
5. After significant work → `titan-grok save --goal ... --outcome ...`
6. Looks broken → `titan-grok doctor` (ignore OpenCode-centric `titan doctor`)
7. Themes / graph → `titan-grok clusters` then `titan-grok graph --open`

Memories are pointers. Verify repo facts before treating them as current. Keep Grok's room separate from Pi/Codex/Claude unless the user asks for `/memory-sync`.

Passive capture is already on via Grok hooks. Do not re-implement it.
