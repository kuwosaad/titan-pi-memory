---
name: titan-memory-workflow
description: Use Titan Memory when prior decisions, work, chronology, agents, preferences, or project history could materially change the answer. Compiles natural-language requests into retrieval-shaped evidence probes, follows memory pointers to source scenes, and verifies current state; skip it when the present conversation or repository already answers.
---

# Titan Memory Workflow

Restore the relevant past with the least retrieval needed for a trustworthy answer.

The agent reasons; Titan retrieves. A human request is rarely a good Titan query by
itself. Translate it into language likely to occur in stored memories, then use the
results as evidence.

## Mental Model

- A **Memory** is a compressed, usually declarative pointer to prior work.
- A **Scene** is the source interaction behind a Memory.
- A **Pattern** is a generalization supported across Memories.
- The **current system** determines what is true now.

The usual direction is `request -> evidence probes -> Memories -> decisive Scenes ->
current verification`. Enter at the cheapest layer that resolves the uncertainty.

## Evidence Loop

### 1. Frame

Identify what the answer needs:

- target: event, decision, reason, chronology, current state, preference, or pattern;
- scope: projects, artifacts, actors, source agents, and time window;
- evidence bar: orientation, scene-grounded history, or current verification.

For broad questions, split the requested answer into temporary evidence claims. This
organizes the current investigation; it does not classify the user's nonlinear
conversation permanently.

**Complete when:** the missing evidence is specific enough to search for.

### 2. Compile

Compile each claim into one or more **memory-shaped probes**. A strong probe resembles
a sentence fragment Titan may have stored:

`distinctive entity or artifact + one action or relationship + expected evidence`

Keep project names, file paths, identifiers, errors, people, models, and concrete
mechanisms. Remove conversational wrappers such as "what all stuff did we do" and
abstract status labels that carry little subject matter.

Use separate probes for separate hypotheses:

- implementation: `<thing> implemented changed files tests passed verified`;
- planning: `<thing> proposed recommendation roadmap next step deferred`;
- rationale: `<thing> reason constraint tradeoff decision`;
- outcome: `<thing> completed connected passed written created`.

These are examples, not magic keywords. Prefer wording likely to appear in the relevant
Memory. Split mechanism from outcome when one query would mix several generations of
history.

**Absence has no embedding.** To decide that something was not started, retrieve
positive planning evidence and positive execution evidence separately, then compare
their timestamps and verify current artifacts. A failed search alone never proves
absence.

Keep scope controls out of the semantic payload when possible:

- pass `sources` when ownership is known or one namespace is causing noise;
- use date parameters when the live schema exposes them, with an inclusive end-of-day
  bound for a whole day;
- when date parameters are unavailable, use recent recall and filter returned
  timestamps;
- use a bare date inside a query only when the date itself is evidence, not merely a
  filter. Titan versions may interpret one date as midnight-to-midnight and exclude
  the rest of that day.

Semantic retrieval is already the search algorithm. `mode` selects memory streams; it
is not `semantic`. When supported, use:

- `both` for broad status, uncertain wording, or competing plan/outcome evidence;
- `rough` for episodic events and chronology;
- `learnings` for distilled decisions, rules, and patterns.

Follow the live schema if it differs. Never invent a mode value.

**Complete when:** every probe contains one clear subject and one evidence relationship,
while time and source are handled structurally where possible.

### 3. Probe

Choose the smallest call or small set of independent calls that could change the
answer. Start with a bounded result count; widen only when coverage requires it.

Use recent or date-bounded recall for activity windows. Use semantic probes for topics,
mechanisms, decisions, and expected evidence. Search all agents when ownership is
unknown; isolate a source when the owner is known or stale foreign history dominates.

A non-empty response is not automatically a hit. Count a result only when it supports
the probe's subject and evidence relationship; generic semantic neighbors are noise.

Treat a response that exactly fills its limit as potentially incomplete. Split by
source, time, or topic before requesting one enormous response.

When results are weak, audit the call before diagnosing Titan:

1. valid mode and tool parameters;
2. date handled as scope rather than accidental semantic text;
3. one topic and relationship per probe;
4. concrete anchors instead of abstract labels;
5. appropriate source and memory stream.

Then change the retrieval angle: use a path or identifier, phrase the expected Memory,
separate mechanism from outcome, narrow the source, or recover chronology. Repeating a
paraphrase of the same bad query is not a new probe.

When a known recent Memory is absent from semantic results, inspect recent recall in
the owning source before escalating. Semantic thresholds can hide a stored record
without implying that capture or federation is broken.

**Complete when:** the relevant evidence space is covered, or the remaining gap is
explicit and cannot be reduced with another cheap probe.

### 4. Reconstruct

Treat results as clues, not answers. Preserve `source_agent` and `scene_id`. Group
repeated fragments by `source_agent + scene_id` before judging coverage.

For lifecycle questions, build a temporary evidence table mentally:

`claim | planning evidence | execution evidence | verification | latest timestamp`

The agent infers status from evidence; Titan does not need to understand labels such as
"done" or "unstarted."

Semantic rank is relevance, not chronology. Recent Memories are orientation, not
automatically a work report. Keep conflicts and superseding evidence visible.

Expand only Scenes that can materially change the conclusion: decisive outcomes,
reasoning, corrections, conflicts, or plans that could be mistaken for execution. If a
Scene is enormous, stop expanding and use targeted Memories plus current verification
instead of opening more of the same session.

**Complete when:** every material claim has supporting evidence, a competing state has
been checked where relevant, and no duplicate Scene is being counted as separate work.

### 5. Prove

A Scene proves origin and context, not present correctness. Verify current technical
claims with files, Git, tests, or live diagnostics. For preferences and conversational
corrections, weigh the source Scene, recency, repetition, and later superseding
evidence.

Current verified reality overrides remembered repository state. Lower reliability
means weaker evidence, not automatic falsehood. State unresolved uncertainty instead
of converting a retrieval miss into certainty.

**Complete when:** each material claim is scene-grounded, currently verified, or
explicitly bounded as an inference or unknown.

### 6. Answer and Preserve

Answer the user's actual question rather than narrating the retrieval process.
Distinguish remembered, scene-grounded, verified, inferred, and unknown only where the
distinction changes trust. Name the originating agent when provenance matters.

After significant work, save one distilled trace packet when future continuity will
benefit. Capture the goal, durable decisions or corrections, verified outcome, and
remaining work. Passive capture already handles routine conversation.

## Boundaries

- Federated Memory and Scene recall is read-only. Preserve the owning
  `source_agent` when opening a foreign Scene.
- Writes, traces, settings, patterns, and learning state remain in the active agent's
  namespace.
- Use diagnostics only after a correctly shaped retrieval still indicates stale or
  broken state.
- Route pattern lifecycle work to the pattern workflow, graph synthesis to the cluster
  workflow, runtime failures to the doctor workflow, and historical imports to
  memory-sync.
- Use direct database inspection only when diagnosing Titan itself.

The governing rule is simple: compile for retrieval, then reason from evidence.
