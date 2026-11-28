---
name: titan-setup
description: Configure Titan Memory for Codex on first use or change the extraction provider, embedding provider, or API keys from the local setup form.
---

# Configure Titan Memory for Codex

Run the plugin’s bundled provider setup when the user requests first-time setup or a provider change.

1. Resolve `../../scripts/titan_plugin_runtime.py` relative to the directory containing this SKILL.md. Run `python3 "<resolved helper path>" configure --agent codex` on the user's machine. Use the installed plugin’s helper so the settings apply to the same engine as the MCP server.
2. Have the user enter keys in the local configuration form. Keep key values out of chat, command arguments and terminal output. If using Ollama embeddings, the local Ollama service needs the selected model. The default is `nomic-embed-text:v1.5`.
3. After the form confirms the settings, have the user restart the MCP server or Codex and run the Titan `doctor` tool. Check the active agent, namespace and missing provider keys. Verify model-backed memory by storing a user-approved test fact and retrieving it in a fresh session.
4. If the user wants passive capture, direct them to review the Titan hooks in `/hooks`.
