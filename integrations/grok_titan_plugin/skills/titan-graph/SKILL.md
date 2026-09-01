---
name: titan-graph
description: Open or describe the Titan memory graph for the Grok agent. Use when the user runs /titan-graph, asks for the memory graph UI, or wants a visual map of memories.
---

# Titan Graph

Show the local Titan graph for agent `grok`.

## Steps

1. Run `titan-grok graph --open` (or `titan graph --agent grok --open`).
2. Optionally `titan-grok clusters` for a text map first.

## Notes

- Graph data lives under `~/.titan/agents/grok`.
- For cluster synthesis inside chat, prefer `/titan-clusters` or `inspect_clusters` / `analyze_clusters`.
