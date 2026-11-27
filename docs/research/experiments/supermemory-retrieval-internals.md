# Supermemory local server: retrieval internals

Inspected 2026-11-11, **statically, without executing the server**. This report
traces the JavaScript embedded in the official macOS ARM64 server v0.0.9. It
describes this artifact, not the hosted service or a benchmark result.

The central finding is concrete: the local search path fetches memories and
source chunks independently, selects a bounded mixed result set, optionally
runs an LLM aggregation step, then attaches graph history. **Graph attachments
are not a graph-based ranking algorithm in this path.** Ordinary reranking
contains a Cloudflare model call, but the local boot environment leaves that
binding undefined. Aggregation is a separate, generative mechanism.

## Evidence identity and navigation

The upstream binary SHA-256 is
`b908080270df6d2eb5712637cb833ebd4fc99a6ee15771d38a555084961332e8`.
The inspection file is
[`server-analysis.js`](../../../supermemory-artifacts/derived-analysis/server-analysis.js),
SHA-256 `89e5209960118a376bd1d4fe3914b966c9d41097010f3612deff02651c4aaf1f`.
It contains 4,954,756 ASCII characters/bytes. Ten large base64-shaped runs were
omitted; their original ranges/hashes are retained in
[`derivation.json`](../../../supermemory-artifacts/derived-analysis/derivation.json).
These are derived inspection bytes, not original source or a runnable rebuild.

The parent investigation already analyzed this artifact with REA; its exported,
partial analysis is in
[`rea-evidence.json`](../../../supermemory-artifacts/rea-evidence.json).
This pass directly read bounded text slices and checked function boundaries
against the separately generated
[`symbol-ranges.json`](../../../supermemory-artifacts/derived-analysis/symbol-ranges.json)
Babel AST index, which records no parse errors. The REA session does not retain
those prior records in this worker's connection; no new target session or
execution was started. This report's anchors are independently reproducible
against the stated inspection-file hash.

Offsets below are **zero-based, half-open derived-file character ranges**;
line:column uses one-based coordinates. They are not binary offsets.

| Symbol | Character range | Start line:column | Role |
| --- | --- | --- | --- |
| `V88` | 3948909–3951741 | 1793:4261 | v5 search body schema |
| `$88` | 3962014–3964516 | 1793:17366 | v5 namespace search handler |
| `U75` | 3947049–3947459 | 1793:2401 | v5-to-shared-search options |
| `n75` | 3947657–3948581 | 1793:3009 | v5 response mapper |
| `UI` | 3526619–3547460 | 1629:2440 | shared memory/chunk search |
| `Fw0` | 3514703–3515237 | 1537:37893 | guarded temporal parsing |
| `yt8` | 3508423–3508680 | 1537:31613 | event-interval overlap filters |
| `At8` | 3524838–3526619 | 1629:659 | chunk candidate retrieval |
| `Ld`, `zT` | 3473204–3473447 | 1531:9018 | normalized-text winner deduplication |
| `Bt8` | 3519321–3519395 | 1561:543 | apply text dedup to mixed candidates |
| `Qt8` | 3519395–3519792 | 1561:617 | reserve memory/chunk result slots |
| `Et8` | 3516758–3519321 | 1537:39948 | LLM aggregation |
| `It8` | 3520191–3524838 | 1561:1413 | bounded relation enrichment |
| `L88` | 4101718–4105021 | 2942:9954 | legacy v4 memory search route |
| `f88` | 4049916–4053287 | 2875:515 | legacy v3 document search route |
| `x25` | 4035217–4037967 | 2846:2568 | legacy document search orchestration |
| `i25` | 4045913–4046681 | 2871:6261 | legacy document reranking |
| `k25` | 4046681–4048135 | 2871:7029 | legacy document/chunk grouping |
| `fv5` | 4944907–4945075 | 3906:1577 | local HTTP environment |

