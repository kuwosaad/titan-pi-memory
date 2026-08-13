---
name: titan-graph
description: Open or describe the Titan memory graph for the Grok agent. Use when the user runs /titan-graph, asks for the memory graph UI, or wants a visual map of memories.
---

# Titan Graph

Show the local Titan graph for agent `grok`.

## Steps

1. Tell the user to run:

```bash
titan graph --agent grok --open
```

2. If the user asks you to run it, use the shell to launch that command.
3. Optionally call `inspect_clusters` for a text-side summary of related memory clusters before or after opening the UI.

## Notes

- Graph data lives under `~/.titan/agents/grok`.
- For cluster synthesis inside chat, prefer `/titan-clusters` or `inspect_clusters` / `analyze_clusters`.
