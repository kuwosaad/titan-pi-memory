# Titan Memory for Codex

Your Codex agent should remember.

Titan Memory gives Codex durable project memory: prior decisions, scene recovery, recent work, graph inspection, pattern workflows, and passive session capture. It runs locally through MCP and stores Codex memory under your own `~/.titan/agents/codex` directory.

## Install

Run one command:

```bash
npx -y titan-memory-cli@latest setup codex
```

That command prepares Titan, creates the Codex memory folder, asks which extraction model to use, configures the required `nomic-embed-text:v1.5` embedding model, installs this plugin, patches Codex MCP config, and runs a health check.

Then do the one manual safety step Codex requires:

```text
open Codex
/hooks
approve Titan Memory
```

Advanced manual install, only if you are debugging the plugin itself:

```bash
npm install -g titan-memory-cli
npx codex-marketplace add kuwosaad/titan-memory-codex --plugin --global
titan setup codex
```

## First run

Codex requires manual hook trust. Titan will not bypass that safety gate.

Inside Codex:

1. run `/hooks` and trust `python3 ${PLUGIN_ROOT}/scripts/titan_codex_hook.py`
2. run `/mcp` and confirm `titan-memory`
3. run `/plugins` and confirm Titan Memory is installed

You can also print these instructions locally:

```bash
python3 scripts/titan_first_run.py
```

## What Codex gets

- `query_memories` for semantic recall
- `get_scene_context` for scene recovery
- `get_recent_memories` for recent work
- `doctor` for health checks
- `inspect_clusters` and `analyze_clusters` for graph inspection
- Titan pattern tools for candidate pattern review and mining
- passive lifecycle capture after hook trust

## First prompts

```text
Use Titan Memory to find prior decisions about this repo.
Remember the outcome of this work in Titan.
Show recent Titan memories for this Codex agent.
Check whether Titan is healthy in this Codex session.
Use Titan patterns workflow to inspect candidate patterns.
```

## Privacy and local storage

Titan Memory is local-first. Codex memory lives under:

```text
~/.titan/agents/codex
```

Passive capture writes hook traces under:

```text
~/.titan/agents/codex/traces
```

Passive capture only starts after you trust hooks in `/hooks`. If you do not trust hooks, active MCP tools can still work, but passive session capture will not run.

## Cross-agent memory

Codex memory is isolated from Pi, Claude Code, Aider, and OpenCode by default. This avoids mixing namespaces unexpectedly.

If Codex memory is empty but you have useful Pi or Claude history, ask explicitly:

```text
Search Pi memories too.
Use memory-sync to import Claude Code history into Codex memory.
```

## Troubleshooting

If `/mcp` does not show `titan-memory`:

```bash
titan codex list-tools
titan codex reinstall-plugin
titan setup codex --verify
```

If hooks are untrusted, run `/hooks` inside Codex and trust the Titan hook manually.

If passive traces do not appear, confirm hooks are trusted and then check:

```bash
ls ~/.titan/agents/codex/traces
```