Additional useful anchors: `cX` at character 2372764, line 426:4997;
`sJ8.searchMemories` at 2009105, line 328:1661;
`sJ8.searchChunks` at 2007631, line 328:187;
cosine-distance SQL helper `pu` at 81135, line 5:31657;
search constants at 3547460, line 1647:13431.

## 1. HTTP admission, scope, and defaults

The registered namespace route is `POST /ns/:namespace/search`, wired to `$88`.
Its visible chain is:

```text
namespace/body/query validation
  → search tracking and quota wrappers
  → G5 namespace permission filtering
  → $88: require organization and user
  → resolve validated namespace
  → U75 map v5 options
  → resolve VectorDBService
  → UI execute search
  → fetch current result flags from SQL
  → n75 map v5 response
```

`V88` is strict. The query is trimmed and must be nonempty. Limit is an integer
from 1 to 100, default 10. Threshold is in [0,1], default 0.3. Search mode defaults
to `hybrid` and accepts `hybrid`, `memories`, or `chunks`. Includes default to no
documents, no related facts, and no forgotten facts. Rerank is one mutually
exclusive enum: `none` (default), `order`, or `aggregate`. Query rewriting
defaults off. The query-string schema is an empty strict object.

The namespace schema `om` preprocesses percent encoding, then uses `n7` at
character 3388267, line 1509:15691: at most 100 characters, restricted to
letters, numbers, underscores, colons, and hyphens. Namespace permission checks
are real middleware, not a semantic similarity filter. `G5` selects authorized
container tags before the handler; the handler also rejects missing user/org.
This report does not claim a complete authorization audit.

`U75` maps v5 `chunks` to the shared engine's `documents` mode; `related` becomes
`relatedMemories`; `documents` enables both document metadata and summaries;
`forgotten` becomes `forgottenMemories`. The enum selects either ordering or
aggregation. Legacy `L88` calls the same `UI`, with legacy input names, and
explicitly rejects simultaneous boolean aggregate/rerank options.

The shared `UI` default threshold is 0.4, but **v5 passes its validated 0.3**.
The API version therefore matters; internal function defaults alone do not
establish HTTP behavior.

## 2. Query preparation and embeddings

`UI` validates the query again and resolves temporal headers through `yw0`:
explicit Supermemory/timezone headers, then UTC; locale headers, then en-US.
`Fw0` only attempts its rule parser for short, single-line queries (at most
200 characters), and has guards for declarative text, future-intent language,
ambiguous parsing, exclusion language, and unsupported range constructions.
These are observable heuristics, not proof that every date is understood.

An accepted parse removes the date phrase from the semantic query and ANDs the
original metadata filter with interval-overlap conditions:
`temporalEventStartMs < requestedEnd` and
`temporalEventEndMs > requestedStart`. Thus it can search **event time**, not
merely record creation time. Ambiguity leaves the original query/filter alone.

Three operations are launched together: primary embedding, optional rewriting,
and namespace-to-space lookup. Unless a date-only memory query qualifies for
the SQL bypass, the primary embedding is requested through `cX` with task type
`QUESTION_ANSWERING` and profile `interactive`. The query is estimated at two
bytes per character and truncated to 18,000 characters if that estimate exceeds
36,000. The estimate is a source-level policy, not measured tokenizer cost.

With rewriting enabled and no accepted rule-based temporal parse, a configured
fast-model call requests alternative queries and an optional date range. The
actual retained list is deduplicated and capped at **three total queries**, not
three extras. Additional query vectors are generated in one separate `cX` call,
sharing a 36,000 estimated-byte cap. Rewrite failure falls back to the original
query. The primary embedding and rewrite are parallel; additional embeddings
wait for rewriting. The namespace lookup can return an empty result if the
namespace has no corresponding space.

