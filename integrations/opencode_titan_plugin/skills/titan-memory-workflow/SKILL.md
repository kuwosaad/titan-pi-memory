---
name: titan-memory-workflow
description: Use Titan Memory when prior decisions, work, chronology, agents, preferences, or project history could materially change the answer. Compile requests into retrieval-shaped evidence probes, follow scene pointers, and verify current state.
---

# Titan Memory Workflow

Restore the smallest amount of relevant history needed for a trustworthy answer.
The current repository and live state outrank remembered state.

## Route

Use this workflow for historical decisions, prior implementation work, chronology,
preferences, cross-agent continuity, or questions whose answer depends on what
happened before. Use the pattern, cluster, doctor, or memory-sync workflow when that
is the actual task.

## Evidence loop

1. **Frame.** Identify the target (decision, event, rationale, outcome, preference, or
   current state), scope (project, artifact, agent, and time), and evidence bar.
2. **Compile.** Turn each claim into a concrete probe: distinctive entity or path +
   one action or relationship + expected evidence. Separate implementation, rationale,
   planning, and outcome probes. Keep dates and source agents in tool parameters when
   supported rather than hiding them in semantic text.
3. **Probe.** Call `titan-memory_query_memories` with a bounded limit. Use
   `titan-memory_get_recent_memories` for chronology and `mode=rough` when supported;
   use `mode=learnings` for decisions and rules; use `mode=both` for broad status.
   Follow the live tool schema and never invent parameter values. A full result limit
   may be incomplete, so split by source, time, or topic before widening.
4. **Reconstruct.** Treat memories as pointers, not answers. Preserve each result's
   `source_agent` and `scene_id`. Group repeated pointers by
   `source_agent + scene_id`. Call `titan-memory_get_scene_context` for decisive
   reasoning, outcomes, corrections, or conflicts, passing the returned
   `source_agent` whenever the scene is foreign.
5. **Prove.** Verify current technical claims against files, Git, tests, or live
   diagnostics. A retrieval miss does not prove absence: compare positive planning and
   execution evidence, then inspect current artifacts.
6. **Answer.** Answer the user's question directly. Mark remembered, scene-grounded,
   currently verified, inferred, and unknown only when the distinction affects trust.

## Tool map

- `titan-memory_query_memories`: semantic recall; use bounded probes.
- `titan-memory_get_recent_memories`: recent chronology and orientation.
- `titan-memory_get_scene_context`: source scene recovery; preserve provenance.
- `titan-memory_store_trace_packet`: save one distilled decision or outcome after
  significant work when future continuity benefits.

Federated recall is read-only. Foreign results may be ranked with local results, but
their `source_agent` is provenance, not decoration: use it when opening a foreign
scene. Writes always belong to the active OpenCode namespace.

**Done when:** every material claim is scene-grounded, currently verified, or clearly
bounded as an inference or unknown, with no duplicate scene counted twice.
