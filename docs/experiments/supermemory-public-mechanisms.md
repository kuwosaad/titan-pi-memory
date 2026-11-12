# Supermemory: useful public mechanisms and the verification boundary

Investigated 11 November 2026. This is source inspection, not a Supermemory quality
benchmark or recovery of its private algorithm. No credentials, memory uploads,
server installation, or Titan runtime changes were used for this investigation.

This page covers the public repository and docs only. A subsequent
[shipped-server investigation](supermemory-server-investigation.md) recovered
inspectable local-server JavaScript and led to a tested Titan feedback-boundary
fix. Its static findings go beyond the public-source boundary described here.

Pinned public sources:

- Supermemory monorepo: [`8af3b9108f1a4dce45b10cfa4f8f0af027b4197b`](https://github.com/supermemoryai/supermemory/tree/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b).
- MemoryBench: [`063b86bfb347336def3a084153f40bfea1e2dfd6`](https://github.com/supermemoryai/memorybench/tree/063b86bfb347336def3a084153f40bfea1e2dfd6).

## What is actually visible

| Finding | Evidence status | Meaning for Titan |
| --- | --- | --- |
| SDK-owned retrieved context is replaceable and excluded from capture. | **Verified source**, specific adapters below. | Read evidence must not become new user evidence merely because it appeared in a prompt. |
| Static/dynamic profile and query-ranked memories are separate reads. | **Verified SDK source**; profile construction is private. | Preferences can be present without pretending they answer the current factual question. |
| Conversation saves pass a stable caller-provided document ID. | **Verified SDK call**; server upsert behavior is a documented contract. | Stable event/document identity differs from merging independently sourced identical claims. |
| Atomic memories resolve references and retain original source chunks. | **Documented private pipeline**, not recovered implementation. | Improve the indexed fact's meaning at capture, rather than add nearby unrelated text during retrieval. |
| Updates, extensions, and inferences are distinct relations; latest state can retain history. | **Public schema and documentation**; relation classifier and scope safeguards remain unknown. | Opposite words across production and lab are not sufficient evidence of supersession. |
| Search can return memories, chunks, or both; optional reranking and aggregation exist. | **Documented API**, with SDK calls visible; underlying ranking is private. | “Hybrid” here means memory plus document results; it does not establish a specific BM25/vector fusion algorithm. |

The official self-hosting page explicitly says the downloadable server comes from
[a separate, non-public codebase](https://supermemory.ai/docs/self-hosting/overview).
Open SDKs, UI, schemas, and plugins therefore do not expose the engine's extraction
prompts, learned models, relation decisions, retrieval traversal, or ranking
weights. Its hosted proprietary extraction model may also differ from a local
bring-your-own-model installation. A repository read cannot establish those
models' quality.

## Exact read-to-write separation in the open SDK

The shared helper owns a specific read-only memory block, escapes nested delimiter
tags, removes earlier owned blocks, and inserts the replacement:
[`memory-context.ts`, lines 1–40](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/shared/memory-context.ts#L1-L40).

For OpenAI Chat, capture converts `updateChatMemoryContexts(messages)` into the
saved conversation **before** adding fresh retrieval context. Calling the helper
without a replacement strips the owned blocks from system/developer messages:
[capture call](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/openai/middleware.ts#L916-L930),
[strip behavior](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/openai/middleware.ts#L344-L393).
The Vercel adapter instead skips system messages when converting the conversation
for saving:
[converter](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/vercel/middleware.ts#L65-L79),
[save path](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/vercel/middleware.ts#L151-L175).
These are adapter-specific guarantees, not proof that arbitrary copied text or
all third-party integrations are protected.

The conversation client sends the caller's ID and formatted conversation to
`add`, with `dreaming: instant`:
[`conversations-client.ts`](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/conversations-client.ts#L119-L139).
The shared read client separately requests profile and memory search, using a
0.6 search threshold in this integration:
[`memory-client.ts`](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/tools/src/shared/memory-client.ts#L27-L74).
That threshold is not a portable relevance rule: the current v5 server docs list
[hybrid search, threshold 0.3, and no reranking as defaults](https://supermemory.ai/docs/recall/search.md).

## Meaningful context and updates: the documented design

Their research description uses semantic session blocks, resolves references
while creating atomic facts, keeps the source chunk, and distinguishes source
creation time from the event's time. It ingests sessions rather than isolated
message pairs. These are specific architectural ideas, not evidence that our
failed packet-embedding bonus recreated their method:
[research architecture](https://supermemory.ai/research/longmembench/).

The docs distinguish an `updates` relation, which replaces current state, from
`extends`, which adds compatible detail, and `derives`, which marks inference:
[pinned graph-memory description](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/apps/docs/concepts/graph-memory.mdx#L56-L100).
Public types expose version, latest, parent/root, inference, and source-count
fields, but not the decision procedure:
[pinned schema](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/packages/validation/schemas.ts#L238-L279).

“Dynamic dreaming” groups related documents into coherent units; document indexing
can finish before fact extraction finishes. The fact-grouping implementation,
window sizes, and exact namespace/scope checks are not public:
[pinned pipeline documentation](https://github.com/supermemoryai/supermemory/blob/8af3b9108f1a4dce45b10cfa4f8f0af027b4197b/apps/docs/concepts/how-it-works.mdx#L115-L144).

## Evaluation claims: what can be reproduced from the code

The vendor research page reports 97% Recall@20 **with aggregation**. Its
[October explanation](https://supermemory.ai/blog/how-ai-memory-works/) distinguishes
that configuration from default MemoryBench. This investigation did not reproduce
the headline or inspect the private search service.

Concrete MemoryBench behavior at the pinned commit:

- The search phase requests the benchmark's result limit, defaulting to 10, and
  threshold 0.3. Provider settings can override this. The adapter sends public API
  requests; it does not contain the memory engine:
  [search phase](https://github.com/supermemoryai/memorybench/blob/063b86bfb347336def3a084153f40bfea1e2dfd6/src/orchestrator/phases/search.ts#L56-L63),
  [Supermemory adapter](https://github.com/supermemoryai/memorybench/blob/063b86bfb347336def3a084153f40bfea1e2dfd6/src/providers/supermemory/index.ts#L195-L214).
- LoCoMo requests 30 results and excludes adversarial questions from its comparable
  headline while retaining per-type scoring:
  [benchmark protocol](https://github.com/supermemoryai/memorybench/blob/063b86bfb347336def3a084153f40bfea1e2dfd6/src/benchmarks/README.md#L47-L67).
- Its retrieval relevance judge sees the question, gold answer, and results.
  `recallAtK` is binary: one if any returned result is judged relevant, otherwise
  zero. It therefore does not measure retention of every required fact in a
  multi-fact answer:
  [judge and metric implementation](https://github.com/supermemoryai/memorybench/blob/063b86bfb347336def3a084153f40bfea1e2dfd6/src/orchestrator/phases/retrieval-eval.ts#L16-L139).
- Context tokens are counted from the formatted answering prompt minus the empty
  context prompt, a useful way to charge actual consumed context:
  [answer phase](https://github.com/supermemoryai/memorybench/blob/063b86bfb347336def3a084153f40bfea1e2dfd6/src/orchestrator/phases/answer.ts#L131-L154).

These are metric/configuration limits, not evidence of improper evaluation.
Titan should continue reporting complete required evidence, irrelevant content,
abstention, source lineage, and actual rendered budget separately.

## Smallest useful Titan adaptation to test

Several advertised concepts already exist in Titan: retained source scenes/event
IDs, atomic extraction instructions, explicit subject/scope anchors, causality,
state distinctions, and exact-evidence retry identity. The current
[compiler prompt](../../app/save_pipeline/extraction/prompts.py) already asks for
concrete subjects, scope, useful causal detail, direct wording instead of
pronouns, and explicit superseding updates. Adding the same instruction again
would not establish an improvement.

First test **raw scene → current compiler → unchanged public retrieval**, rather
than insert deliberately fragmented memory rows. For example, the scene's
“Kestrel adopted bounded retries” plus “It is limited to two seconds” should
produce a self-contained Kestrel invoice-export fact with both original event
references. The prior retrieval-only experiment showed that enriching context
can rescue this information, but did not establish that today's compiler emits
those fragments.

If that end-to-end test fails, change the smallest input/encoding seam that loses
the subject or scope, and compare against the existing compiler. Require improved
complete evidence with no wrong-project context, unsupported causal claim,
production/lab false update, lost lineage, or duplicate save. Test explicit scoped
supersession separately; do not infer replacement from similarity plus antonyms.
Retain the read-block exclusion boundary regardless of any ranking experiment.
