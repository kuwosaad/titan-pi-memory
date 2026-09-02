---
name: titan-patterns-workflow
description: Use when mining, reviewing, importing, exporting, creating, accepting, or rejecting Titan learned-behavior patterns from memory evidence.
---

# Titan Patterns Workflow

Patterns are reusable behaviors backed by evidence. Every mined pattern is a
candidate until the user explicitly accepts or rejects it.

## Review existing patterns

1. Call `titan-memory_patterns_status` to inspect mining state.
2. Call `titan-memory_patterns_list` for candidate, accepted, rejected, or scoped
   views.
3. Call `titan-memory_pattern_get` before discussing a specific pattern so its
   evidence is visible.
4. Call `titan-memory_pattern_accept` or `titan-memory_pattern_reject` only when the
   user explicitly requests that decision.

## Mine or backfill

1. Call `titan-memory_patterns_evidence_packet` with a bounded batch size.
2. Inspect returned memory IDs, scene IDs, and context. Expand ambiguous evidence
   with `titan-memory_get_scene_context`, passing `source_agent` for foreign scenes.
3. Create only evidence-backed candidates with `titan-memory_pattern_create`.
4. Include evidence memory IDs and scene IDs in `evidence_json`; do not create a
   pattern from a plausible story alone.
5. Call `titan-memory_patterns_mark_processed` after inspecting each evidence packet,
   including packets that yield no useful candidate.

## Import and export

Use `titan-memory_patterns_export_bundle` for shareable bundles. Export accepted
patterns by default; include candidates only for an explicit review handoff. Use
`titan-memory_patterns_import_bundle` only with a user-provided local bundle path and
never overwrite existing patterns without explicit overwrite instruction.

Never turn memories into psychological claims. Prefer concrete workflow observations,
repository conventions, and repeated implementation lessons. If evidence is thin,
create no pattern and say what evidence is missing.

**Done when:** every created or changed pattern has inspectable evidence, its status
change was explicitly authorized, and processed evidence is recorded.
