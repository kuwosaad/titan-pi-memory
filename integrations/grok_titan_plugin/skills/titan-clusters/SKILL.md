---
name: titan-clusters
description: Inspect and analyze Titan memory clusters for the Grok agent. Use when the user runs /titan-clusters, asks for themes, bridges, tensions, or graph-shaped synthesis.
---

# Titan Clusters

Map related memories in the Grok namespace (`~/.titan/agents/grok`).

## Steps

1. Run `titan-grok clusters`. Detail: `titan-grok clusters --id 2`.
2. Deeper synthesis: `titan-grok cortex 1,2 --question "how do these relate"`.
3. MCP equivalents: `titan-memory__inspect_clusters` / `titan-memory__analyze_clusters`.
4. Expand important scenes with `titan-grok scene <id>`.

## Output

Summarize each useful cluster as topic, evidence, and why it matters. Keep the map bounded (about 3–7 clusters) unless the user asks for more.

## Rules

Treat tensions as signals, not final contradictions. If a cluster is sparse, say the evidence is weak.
