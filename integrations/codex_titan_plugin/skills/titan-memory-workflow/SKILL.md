---
name: titan-memory-workflow
description: Use when the user asks about prior work, decisions, project history, implementation archaeology, or when Codex needs durable context from Titan Memory.
---

# Titan Memory Workflow

Titan memories are semantic pointers, not final answers. Use them to find the right prior scene, then verify concrete facts in the repository before answering or changing code.

## Routing

Use this skill when the user asks what happened before, why a decision was made, where prior work lives, what changed recently, or what context Titan remembers about a topic.

## Workflow

1. Start with `query_memories` using the user's topic and current repo/session context.
2. Use `get_recent_memories` when the user asks what has been happening lately.
3. Expand important `scene_id` values with `get_scene_context` before relying on a memory.
4. Deduplicate repeated memories and prefer newer, verified, or higher-reliability records when memories disagree.
5. Verify concrete repo facts with file inspection, tests, or git history when the answer depends on current code.
6. Use `inspect_clusters` and `analyze_clusters` for graph-shaped synthesis, recurring themes, bridges, or possible tensions.
7. Use `doctor` when memory capture, retrieval, or config appears broken.
8. Keep Codex memory scoped to the Codex namespace by default. If Codex memory is empty but the user asks for Pi, Claude Code, Aider, or OpenCode history, ask explicitly before using memory-sync/import guidance or cross-agent search.
9. After significant work, call `store_trace_packet` with the goal, important decisions, tool summary, outcome, and follow-up context worth remembering.

## Answer Rules

Say when a claim came from Titan memory versus current repo verification. If memories conflict, recover the relevant scenes before deciding. Do not present old memory as current truth until the repo confirms it. Do not present Pi, Claude Code, Aider, or OpenCode memories as Codex memories unless the user explicitly asked for cross-agent history.
