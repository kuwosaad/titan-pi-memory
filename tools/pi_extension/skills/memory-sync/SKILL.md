---
name: memory-sync
description: Import durable memory from local agent session history into Titan. Use when the user says memory sync, claude sync, codex sync, wants Claude Code/Codex memories imported, wants to backfill Titan from agent sessions, or wants cross-agent continuity through Titan.
---

# Memory Sync

Memory Sync is a safe local-agent memory import workflow. It reads local session history from coding agents such as Claude Code and Codex, extracts durable memories, and stores distilled summaries in Titan using trace packets.

This skill is not a daemon, not a new database, and not a direct file-level sync engine. Titan is the shared memory layer. Source agents remain untouched.

Historical note: this workflow was originally called Claude Sync. Treat `claude-sync` as a legacy alias. Prefer the name Memory Sync going forward.

## Hard Rules

- Never modify files under `~/.claude`.
- Never modify files under `~/.codex`.
- Never read auth files, token files, key files, `.env` files, or credential blobs.
- Never store raw transcripts in Titan.
- Never store API keys, OAuth tokens, cookies, passwords, private keys, or full environment dumps.
- Always inventory first before importing.
- Always ask for user approval before the first import write in a session.
- Always use a dry-run summary for large imports before writing memories.
- Always prefer distilled trace packets over verbatim session logs.
- Always record imported and skipped session fingerprints in a manifest so repeated runs skip duplicates.
- Always verify retrieval after import by querying Titan for representative imported topics.

## Core Mental Model

Treat Memory Sync as an ETL pipeline:

```txt
Inventory -> Normalize -> Filter -> Cluster -> Distill -> Redact -> Store -> Manifest -> Verify
```

Do not mechanically import every file. The job is to preserve useful durable knowledge while avoiding transcript sludge, secrets, duplicates, and low-signal startup noise.

## Source Locations

Inspect these paths if they exist. Missing paths are normal and should not be treated as errors.

Claude Code paths:

```txt
~/.claude/history.jsonl
~/.claude/sessions/
~/.claude/projects/
~/.claude/skills/
```

Codex paths:

```txt
~/.codex/history.jsonl
~/.codex/session_index.jsonl
~/.codex/sessions/
~/.codex/memories/
~/.codex/AGENTS.md
```

Primary Memory Sync manifest path:

```txt
~/.titan/imports/memory-sync/manifest.jsonl
```

Legacy Claude Sync manifest path to inspect for duplicate prevention:

```txt
~/.titan/imports/claude-sync/manifest.jsonl
```

Never read these paths except to note that they exist if needed:

```txt
~/.codex/auth.json
~/.claude.json
~/.claude/**/*.key
~/.claude/**/*.pem
~/.codex/**/*.key
~/.codex/**/*.pem
**/.env
**/.env.*
```

## Slash Command Behavior

When invoked through `/memory-sync`, treat the user's arguments as import preferences.

Legacy `/claude-sync` prompts may still appear in old installs. If invoked, run this Memory Sync workflow and recommend `/memory-sync` going forward.

Examples:

```txt
/memory-sync
/memory-sync last 20
/memory-sync codex last 50
/memory-sync claude project-only
/memory-sync all dry-run
/memory-sync since 2026-05-01
```

If no arguments are provided, run inventory and recommend a safe default.

Default recommendation:

```txt
Import the last 20 sessions per available agent, scoped to the current project when session cwd/project metadata is available.
```

Do not import all sessions unless the user explicitly approves it after seeing the inventory.

## Workflow

### 1. Inventory

First collect a read-only inventory.

Use shell commands like these, adapted to the user's machine:

```bash
printf 'Claude Code candidates:\n'
find "$HOME/.claude" -maxdepth 6 -type f \
  \( -name '*.jsonl' -o -name '*.json' -o -name 'history.jsonl' \) \
  ! -name 'auth.json' ! -name '*.key' ! -name '*.pem' 2>/dev/null | sort | head -200

printf '\nCodex candidates:\n'
find "$HOME/.codex" -maxdepth 8 -type f \
  \( -name '*.jsonl' -o -name '*.md' \) \
  ! -name 'auth.json' ! -name '*.key' ! -name '*.pem' 2>/dev/null | sort | head -300
```

Also inspect both manifests if present:

```bash
test -f "$HOME/.titan/imports/memory-sync/manifest.jsonl" && tail -50 "$HOME/.titan/imports/memory-sync/manifest.jsonl"
test -f "$HOME/.titan/imports/claude-sync/manifest.jsonl" && tail -50 "$HOME/.titan/imports/claude-sync/manifest.jsonl"
```

Summarize:

