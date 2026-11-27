# Titan Memory for Hermes Agent

Titan captures successful text turns on Hermes Agent's standard runtime.
Titan turns those exchanges into searchable memories with source scenes.
The plugin works alongside Hermes' built-in memory. Hermes' optional
Codex app-server runtime supports on-demand Titan recall through MCP.

## Connect your profile

Use Hermes Agent 0.23.4 or later. This version of the plugin includes a launcher
that prepares Titan's Python runtime under your account. From a checkout of
this version, copy the plugin to the active Hermes profile and enable it:

```bash
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$hermes_home/plugins/titan-memory"
cp integrations/hermes_titan_plugin/{plugin.yaml,__init__.py,titan_runtime.py,titan_engine.zip} \
  "$hermes_home/plugins/titan-memory/"
hermes plugins enable titan-memory
hermes titan-connect --configure-providers
```

Catalog releases before 0.2.0 use the earlier setup. When the catalog lists
this revision or later, use `hermes plugins install titan-memory --enable` in place of the
copy and enable steps. For a named profile, select it with Hermes' `-p NAME`
flag and copy into its plugin directory. For example, use
`hermes -p work titan-connect --configure-providers` to configure the work profile.

The connection command prepares the runtime, opens provider setup, configures the
profile's local `titan-memory` MCP server and grants this plugin permission to
call it. Use `hermes titan-connect` if you already configured your providers.
The first run needs network access for the runtime. Restart the chat or gateway
when setup finishes. OpenCode Go extraction needs a credential and the default
Ollama embedding model must be available locally. Provider setup offers other
choices.

The default profile uses the `hermes` Titan store. A named profile uses
`hermes-<profile>`, and custom Hermes homes have separate stores. Supply
`--agent NAME` to select an existing Titan store. Leave `TITAN_HOME` unset
for automatic store separation. On upgrade, the command preserves the store
from the previously documented `titan mcp --agent NAME` configuration.
If you already configured a custom MCP endpoint, the command asks you to
review that setting before adding a grant.

## Data and permissions

The plugin sends the current prompt and final reply to the configured local
Titan MCP server after Hermes confirms the turn succeeded. Attachments,
tool data, and conversation history stay in Hermes. Failed, interrupted,
and policy-vetoed turns are discarded. The plugin caps the prompt and the
reply at 12,000 characters each before the MCP call. A word cut by that limit
is dropped whole, so a trailing partial word after whitespace stays in Hermes. Titan then redacts
recognized credentials before storing anything.

Hermes starts the installed plugin's managed MCP subprocess. Titan saves traces,
source scenes, and extracted memories under the selected agent's Titan
directory. Titan's own extraction and embedding settings determine which
text goes to external providers. For example, OpenCode Go receives text
for extraction and local Ollama handles embeddings. Provider keys are
managed by Titan. The plugin does not read credential files or send
telemetry.

Hermes waits for capture during turn finalization. The MCP request uses a
ten-second timeout. A failed call produces a warning and Hermes still
returns the reply. That turn is not retried, so check the Hermes log for
`Titan capture` warnings if a memory is missing.

## Check capture and recall

1. Run `hermes plugins list` and confirm `titan-memory` is enabled. Restart the
   Hermes chat or gateway after editing the configuration.
2. Give Hermes a concrete decision to remember. In a new conversation, ask it
   to look up the decision using Titan. The `query_memories` tool returns scene
   references that Hermes can expand through `get_scene_context`. If the
   memory is missing, check the Titan MCP `doctor` tool for provider settings.
3. Ask Hermes to open the returned source scene and check the original exchange.

To disable automatic capture, remove `titan-memory` from `plugins.enabled`.
Keep the MCP server entry if you still want on-demand recall.
