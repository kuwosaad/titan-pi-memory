# Titan Memory for OpenCode

Titan Memory gives OpenCode durable project memory, scene recovery, graph and
pattern workflows, and passive capture of completed conversation activity. This
integration targets the classic OpenCode 1.x plugin contract and connects to the
existing Titan engine through local stdio MCP. Titan itself is unchanged.

## Install

From the repository root, run:

```bash
cd integrations/opencode_titan_plugin
bun run build
bun run setup
```

`bun run setup` builds the self-contained plugin bundle and installs it globally,
copies the five workflow skills, and adds the direct Titan MCP server entry to the
active OpenCode configuration. Run `bun run build` when you only need to regenerate
the bundle.

Global installation locations:

```text
~/.config/opencode/plugins/titan_v2_spool_plugin.ts
~/.config/opencode/skills/<skill-name>/SKILL.md
~/.config/opencode/opencode.json  (or opencode.jsonc)
```

The setup command preserves unrelated configuration and keeps a backup before a
changed configuration is written. It is safe to run repeatedly.

The MCP entry is intentionally direct and local:

```json
{
  "mcp": {
    "titan-memory": {
      "type": "local",
      "command": ["titan", "mcp", "--agent", "opencode"],
      "enabled": true
    }
  }
}
```

After setup, restart OpenCode so it reloads the global plugin and skills. Confirm
that the Titan tools are visible under the `titan-memory_` names, then start a small
conversation and let it reach idle. Capture is asynchronous; the trace file may
appear shortly after the turn completes.

## What is installed

The plugin listens to persisted OpenCode message and tool-part events, then writes
redacted, immutable Titan trace batches. It synchronizes completed user messages,
completed assistant responses, terminal tool results, and stable idle boundaries.
Repeated events are deduplicated by stable IDs.

The five globally installed skills are:

- `titan-memory-workflow` — retrieve and verify prior work.
- `titan-doctor-workflow` — diagnose setup, capture, ingestion, and retrieval.
- `titan-cluster-graph-workflow` — synthesize clusters, bridges, and themes.
- `titan-patterns-workflow` — review and mine evidence-backed patterns.
- `memory-sync` — import approved, distilled history from other local agents.

Titan MCP tools appear with the `titan-memory_` prefix in OpenCode, including
`titan-memory_query_memories`, `titan-memory_get_scene_context`,
`titan-memory_get_recent_memories`, and `titan-memory_store_trace_packet`.

## Storage and privacy

OpenCode's active namespace is `opencode`:

```text
~/.titan/agents/opencode/
  traces/
  out/memories/memory_store.db
```

Capture stores user and completed assistant text after recursive credential
redaction. Tool fields are allowlisted and output is compacted. Titan MCP results are excluded
from capture so recalled memories do not become new evidence. Trace files are
private (`0700` directories, `0600` files) and written through a temporary file and
atomic rename.

Federated recall can read other valid Titan agent namespaces, but every foreign
result keeps its `source_agent`; opening a foreign scene uses that same provenance.
Writes remain in the active OpenCode namespace. Read [PRIVACY.md](PRIVACY.md) for
the data categories and [TERMS.md](TERMS.md) for use conditions.

## Troubleshooting

If the plugin or tools are missing:

1. Confirm the bundle exists at `~/.config/opencode/plugins/titan_v2_spool_plugin.ts`.
2. Run `bun run setup` from this repository.
3. Restart OpenCode and check the `titan-memory_` tool list.
4. Run `titan doctor --agent opencode` and inspect the reported namespace, paths,
   tool count, and provider-key status.

If capture is missing, check:

```bash
ls -la ~/.titan/agents/opencode/traces
titan doctor --agent opencode
```

Then make one fresh conversation reach idle. A failed provider key affects
extraction, not plugin loading; `provider_keys.missing_envs` identifies that case.
If setup reports an unsupported OpenCode major version, stop and use a compatible
release rather than forcing the bundle.

## Scope

This is a local integration in this repository. It does not replace or modify the
Titan engine, CLI, MCP server, ingestion pipeline, storage, or packaging. It does
not publish an npm package. Only the classic OpenCode 1.x contract is in scope.
