# Titan evidence-first experiment

This is an experimental branch, not a production release or a claim of perfect
memory. The comparison baseline is a frozen copy of the working tree, including
its pre-existing uncommitted changes, at the start of this experiment. It is not
just the Git HEAD. A file hash manifest is retained outside the repository.

## Strategy

**Terminal state:** an alternate Titan that preserves captured evidence, saves
useful claims without duplicate derivations, and returns relevant, inspectable
evidence with less unnecessary work. It must beat or match the baseline on
declared evaluation cases and pass integrity and adapter checks. A production
promotion remains a separate decision.

**Required conditions:** durable evidence -> trustworthy derived claims ->
conservative duplicate handling -> relevant recall -> honest evaluation.

**Bottleneck:** there is no independently judged general quality benchmark that
establishes the benefit of all current ranking and filtering heuristics.

**Next move:** freeze the current implementation and test cases, make a bounded
alternative, and compare both with the same isolated data and providers.

**Readout:** expected claims retrieved, irrelevant claims returned, justified
abstention, surviving evidence pointers, retry idempotency, provider calls,
serialized response size, and local processing time. Synthetic vectors measure
pipeline behavior, not real-world embedding quality or hosted-model latency.

**Update rule:** keep a simplification only when its behavior is explainable and
it preserves evidence and required contracts. Investigate regressions instead of
loosening acceptance criteria. Retain optional mechanisms for ablation when the
evidence is insufficient to justify deleting them.

**Fallback:** the original working checkout and live memories remain unchanged;
the frozen baseline can reproduce previous behavior. No historical semantic
merges, data migrations, deployment, or main-branch merge are part of this run.

## Design boundaries

The core remains:

`events -> durable scenes -> claim records -> hybrid recall -> bounded evidence brief`

- An event or scene is evidence, not disposable extraction scaffolding.
- An extraction error is different from an intentional empty result. Failed
  derivations must be recoverable from durable scenes.
- A repeated claim from the same evidence can be idempotent. The same words
  from another scene, event, or agent are separate evidence.
- Semantic similarity does not establish identical claims or contradiction.
- Display grouping must retain source-qualified alternate evidence pointers
  and disclose bounded overflow.
- Ordinary retrieval must not modify memory/scene/event content. Optional
  pattern-application telemetry requires explicit opt-in.
- Original scene storage, retries, source isolation, date filters, keyword
  fallback, and output budgets remain essential.

## Work lanes

| Lane | Responsibility |
| --- | --- |
| Astra advisor | Architecture, acceptance gates, final independent review |
| Sol capture | Admission policy, extraction failures, optional projections |
| Sol storage | Same-evidence idempotency, explicit storage failure |
| Sol retrieval | Conservative grouping, evidence retention, ranking |
| Sol evaluation | Frozen comparative cases and reproducible harness |
| Terra defaults | Lean defaults, core tools, optional telemetry |
| Terra integration | Fault injection and end-to-end evidence invariants |
| Root integrator | Ownership, baseline, cross-lane contracts, full verification |

## Initial experiment decisions

Remove synchronous Markdown export and fabricated file-outcome memories from
the normal save path. Narrow generic noise policy so vocabulary such as
“Telegram” and incidental greetings cannot erase substantive facts. Do not
silently turn invalid extraction output into ordinary memories.

Search both streams by default. Disable Step2 ranking by default while retaining
an explicit ablation path. Replace lossy semantic duplicate suppression and hard
scene quotas with conservative display grouping and query-facet selection.

Configured SQLite failures must surface rather than silently selecting a
writable JSON repository. Keep explicit legacy import/read compatibility.
Stop computing unused LNN initialization for new records without migrating old
schemas. Defer provenance compaction until it can preserve legacy records and
full-record compatibility demonstrably.

Core MCP tools become the default profile; the full tool inventory remains an
explicit option. Graphs and learned patterns remain available optional features.

## Verification record

The frozen baseline is local commit `b2c134c`. It captures the existing working
tree separately from experiment changes; comparing only against the earlier
main-branch HEAD would incorrectly attribute existing work to this experiment.
On a fresh checkout it can be recreated with:

```bash
git worktree add ../titan-baseline b2c134c
```

The experiment removes 348 net lines across production Python/configuration
files relative to that baseline. It retains explicit Step2 ablation, graph and
pattern functionality, historical schemas, and legacy imports. It does not
claim that every optional feature has become unnecessary.

Final complete Python suite: **794 passed, 3 skipped, 194 subtests passed,
4 failed**. The frozen baseline has **729 passed, 3 skipped, 156 subtests passed,
the same 4 failures**. These are existing release-artifact tests: the npm runtime
allowlist rejects three inherited onboarding assets (`FONT-SOURCE.txt`, the
Source Serif font, and `titan-logo.svg`). No new failing test remains. This
experiment has not repaired or validated that release packaging boundary.

Tests run against disposable stores. The isolated validation virtual environment
uses already installed dependencies and an explicit package path so subprocess
home isolation does not hide them. npm uses a disposable cache. Initial runs
exposed environment/test setup issues; the final counts above are from complete
runs with those resolved. No live memory corpus or production installation was
modified. Every frozen baseline file still matches its initial content hash.

Independent review found and prompted additional fixes: extraction failure ID
collisions, loss of leading numeric facts, lost duplicate evidence labels,
explicit opt-out regressions, and historical-ID loss during migration. New
scene-backed IDs are stable per scene; opaque historical IDs and migration
references remain intact. Exact display grouping preserves case and internal
whitespace so case-sensitive paths, settings, and string values stay distinct.

## Comparative outcome and next decision

See [evaluation methodology and results](../lean-memory-evaluation.md) and the
[portable result summary](ayanokoji-memory-results.json). The 13 frozen queries
use 14 fictional facts and two agent namespaces; these are a small behavior
evaluation, not an estimate of performance over a personal lifetime of memory.

| Real local embedding result | Baseline | Experiment |
| --- | --- | --- |
| Expected evidence retained | 12/14 | 14/14 |
| Irrelevant evidence items | 5 | 4 |
| Queries meeting every strict gate | 10/13 | 11/13 |
| Capture cases | 4/5 | 5/5 |
| Local steady median latency | 25.23 ms | 20.56 ms |
| Local steady p95 latency | 40.76 ms | 39.72 ms |

The local run uses actual `nomic-embed-text:v1.5` calls, includes embedding time,
and has identical seed-vector hashes across both versions and all Step2
variants. Retrieval provider calls are unchanged. Synthetic timing varied and
was worse in one experiment sample; no general latency, throughput, or disk-space
improvement is established. All six local Step2 variants have identical quality
counts within each checkout, supporting the simpler default on these cases only.

**Do not promote this branch yet.** Both versions return unrelated preferences
for an unknown passport number. The experiment also includes unrelated hosting
facts in a deadline query. Returning a candidate does not establish that it
answers a question. The next gate is independently labeled, held-out answerable
and unanswerable questions across people, projects, dates and numbers, plus real
extraction evaluation and longer capture/replay runs. Do not tune thresholds to
make these thirteen known cases pass.

Astra's final review found no unresolved implementation blocker, but identified
answerability/abstention as the remaining promotion condition. The branch is a
measurable reliability experiment ready for review, not a perfect-memory claim.
