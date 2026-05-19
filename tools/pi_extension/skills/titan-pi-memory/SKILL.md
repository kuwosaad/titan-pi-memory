---
description: >
  Use Titan Memory to recall previous sessions, decisions, and project context when
  working with the Pi coding agent. Provides passive memory capture and active query tools.
---

> **📘 Pi users:** The Titan tools (`titan_query_memories`, etc.) and slash commands
> (`/titan-query`, `/titan-graph`, etc.) listed in [README.md](../../../README.md) are
> **automatically available** in every Pi session. You do not need to load this skill
> or any additional configuration. This skill exists for other agents that require
> explicit skill loading.

# Titan Pi Memory

Titan gives this coding agent persistent memory across sessions. It works in two layers:

## 1. Passive capture (automatic)

Every conversation turn is automatically recorded as a "trace event" and stored in Titan's
spool directory (`~/.titan/agents/pi/traces/`). Titan's pipeline processes these events
into scenes (chunks of conversation) and memories (extracted facts).

**You don't need to do anything for this** — events are captured from Pi's lifecycle hooks
(`session_start`, `message_end`, `tool_result`, `turn_end`, `session_shutdown`).

## 2. Active query (explicit)

Use the following tools and commands to search and explore stored memories:

### Tools (LLM-callable)

```
titan_query_memories          — Semantic search: "what did we decide about X?"
titan_query_memories(query,   — Search with date bracket:
  date_from="2026-05-17",       "what happened around May 17?"
  date_to="2026-05-18")         "when did we talk about X?"
titan_get_scene_context       — Full scene by ID: "show me the original context"
titan_store_trace_packet      — Manual save: "remember this decision"
titan_get_recent_memories     — Browse: "what have we been working on?"
titan_doctor                  — Diagnostics: "is Titan working?"
```

### Commands (user-callable)

```
/titan-query <question>   — Search memories for relevant context
/titan-recent             — Show most recent memories
/titan-save <goal>        — Manually save a memory
/titan-status             — Check if Titan is running
/titan-setup              — Prepare workspace/config and start the server
/titan-key                — Select extraction provider and save its API key
/titan-graph              — Open the local Titan knowledge graph in a browser
/titan-dashboard          — Open the rich terminal memory dashboard
/titan-start              — Start the Titan server
```

## Usage patterns

### Pattern 1: Recall what happened before

```
User: "What were we working on last time?"
→ I use titan_query_memories("current project recent tasks")
→ I return a summary of relevant memories
```

### Pattern 2: Get full context for a memory

```
If a memory has a scene_id attached:
→ I use titan_get_scene_context(scene_id)
→ Full conversation context is returned
```

### Pattern 3: Manually persist important decisions

```
After completing significant work:
→ I use titan_store_trace_packet({goal, outcome})
→ Future sessions can recall this
```

### Pattern 4: Temporal queries — "What happened on the 17th of May?"

Titan's `/api/retrieve` endpoint now supports `from_date` and `to_date` query parameters (ISO 8601 format). When the user asks about a specific date or date range:

```
User: "What happened on the 17th of May?"
→ I know Titan can bracket by date.
→ I retrieve memories with from_date="2026-05-17" and to_date="2026-05-18"
→ If the user adds a topic ("What happened with the ODE solver on May 17?"),
  semantic search is automatically scoped inside that date bracket.
```

**How it works under the hood:**
- `from_date`/`to_date` are passed through `CandidateFilters` → SQL `WHERE ts >= ? AND ts <= ?`
- The date filter runs **before** semantic scoring — it narrows the candidate pool first, then searches within that scope
- A `"temporal"` intent is automatically detected for queries containing "when did", "what date", "first time", "which day"

### Pattern 5: Vague temporal navigation — "When did we first talk about X?"

For questions where the user wants to locate **when** something happened, use a two-phase strategy:

```
User: "When did I first talk to you about the ODE solver?"

Phase 1 — Find temporal anchor:
→ titan_query_memories("ODE solver")
→ Look at the earliest `ts` (timestamp) in the results
→ That's my anchor date

Phase 2 — Navigate temporally from anchor:
→ titan_query_memories("first time we discussed ODE solver",
    date_from=anchor_date - 1 day, date_to=anchor_date + 1 day)
→ Or just retrieve raw context around that date to pinpoint the exact conversation
```

**The principle:** Use semantic search to find a temporal landmark, then use date-bracketing to zoom in and navigate forward/backward from it. Semantic search gives you the island; temporal bracket lets you explore it.

## Memory structure

- **Memory**: An atomic extracted fact (e.g. "user prefers dark mode for the UI")
- **Scene**: The full conversation chunk a memory was extracted from
- **Scene ID**: If present on a memory, you should open it for full context

## Order of operations

1. When the user asks about previous work, start with `titan_query_memories`
2. If a returned memory has a `scene_id`, open the scene for richer context
3. Memories are semantically ranked — the first results are most relevant
4. If you find contradictory memories, open both scenes to resolve

## Related skills

- `explain-like-im-12` — for simple explanations of how Titan works
- `titan-memory-workflow` — for detailed memory archaeology and scene analysis
