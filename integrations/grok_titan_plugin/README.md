# Titan Memory for Grok Build

From the root of a checkout you trust, install the Grok plugin with

```sh
grok plugin install ./integrations/grok_titan_plugin --trust
```

Open a new Grok session. The plugin appears in `/plugins`. The plugin uses Python 3.10 or later, available as `python3`, with venv and pip. First-time setup downloads the runtime dependencies. Later launches reuse them.

## Configure and verify

1. Run `/titan-setup` in Grok, even if MCP initially shows a timeout. The skill prepares the runtime independently of Grok’s 30-second MCP connection check. Select the extraction and embedding providers in the local form. Enter required API keys there. If you choose Ollama, make sure the selected model is available locally.
2. After saving the form, restart Grok. Run `grok mcp doctor titan-memory` to check the MCP handshake. Use `/titan-status` to check the Grok workspace, provider configuration, and trace activity.
3. Ask Grok to remember a sample decision. Confirm the store result has `stored: true`, then start a new session and ask for that decision. A pending extraction remains queued until the provider succeeds.

## Tools and storage

The plugin exposes the full Titan MCP profile, including `query_memories`, `get_scene_context`, `get_recent_memories`, `store_trace_packet`, `doctor`, cluster analysis and pattern review. Grok displays them as `titan-memory__<tool>`. The plugin also includes skills for setup, recall, saving and graph inspection.

Grok keeps its agent data in `~/.titan/agents/grok`. Session hooks append redacted conversation and tool excerpts to `traces/`. While the MCP server is running, Titan consumes those traces. Configured cloud providers receive the inputs needed for extraction or embedding. Explicitly set `GROK_TITAN_HOME` before launching Grok to choose a different workspace for all three entry points: hooks, MCP, and bundled commands.

Recall can search the other local Titan agent stores. To open a returned scene, pass its `source_agent` and `scene_id` together. The bundled `scripts/titan-grok` also supports `scene <id> --source-agent <owner>` and `--json recent`.

For integrations that already call `install_grok.sh`, that script remains available as a source-based installer. The native Grok install above registers the plugin without adding any commands to the user's PATH.
