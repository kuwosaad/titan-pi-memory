---
name: titan-clusters
description: Inspect and analyze Titan memory clusters for the Grok agent. Use when the user runs /titan-clusters, asks for themes, bridges, tensions, or graph-shaped synthesis.
---

# Titan Clusters

Map related memories in the Grok namespace (`~/.titan/agents/grok`).

## Steps

1. Call `inspect_clusters` via `use_tool` with `titan-memory__inspect_clusters` (or the listed `titan-memory` tool). Use a full-corpus view unless the user asks for a smaller window.
2. Identify the most relevant cluster IDs from topics, keywords, representative memories, and counts.
3. Optionally call `analyze_clusters` with a comma-separated `cluster_ids` string for deeper synthesis (central memories, bridges, tensions).
4. Expand important `scene_id` values with `get_scene_context` before relying on them.
5. Verify implementation claims against the repo when the user needs current truth.

## Output

Summarize each useful cluster as topic, evidence, and why it matters. Keep the map bounded (about 3–7 clusters) unless the user asks for more.

## Rules

Treat tensions as signals, not final contradictions. If a cluster is sparse, say the evidence is weak.
