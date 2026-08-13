---
name: titan-memory-workflow
description: Use when the user asks about prior work, decisions, project history, implementation archaeology, work reports, or when Claude Code needs durable context from Titan Memory.
---

# Titan Memory Workflow

Titan memories are semantic pointers, not final answers. Use them to find the right prior scene, then verify concrete facts in the repository before answering or changing code.

## Workflow

1. Start with `query_memories` for recall, project history, prior decisions, work reports, or implementation archaeology.
2. Expand important `scene_id` values with `get_scene_context` before relying on a memory.
3. Deduplicate repeated memories and prefer the scene with the clearest source evidence.
4. Verify concrete repo facts with file inspection, tests, or git history when the answer depends on current code.
5. Use `get_recent_memories` when the user asks what has been happening lately.
6. Use `doctor` when memory capture, retrieval, or config appears broken.
7. Use `inspect_clusters` and `analyze_clusters` for graph questions, recurring themes, or cross-session synthesis.
8. After significant work, call `store_trace_packet` with the goal, important context, tool summary, and outcome.

## Answer Style

Keep final answers concise and evidence-grounded. Mention when a claim came from Titan memory versus current repo verification. If memories conflict, say so and recover the relevant scenes before deciding.