- discovered Claude Code files
- discovered Codex files
- date range if inferable from filenames or metadata
- likely projects/cwds
- already-imported count from primary manifest
- already-imported count from legacy manifest
- source quality for each agent
- recommended import scope

### 2. Normalize Session Formats

Agent history formats drift over time. Never assume a single JSONL schema.

Codex may use newer records like:

```json
{"type":"session_meta","payload":{"id":"...","cwd":"..."}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[...]}}
```

Codex may also use older records like:

```json
{"type":"message","role":"user","content":[{"type":"input_text","text":"..."}]}
{"type":"function_call","name":"shell","arguments":"..."}
```

Claude Code storage can vary by version. Common useful locations are:

- `~/.claude/history.jsonl` for prompt history and session ids
- `~/.claude/sessions/*.json` for session metadata
- `~/.claude/projects/**/*.jsonl` when present for project transcripts

Before import, inspect a small sample from each discovered source family and adapt parsing. If a schema is unknown, classify it as `unknown_schema` and report it rather than guessing.

### 3. Ask Approval

Ask the user what scope to import unless they already gave a clear, bounded scope.

Good choices:

```txt
1. Last 20 sessions per agent, current project only when detectable. Recommended.
2. Last 50 sessions per agent.
3. Codex only.
4. Claude Code only.
5. Dry-run all sessions and ask again before importing.
```

If the user asks for all sessions and there are many, warn that the import may be slow and propose batching.

### 4. Read Selected Sessions

Read only selected files. For each session, extract metadata first:

- source agent: `claude-code` or `codex`
- session id
- timestamp/date
- cwd/project path
- thread/title if available
- model/provider if available
- file path
- content fingerprint
- schema family
- signal quality

Do not bulk-load all raw transcript text into the final response. Read enough to extract durable memory safely.

### 5. Filter Low-Signal Sessions

Not every session deserves memory.

Classify as `skipped_low_signal` when the session contains only:

- `/exit`
- login/model commands only
- startup/environment context only
- empty assistant messages
- shell setup with no durable decision
- duplicated context blocks without user intent

Do not store standalone Titan memories for low-signal sessions. Still write a manifest record saying they were reviewed and skipped.

### 6. Cluster Before Store

Cluster related sessions by task/project before storing.

Prefer clusters like:

```txt
titan-mem-ingestion-debugging
openclaw-persona-loading
pipx-packaging-cleanup
backend-redesign
beginner-python-learning
low-signal-reviewed
```

Use one trace packet per meaningful cluster instead of one memory per session when sessions are tightly related. This reduces noise and improves retrieval.

### 7. Extract Durable Memory

For each selected session or cluster, extract only durable information.

Save these categories:

- project decisions
- architecture choices
- implementation outcomes
- debugging root causes
- recurring bugs or pitfalls
- user preferences
- standing workflow instructions
- important unresolved todos
- repo-specific context that future agents need
- source-quality observations about the agent history itself

Skip these categories:

- one-off shell output with no future value
- raw stack traces unless the root cause matters
- private credentials or secrets
- auth/config contents
- huge pasted files
- transient chit-chat
- repeated environment blocks
- messages that are already clearly in Titan

Good memory shape:

```txt
Root cause was store-path drift: MCP inherited stale TITAN_BASE_DIR and read the wrong store.
```

Bad memory shape:

```txt
The user ran a command and saw a long stack trace.
```

Good memory shape:

```txt
OpenClaw does not auto-inject arbitrary karu.md; put persona in SOUL.md and identity in IDENTITY.md.
```

Bad memory shape:

```txt
The user asked about OpenClaw.
```

### 8. Redact Before Storing

Before calling Titan, redact suspicious content.

Patterns to remove or summarize:

```txt
sk-...
ghp_...
gho_...
ghu_...
ghs_...
ghr_...
xoxb-...
BEGIN RSA PRIVATE KEY
BEGIN OPENSSH PRIVATE KEY
Authorization: Bearer ...
api_key=...
API_KEY=...
password=...
secret=...
token=...
~/.ssh/*
```

If a session appears credential-heavy, skip it and report that it was skipped for safety.

### 9. Store In Titan

In Pi, use the native tool:

```txt
titan_store_trace_packet
```

Use one trace packet per meaningful session or per cluster of tightly related sessions. Do not create dozens of tiny memories from the same session unless the user explicitly wants a deep import.

Trace packet format:

```json
{
  "goal": "Import Codex session cluster into Titan: <cluster name>",
  "thoughts": "Sources: ... Session ids: ... Projects: ... Durable findings: ... Decisions: ... User preferences: ... Redactions: none/secrets skipped.",
  "outcome": "Stored distilled memory from this cluster so future agents can recall project context without reading raw transcripts."
}
```

For Claude Code:

