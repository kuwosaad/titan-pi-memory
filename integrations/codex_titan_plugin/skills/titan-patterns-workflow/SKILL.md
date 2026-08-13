---
name: titan-patterns-workflow
description: Use when mining, reviewing, importing, exporting, creating, accepting, or rejecting Titan learned behavior patterns from memory evidence.
---

# Titan Patterns Workflow

Patterns are reusable behaviors backed by memory evidence. Treat every new pattern as a candidate until the user explicitly asks to accept or reject it.

## Routing

Use this skill when the user asks about learned behaviors, repeated mistakes, reusable project habits, pattern mining, pattern backfills, candidate patterns, or importing/exporting Titan patterns.

## Review Existing Patterns

1. Call `patterns_status` to understand mining state.
2. Call `patterns_list` for candidate, accepted, rejected, or scoped views.
3. Call `pattern_get` before discussing a specific pattern so the evidence is visible.
4. Use `pattern_accept` or `pattern_reject` only when the user explicitly asks for that decision.

## Mine Or Backfill Patterns

1. Call `patterns_evidence_packet` with a bounded batch size.
2. Inspect the returned memory IDs, scene IDs, and context. Expand important scenes with `get_scene_context` when evidence is ambiguous.
3. Create only evidence-backed candidate patterns with `pattern_create`.
4. Include evidence memory IDs and scene IDs in `evidence_json`; do not create patterns from vibes.
5. Call `patterns_mark_processed` after inspecting each evidence packet, including packets that produce no useful pattern.

## Import And Export

Use `patterns_export_bundle` for shareable pattern bundles. By default it exports accepted patterns; include candidates only when the user asks or when doing a deliberate review handoff.

Use `patterns_import_bundle` only with a user-provided local bundle path. Do not overwrite existing patterns unless the user explicitly asks for overwrite behavior.

## Safety Rules

Never auto-accept mined patterns. Never turn memories into psychological claims. Prefer specific workflow observations, repo conventions, and repeated implementation lessons. If evidence is thin, create no pattern and explain what evidence would be needed.
