# Titan Memory for Hermes Agent

The Hermes plugin sends the user’s text and final reply from each completed
text turn to Titan. Titan extracts durable memories and keeps their source
scenes. Hermes can search Titan and open those scenes through its native MCP
tools. The plugin runs alongside Hermes’ built-in memory.

## Install

Use Hermes Agent 0.23.4 or later and the Titan CLI that ships with this plugin.
Put `titan` on the Hermes process’s `PATH`. Set `hermes_home` to the active Hermes
profile and `plugin_source` to the plugin folder in your Titan checkout or npm
installation.

```bash
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
plugin_source=integrations/hermes_titan_plugin
# For a global npm install, use this path instead.
# plugin_source="$(npm root -g)/titan-memory-cli/runtime/integrations/hermes_titan_plugin"
mkdir -p "$hermes_home/plugins/titan-memory"
cp "$plugin_source/plugin.yaml" "$plugin_source/__init__.py" \
  "$hermes_home/plugins/titan-memory/"
```

Merge these entries into that profile’s `config.yaml` and preserve any existing
plugins and MCP servers.

```yaml
plugins:
  enabled: [titan-memory]
  entries:
    titan-memory:
      mcp_allowlist: [titan-memory]

mcp_servers:
  titan-memory:
    command: titan
    args: [mcp, --agent, hermes]
```

Use the absolute path from `command -v titan` when running Hermes as a service
with a different `PATH`. Each additional Hermes profile can use its own Titan
store by replacing `hermes` in the MCP arguments with a name such as
`hermes-work`. Use that same name in Titan commands for the profile. Leave
`TITAN_HOME` unset to use the default per-agent directories.

Configure Titan’s extraction and embedding providers before the first capture.
For the bundled OpenCode Go and Ollama defaults, run these commands and keep
Ollama running.

```bash
titan key set OPENCODE_GO_API_KEY --agent hermes
ollama pull nomic-embed-text:v1.5
```

See [Titan configuration](https://github.com/kuwosaad/titan-pi-memory#configuration-and-data) for other
providers. The hook sends only the text of the current prompt and final reply
to local Titan MCP. Titan redacts recognized credentials before capping each
message at 12,000 characters. Hermes waits for capture during turn finalization.
The MCP request uses a ten-second timeout. Hermes logs a warning on a failed
call and still returns the reply.

## Check capture and recall

1. Run `hermes plugins list` and confirm `titan-memory` is enabled. Restart the
   Hermes chat or gateway after editing the configuration.
2. Give Hermes a concrete decision to remember. In a new conversation, ask it
   to look up the decision using Titan. The `query_memories` tool returns scene
   references that Hermes can expand through `get_scene_context`. If the
   memory is missing, check the Titan MCP `doctor` tool for provider settings.
3. Open the graph with `titan graph --agent hermes --open`.

To disable automatic capture, remove `titan-memory` from `plugins.enabled`.
Keep the MCP server entry if you still want on-demand recall.
