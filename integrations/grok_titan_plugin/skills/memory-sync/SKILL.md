---
name: memory-sync
description: >
  Import durable memory from local agent session history into Grok's Titan
  namespace. Use when the user says memory sync, claude sync, codex sync,
  pi sync, wants Claude/Codex/Pi/Grok memories imported, wants to backfill
  Titan, or wants cross-agent continuity. Also use for /memory-sync.
argument-hint: "[last N|all|project-only|pi|codex|claude|grok|dry-run]"
---

# Memory Sync

Memory Sync is a safe local-agent import into **Grok Titan** (`~/.titan/agents/grok`). It reads other agents' session archives, distills durable facts, and stores them with `titan-memory__store_trace_packet`.

Not a daemon. Not a new database. Not a raw transcript copier. Source agents stay untouched.

Treat `claude-sync` as a legacy alias. Prefer Memory Sync.

## Hard Rules

- Never modify `~/.claude`, `~/.codex`, `~/.pi`, `~/.titan/agents/pi`, or source Grok session files except the Grok Titan import manifest.
- Never read auth files, token files, key files, `.env` files, or credential blobs.
- Never store raw transcripts in Titan.
- Never store API keys, OAuth tokens, cookies, passwords, private keys, or full environment dumps.
- Always inventory first.
- Always ask before the first import write in a session.
- Dry-run large imports before writing.
- Prefer distilled trace packets over verbatim logs.
- Record imported and skipped fingerprints in the manifest.
- Verify retrieval after import.
- Tag every stored finding with its source (`[source:pi]`, `[source:codex]`, `[source:claude-code]`, `[source:grok]`).

## Mental model

```txt
Inventory -> Normalize -> Filter -> Cluster -> Distill -> Redact -> Store -> Manifest -> Verify
```

Do not mechanically import every file.

## Sources (read-only)

Claude Code:

```txt
~/.claude/history.jsonl
~/.claude/sessions/
~/.claude/projects/
```

Codex:

```txt
~/.codex/history.jsonl
~/.codex/session_index.jsonl
~/.codex/sessions/
~/.codex/memories/
```

Pi (already Titan-shaped — prefer distilled notes, not raw sqlite):

```txt
~/.titan/agents/pi/out/memory_notes/learnings/
~/.titan/agents/pi/out/memory_notes/rough/
```

Grok session archives (other Grok chats, not this Titan store):

```txt
~/.grok/sessions/
~/.grok/memory/
```

Manifests:

```txt
~/.titan/imports/memory-sync/manifest.jsonl
~/.titan/imports/claude-sync/manifest.jsonl
```

Never open:

```txt
~/.codex/auth.json
~/.claude.json
~/.grok/auth.json
**/*.key
**/*.pem
**/.env
**/.env.*
```

## Slash command

`/memory-sync` arguments are import preferences:

```txt
/memory-sync
/memory-sync last 20
/memory-sync pi last 50
/memory-sync codex last 50
/memory-sync claude project-only
/memory-sync all dry-run
/memory-sync since 2026-05-01
```

No args → inventory, then recommend: last 20 sessions per available agent, current project when cwd metadata exists. Do not import all unless the user approves after seeing inventory.

## Workflow

### 1. Inventory

```bash
printf 'Claude Code candidates:\n'
find "$HOME/.claude" -maxdepth 6 -type f \
  \( -name '*.jsonl' -o -name '*.json' \) \
  ! -name 'auth.json' ! -name '*.key' ! -name '*.pem' 2>/dev/null | sort | head -200

printf '\nCodex candidates:\n'
find "$HOME/.codex" -maxdepth 8 -type f \
  \( -name '*.jsonl' -o -name '*.md' \) \
  ! -name 'auth.json' ! -name '*.key' ! -name '*.pem' 2>/dev/null | sort | head -300

printf '\nPi Titan notes:\n'
find "$HOME/.titan/agents/pi/out/memory_notes" -type f -name '*.md' 2>/dev/null | wc -l

printf '\nGrok sessions:\n'
find "$HOME/.grok/sessions" -maxdepth 4 -type f -name 'chat_history.jsonl' 2>/dev/null | head -100

test -f "$HOME/.titan/imports/memory-sync/manifest.jsonl" && tail -50 "$HOME/.titan/imports/memory-sync/manifest.jsonl"
```

