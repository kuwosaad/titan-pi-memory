---
name: titan-grok-memory
description: >
  Use Titan Memory from Grok through Grok’s connected MCP tools.
  Use when recalling prior work, decisions, or dates; when the user says remember
  this; when checking Titan health or the graph; or for /titan-query /titan-save
  /titan-recent /titan-status /titan-clusters /titan-cortex /titan-graph.
---

# Titan Grok Memory

Use the connected Titan tools in Grok. Grok writes to `~/.titan/agents/grok` by default. Recall can read the user's other Titan stores.

- Use `titan-memory__query_memories` to find prior decisions.
- Read an original conversation with `titan-memory__get_scene_context`. Pass the returned `scene_id` and `source_agent` together.
- Use `titan-memory__get_recent_memories` to browse activity and `titan-memory__store_trace_packet` to save a decision. Report a save as durable only when `stored` is true.
- Use `titan-memory__doctor` to check the workspace, extraction configuration and passive capture. Open `/titan-setup` to configure providers.