The embedding/provider implementation, SQL driver, model weights and execution
cost are separate layers. This pass observes calls into them, not their runtime
quality. The local vector adapter itself is visible SQL, not an opaque native
bridge: it computes `1 - cosine_distance`, orders by distance, and returns
scoped rows. The companion [embedding/vector audit](supermemory-vector-internals.md) traces
that lower layer; omitted blobs and the native/WASM engines remain unexamined here.

## 3. Candidate collection and current-state filtering

For a normal query, each retained query vector requests up to `3 × limit`
memory candidates; aggregation increases the multiplier from 3 to 5. Chunk
retrieval runs concurrently in hybrid/chunks mode, using **only the primary
query vector**, with the same 3× or 5× limit. Query rewrites therefore broaden
memory retrieval but do not independently fan out the chunk lane in this path.

The local `searchMemories` adapter resolves org and namespace to a space,
requires a stored embedding, and ordinarily requires `isLatest=true`,
`isForgotten=false`, and an absent or future `forgetAfter`. With forgotten
included, its current-state condition becomes latest OR forgotten; that does
not mean every superseded historical fact becomes a primary match. Metadata
conditions are applied before SQL ordering/limit, and similarity thresholding
is applied to returned rows. `UI` repeats latest/forgotten/expiry checks and
retains the strongest score for each memory ID across rewritten queries.

The chunk lane resolves scoped active chunks, applies metadata constraints and
thresholds, then hydrates their documents and checks document membership in the
requested org/namespace. Its returned chunk text is the source content; response
metadata comes from the source document. This is a memory-plus-source-chunk
hybrid. **No BM25/vector fusion or lexical search is visible in this inspected
main local path.** That bounded observation does not establish absence from all
other builds or search surfaces.

Two temporal branches differ:

- A rule-parsed date-only memory query, with an existing space and no filepath,
  skips embeddings. SQL applies event-time metadata filters, interleaves groups
  by event start, and assigns ordering scores near `0.95 - index × 0.0001`.
  These scores are assigned ordering values, not semantic similarities.
- When the optional LLM rewrite supplies a date range, each query searches both
  that range and an unrestricted lane. The dated lane uses
  `ceil(limit × multiplier / 2)` candidates and receives a +0.1 score cap at 1;
  the unrestricted lane gets three times that candidate count. Local adapter
  range filtering is over creation time. A later 0.85 multiplier applies to
  returned memory rows lacking `matchedByDate`; the visible assembled rows do
  not set that property. This is a static scoring asymmetry worth testing,
  rather than assuming the labels implement a correct date preference.

The catch branch described as falling back to SQL in the rewrite/date search
still calls `vectordb.searchMemories` again. Its log message alone does not prove
a distinct fallback algorithm.

## 4. Merge, deduplication, and selection

`UI` combines memory and chunk candidates, then `Bt8 → zT → Ld` selects one
winner per normalized text. Despite telemetry calling it ID deduplication, the
key lowercases text, removes punctuation/non-ASCII word characters through
`[^\\w\\s]`, collapses whitespace, and trims. Highest similarity wins.
It does not union source IDs or evidence references in this function.

That normalization can conflate code/path case and decimal punctuation—for
example, `1.2` and `12`. Distinct source evidence may disappear with the losing
candidate. This is a reason to retain Titan's more conservative,
evidence-preserving display grouping, not an idea to import.

Candidates below threshold are discarded and the rest sorted by similarity.
Without aggregation, only the requested limit enters the postprocessing stage.
With aggregation, up to `3 × limit` enters it.

Hybrid selector `Qt8` reserves
`min(floor(requestedCount × 0.2), floor(requestedCount / 2))` slots for each
available surface, then fills the remaining slots from the common score order.
It finally preserves the original order of retained candidates. With a normal
limit of 10, that is up to two guaranteed memory and two guaranteed chunk slots
when both surfaces are available. It is a coverage quota by **surface**, not a
learned relevance score or an answer-completeness guarantee.

## 5. Ordering rerank and generative aggregation are different