Summarize discovered files, date range, projects, already-imported counts, source quality, and a recommended scope.

### 2. Normalize

Agent history formats drift. Sample a few files from each family before parsing. Unknown schema → `unknown_schema`, do not guess.

### 3. Ask approval

Unless the user already gave a bounded scope:

```txt
1. Last 20 sessions per agent, current project when detectable. Recommended.
2. Last 50 sessions per agent.
3. Pi Titan notes only.
4. Codex only.
5. Claude Code only.
6. Dry-run all, then ask again.
```

### 4–6. Read, filter, cluster

Extract metadata first (source, session id, ts, cwd, title, fingerprint, schema, signal). Do not dump full transcripts into the chat.

Skip as `skipped_low_signal`: `/exit`, login-only, empty turns, shell setup with no decision, duplicated context blocks.

Cluster related sessions by task. One packet per cluster, not one memory per file.

### 7. Extract durable memory

Save: decisions, architecture, outcomes, root causes, recurring pitfalls, user preferences, standing instructions, unresolved todos, repo context.

Skip: one-off shell, raw stacks unless the cause matters, secrets, huge pastes, chit-chat, things already in Titan.

Good: `Root cause was store-path drift: MCP inherited stale TITAN_BASE_DIR.`
Bad: `The user ran a command and saw a stack trace.`

### 8. Redact

Strip `sk-`, `ghp_`, bearer tokens, `API_KEY=`, passwords, private keys. Credential-heavy session → skip.

### 9. Source-tag every finding

Prefix each line in `thoughts` so extraction keeps the origin:

```txt
[source:pi] Titan Pi extension writes spool events to ~/.titan/agents/pi/traces.
[source:codex] Root cause of Titan-Mem issue was store-path drift.
[source:claude-code] Kuwo prefers direct instructions when told to commit and push.
[source:grok] Titan Grok plugin lives at integrations/grok_titan_plugin.
```

Without tags, imported facts share this Grok session ID and drown in process noise. That already happened on a 2026-05-30 Codex import.

### 10. Store in Grok Titan

```
use_tool titan-memory__store_trace_packet
```

One packet per session or cluster:

```json
{
  "goal": "Import Pi memory cluster into Grok Titan (source:pi): <cluster>",
  "thoughts": "[source:pi] Decision: ... [source:pi] Preference: ...",
  "outcome": "Stored source-tagged memories from Pi into Grok Titan. Query with '[source:pi]'."
}
```

If MCP/Titan is down, stop. Do not fake the import.

### 11. Manifest

After a successful store (or a known skip), append to `~/.titan/imports/memory-sync/manifest.jsonl`:

```json
{
  "imported_at": "2026-08-13T00:00:00Z",
  "source_agent": "pi",
  "session_id": "...",
  "session_file": "~/.titan/agents/pi/out/memory_notes/learnings/....md",
  "fingerprint": "sha256:...",
  "titan_method": "store_trace_packet",
  "status": "imported_cluster",
  "cluster": "titan-grok-attach"
}
```

Statuses: `imported_cluster`, `imported_single`, `skipped_low_signal`, `skipped_duplicate`, `skipped_secret_heavy`, `skipped_unknown_schema`, `failed_titan_store`.

Duplicate if session id or fingerprint already exists in the primary or legacy Claude Sync manifest.

### 12. Verify

Query with the source tag:

```txt
[source:pi] Titan Pi extension spool
[source:codex] store-path drift
```

Also try a plain semantic query. Success means stored **and** retrievable.

### 13. Report

Imported / skipped counts, agents, projects, clusters, source quality, redactions, manifest path, verification queries, next recommended batch.

## Defaults

- Small session → one packet
- Many related sessions → one cluster packet
- All-history → batches of 10, ask before continuing if noisy
- Pi notes are already distilled — cluster by topic, do not re-copy every markdown file as its own packet
