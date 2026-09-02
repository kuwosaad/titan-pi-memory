---
name: memory-sync
description: Use when the user wants to import or backfill durable memory from Claude Code, Codex, Aider, OpenCode, or another local agent's session history into Titan.
---

# Memory Sync

Import useful local history as distilled trace packets. This is a bounded ETL
workflow, not a raw transcript copier or a background process.

## Hard boundaries

- Read source histories only after the user approves the bounded scope.
- Keep source agent directories unchanged.
- Never read authentication, token, key, cookie, private-key, credential, or
  environment-secret files.
- Never store raw transcripts, secrets, or full environment dumps in Titan.
- Write only to the active OpenCode Titan namespace.
- Tag every packet with its origin, such as `[source:claude-code]`, `[source:codex]`,
  or `[source:opencode]`.

## Workflow

1. **Inventory.** Read directory listings and small metadata samples only. Candidate
   locations include `~/.claude/history.jsonl`, `~/.claude/projects/`,
   `~/.codex/history.jsonl`, `~/.codex/sessions/`, and OpenCode's local session
   storage. Missing paths are normal. Inspect
   `~/.titan/imports/memory-sync/manifest.jsonl` and the legacy Claude import
   manifest when present.
2. **Summarize.** Report available source agents, likely projects, date ranges,
   schema families, prior-import counts, and a recommended bounded scope. Prefer the
   last 20 sessions per available agent, current project when detectable.
3. **Approve.** Ask for approval before the first write. If the user asks for a large
   import, propose batches or a dry run first.
4. **Normalize.** Sample each source format before parsing. If a schema is unknown,
   classify it and report it rather than guessing. Extract source agent, session ID,
   timestamp, project/cwd, and a content fingerprint.
5. **Filter and cluster.** Skip login-only, empty, duplicated, startup-only, and
   low-signal sessions. Group related sessions by project and task.
6. **Distill and redact.** Keep durable decisions, outcomes, constraints, corrections,
   and reusable lessons. Remove chatter, raw logs, credentials, and duplicates.
7. **Store.** For each meaningful cluster, call `titan-memory_store_trace_packet`
   with a concise goal, context, outcome, and source tag. Do not create dozens of
   tiny memories from one session.
8. **Manifest.** Record imported and skipped fingerprints in the memory-sync
   manifest when local bookkeeping is needed.
9. **Verify.** Call `titan-memory_query_memories` for representative imported topics
   and report what was found.

**Done when:** the approved scope is covered, every packet is distilled, redacted,
source-tagged, and deduplicated, and representative retrieval has been verified.
