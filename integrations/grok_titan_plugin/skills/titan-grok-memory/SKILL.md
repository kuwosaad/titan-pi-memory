---
name: titan-grok-memory
description: >
  Use Titan Memory to recall previous sessions, decisions, and project context when
  working in Grok. Covers passive capture, semantic query, scene recovery, temporal
  brackets, and saving outcomes. Use when the user asks what we decided, what happened
  last time, when we first talked about something, or says remember this. Also use
  for /titan-query, /titan-save, /titan-recent, /titan-status.
---

# Titan Grok Memory

Titan gives this Grok agent persistent memory across sessions. Ported from the Pi Titan skill.

Grok is only the adapter. Titan owns traces, extraction, storage, retrieval, patterns, and the graph. Grok uses its own namespace (`~/.titan/agents/grok`), separate from Pi, Codex, and Claude.

## 1. Passive capture (automatic)

Every Grok turn is recorded as a trace event under `~/.titan/agents/grok/traces/`. Titan turns those into scenes (conversation chunks) and memories (extracted facts).

This is already hooked: SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd. Do not re-implement capture.

## 2. Active query (explicit)

Call Titan over the `titan-memory` MCP server via `use_tool`. Qualified names:

```
titan-memory__query_memories        — Semantic search: "what did we decide about X?"
titan-memory__query_memories        — With date bracket:
  query, date_from, date_to           "what happened around May 17?"
titan-memory__get_scene_context     — Full scene by ID
titan-memory__store_trace_packet    — Manual save: "remember this decision"
titan-memory__get_recent_memories   — Browse recent work
titan-memory__doctor                — Is Titan healthy?
titan-memory__inspect_clusters      — Topic clusters
titan-memory__analyze_clusters      — Bridges, tensions, subclusters
```

If MCP is connected, the same tools may also appear without the `titan-memory__` prefix.

Slash commands that route here: `/titan-query`, `/titan-recent`, `/titan-save`, `/titan-status`, `/titan-graph`, `/titan-setup`, `/titan-clusters`.

## Usage patterns

### Pattern 1: Recall what happened before

```
User: "What were we working on last time?"
→ query_memories("current project recent tasks")
→ summarize the strongest matches
```

### Pattern 2: Get full context for a memory

```
If a memory has a scene_id:
→ get_scene_context(scene_id)
→ use the conversation, not just the extracted line
```

### Pattern 3: Manually persist important decisions

```
After significant work:
→ store_trace_packet({goal, thoughts, outcome})
```

### Pattern 4: Temporal queries — "What happened on the 17th of May?"

```
User: "What happened on the 17th of May?"
→ query_memories("", date_from="2026-05-17", date_to="2026-05-18")
→ empty query + date bracket = all memories that day, no semantic filter

User: "What happened with Titan Grok on Aug 13?"
→ query_memories("Titan Grok", date_from="2026-08-13", date_to="2026-08-14")
```

Date filters run before semantic scoring. Empty query + range skips embedding and returns recency-sorted memories.

### Pattern 5: Vague temporal navigation — "When did we first talk about X?"

```
Phase 1 — Find the island:
→ query_memories("X")
→ earliest ts is the anchor date

Phase 2 — Walk the island:
→ query_memories("X", date_from=anchor-1d, date_to=anchor+1d)
```

Semantic search finds the landmark. The date bracket lets you move forward and backward from it.

## Memory structure

- **Memory**: one atomic extracted fact
- **Scene**: the conversation chunk it came from
- **Scene ID**: open it before treating a memory as settled truth

## Order of operations

1. Previous work / decisions → `query_memories`
2. Memory has `scene_id` → `get_scene_context`
3. Trust rank order; first hits are usually best
4. Contradictions → open both scenes
5. Repo-dependent facts → verify in the working tree
6. Significant outcome → `store_trace_packet`
7. Looks broken → `doctor` (or `/titan-status`)

## Temporal cheat sheet

| Question | Query |
|---|---|
| "What happened on May 17?" | `query_memories("", date_from="2026-05-17", date_to="2026-05-18")` |
| "What did we do about X in March?" | `query_memories("X", date_from="2026-03-01", date_to="2026-03-31")` |
| "When did we first talk about X?" | Phase 1: `query_memories("X")` → earliest ts. Phase 2: bracket ±1 day |

Default namespace is Grok. Do not present Pi, Codex, or Claude memories as Grok memories unless the user asked for cross-agent history (`/memory-sync`).

## Related skills

- `titan-memory-workflow` — archaeology / graph-shaped synthesis
- `titan-doctor-workflow` — when capture or MCP looks broken
- `memory-sync` — import other agents' session history into Grok Titan
