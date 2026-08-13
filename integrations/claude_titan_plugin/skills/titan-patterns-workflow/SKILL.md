---
name: titan-patterns-workflow
description: Use when mining, reviewing, creating, accepting, or rejecting Titan behavioral patterns from memory evidence.
---

# Titan Patterns Workflow

Patterns are reusable behaviors backed by memory evidence. Treat them as candidates until the user explicitly asks to accept or reject them.

## Workflow

1. Use `patterns_status` to understand mining state before changing anything.
2. Use `patterns_evidence_packet` to inspect candidate evidence, then expand important scene IDs with `get_scene_context` when needed.
3. Create only candidate patterns with `pattern_create` unless the user explicitly asks for acceptance or rejection.
4. Every candidate must include evidence memory IDs and scene IDs. Do not create patterns from vibes.
5. Use `patterns_list` and `pattern_get` before reviewing existing patterns.
6. Use `pattern_accept` or `pattern_reject` only for explicit review decisions.
7. Use `patterns_mark_processed` after evidence has been inspected so future mining skips it.

## Safety Rules

Do not turn memories into psychological claims. Prefer specific workflow observations, project conventions, and repeated implementation lessons. If evidence is thin, create no pattern and explain what evidence would be needed.
