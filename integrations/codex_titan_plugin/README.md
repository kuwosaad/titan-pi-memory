# Titan Memory for Codex

Titan remembers project decisions and retrieves the conversation that supports each memory. Codex writes its own memories under `~/.titan/agents/codex` and can read other Titan namespaces with source attribution.

## Install in Codex

From the root of this Titan checkout, use Codex’s marketplace commands:

```sh
codex plugin marketplace add .
codex plugin add titan-memory@titan-pi-memory
```

Open a new Codex session and ask **“Set up Titan Memory for Codex.”** The plugin starts its own local MCP runtime using Python 3.10 or newer. The first launch requires internet access to install its Python dependencies. Later launches reuse the installed runtime.

The setup skill opens a local configuration form. Choose an extraction provider and an embedding provider. Enter any required API key in the form. With the default OpenCode Go extractor and Ollama embeddings, supply an OpenCode Go key and run Ollama locally with `nomic-embed-text:v1.5` installed. Run `ollama pull nomic-embed-text:v1.5` to download that model. Restart Codex after saving provider settings.

## Verify the connection

1. Open `/plugins` and confirm Titan Memory is enabled.
2. Open `/mcp` and confirm `titan-memory` is available. Ask **“Run Titan doctor and check my configured providers.”**
3. To capture conversations automatically, open `/hooks` and review and trust the Titan hooks. Active memory tools are available independently of hook trust.
4. Ask **“Remember that the test project uses a cobalt notebook.”** Start a new session and ask **“What notebook does the test project use? Use Titan and show the source.”**

The default tools are `store_trace_packet`, `query_memories`, `get_scene_context`, and `doctor`. To use dedicated recent-memory, graph, and pattern tools, add `TITAN_MCP_TOOL_PROFILE=full` to the Codex namespace’s `.env` file and restart the MCP server.

## Troubleshooting

If `/mcp` lists no Titan server, check the installation with `codex plugin list --json` and the launcher path with `codex mcp get titan-memory --json`. Start a fresh session after installing the plugin. If the server is visible but memory extraction or semantic search fails, rerun **“Set up Titan Memory for Codex”** to change the provider settings. An existing `TITAN_RUNTIME_MANIFEST` override selects the specified managed runtime.

Passive capture writes traces under `~/.titan/agents/codex/traces`. If the directory stays empty after a completed turn, check `/hooks` for disabled or untrusted Titan events. See [privacy and network behavior](PRIVACY.md).
