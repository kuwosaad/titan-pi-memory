#!/usr/bin/env bash
# Install Titan Memory for Grok: prepare agent home + symlink plugin.
# Does NOT rewrite ~/.grok/config.toml (use the install task / manual patch for that).
set -euo pipefail

PLUGIN_SRC="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_DST="${HOME}/.grok/plugins/titan-memory"
FIRST_RUN="${PLUGIN_SRC}/scripts/titan_first_run.py"

echo "Titan Memory for Grok — install"
echo "  plugin source: ${PLUGIN_SRC}"
echo "  plugin dest:   ${PLUGIN_DST}"

# 1. Prepare agent home (~/.titan/agents/grok)
if [[ -f "${FIRST_RUN}" ]]; then
  python3 "${FIRST_RUN}" --prepare
else
  echo "warning: titan_first_run.py not found; creating agent home dirs only"
  mkdir -p "${HOME}/.titan/agents/grok"/{config,traces,out/memories,out/graphs,out/sessions,out/traces}
fi

# Ensure full out/ tree even if first_run only creates a subset
mkdir -p "${HOME}/.titan/agents/grok"/{config,traces,out/memories,out/graphs,out/sessions,out/traces}

# 2. Symlink plugin into Grok's plugins directory
mkdir -p "${HOME}/.grok/plugins"
ln -sfn "${PLUGIN_SRC}" "${PLUGIN_DST}"

# 3. Ensure hook + CLI scripts are executable
chmod +x "${PLUGIN_SRC}/scripts/titan_grok_hook.py" \
         "${PLUGIN_SRC}/scripts/titan_first_run.py" \
         "${PLUGIN_SRC}/scripts/titan_mcp_launcher.py" \
         "${PLUGIN_SRC}/scripts/titan_grok_tools.py" \
         "${PLUGIN_SRC}/scripts/titan-grok" 2>/dev/null || true

# 4. Put titan-grok on PATH so this Grok session can use Titan before MCP reloads
mkdir -p "${HOME}/.local/bin"
ln -sfn "${PLUGIN_SRC}/scripts/titan-grok" "${HOME}/.local/bin/titan-grok"

echo
echo "Symlink:"
ls -la "${PLUGIN_DST}"
echo
echo "CLI:"
ls -la "${HOME}/.local/bin/titan-grok"
echo
echo "Agent home:"
ls -la "${HOME}/.titan/agents/grok" || true
echo
echo "Done. If not already configured, add to ~/.grok/config.toml:"
echo
cat <<'TOML'
[plugins]
enabled = ["titan-memory"]
TOML
echo
echo "MCP is provided by the plugin .mcp.json (launcher + --agent grok)."
echo "Do not add a manual [mcp_servers.titan-memory] with a machine-local"
echo "absolute path unless you intentionally override the plugin server."
echo
echo "CLI is ~/.local/bin/titan-grok (add ~/.local/bin to PATH if needed)."
echo "Then restart Grok, or press r in /plugins to reload."
echo "Confirm with /plugins and /mcps, or: titan-grok doctor"
