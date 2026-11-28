---
name: titan-recent
description: Show recent Titan memories for the Grok agent. Use when the user runs /titan-recent, asks what happened lately, or wants the latest captures.
---

# Titan Recent

Use `titan-memory__get_recent_memories` to browse recent work. Pass `sources=["grok"]` for Grok-only activity. Preserve the returned source on each result and include it when opening that scene with `titan-memory__get_scene_context`.
