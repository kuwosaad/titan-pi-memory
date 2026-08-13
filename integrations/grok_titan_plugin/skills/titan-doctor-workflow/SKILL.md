---
name: titan-doctor-workflow
description: Use when Titan Memory, Grok plugin tools, MCP visibility, passive capture, trace ingestion, provider keys, or memory retrieval appears broken or stale. Use when the user runs /titan-status or asks if Titan is healthy.
---

# Titan Doctor Workflow

Doctor is the first move when Titan looks broken. Diagnose from reported state before guessing or editing config.

Agent namespace is always `grok`. Memory lives at `~/.titan/agents/grok`.

## Routing

Use this skill when the user says Titan is missing, `/mcps` does not show expected tools, memory search is stale, traces are not captured, hooks are noisy, provider keys are missing, or the Grok plugin feels disconnected.

## Workflow

1. Call `doctor` first via `use_tool` with `titan-memory__doctor` (or the `titan-memory` doctor tool if listed).
2. Check `mcp_tool_count` and `mcp_tools`. A healthy Grok surface should include memory, doctor, cluster, and analysis tools (and pattern tools when available).
3. Check `agent_name`, `agent_namespace`, `trace_dir`, `trace_dir_exists`, `trace_file_count`, and recent trace file names. Expect agent `grok` and home under `~/.titan/agents/grok`.
4. Check `provider_keys.missing_envs` before blaming retrieval or extraction logic. Keys belong in `~/.titan/agents/grok/.env`.
5. If MCP tools are missing, ask the user to re-enable the `titan-memory` plugin, press `r` in `/plugins`, confirm `/mcps`, then re-check.
6. If traces are missing, confirm hooks are loaded for the plugin and that capture writes under `~/.titan/agents/grok/traces`.
7. If memories are stale, compare `trace_file_count`, `memory_count`, and recent trace files, then run a targeted query with `query_memories`.

## Escalation

Use repo inspection only after `doctor` narrows the likely cause. Avoid changing `~/.grok`, `~/.titan`, or plugin config unless the user asks or the diagnosis clearly points there.

## Answer Rules

Report the observed failing subsystem: MCP registration, hook capture, trace files, auto-ingest, provider keys, storage, or retrieval. Include the next concrete check instead of vague troubleshooting advice.
