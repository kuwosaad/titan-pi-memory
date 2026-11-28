---
name: titan-setup
description: Create or verify Titan Memory setup for Grok. Use when the user runs /titan-setup, first enables Titan, or needs agent home, env keys, plugin, or MCP wiring.
---

# Titan Setup

Run the plugin’s provider setup when the user requests setup or a provider change.

1. Resolve `../../scripts/titan_mcp_launcher.py` relative to this skill's directory. Run `python3 "<resolved script path>" configure` to open Titan's local provider form. This command also works while the MCP server is disconnected.
2. Have the user select an extraction provider and embedding model and enter any required keys in that form. For Ollama embeddings, the selected model must already be available locally.
3. After the settings are saved, restart the Titan server from `/mcps` or restart Grok. Call `titan-memory__doctor` and check the active namespace and missing provider keys.
4. To verify persistence, save an agreed test fact with `store_trace_packet`, check `stored`, then recall the fact from a fresh Grok session.
