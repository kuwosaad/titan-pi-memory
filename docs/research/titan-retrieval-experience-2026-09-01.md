# Titan Retrieval Experience — 2026-09-01

## Context

Codex used Titan heavily to reconstruct work and ideas across Pi, Codex, and
Grok. Titan made cross-agent continuity possible, but turning the retrieved
memories into a precise answer required substantial manual cleanup and current
repository verification.

## What Worked

- Federated recall exposed useful context across Pi, Codex, and Grok.
- Chronological recall recovered 236 memories across 54 scenes for the day.
- `source_agent` provenance helped preserve ownership of foreign memories.
- Scene references provided a path back to source evidence.

## Problems Observed

- Broad semantic searches for today's completed work returned zero results even
  though hundreds of relevant recent memories existed.
- The same scene often produced several near-duplicate memories.
- Large retrieval responses were truncated.
- Ideas, plans, decisions, started work, completed work, and verified outcomes
  were mixed together. This caused an initial summary to blur proposed ideas
  with actual accomplishments.
- Exact-match retrieval was unreliable in at least one observed test.
- Concrete agent work reports were frequently marked `unverified` or given low
  reliability metadata.
- Legacy Kuwo/Karu identity labels created avoidable ambiguity.
- Codex had to manually deduplicate results, classify their status, and verify
  repository state before answering safely.

## Recommended Fix: Per-Scene Outcome Ledger

Give every scene one normalized lifecycle status:

`idea -> planned -> started -> completed -> verified -> superseded/deferred`

The canonical scene record should also carry:

- source agent;
- artifacts and changed files;
- verification evidence;
- remaining work;
- superseding scene or decision, when applicable;
- one canonical scene summary with underlying memories and evidence expandable.

Retrieval should return one deduplicated scene-level record by default instead
of several overlapping memory fragments. This would make questions such as
"What did we finish today?" answerable directly without throwing away Titan's
current strengths: broad memory coverage, provenance, scene expansion, and
federated cross-agent continuity.

## Assessment

Titan has crossed from an interesting archive into genuinely useful shared
memory. Its main weakness is no longer memory coverage; it is converting that
coverage into clean, trustworthy state.
