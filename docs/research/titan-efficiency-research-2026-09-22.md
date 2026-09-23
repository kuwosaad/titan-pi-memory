# Titan efficiency research — 2026-09-22

**Status:** final integrated research report for commit `ee6d634`. It combines
the ten research lanes and proposes no runtime changes.

## Scope and method

I read the repository `AGENTS.md`, traced the HTTP and MCP entrypoints through
retrieval, federation, storage, scene references, scene expansion, and public
serialization, and inspected the corresponding regression tests. The review
was read-only apart from this file: this lane ran no live memories, model
calls, installs, commits, benchmarks, or heavy tests. The report also records
parent-provided synthetic cProfile and benchmark observations, explicitly
labeled with their measurement limits. Line references below are to the
current checkout and are intended to be rechecked before implementation.

The first-principles target is a smaller request working set and less data
movement while preserving the current query candidate universe, ranking,
opposition protection, source-qualified identity, scene evidence contract, and
explicit full-scene expansion. The old attention/LNN machinery is out of scope.

## Findings

### 1. The HTTP recent-memory endpoint may eagerly materialize and return internal fields

**Observed.** `app/api/routes.py:159-175` calls `get_recent_memories()` and then
uses `mem.model_dump()` without the public whitelist. The `Memory` model contains
`embedding`, `provenance`, `h`, `tau`, `incoming_weights`, and
`outgoing_weights` (`app/storage/models.py:5-32`), so `/api/memories` can return
large vectors and internal state even though the Pi/dashboard consumers only
need a compact memory summary. This is a wire-payload problem as well as an
in-memory copy problem.

The existing stable whitelist is already implemented in
`app/save_pipeline/pipeline.py:2309-2340` as `serialize_public_memory()`, and
MCP query/recent tools use it at `entrypoints/mcp_server.py:159-226`. The HTTP
route is the inconsistent adapter.

The underlying recent-memory path is also eager: SQLite
`get_recent_memories()` calls `_row_to_memory(..., decode_embedding=True)`
(`app/storage/memories.py:643-656`), and `_row_to_memory()` converts the blob to
a Python list at `:558-571` while always constructing provenance and decoding
weight blobs at `:518-556`. Those values are then discarded by the MCP
whitelist and are not needed by the compact HTTP response.

**Capability-preserving recommendation, gated by compatibility.** Define one
compact summary projection for new/versioned HTTP, MCP, Pi, and dashboard
recent/search payloads. Keep the current public fields, especially
`source_agent`, `source_event_ids`, `scene_id`, reliability, verification, and
memory kind. Do not expose embeddings, provenance, `h`/`tau`, or edge weights
by default. Use a typed response model or explicit include set at the adapter
boundary, but retain the existing `/api/memories` shape behind a compatibility
version/flag until consumers are checked; a silent whitelist replacement is a
public-shape change, not a free optimization. Leave an explicit detail path
for callers that really need full memory state. Prefer a repository summary
read that never decodes the embedding or edge blobs, rather than constructing a
full `Memory` model and stripping it later.

