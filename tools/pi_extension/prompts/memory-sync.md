---
description: Import local agent session memories into Titan safely
argument-hint: "[last N|all|project-only|codex|claude|dry-run]"
---
Use the `memory-sync` skill to import useful memory from local agent session history into Titan.

User arguments: `$ARGUMENTS`

Follow the skill exactly:

- inventory local Claude Code and Codex session files first
- inspect both the new Memory Sync manifest and the legacy Claude Sync manifest for duplicates
- never modify `~/.claude` or `~/.codex`
- never read auth/token/secret files
- ask before writing the first Titan import unless the user already approved a clear bounded scope
- normalize schema differences before parsing sessions
- filter low-signal sessions instead of importing all files mechanically
- cluster related sessions by task before storing
- store only distilled, redacted trace packets with `titan_store_trace_packet`
- write/update the Memory Sync import manifest only after successful Titan writes or explicit skip decisions
- verify retrieval after import by querying Titan for representative imported topics
