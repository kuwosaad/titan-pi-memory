---
description: Import local agent session memories into Grok Titan
argument-hint: "[last N|all|project-only|pi|codex|claude|grok|dry-run]"
---
Use the `memory-sync` skill to import useful memory from local agent session history into Grok Titan (`~/.titan/agents/grok`).

User arguments: `$ARGUMENTS`

Follow `skills/memory-sync/SKILL.md` exactly:

- inventory Pi notes, Claude Code, Codex, and Grok session files first
- inspect Memory Sync and legacy Claude Sync manifests for duplicates
- never modify source agent directories
- never read auth/token/secret files
- ask before the first Titan write unless the user already approved a bounded scope
- distill and redact; store with `titan-memory__store_trace_packet`
- source-tag every finding
- verify with `titan-memory__query_memories`
