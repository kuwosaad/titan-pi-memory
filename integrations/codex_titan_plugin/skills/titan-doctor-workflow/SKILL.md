---
name: titan-doctor-workflow
description: Use when Titan Memory, Codex plugin tools, MCP visibility, passive capture, trace ingestion, provider keys, or memory retrieval appears broken or stale.
---

# Titan Doctor Workflow

Doctor is the first move when Titan looks broken. Diagnose from reported state before guessing or editing config.

## Routing

Use this skill when the user says Titan is missing, `/mcp` does not show expected tools, memory search is stale, traces are not captured, hooks are noisy, provider keys are missing, or the Codex plugin feels disconnected.

## Workflow

1. Call `doctor` first.
2. Check `mcp_tool_count` and `mcp_tools`. The default profile exports `query_memories`, `get_scene_context`, `store_trace_packet`, and `doctor`. Graph and pattern tools require the full profile.
3. Check `agent_name`, `agent_namespace`, `trace_dir`, `trace_dir_exists`, `trace_file_count`, and recent trace file names.
4. Check `provider_keys.missing_envs`. Use the titan-setup skill when the user needs to select or configure providers.
5. If MCP tools are missing, ask the user to reinstall or refresh the local Codex plugin, then re-check `/mcp`.
6. If traces are missing, inspect hook trust/config before assuming ingestion failed.
7. If memories are stale, compare `trace_file_count`, `memory_count`, and recent trace files, then run a targeted query with `query_memories`.

## Escalation

Use repo inspection only after `doctor` narrows the likely cause. Avoid changing `.codex`, `.titan`, or plugin config unless the user asks or the diagnosis clearly points there.

## Answer Rules

Report the observed failing subsystem: MCP registration, hook capture, trace files, auto-ingest, provider keys, storage, or retrieval. Include the next concrete check instead of vague troubleshooting advice.
