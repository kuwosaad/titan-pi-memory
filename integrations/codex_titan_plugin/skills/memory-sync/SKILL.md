---
name: memory-sync
description: Use when the user wants to import or backfill durable memory from Claude Code, Codex, Aider, OpenCode, or other local agent session history into Titan.
---

# Memory Sync

Memory Sync imports useful local agent history into Titan as distilled trace packets. It is not a daemon, not a new database, and not a raw transcript copier.

## Hard Rules

Never modify source agent directories such as `~/.codex`, `~/.claude`, `~/.aider`, or OpenCode state directories. Never read auth files, token files, key files, `.env` files, cookies, private keys, or credential blobs. Never store raw transcripts in Titan. Always ask for user approval before the first import write in a session.

## Routing

Use this skill when the user says memory sync, import agent history, backfill Titan, Claude sync, Codex sync, cross-agent continuity, or asks to move memories from another coding agent into Titan.

## Workflow

1. Inventory candidate local history files read-only. Missing paths are normal.
2. Check existing import manifests when present: `~/.titan/imports/memory-sync/manifest.jsonl` and legacy `~/.titan/imports/claude-sync/manifest.jsonl`.
3. Summarize candidate agents, likely projects, date ranges, already-imported counts, and a recommended bounded scope.
4. Ask the user to approve the import scope before writing.
5. Read only the approved source files. Sample schemas first; if a schema is unknown, report it instead of guessing.
6. Distill durable facts, decisions, outcomes, and reusable lessons. Drop low-signal chatter, raw logs, secrets, and duplicate sessions.
7. Store each meaningful session or cluster with `store_trace_packet`. Include a source tag such as `[source:codex]` or `[source:claude-code]` in the goal or context.
8. Record imported/skipped fingerprints in the manifest if you create local import bookkeeping.
9. Verify retrieval with `query_memories` for representative imported topics.

## Safe Source Hints

Codex candidates may live under `~/.codex/history.jsonl`, `~/.codex/session_index.jsonl`, `~/.codex/sessions/`, or `~/.codex/memories/`.

Claude Code candidates may live under `~/.claude/history.jsonl`, `~/.claude/sessions/`, or `~/.claude/projects/`.

Do not inspect `~/.codex/auth.json`, `~/.claude.json`, key files, pem files, `.env` files, or credential-shaped files.

## Storage Shape

Prefer one trace packet per meaningful session or cluster. The packet should contain the import goal, distilled context, important decisions, outcomes, and source metadata. Do not create dozens of tiny memories from one transcript unless the user explicitly asks for a deep import.
