---
name: titan-query
description: Semantic recall from Titan Memory for Grok. Use when the user runs /titan-query, asks what Titan remembers, searches prior decisions, or wants memory about a topic.
---

# Titan Query

Call `titan-memory__query_memories` with the user's question. Use its supported date range and source filters for questions about a period or agent. For each scene you open with `titan-memory__get_scene_context`, pass the returned `source_agent` and `scene_id` together. Check current files before presenting a remembered implementation detail as current.
