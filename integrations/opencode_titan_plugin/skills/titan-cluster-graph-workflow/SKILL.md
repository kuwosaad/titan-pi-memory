---
name: titan-cluster-graph-workflow
description: Use when the user asks for Titan graph views, memory clusters, recurring themes, bridges between topics, knowledge-graph summaries, or graph-shaped project synthesis.
---

# Titan Cluster Graph Workflow

Clusters orient the investigation; scenes and current files establish what the
orientation means.

## Workflow

1. Call `titan-memory_inspect_clusters` with `limit=0` for a full-corpus view unless
   the user requests a bounded or session-specific view.
2. Select relevant cluster IDs from topics, keywords, representative memories, and
   counts. Keep the set small enough to explain.
3. Call `titan-memory_analyze_clusters` with a comma-separated `cluster_ids` value for
   deeper synthesis.
4. Expand representative or surprising scenes with
   `titan-memory_get_scene_context`. Preserve `source_agent` from each memory and
   pass it when the scene belongs to another agent namespace.
5. Verify implementation or project-status claims against current files, tests, Git,
   or live diagnostics.

## Output

For a quick answer, give topic, evidence, and why it matters. For a graph answer,
render a bounded Mermaid or ASCII map with the most important 3–7 clusters or
bridges. Do not dump the complete corpus.

Treat tensions as signals for inspection, not proof of contradiction or intent. State
when a cluster is sparse and the evidence is weak.

**Done when:** the synthesis is bounded, each important relationship has memory or
scene evidence, foreign provenance is retained, and current claims are verified.
