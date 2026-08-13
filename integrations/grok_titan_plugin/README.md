# Titan Memory for Grok

Your Grok agent should remember.

Titan Memory gives Grok durable project memory: prior decisions, scene recovery, recent work, graph inspection, passive session capture, and slash skills that call Titan over MCP. It runs locally and stores Grok memory under your own `~/.titan/agents/grok` directory.

This plugin does **not** invent a new memory engine. Grok talks to the existing Titan CLI via MCP (`titan mcp --agent grok`) plus skills and hooks.

## Local dogfood install

From the `titan-karu` repo:

```bash
# prepare agent home + print first-run steps
python3 integrations/grok_titan_plugin/scripts/titan_first_run.py --prepare

# copy or symlink the plugin into Grok's user plugin dir
mkdir -p ~/.grok/plugins
ln -sfn "$(pwd)/integrations/grok_titan_plugin" ~/.grok/plugins/titan-memory
```

Then enable the plugin if needed in `~/.grok/config.toml`:

```toml
[plugins]
enabled = ["titan-memory"]
```

Restart Grok, or open `/plugins` and press `r` to reload. Plugins under `~/.grok/plugins/` are trusted automatically.

There is no separate npm package for the Grok plugin yet. Dogfood from this repo.

## First run

Inside Grok:

1. `/plugins` — confirm `titan-memory` is enabled
2. `/mcps` — confirm `titan-memory` MCP is connected
3. Try `/titan-status`, `/titan-query`, `/titan-save`, `/titan-recent`

You can reprint the onboarding checklist:

```bash
python3 integrations/grok_titan_plugin/scripts/titan_first_run.py
```

## What Grok gets

### MCP tools

Exposed by the `titan-memory` server (call via `use_tool` as `titan-memory__<tool>`):

| Tool | Purpose |
|------|---------|
| `query_memories` | Semantic recall |
| `get_scene_context` | Recover a full scene from a `scene_id` |
| `get_recent_memories` | Browse recent work |
| `store_trace_packet` | Manually save a decision / outcome |
| `store_trace_event` | Low-level event store |
| `doctor` | Health check |
| `inspect_clusters` | Cluster / graph overview |
| `analyze_clusters` | Deeper cluster synthesis |
| pattern tools | Optional pattern mining / review when available |

### Skills / slash commands

| Skill | Role |
|-------|------|
| `/titan-query` | Semantic recall |
| `/titan-save` | Store a durable packet |
| `/titan-status` | Run doctor |
| `/titan-recent` | Recent memories |
| `/titan-graph` | Open the local graph UI |
| `/titan-setup` | Create/verify agent home and wiring |
| `/titan-clusters` | Inspect / analyze clusters |
| `titan-grok-memory` | Pi-style operating manual (query, scenes, temporal, save) |
| `/memory-sync` | Import Pi / Claude / Codex / Grok history into Grok Titan |
| `titan-memory-workflow` | Archaeology / graph-shaped synthesis |
| `titan-doctor-workflow` | Auto-routed diagnostics |
| `titan-patterns-workflow` | Optional pattern review workflow |

### Passive capture

Hooks call:

```bash
python3 "${GROK_PLUGIN_ROOT}/scripts/titan_grok_hook.py"
```

on session start/end, prompts, tool use, compact, and stop. Capture writes redacted JSONL traces under the Grok agent home.

## Workspace layout

```text
~/.titan/agents/grok/
  .env
  config/
  traces/
  out/memories/memory_store.db
```

## MCP launch

The plugin `.mcp.json` starts Titan through the Python launcher so a broken global `titan` is not a hard dependency:

```json
{
  "mcpServers": {
    "titan-memory": {
      "command": "python3",
      "args": ["${GROK_PLUGIN_ROOT}/scripts/titan_mcp_launcher.py", "--agent", "grok"],
      "env": {
        "TITAN_AGENT_NAME": "grok"
      }
    }
  }
}
```

The launcher prefers a healthy local `titan` CLI, then `python3 -m tools.cli.titan` from the repo, then `npx titan-memory-cli`.

## First prompts

```text
Use Titan Memory to find prior decisions about this repo.
Remember the outcome of this work in Titan.
Show recent Titan memories for this Grok agent.
Check whether Titan is healthy in this Grok session.
```

## Privacy and local storage

Titan Memory is local-first. Grok memory lives under:

```text
~/.titan/agents/grok
```

Passive capture writes hook traces under:

```text
~/.titan/agents/grok/traces
```

Put provider keys in `~/.titan/agents/grok/.env` (for example `GEMINI_API_KEY`). Do not commit secrets.

## Cross-agent memory

Grok memory is isolated from Pi, Codex, Claude Code, Aider, and OpenCode by default. Ask explicitly if you want cross-agent history.

## Troubleshooting

If `/mcps` does not show `titan-memory`:

1. Confirm the plugin path and `[plugins].enabled`
2. Press `r` in `/plugins` or restart Grok
3. Run `python3 integrations/grok_titan_plugin/scripts/titan_first_run.py --prepare`
4. Verify the CLI: `titan mcp --agent grok --help`

If passive traces do not appear:

```bash
ls ~/.titan/agents/grok/traces
```

Then check `/hooks` for the Titan Grok hook and `/titan-status` for doctor output.