The ordering branch in `UI` sends selected candidate texts and the query to
`@cf/baai/bge-reranker-base` via `env.AI.run`. Returned model indices reorder the
candidate list; the original similarity field is retained. There is no visible
reranker rescue of candidates outside the already selected limit. Empty model
output or an exception returns the previous selection. This branch has less
index validation than legacy document reranking, whose `T25/Z25` validate finite
scores, reject duplicate/out-of-range indices, and append unranked candidates.

**Local qualification:** `fv5` constructs the local HTTP environment with
`AI: undefined`. Its boot caller at character 4950655 supplies workflow bindings,
not AI. `UI` explicitly skips ordering when AI is absent. A configured LLM key
for extraction/aggregation does not itself populate this Cloudflare binding.
This is static wiring evidence; we did not execute a request or assert that
all alternative launch configurations are identical.

Aggregation calls `Et8`, which receives up to three times the requested result
count. It divides candidates into batches of **10**, with **10 concurrent batch
operations**, and invokes the configured fast model for each batch. Inputs carry
text, candidate index, surface type, similarity, and available date/version/path.
The output schema requests summary text and a model-supplied relevance score.
Each call explicitly sets one retry. If summaries outnumber the requested
limit, another model call synthesizes the batches. Failed batches fall back to
individual texts; failed final synthesis takes the first available summaries.

This is substantive synthesis, not merely sorting. The prompt directs merging
related items and tells the model never to claim information is missing. That
instruction is a potential missing-answer failure mode, **not evidence that a
hallucination actually occurred**. The visible output contains no explicit
summary-to-input-ID attribution map or answerability check.

Generated items receive `aggregated_*` IDs. `UI` assigns null metadata, version
1, null root ID, and a current update timestamp, then mixes summaries with the
original items and applies the result limit. `n75` does not expose the internal
`isAggregated` flag. SQL has no corresponding synthetic row, so its response
mapper defaults such results to latest=true and inference=false. Attachments
that rely on stored source joins cannot recover an explicit generated-summary
lineage from those IDs in the inspected code. These are representation
limitations to avoid copying into Titan.

At limit 10, aggregation may inspect 30 selected candidates, issue three batch
model calls, and potentially a fourth consolidation call. At limit 100, the
same visible bounds permit 30 batch calls plus a consolidation call. Retries
and provider-level failover can add work. These are source-derived call counts,
not latency, token, billing, or quality measurements.

## 6. Graph/history and source attachments happen after ranking

After selection/aggregation/reranking, `UI` optionally invokes `It8`:

- `updates` links are traversed one and two hops in both directions. Candidate
  traversal caps are 30, with at most 10 returned parents and 10 children per
  selected ID.
- Non-update links (`extends`/`derives` under the declared relation schema) are
  fetched in either direction at one hop. SQL has a global `selectedCount × 5`
  limit, followed by at most 10 related entries per selected ID. The query has
  no explicit relevance ordering; this is not a top-10 semantic rerank.
- Attached facts must match org, space, supplied metadata filter, not forgotten,
  and unexpired. They are **not required to be latest**, allowing historical
  versions to be shown. The call uses the original metadata filter, rather
  than the temporal-parser-augmented filter, and omits the optional forgotten
  argument. Thus primary forgotten inclusion is not automatically inherited
  by attached history.

There is no observed feedback from these attachments into the primary scores,
no second admission stage, and no graph rescue when there are no selected hits.
The response calls these parents, children and siblings; those names do not
establish an additional hidden ranking mechanism.

Document attachments use memory-to-document source joins. Legacy `include.chunks`
can fetch directly linked chunk IDs, then fall back to active chunks from the
source documents; its query cap is `3 × limit` and output takes up to five
chunks per memory. V5 maps its options with this legacy flag disabled; its
primary chunk surface is the independent candidate lane instead.