```json
{
  "goal": "Import Claude Code history into Titan",
  "thoughts": "Source: ~/.claude/... Session ids: ... Project: ... Durable findings: ... Source quality: rich/sparse/metadata-only.",
  "outcome": "Backfilled Titan with Claude Code context while leaving Claude files unchanged."
}
```

If Titan tools are unavailable, stop and tell the user to install/start the Titan Pi package. Do not fake a memory import.

### 10. Write Manifest

After a successful Titan write, append one manifest record per processed source session.

Primary path:

```txt
~/.titan/imports/memory-sync/manifest.jsonl
```

Record shape:

```json
{
  "imported_at": "2026-05-30T00:00:00Z",
  "source_agent": "codex",
  "session_id": "019c75ed-47d4-70a1-870b-eda732caadde",
  "session_file": "~/.codex/sessions/2026/02/19/rollout-...jsonl",
  "fingerprint": "sha256:...",
  "project": "/Users/mohammadsaad/Desktop/Code/Titan-Mem...",
  "thread_name": "Explain titan-go duplicate events",
  "timestamp": "2026-02-19T12:43:30.644Z",
  "schema_family": "codex-new-jsonl",
  "signal_quality": "high",
  "titan_method": "titan_store_trace_packet",
  "status": "imported_cluster",
  "cluster": "titan-mem-ingestion-debugging",
  "notes": "Stored as part of a distilled Memory Sync cluster trace packet."
}
```

Supported status values:

```txt
imported_cluster
imported_single
skipped_low_signal
skipped_duplicate
skipped_secret_heavy
skipped_unknown_schema
failed_titan_store
```

Only write `imported_*` manifest records after the Titan store call succeeds. For skipped records, write after the reason is known.

### 11. Verify Retrieval

After import, query Titan for representative topics from the imported clusters.

Examples:

```txt
Titan-Mem store-path drift
OpenClaw SOUL.md IDENTITY.md
pipx-only packaging Titan-Mem
Codex imported Claude Code sparse metadata
```

Report whether imported memories appear in retrieval. If storage succeeded but retrieval fails, say so and recommend re-indexing/restarting Titan if applicable.

### 12. Final Report

Report concisely:

- imported sessions count
- skipped sessions count
- source agents covered
- projects covered
- clusters created
- source quality notes
- redactions/skips
- manifest path
- verification queries and whether they retrieved results
- next recommended import if any

## Import Granularity

Use this default batching:

- Small session: one trace packet.
- Long session: one trace packet with structured summary.
- Many related sessions for the same task: one cluster trace packet.
- Large all-history import: batch by 10 sessions and ask before continuing after each batch if import looks noisy.

## Duplicate Detection

A session is already imported if either:

- its session id exists in the primary Memory Sync manifest for the same source agent
- its file fingerprint exists in the primary Memory Sync manifest
- its session id exists in the legacy Claude Sync manifest for the same source agent
- its file fingerprint exists in the legacy Claude Sync manifest

If no manifest exists, use Titan query as a weak secondary check by searching for the session id or exact project/task name. Still create the primary Memory Sync manifest after import.

## Source Quality Report

Always report source quality. Example:

```txt
Codex: rich transcripts, multiple project clusters, high import value.
Claude Code: sparse metadata only on this machine, no rich ~/.claude/projects transcripts found.
```

This prevents pretending that every agent source has equal memory value.

## Current-Project Scoping

When the user asks for `project-only`, include sessions where:

- session cwd equals current cwd
- session cwd is an ancestor/descendant of current cwd
- session project field matches current repo path
- history record project field matches current repo path

If cwd/project metadata is missing, classify the session as `unknown-project` and ask before importing it in project-only mode.

## Process Learnings To Preserve

The Memory Sync workflow itself can produce important reusable knowledge. If an import reveals better parsing, filtering, clustering, or verification rules, store those learnings in Titan and update this skill when the user asks.

Patterns worth preserving:

- Schema drift is normal across agent versions.
- Source quality is asymmetric; one agent may have rich transcripts while another has only metadata.
- Low-signal filtering is necessary to keep Titan useful.
- Clustered memories are more useful than one trace packet per file.
- A manifest should track imported and skipped sessions, not just successes.
- Import success means stored and retrievable, not merely that `titan_store_trace_packet` returned successfully.
- Raw transcript import is almost always the wrong default.

## Mental Model

This workflow does not make agents share hidden model memory. It makes Titan remember the useful parts of prior agent sessions by reading local session archives once and converting them into durable trace packets.

The safe path is:

```txt
Agent session files, read-only
  -> schema-aware parsing
  -> low-signal filtering
  -> task clustering
  -> redacted trace packets
  -> Titan memory
  -> manifest for dedupe
  -> retrieval verification
```
