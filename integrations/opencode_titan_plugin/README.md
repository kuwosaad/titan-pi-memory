# Titan Memory for OpenCode

Titan captures conversation turns and provides local memory tools in OpenCode 2.x and classic 1.x. The plugin ships its Python engine. First use requires Python 3.10 or newer with venv and pip, and network access to install Python dependencies. Keep the plugin directory in a stable location.

## OpenCode 2.x

Add the absolute path to this plugin directory to the `plugins` array in your global `~/.config/opencode/opencode.jsonc` or project `opencode.jsonc`, preserving existing entries.

```jsonc
{
  "plugins": ["/absolute/path/to/titan-pi-memory/integrations/opencode_titan_plugin"]
}
```

OpenCode loads the included server bundle, registers the five Titan skills and connects to its bundled engine over stdio. Alternatively, with Bun on PATH, run `bun run setup` from this directory to install a global plugin loader. Choose one installation method.

1. Restart OpenCode. Enter `/titan-setup` to open the local provider configuration form in the browser. Select and validate the extraction and embedding models there. Keys go into the form.
2. After saving the form, restart OpenCode to load the provider settings. `opencode mcp list` should show `titan-memory` connected. The first connection creates a private Python environment and may take several minutes.
3. Ask OpenCode to remember a distinctive project decision. Open a fresh session and ask it to recall that decision. The agent can use `titan-memory_store_trace_packet`, `titan-memory_query_memories` and `titan-memory_get_scene_context`.

OpenCode manages the MCP server through the V2 plugin. A user-configured custom `titan-memory` endpoint takes precedence. A classic `titan mcp --agent opencode` entry is upgraded to the bundled engine while keeping its existing settings. The browser form runs on the OpenCode host. [Provider plugin documentation](https://opencode.ai/docs/build/plugins/server)

## Classic OpenCode 1.x

From this directory, with Bun and OpenCode on PATH, run:

```bash
bun run setup
```

Restart OpenCode and ask it to set up Titan’s memory providers using the installed `titan-doctor-workflow` skill. The agent opens the local provider form. After saving it, restart OpenCode and run `opencode mcp list`. Use the same save-and-recall check above.

Classic setup installs the capture bundle at `~/.config/opencode/plugins/titan_v2_spool_plugin.ts`, copies five skills under `~/.config/opencode/skills/`, and adds the bundled Python launcher to the existing OpenCode JSON or JSONC configuration. It respects `XDG_CONFIG_HOME`, keeps unrelated configuration, and backs up changed configuration or plugin files.

## Storage and diagnosis

OpenCode writes private, redacted trace batches under `~/.titan/agents/opencode/traces/`. The plugin skips Titan tool results and model reasoning. Federated search retains the source agent of foreign memories. [Privacy](PRIVACY.md) · [Terms](TERMS.md)

If the MCP server fails to connect, check that `python3` is available to the OpenCode process and that the plugin directory still contains `scripts/titan_runtime.py` and `scripts/titan_engine.zip`. If tools connect but a save reports pending extraction, invoke the provider form and validate the model. The `titan-memory_doctor` tool reports missing provider configuration and the active namespace.