`n75` exposes one document per result, choosing the newest document update
(and ID to break a tie) when several were attached. Its public result fields
include memory or chunk text, similarity, public metadata, current-state flags,
time/path fields, and optional related/source objects. The API limit is a
primary-result count. No equivalent to Titan's complete rendered-character
budget is visible here; attached history and document material add text beyond
that count.

## 7. Legacy document search is a separate pipeline

`POST /v3/search` uses `f88 → x25`, not the mixed `UI` pipeline:

```text
validate/scoped tags → optional document-ID resolution → temporal parser
  → optional f25 query classification/rewrite (up to 5 distinct queries)
  → S25 embeddings, unless date-only
  → H25/yZ0 per-query, per-namespace chunk search
  → strongest score per chunk ID → optional i25 reranking
  → k25 group chunks by document → response
```

`S25` uses `RETRIEVAL_QUERY` and the same 36,000 estimated-byte batch cap. Normal
candidate count is the requested limit. Ordering rerank expands it to
`max(limit, min(3 × limit, 30))`; date-only search instead uses
`max(limit, min(5 × limit, 50))`. `i25` reranks only its first 30 candidates and
also skips when the local AI binding is absent.

The date-only branch uses active source chunks sorted by document creation
and chunk position, with assigned scores `max(0, 0.9 - index × 0.01)`.
`k25` groups by document and budgets the number of relevant chunks, rather than
simply counting documents. With `onlyMatchingChunks=false`, adjacent positions
may be returned as additional context, marked `isRelevant=false` with score 0.
Those neighbors do not consume that relevant-chunk limit. This is an explicit
context attachment contract, not proof of answer relevance.

## 8. Costs, unknowns, and Titan decisions

The self-hosted artifact stubs quota/usage helpers: `Pr` is pass-through and
`WP` is empty (characters 3472678 and 3472770). Hosted operation charges and
published latency figures cannot be inferred from those wrappers. Embedding
inference, configured-model calls, SQL work and optional text generation still
have real computational cost.

Nothing here was executed: HTTP reachability, actual query results, model output,
native/WASM behavior, index performance, provider availability, and hosted parity
remain unverified. Omitted binary-shaped blobs were not searched or decoded.
Static function/SQL wiring is stronger evidence than API labels, but it is not
an end-to-end correctness test.

Three focused Titan adaptations are justified as experiments, not automatic
production changes:

1. **Typed historical context after selection.** Titan already has
   [source-qualified scene expansion](../../app/retrieval_pipeline/federated.py)
   and [scene lookup](../../app/save_pipeline/pipeline.py). When explicit,
   source-backed update links exist, attach bounded old/new evidence under a
   history field while leaving current ranking and both sources inspectable.
   Carry the original filters and charge every attached character. Do not infer
   update links from antonyms or copy Supermemory's unfiltered-by-latest history
   into a current-facts list.
2. **Event-time filters distinct from capture time.** Titan's
   [date extraction](../../app/retrieval_pipeline/retriever.py) currently feeds
   candidate date filters. Test an explicit occurrence-time interval separately
   from storage timestamp, with conservative ambiguity handling and a SQL-only
   path for date-only requests. Backfilled historical incidents should not be
   dated by when the agent happened to import them.
3. **Budgeted synthesis as a distinct opt-in operation.** If testing a summary
   of several evidence pointers, keep it separate from
   [ordinary ranking and the memory brief](../../app/retrieval_pipeline/brief.py).
   Require source IDs for every summary, expose its derived status, allow an
   honest missing-answer outcome, cap calls/tokens, and count its complete
   rendered context. Compare against unchanged Titan selection on the same
   frozen cases. Do not copy the synthetic-ID provenance loss, normalization
   collisions, or absence of an answerability check.

The earlier [scene-context experiment](scene-context-evaluation.md) used bounded
association scoring, not this generative aggregation pipeline. Its results do
not establish the behavior or quality of Supermemory's aggregation. No Titan
production code, default setting, or experiment parameter changed in this pass.
