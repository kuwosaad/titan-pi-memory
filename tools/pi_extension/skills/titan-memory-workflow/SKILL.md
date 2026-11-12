---
name: titan-memory-workflow
description: Use Titan Memory when prior decisions, work, chronology, agents, preferences, or project history could materially change the answer. Run an agent-led investigation from concrete pointers to source scenes, then verify against current state; skip it when the present context or current files already answer.
---

# Titan Memory Workflow

Use Titan when the answer depends on what happened before. The agent investigates;
Titan supplies searchable pointers and source scenes.

## Evidence model

- A **Memory** is a possible pointer: a compressed lead, not an answer.
- A **Scene** is the source evidence behind a pointer.
- The **current system**—files, Git, tests, and live diagnostics—is authoritative
  for what is true now.
- Preserve `source_agent`, `session_id`, `scene_id`, and timestamps whenever they are
  returned. Foreign recall is read-only.

## Investigative loop

1. **Frame.** State the target (decision, event, reason, chronology, preference,
   outcome, or current state), its project/artifact/agent/time scope, and the
   evidence needed. Split broad requests into temporary claims.

   **Complete when:** the uncertainty is specific enough to search.

2. **Probe.** Compile one hypothesis per query using concrete language likely to
   occur in memory: a distinctive name, path, identifier, error, mechanism, or
   lifecycle event plus one relationship or expected outcome. Use source, date,
   `session_id`, and memory-stream parameters when the live schema supports them. Use
   `both` for uncertain status, `rough` for events and chronology, and `learnings`
   for decisions and rules.

   **Complete when:** each probe has one subject and one evidence relationship.

3. **Investigate.** Read results as leads. Extract only anchors already present:
   names, paths, errors, dates, decisions, lifecycle terms, source agents,
   `session_id` values, scene IDs, and timestamps. Infer the strongest unexplored connection,
   such as the same artifact or person, plan-to-execution, failure-to-workaround,
   or chronology connecting earlier and later scenes. Use returned timestamps,
   `session_id`, source, and scene IDs to form the next search.

   Build the next probe by changing one search dimension—anchor, relationship,
   time, source, stream, or chronology. A paraphrase of the same failed query is
   not a new probe.

   **Continue when:** new evidence creates a useful unexplored direction.

4. **Follow.** Open a Scene when it can confirm, reject, or redirect the current
   hypothesis. Open each source event you will cite, including events surfaced in
   source passages. Preserve its owning `source_agent`; group repeated pointers by
   `source_agent + scene_id` so one Scene is not counted as several. Expand only
   scenes that could change the conclusion. Use targeted pointers and verification
   when a scene is too large to inspect usefully.

   **Complete when:** the material history needed for the answer is covered or the
   remaining gap has no useful unexplored direction. For missing operands,
   completeness of lists or updates, and unknowns, inspect original conversation
   text and nearby exchanges in promising unopened sessions returned by retrieval
   where navigation is available; summaries are leads, not exhaustive accounts.
   Before declaring a fact unknown, search for its original conversation text.
   There is no preset probe or retry count.

5. **Prove.** A Scene establishes origin and context; it does not establish present
   correctness. Compare technical claims with current evidence when available.
   For absence, compare positive planning evidence with positive
   execution evidence and their timestamps. A retrieval miss is not proof of
   absence. Current verified reality overrides remembered state.

   Before finalizing, identify every arithmetic operand and open evidence supporting
   each one; seek missing operands by their property and linked event. Check that
   opened evidence covers every required list or counted item and matches the exact
   entity, role, event, property, and time anchor. Count distinct completed events,
   not plans or repeated mentions. Resolve relevant updates as of the question's
   reference time, including later statements on the same day; a historical question
   may target an earlier state. A partial reminder or later partial list does not
   establish that omitted earlier items ended. Distinguish event time from report time
   and user-stated dates from unsupported assistant-stated dates; attribute the latter
   as estimates. Reflect role, time anchor, and date uncertainty in the main answer,
   not only a caveat. Keep other unresolved assumptions and uncertainty in the
   answer itself.

   **Complete when:** every material claim is scene-grounded, currently verified,
   or clearly bounded as an inference or unknown.

6. **Answer.** Answer the user directly. Distinguish remembered, scene-grounded,
   currently verified, inferred, and unknown only when that changes trust.

## Tool map

- `titan-memory_query_memories`: retrieve possible pointers with a bounded initial
  result set.
- `titan-memory_get_recent_memories`: recover chronology and recent orientation.
- `titan-memory_get_scene_context`: open source evidence with preserved provenance.

Follow the live tool schema. Titan is the recall layer; the agent owns anchor
extraction, hypothesis testing, connection inference, search-path choice, and the
stopping decision.

The governing rule is: retrieve pointers, investigate through evidence, then compare
with the present when current evidence is available.
