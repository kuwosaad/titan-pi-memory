---
name: titan-doctor-workflow
description: Use when Titan Memory, OpenCode MCP tools, passive capture, trace ingestion, provider keys, or memory retrieval appears broken, missing, or stale.
---

# Titan Doctor Workflow

Diagnose from observed state before changing configuration.

## Workflow

1. Call `titan-memory_doctor` first.
2. Inspect `mcp_tool_count` and `mcp_tools`. A healthy surface includes recall,
   scene, health, cluster, analysis, pattern, evidence, import, and export tools when
   those capabilities are installed.
3. Check `agent_name`, `agent_namespace`, `trace_dir`, `trace_dir_exists`,
   `trace_file_count`, and recent trace filenames. Confirm the namespace is
   `opencode` unless the user deliberately configured another agent name.
4. Check `provider_keys.missing_envs` before blaming retrieval or extraction.
5. If the tool surface is missing, run `bun run setup`, then restart OpenCode and
   call `titan-memory_doctor` again. Report the exact missing tool names.
6. If traces are missing, inspect the configured plugin path, file permissions, and
   the latest event-capture errors. Then check whether a new conversation produces a
   trace file before investigating ingestion.
7. If memories are stale, compare trace-file count, memory count, and timestamps;
   run a targeted `titan-memory_query_memories` probe only after capture and ingestion
   state are understood.

## Diagnosis rules

Name the failing subsystem: plugin loading, MCP registration, event capture, trace
ingestion, provider configuration, storage, or retrieval. Separate an absent file from
an empty result and an extraction failure from a search failure. Do not rewrite config
or delete data as a diagnostic step.

**Done when:** the report identifies the observed failing subsystem, cites the doctor
fields that support it, and gives one concrete next check or confirms the system is
healthy.
