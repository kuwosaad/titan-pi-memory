# Three memory features: implementation plan

Status: implemented and verified on reinventing-titan; see memory-features-results.md. User authorizes implementation
and local validation. Original checkout and live memories are outside test scope.

## Observable finish line

The public save/query paths can retain source-backed correction history, retrieve
backfilled facts by occurrence date, and find a source passage even when extraction
missed its detail. Both storage adapters preserve evidence and old records. Tests
demonstrate each behavior and reject false replacements, guessed dates, scope
leaks, and output-budget overflow. No additional autonomous writer loop is needed.

## Evidence and strategy

The pinned shipped Supermemory local server has already been statically traced in
supermemory-save-internals.md and supermemory-retrieval-internals.md, with original
artifact hashes and exported REA evidence. Reuse that evidence; inspect narrower
code only when an implementation decision remains unresolved. Its hosted quality
is unknown. Independently implement the useful contracts, preserving Titan's
stronger scene/event provenance and exact-evidence retry identity.

The bottleneck is truthful integration: every new link/date/passage must survive
the real save, storage, federation, selection and rendering paths. Component-only
tests are insufficient. Build those seams before broad performance claims.

## Ordered work

1. Snapshot current code and preserve all existing experimental work. Define
   narrow shared contracts. Astra reviews architecture and final implementation.
2. Parallel Terra lanes own disjoint components: correction storage/resolution;
   occurrence-time normalization/query parsing; independent bounded scene search;
   additive storage fields; public retrieval/federation/rendering integration.
   Root owns extractor/prompt/save integration and combined verification.
3. Corrections: expose at most three plausible existing records as comparison
   context only for explicit correction language. Extract a proposed old-ID link,
   current-source quote and explicit subject/scope/attribute. Validate against
   actual persisted records; ambiguous or stale proposals keep both. Persist
   additive links with idempotent operations; current queries filter retired
   facts, history queries preserve and label them.
4. Time: retain capture ts; store optional normalized occurrence intervals.
   Accept explicit grounded dates; relative dates require source-time anchoring.
   Event-time queries use half-open intervals and exclude unknown occurrences;
   never apply capture-date filtering to the same occurrence request.
5. Source lane: query bounded original scene messages independently, preserve
   source/scene/event identity and evidence status, exclude recalled tool results,
   label source passages clearly. Merge under real query and rendered budgets.
6. Verify meaningful focused tests first, then all Python tests because storage,
   public retrieval and multiple adapters change. Run fresh scenarios through
   actual writer and public query with controlled extraction/embeddings. Compare
   complete evidence, false replacements, wrong-date results, duplicates, and
   budget. Preserve the known baseline packaging failures as an explicit limit.
7. Resolve failures and independent Astra review. Record actual outcomes,
   remaining uncertainty and operational limits; no commit/push/deploy implied.

## Update rules and fallback

Unsupported correction: retain both with no link. Unresolvable date: unknown,
never ingestion-time substitution. Empty/irrelevant source lane: return ordinary
memory results. If a component adds irrelevant evidence or breaks contracts,
repair/narrow its admission rule and rerun the failing scenario before enabling.
The terminal goal remains trustworthy useful recall; feature presence alone is
not success. Stop only after all lanes are accounted for and integrated checks
and source/budget audits finish.
