# Titan Memory for Hermes Agent

Titan captures successful text turns on Hermes Agent's standard runtime.
Titan turns those exchanges into searchable memories with source scenes.
The plugin works alongside Hermes' built-in memory. Hermes' optional
Codex app-server runtime supports on-demand Titan recall through MCP.

## Install

Use Hermes Agent 0.23.4 or later and the Titan CLI from the same Titan revision
as the plugin. For a catalog install, use the commit listed in the catalog
entry. Check out that commit in the Titan repository and run `pipx install .`
from its root. Put `titan` on the Hermes process's `PATH`.

Install and enable the plugin from the Hermes catalog once listed, or copy
it from a matching Titan checkout. Set `hermes_home` to your active Hermes
profile's directory.

```bash
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
plugin_source=integrations/hermes_titan_plugin
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

See [Titan configuration](https://github.com/kuwosaad/titan-pi-memory#configuration-and-data)
for other providers.

## Data and permissions

The plugin sends the current prompt and final reply to the configured local
Titan MCP server after Hermes confirms the turn succeeded. Attachments,
tool data, and conversation history stay in Hermes. Failed, interrupted,
and policy-vetoed turns are discarded. Titan redacts recognized credentials
before capping each message at 12,000 characters.

Hermes starts the configured `titan mcp` subprocess. Titan saves traces,
source scenes, and extracted memories under the selected agent's Titan
directory. Titan's own extraction and embedding settings determine which
text goes to external providers. For example, OpenCode Go receives text
for extraction and local Ollama handles embeddings. Provider keys are
managed by Titan. The plugin does not read credential files or send
telemetry.

Hermes waits for capture during turn finalization. The MCP request uses a
ten-second timeout. A failed call produces a warning and Hermes still
returns the reply.

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
