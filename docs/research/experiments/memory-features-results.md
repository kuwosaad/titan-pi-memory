# Corrections, occurrence time and original-source recall

Implemented on `reinventing-titan`. This follows
[the implementation plan](memory-features-plan.md) and the pinned
[Supermemory investigation](supermemory-server-investigation.md). Independent
implementation uses the previously inspected local-server mechanisms as design
evidence; no downloaded server code is included or executed.

## What now works

### Source-backed correction history

The normal save path supplies at most three old facts as read-only comparison
context when the user uses explicit correction language. Each fact must have
subject, scope and attribute slots. This context is supplied separately to the
existing extractor; it does not replace the current source or add a model call.

An extractor proposal may name `supersedes` and `correction_evidence`. Automatic
links require a complete, single-sentence current user message with explicit
correction language, matching subject/scope/attribute, visible replacement words,
and event references supporting that exact message. Hypothetical, quoted,
negated, unrelated-entity and ambiguous evidence is rejected. Both facts remain
stored in every case. For example:

> Correction: Titan local storage database uses SQLite instead of PostgreSQL.

A same-slot older PostgreSQL fact can be linked to the new SQLite fact. A lab
statement cannot retire a production fact. Vague first-person updates or legacy
facts lacking explicit slots often retain both; this is intentionally conservative.
The gate is lexical validation around a model proposal, not a semantic proof.

Durable correction intent is committed with the fact. If link creation fails
after fact persistence, the existing extraction retry completes the link before
considering that scene repaired. Replays preserve canonical IDs and intent.
SQLite links use an immediate transaction; JSON uses the existing cross-process
lock and atomic file-write mechanism. Competing stale targets retain both.
Normal retrieval filters superseded facts; history queries keep them visibly
labeled. Full, compact and timeline output preserve the distinction.

### Event dates separate from capture timestamps

Optional `event_date`, `event_start` and `event_end` survive both storage adapters,
retrieval projections, hydration and public serialization. The original `ts`
still means capture time. Current saving accepts an exact current-source quote
containing its proposed ISO day or month. An ungrounded date stays unknown.

Intervals are calendar dates with inclusive start and exclusive end. For example,
`2024-02-29` ends at `2024-03-01`, so the entire leap day is included. Queries
such as `2024-02-29` or `launch [2024-02]` filter occurrence dates, allowing a
fact saved in November to answer a February question. Unknown occurrence dates
are excluded from explicit occurrence queries. A filename containing a date does
not automatically become a time filter. Parsed event queries do not also apply
capture recency. An empty preferred-session result retries across sessions.

Existing explicit `date_from`/`date_to` arguments retain capture-time semantics;
Python retrieval also accepts `date_basis="event"`. Date-only capture bounds
cover the whole UTC day on JSON and SQLite. Natural-language date understanding
is deliberately limited; this is not a full calendar interpreter. Relative-date
helpers require an explicit reference time, and saving does not infer an event
date merely because one can be guessed from capture time.

### Independent original-conversation passages

Ordinary local and federated recall can search original conversational messages
even when extraction produced no fact. This lane uses bounded lexical matching,
zero model calls and zero new embedding calls. It searches up to 200 recent
scenes per selected source, up to 200 messages and 100,000 characters per scene,
and returns excerpts of at most 500 characters with source/scene/event identity.
System messages, raw tools and recalled-tool projections are excluded.

Passages fill unused result slots; they never displace a selected fact at a small
limit. A source passage is labeled with its role, recorded date and scene, and
kept distinct from current facts in full/compact/timeline output. Brief character
and item limits still apply, including the new labels. The lane is disabled for
occurrence/capture date filtering or restricted memory types/modes rather than
silently broadening those filters. Source selection and session selection carry
through the explicit scene repository.

This is independent source search, not the earlier failed scene-neighbor bonus.
It can miss old scenes, details beyond scan bounds, and paraphrases with no
shared words. It does not claim whole-corpus semantic source search.

## Verification and operational limits

Regression tests exercise both SQLite and JSON with temporary stores, actual
extraction parsing, source scenes, persistence/reopening and public recall. Model
responses and embeddings are controlled so tests can isolate the implementation.
Cases include correct replacement, scope/quote/negation/hypothetical/wrong-entity
rejection, partial-write recovery without re-extraction, duplicate retries,
backfills/leap days/unknown dates, preferred-session fallback, source-only recall,
federated scope, and full/compact/timeline provenance and brief budgets.

Astra independently reviewed the integration and passed 78 focused tests. The
final complete-suite run finished with **919 passed, 194 subtests passed, 3 skipped,
and 4 failed**. All four failures are the inherited release-artifact allowlist
failures previously reproduced on the frozen baseline (onboarding font/license/SVG
assets rejected by packaging). There are no remaining new test failures.
`git diff --check` passed. Live
extraction-model quality, latency at scale and superiority to Supermemory have
not been measured by these scripted tests.

One early acceptance fixture wrote three synthetic scenes to the experimental
checkout's ignored `out/memories/memory_store.db`. The fixture now isolates both
scene readers and writers. The exact fixture rows were verified against their
contents, backed up outside the repository in `test-scene-cache-recovery.json`,
and removed; no associated memory rows existed. This did not touch the original
checkout. All subsequent verification uses disposable `TITAN_HOME` directories.

No commit, push, deployment, or migration of the user's live memories was done.
Opening an experimental SQLite store applies additive optional-column migration;
legacy read-only databases remain readable without migration. Original source
evidence and prior experimental work are retained.
