# Titan Memory Claude Code Plugin

This plugin makes Titan available inside Claude Code through MCP, workflow skills, and passive trace capture hooks.

## Local Dogfood

Run Claude Code with the local plugin directory:

```bash
claude --plugin-dir ./integrations/claude_titan_plugin --debug
```

Inside Claude Code, verify:

```text
/reload-plugins
/plugin
/mcp
/hooks
/titan-memory:titan-memory-workflow
```

Expected results:

- `/plugin` lists `titan-memory`.
- `/mcp` shows the `titan-memory` server connected.
- `/hooks` shows the Titan hook commands for Claude lifecycle events.
- `/titan-memory:titan-memory-workflow` loads the recall workflow.
- Trace files appear under `~/.titan/agents/claude-code/traces/` after hook approval.

## MCP Server

The plugin starts Titan with:

```bash
titan mcp --agent claude-code
```

The agent name defaults to `claude-code`, but can be changed during plugin enablement. Keep the name stable because it controls the memory namespace under `~/.titan/agents/`.

## Passive Capture

The hook script reads Claude hook JSON from stdin, compacts and redacts useful fields, and appends Titan-compatible JSONL events. It never retrieves memories automatically and should not block Claude Code if capture fails.

Captured events include session start/end, user prompts, tool use, compaction, assistant stop markers, and turn completion.
