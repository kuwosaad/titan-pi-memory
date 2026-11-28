# Supermemory local vector and embedding path

Scope: static inspection of official local server `server-v0.0.9`; artifact
hashes, derivation limits and initial REA Evidence are in
[the investigation ledger](supermemory-server-investigation.md). All ranges here
are JavaScript character offsets in the exact derived inspection file, not
original binary addresses. No target code was run.

## Actual local search primitive

`sJ8` at `[2007002, 2011534)`, line 322, supplies the local vector service.
It uses the same database tables as the document and memory state. There is no
separate opaque graph-ranking engine in this inspected adapter.

Memory search:

1. Parse the namespace into organization and tag (`xz0`), resolve a matching
   space from both fields, and return no candidates if that space is absent.
2. Restrict rows to that space and non-null embeddings. By default require
   `isLatest`, not forgotten, and no expired `forgetAfter`.
3. Add caller metadata and optional creation-date filters.
4. Order by pgvector cosine distance; fetch the requested limit.
5. Convert distance to similarity using `1 - distance`, then apply the requested
   similarity threshold in JavaScript.

With forgotten memories included, the state predicate becomes latest OR
forgotten, rather than every historical version. Expiry filtering is disabled
in this mode. Date filters in this primitive use `createdAt`; they do not read
the fact's event date. Higher-level temporal retrieval has additional logic.

Chunk search:

1. Select documents in the organization whose container tags contain the tag,
   with requested document metadata and creation-date restrictions.
2. Select active chunks belonging to those document IDs with non-null vectors.
3. Order by cosine distance, limit, then apply similarity threshold.

Both result mappings set `filepath` to null in this adapter. Do not infer
filepath enforcement merely because a higher-level request accepts that field.
The memory branch visibly does not consume a `filepath` argument here; other
layers must be checked separately.

Cosine-distance SQL is established by helper `pu`, not inferred from the word
“vector.” Schema migration code declares HNSW indexes with `vector_cosine_ops`.
That declaration does not prove a particular query plan, latency or recall on a
running database. No BM25 calculation appears in this inspected local primitive.
The higher-level hybrid mode mixes memory and chunk candidates; see the separate
retrieval trace for its quotas and post-processing.

Embedding upserts update existing rows' vector and embedding-model columns.
`invalidateMemories` changes `isLatest` in those same rows. Some low-level write
helpers operate by IDs without rechecking organization; caller authorization is
therefore material. These observations do not establish an exploitable endpoint.

## Embedding production

`Gj` resolves provider, model, dimensions and optional endpoint. Default local
model is `Xenova/bge-base-en-v1.5`, 768 dimensions. Local, OpenAI,
OpenAI-compatible and Google providers have explicit configuration branches.
Configured dimensions above 2,000 are rejected in this build because of the
configured HNSW representation; this is a local implementation constraint, not
a universal statement about vector databases.

`of` at `[2296418, 2297157)`, line 363, builds and caches the chosen embedding
model. The local wrapper `bj8` at `[2294620, 2295583)` has these defaults:

- At most 32 inputs per SDK embedding call, with parallel calls supported.
- Worker batches of 8 (`qj8`), configurable.
- Truncate each input to 8,000 characters (`Wj8`), configurable; zero disables
  this wrapper's truncation. Tokenizer/model limits are a separate boundary.
- Mean pooling by default; the configured BGE-M3 case selects CLS pooling.
- Normalization enabled; verify every returned vector's dimension.

The central `cX` binding at `[2372764, 2372968)`, line 426, passes task type,
dimensions and configured primary/fallback models into `pN8`. The final `rS8`
normalizes vectors to unit length; a zero vector stays all-zero.

`uN8` at `[2370860, 2372110)` constructs the provider attempt list and timeout
policy. `xN8` at `[2372110, 2372460)` tries those providers in sequence with abort
signals and throws when all fail. Batch SDK timeout is 20 seconds per attempt;
interactive custom SDK timeout is 15 seconds. Stock Vertex/SDK paths have their
own shorter interactive values. Do not claim these are complete request-latency
caps: model retries, input batching, other work and optional fallbacks matter.

We did not benchmark this model against Titan's configured model. These settings
are not evidence that swapping Titan's embeddings would improve memory quality.

## What this means for Titan

The inspected local retrieval foundation is ordinary cosine search plus explicit
state and scope rules. Its useful extra behavior sits above that primitive:
fact/chunk representation, correction history, query handling and result assembly.
Titan already has lexical and semantic candidate lanes in
`app/retrieval_pipeline/retriever.py`, and request-local query-embedding reuse in
`app/embedding/embedder.py`. Replacing those with this adapter has no demonstrated
benefit. Test better saved facts and explicit version links before changing
embedding models or adding another ranking layer.

## Focused REA follow-up

A static Babel parse of the complete derived text produced 36,007 symbol/range
records with no parse errors. Nineteen selected pipeline functions/initializers
were sliced into `../supermemory-artifacts/focused-analysis/pipelines.js` for
focused REA inspection. No target initialization or function was executed.

Slice SHA-256:
`0070e4881048495a9406f10fbe3083b08fd655d3377326612edad23d07eaa0d1`.
The adjacent derivation manifest maps each slice back to the derived input.

Retained REA parent:
`ev_c5041a1db5d91f180f461e09c6250996f336babcd0f559fa1b2d5c0aaaed1735`.
Summary:
`ev_c742266cc00165289287a79e7469f030a8548bc5cbc80eb71de5a7419a6d1de8`.

REA parsed the slice without failures and visited 11,943 AST nodes. Graph coverage
remains partial: omitted dependencies, dynamic calls and unknown relationships
are explicit; unknown retention reached 3,000. This is supporting static
analysis of selected code, not proof of a complete application call graph.
The two focused Evidence records were exported to the sibling artifact directory
as `pipeline-rea-evidence.json`. This target-free analysis did not reopen or run
the already-closed native binary.
