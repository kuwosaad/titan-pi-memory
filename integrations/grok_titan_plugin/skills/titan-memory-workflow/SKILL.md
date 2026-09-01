---
name: titan-memory-workflow
description: Use when the user asks about prior work, decisions, project history, implementation archaeology, or when Grok needs durable context from Titan Memory. Also use after significant work that should be remembered.
---

# Titan Memory Workflow

Day-to-day Titan use (query patterns, temporal brackets, save after work) lives in `titan-grok-memory`. This skill is the archaeology / synthesis pass.

Titan memories are semantic pointers, not final answers. Use them to find the right prior scene, then verify concrete facts in the repository before answering or changing code.

Agent namespace is always `grok`. Memory lives at `~/.titan/agents/grok`.

## Routing

Use this skill when the user asks what happened before, why a decision was made, where prior work lives, what changed recently, or what context Titan remembers about a topic.

## MCP tools

Call tools via `use_tool` with qualified names like `titan-memory__query_memories`. If MCP is connected, tools also appear as `titan-memory` tools.

Core tools: `query_memories`, `get_scene_context`, `get_recent_memories`, `store_trace_packet`, `store_trace_event`, `doctor`, `inspect_clusters`, `analyze_clusters`.

## Workflow

1. Start with `query_memories` using the user's topic and current repo/session context. Recall searches every discovered agent namespace by default. Support `date_from` / `date_to` when the user names a time range.
2. Use `get_recent_memories` when the user asks what has been happening lately.
3. Expand important `scene_id` values with `get_scene_context` before relying on a memory. Pass the memory's `source_agent` when the scene belongs to another agent.
4. Deduplicate repeated memories and prefer newer, verified, or higher-reliability records when memories disagree.
5. Verify concrete repo facts with file inspection, tests, or git history when the answer depends on current code.
6. Use `inspect_clusters` and `analyze_clusters` for graph-shaped synthesis, recurring themes, bridges, or possible tensions.
7. Use `doctor` when memory capture, retrieval, or config appears broken.
8. Writes, passive capture, traces, pending state, settings, and patterns stay in the Grok namespace. Read-only Memory and Scene recall is shared across all discovered agent namespaces.
9. After significant work, call `store_trace_packet` with the goal, important decisions, tool summary, outcome, and follow-up context worth remembering.

## Answer Rules

Say when a claim came from Titan memory versus current repo verification. Preserve `source_agent` attribution, recover conflicting scenes before deciding, and do not present old memory as current truth until the repo confirms it.