For the existing `serialize_public_memory()` helper, avoid the unconditional
`model_dump()` of a complete `Memory` object (`pipeline.py:2333-2338`) when the
input is a model; select only the public fields or read those attributes
directly. Pydantic documents both JSON-compatible serialization and
field-level `include`/`exclude` controls, so this need not invent a new wire
format: [Pydantic serialization](https://docs.pydantic.dev/latest/concepts/serialization/).
FastAPI's response-model path likewise filters and serializes to the declared
shape, while an untyped direct response leaves the route responsible for the
shape: [FastAPI response models](https://fastapi.tiangolo.com/tutorial/response-model/).

**Tradeoff.** Existing undocumented clients that rely on internal fields from
`/api/memories` could break. Before changing the wire shape, search consumers
and add a compact contract test. Explicit scene and graph/detail surfaces are
separate contracts; they do not make it safe to remove the old memory payload,
which must remain available through the compatibility version/flag.

### 2. Candidate retrieval reads more SQLite data than ranking needs

**Observed.** Both `SqliteMemoryRepository.query_candidates()` and
`query_candidates_with_text()` eventually call `_filtered_rows()`
(`app/storage/memories.py:675-751`). `_filtered_rows()` executes `SELECT * FROM
memories` at `:714`, fetching embedding, weight, provenance, and every other
column. `_row_to_memory(..., decode_embedding=False, include_blob=True)` still
builds provenance, decodes `outgoing_weights`/`incoming_weights`, and retains
the embedding blob (`:518-571`). Current retrieval uses text, IDs, dates,
reliability, stream/type metadata, source-event IDs, scene ID, and the stored
embedding blob/dimension/dtype. It does not use the weight dictionaries or
provenance in `app/retrieval_pipeline/retriever.py:1030-1229`.

The JSON fallback is more eager: `JsonMemoryRepository.query_candidates()`
starts with `load_all_memories()` (`app/storage/memories.py:325-365`), which
normalizes the complete store including stored embedding lists. This is a
compatibility fallback, not the configured default, but it is still an
unbounded resident working set.

SQLite's own SELECT documentation makes the relevant behavior explicit: `*`
substitutes all input columns into the result expression list, whereas an
explicit result list controls which columns cross the query boundary: [SQLite
SELECT](https://sqlite.org/lang_select.html).

**Capability-preserving recommendation.** Split storage reads into a lean
candidate projection and a full-detail projection. For SQLite, replace `SELECT
*` only on retrieval candidate paths with an explicit column list containing
the fields required by the current filters/ranker plus the embedding blob
metadata. Decode edge weights, provenance, and legacy activation fields only
for explicit detail/graph paths. For JSON, keep the fallback readable but
project candidate dictionaries before ranking and avoid constructing
Pydantic/full-detail objects on the summary path. Keep `query_by_ids()`,
`get_scene()`, and other explicit detail APIs fully hydrated so durable data
and old stores remain readable.

Do not put an arbitrary SQL `LIMIT` on candidates as a shortcut: the current
retriever scores the entire filtered candidate set before sorting, duplicate
collapse, and diversity selection. If a bounded working set is required,
implement streaming/chunked candidate scoring while retaining the same
candidate set and deterministic final top-pool semantics, then hydrate selected
winners before returning the existing direct `retrieve_memories` full-payload
contract. Verify IDs and scene refs against the current path.

### 3. Scene pointers are intentionally cheap on SQLite, but two paths defeat that

**Observed good boundary.** `retrieve_memory_brief()` defaults to an empty
`scenes` array and returns metadata-only `scene_refs`
(`app/save_pipeline/pipeline.py:2417-2447`). `scene_references_from_memories()`
deduplicates scene IDs and performs one metadata lookup for the active store
(`pipeline.py:2262-2303`). SQLite's `get_scene_references()` selects only
`scene_id`, evidence version/status, and missing event IDs
(`app/storage/scenes.py:757-777`), so it does not hydrate raw events. The
explicit `get_scene_context()` path (`pipeline.py:2450-2470`) is the intended
full evidence expansion and should remain explicit.

**Observed waste.** Federated scene references collect per-source IDs in
`app/retrieval_pipeline/federated.py:349-367`, but then call
`repository.get_scene_references([scene_id])` once for every scene at
`:369-381`. The `grouped` map is not used to batch the call. This multiplies
SQLite round trips and, for the JSON adapter, multiplies full-file loads.

`JsonSceneRepository.get_scene_references()` (`app/storage/scenes.py:505-510`)
calls `load_all_scenes()`, which normalizes every scene and validates its
evidence payload (`:427-430` and `:63-161`), even though the returned value is
only a four-field reference. That is unnecessary eager evidence hydration and
retention on the compatibility path.

**Capability-preserving recommendation.** Batch scene IDs once per source in
`FederatedRecall.scene_references()`, preserve input order, and add
`source_agent` to every returned reference exactly as today. For JSON fallback,
use one pass that extracts only reference metadata (or a separately maintained
metadata index) and reserve full scene normalization for `get_scene()`/explicit
expansion. Keep the current default pointer-only behavior and the legacy
`include_scenes=True` alias, which currently aliases lightweight refs rather
than bodies (`tests/test_retrieval_scene_refs.py:91-115`). Do not silently add
raw events/messages/tool calls to search responses.

**Tradeoff.** A reference index adds invalidation/recovery obligations. A
single-pass JSON read is the lower-risk compatibility fix but still has JSON
parser memory proportional to the file. SQLite remains the efficient metadata
path; the fallback should not acquire a different evidence contract.

### 4. Federation repeats query embedding work and retains duplicate hit graphs

**Observed.** One local retrieval call embeds the query aspects once at
`app/retrieval_pipeline/retriever.py:1077-1087`. Federation calls
`retrieve_memories()` independently for each selected namespace at
`app/retrieval_pipeline/federated.py:264-304`, so the same normalized query and
its aspects are embedded once per source. Each call also builds its own hit
objects, aspect-score lists, and `embedding_by_id` map before federation merges
them.

The final merge is intentionally bounded to the requested limit, but it happens
after all per-source work. `_merge_hits()` keys by normalized memory text and
keeps only one winner (`federated.py:316-329`), with active-source preference
and score/timestamp tie-breaking (`:331-347`). This avoids repeated visible
text, but it can discard a sibling source's scene pointer because
`retrieve_memory_brief()` asks for scene refs from the surviving memories only
(`pipeline.py:2436-2440`). That is a source-lineage risk, not a reason to
remove deduplication blindly.

**Capability-preserving recommendation.** Add a request-local retrieval context
that computes the ordered query/aspect vectors once and passes immutable vectors
to each namespace runner. Key any optional cache by the exact effective text
sent to the provider *and* the full embedding identity: provider, endpoint,
immutable model/revision, options, and dimensions; bound its lifetime to the
request or use a small byte-bounded cache. Likewise deduplicate missing
candidate texts across namespaces before embedding, while preserving each
source's candidate IDs and scores.

Keep the current opposition guard, ranking, and merge behavior in this
efficiency change. The existing same-text/source-lineage question—especially
when sibling namespaces have different scene IDs—is a separate semantic
decision, not permission to union or rewrite lineage while reducing work. Any
future change needs a regression case with identical text in two agents and
different scene IDs; otherwise a cheap dedup optimization silently changes
federation semantics.

The embedding function returns NumPy arrays (`app/embedding/embedder.py:20-60`)
and retrieval keeps them in `vectors` and `embedding_by_id`
(`retriever.py:1097-1143`) until selection completes. This is useful for exact
cosine comparisons, but it is internal state and should never cross the public
MCP/HTTP boundary. NumPy documents that `.tolist()` creates a Python copy of
the array data: [NumPy `ndarray.tolist`](https://numpy.org/doc/stable/reference/generated/numpy.ndarray.tolist.html).
Prefer bounded request-local arrays, chunked missing-text embedding, and
post-selection detail hydration over converting vectors into public lists.

### 5. Query-time duplicate/text work should be cached only when semantics stay fixed

**Observed.** Retrieval intentionally performs several text-derived passes:
canonical text hashing (`retriever.py:322-341`), near-duplicate tokenization and
opposition checks (`:369-409`), query-aspect/lexical expansion (`:498-537`),
query-echo detection (`:590-603`), and final diversity checks
(`:778-855`). These passes protect exact behavior: opposition statements are
not collapsed, query echoes are excluded, and per-scene/source-event limits are
enforced.

**Parent-provided diagnostic.** The 5k-memory/768-dimensional SQLite fixture
profile reports 9.881 seconds across ten fixed-inference queries and
18,358,311 instrumented calls: hidden-metadata filtering 3.280 seconds (33%),
near-duplicate collapse 2.846 seconds (29%), candidate querying 1.710 seconds
(17%), and cosine aggregation 0.568 seconds (5.7%). The artifact also reports
`_row_to_memory` at 1.199 seconds across roughly 55k rows, about 1.1 million
regex calls, 226,630 tokenizations, and 123,040 content-token calls
(from a local profiling artifact). These are
instrumented cProfile shares, not production CPU fractions or a claimed
speedup opportunity: Python's profiler documentation warns that profilers are
not benchmarks and that Python-call overhead differs from C-call overhead:
[Python profiling](https://docs.python.org/3/library/profile.html).

**Recommendation.** If profiling confirms text processing as a bottleneck,
compute immutable per-memory text features lazily, on first use by a participating
duplicate/selection check (or store a versioned feature cache keyed by the exact
memory text and text-policy/config revision). Reuse token sets, canonical text,
lexical terms, and opposition markers across dedup, selection, and echo checks.
Keep the feature version/config in the cache key, bound resident cache size,
and retain the current exact opposition guards. Do not eagerly build token sets
for the entire corpus merely to eliminate a later pass.
This is safer than replacing the ranking path with ANN, quantization, or a new
model whose equivalence has not been demonstrated.

### 6. Runtime reuse should stop at the resolved namespace boundary

**Observed locally.** The Codex MCP launcher passes through to the managed
runtime with `os.execvpe` (`integrations/codex_titan_plugin/scripts/titan_mcp_launcher.py:29-61`),
so its launcher is not an additional resident wrapper after startup. Pi's
extension owns a local HTTP child process (`tools/pi_extension/index.ts:1-20`),
while its extension process also owns short-lived browser graph servers
(`tools/pi_extension/index.ts:1140-1188`). Runtime contexts are cached by the
resolved environment signature (`app/runtime/context.py:246-288`), and memory
and scene repositories are likewise keyed by backend/path
(`app/storage/memories.py:767-793`, `app/storage/scenes.py:806-830`). These
keys are namespace isolation boundaries, not evidence that every client can
safely share one global process.

**Accepted cross-lane finding.** Codex/OpenCode stdio clients each start their
own engine/ingester, Pi owns one HTTP child per Node host, and Claude already
has a singleton authenticated daemon/light-proxy with leases and idle exit.
No lane measured installed-process counts, RSS multiplied by clients, or net
physical memory, so those are not performance claims here. MCP's transport
specification makes process/connection lifecycle a host concern rather than a
reason to erase namespace identity: [MCP transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).

**Capability-preserving recommendation.** Reuse an existing runtime only when
the resolved Titan storage home (`TITAN_HOME`), explicit database path, agent
identity, and runtime version match; do not introduce a process shared across
namespaces. Global runtime/repository caches and
source-qualified origin, token, and identity checks make a cross-namespace
singleton a correctness risk. Preserve the existing lifecycle and identity
tests. If optional imports are moved off startup paths, function-local imports
are the smallest change; Python's `LazyLoader` is available but adding a new
loader layer is not automatically cheaper: [Python importlib lazy loading](https://docs.python.org/3/library/importlib.html#importlib.util.LazyLoader).

### 7. Ingestion reconciliation rereads broad state; durability outranks scan reduction

**Observed.** The automatic ingest worker discovers every spool `*.jsonl`,
processes every discovered session, then runs maintenance on each interval
(`app/save_pipeline/auto_ingest.py:14-72`); the default interval is three
seconds (`:75-100`). The legacy event-ledger readers load the entire ledger
with `read_text(...).splitlines()` before filtering by session or checkpoint
(`app/storage/traces.py:510-531`, `:710-716`). The incremental spool path does
read from a cursor, but rewrites cursor state after the read
(`app/storage/traces.py:1092-1152`). Pending-recovery inspection can scan up to
100,000 scenes (`integrations/codex_titan_plugin/pending_recovery.py:378-464`).

**Capability-preserving recommendation.** Add a cheap no-op/due gate before
expensive reconciliation. Native watchers are an optional later hint, not a
required dependency; treat notifications only as hints and retain periodic
reconciliation for missed events. Preserve
pending and retry records even when a source reaches EOF; a notification/EOF
optimization that drops them changes crash recovery. A later durable indexed
ledger with incremental queries and transactions is the coherent long-term
direction, but it is not a safe justification for an immediate storage rewrite.
If a cache invalidation shortcut is considered, SQLite `PRAGMA data_version`
only compares changes observed by one connection; it is not a global revision
number that can be read from a newly opened connection:
[SQLite `data_version`](https://sqlite.org/pragma.html#pragma_data_version).

### 8. Embedding and optional-analysis residency need explicit ownership and keys

**Accepted cross-lane finding.** Embedding already batches texts per call, but
federated namespaces repeat the same query/aspect embedding. The first safe
move is a request-local shared vector keyed by provider, endpoint, immutable
model version, options, dimensions, and the exact effective text sent to the
provider; an exact bounded cache is a later option. Do not cache failures.
Missing-candidate vectors may
be transient or held in a private read cache, never written into a foreign
namespace. Stored rows currently carry blob/dimension/dtype but no model
identity, so new vectors should be tagged and legacy-vector migration should
have an explicit owner; treating every untagged row as missing would trigger a
large, behavior-changing re-embed. Ollama documents batched `/api/embed`
inputs and `keep_alive`; the latter trades idle residency for cold-start cost:
[Ollama embeddings API](https://docs.ollama.com/api/embed), [Ollama FAQ](https://docs.ollama.com/faq).

**Cache boundary.** Existing corpus analysis already uses exact blockwise
computation and a two-entry snapshot cache (`app/graph/corpus_analysis.py:219-243`),
and pattern mining reuses that corpus path. Any new cache must include every
output-affecting field (metadata/source/detail/config, database identity, and
budget), not merely `(id, text, embedding)`, and must have a byte/count bound.
This matters even for standard memoization: Python documents that
`functools.lru_cache` retains arguments and return values until eviction or
clear, so a cache hit is also resident working set:
[Python functools caching](https://docs.python.org/3/library/functools.html#functools.lru_cache).
The current graph/Cortex/pattern routes eagerly import heavy optional modules
(`app/api/routes.py:21-39`, `app/graph/cortex_analysis.py:1-25`,
`app/patterns/miner.py:1-22`). Function-local imports can reduce baseline
startup residency while retaining the optional features. Do not claim sparse
graph edge truncation, changed page contracts, ANN, quantization, or a smaller
model are exactly equivalent to current analysis.

**Related UI payload.** Pattern graph serialization builds `nodes` and `links`
then embeds the complete JSON in HTML (`app/patterns/graph.py:30-70`). Reduce
this only through an explicit, versioned graph/detail contract; silently
truncating edges or changing pagination can remove evidence relationships.

### 9. Candidate-universe preservation is the hard limit on read reduction

**Accepted correction.** The lexical and semantic paths are not universally
redundant: session-biased FTS fallback can reach records outside the active
semantic subset. Therefore a lean FTS rowid set plus an explicit semantic
projection may reduce hydration, but it must preserve the lexical-plus-semantic
union, fallback behavior, stable ordering, and source/scene lineage. Do not
pre-limit by timestamp or FTS rank. Existing pool limits apply only after the
filtered universe has been scored (`app/retrieval_pipeline/retriever.py:1181-1229`).
The safe shape is: select the union of candidate IDs, fetch a private minimal
projection, score all candidates, then hydrate only selected winners. SQLite's
query planner and explicit column projection support this direction, but no
universal PRAGMA (`synchronous=off`, `read_uncommitted`, or `immutable=1`) is
safe for mutable durable files.

### 10. Write batching is promising only when the durability contract stays per item

**Observed.** The Claude runtime's `_ingest_event_batch()` still validates and
passes events one at a time through `ingest_trace_event(...,
process_new=False)` before processing touched sessions
(`integrations/claude_titan_plugin/runtime/daemon.py:90-108`). The event-first
pipeline's public boundary is likewise per-event
(`app/save_pipeline/pipeline.py:1988-2008`, `:2150-2164`). The repository also
has `append_events_batch()`, which deliberately holds one lock and performs one
index load/save for a batch (`app/storage/traces.py:605-691`). The useful
optimization is therefore batch admission at the durable boundary, not merely
wrapping a per-event loop.

**Capability-preserving recommendation.** A future batch path must retain
validation, redaction, per-item status/receipt, sequence assignment, fsync and
durable-ack semantics. Batch retry removal and derived-note projection only
after recovery and return-contract tests prove that a crash cannot lose a
pending item or make an acknowledged item invisible. Version-guard any
schema-on-open change. SQLite's transaction, WAL, synchronous, and corruption
guidance are the relevant primary constraints: [transactions](https://sqlite.org/lang_transaction.html),
[WAL](https://sqlite.org/wal.html), [`synchronous`](https://sqlite.org/pragma.html#pragma_synchronous),
and [how SQLite files become corrupt](https://sqlite.org/howtocorrupt.html).

## Public transport contract to preserve

The current safe shape is already visible in `serialize_public_memory()` and
the tests: compact memory fields plus source-qualified scene pointers; full
scene evidence only from `get_scene_context`. MCP's official tools
specification separates JSON `structuredContent` from text content and permits
an output schema; a server-provided schema must be conformed to:
[MCP server tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
The official Python SDK documents `content` as model-facing text and
`structured_content` as typed client data: [MCP Python SDK tools](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md).

That supports a compact typed MCP result without removing the explicit scene
tool. Any payload reduction must preserve:

- query mode, count, brief, route, and timeline fields already returned;
- memory IDs, text, timestamps, stream/type, source-agent identity, source-event
  lineage, reliability/verification metadata, and scene IDs;
- metadata-only `scene_refs` with evidence status/version/missing event IDs;
- explicit full-scene access, including raw events/messages/tool calls and
  evidence completeness checks;
- lexical fallback and exact query/ranking behavior when embeddings are
  unavailable.

## Equivalence and verification gates

Any implementation must preserve, or explicitly version, all of the following:

- IDs, encounter order, scores within an explicit numeric tolerance, hidden
  exclusion, duplicate metadata, opposition protection, and diversity rules;
- lexical/semantic hybrid union, filters, session fallback, source-qualified
  scene lineage, and identical IDs across namespaces;
- zero, invalid-dimension, short, malformed, and reordered embedding batches
  without assigning a vector to the wrong record; durable vectorless saves and
  lexical fallback must continue to work;
- cache invalidation on exact text, retrieval settings, model identity, and DB
  replacement, with foreign stores remaining read-only;
- current encounter-order rules: a `>=` timestamp duplicate replaces the
  earlier record, while a maximum duplicate-quality rule keeps the first. SQL
  joins, `argpartition`, vector batching, or changed tie handling can silently
  alter this behavior;
- graph helper edge cases such as `top_k=0` and ties. Vectorized batching is
  conditional on preserving zeros, invalid dimensions, thresholds, and ties;
  it is not automatically drop-in equivalent.

The baseline numbers available to the parent use a fixed synthetic 768d
provider stub. They do not measure a real model, daemon lifecycle, or ingestion
load. Paired fresh-process timings, idle-memory tests, ingest/recovery tests,
and full-model residency tests remain necessary before claiming an improvement.
The cProfile shares in section 5 include instrumentation and overlapping call
cost; they are diagnostic, not uninstrumented CPU shares or savings estimates.

## Prioritized roadmap

1. Add lazy, exact text features only when participating records reach the
   duplicate/selection checks, and use a lean private candidate projection;
   preserve the complete lexical-plus-semantic candidate universe. A bounded,
   versioned cross-request policy cache is optional and must not be implied by
   request-local memoization.
2. Prepare exact query/aspect embeddings once per request for namespaces with
   the same provider fingerprint; deduplicate missing candidate texts only when
   vector assignment and numerical edge cases are proven equivalent.
3. Batch federated scene-reference metadata reads and stop JSON reference paths
   from normalizing full evidence bodies; hydrate selected winners for the
   existing direct-retrieval contract.
4. Reuse the existing daemon/runtime pattern only within the same resolved
   `TITAN_HOME`, DB, agent, and version identity. Move optional imports local
   and tune model idle residency only with lifecycle and cache-key tests.
5. Add an ingestion no-op/due gate before expensive reconciliation; retain
   periodic recovery, pending/retry records, and EOF behavior. Add durable
   write batching only after per-item receipt and crash tests pass.
6. Treat compact HTTP serialization as a later opt-in/versioned compatibility
   change, not a silent replacement of the existing `/api/memories` shape.
   Defer an indexed ledger rewrite, ANN, quantization, smaller models, graph
   truncation, and page-contract changes because they are not guaranteed
   same-capability defaults.

## Known and unknown

Known: the current code has public summary serializers, pointer-only scene
briefs, SQLite candidate and scene projections that can be made leaner, a
request-local opportunity for repeated query embedding, exact batch append
machinery, and explicit namespace-scoped caches. The ten lanes are represented
by the numbered findings: API/MCP payload shape; candidate hydration; scene
expansion; federation; text/cProfile; process lifecycle; ingestion; embedding
residency; graph/Cortex/pattern optional paths; and durable writes/portability.

Unknown: production distributions of namespaces and clients, real model
latency/RSS, daemon idle residency, ingestion event rates, and whether any
consumer depends on the undocumented HTTP internal fields. Those require
paired deployment-like measurement and consumer inventory; this report does not
invent them.
