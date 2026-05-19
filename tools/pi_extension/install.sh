#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
TITAN_PI_HOME="${TITAN_HOME:-$HOME/.titan/agents/pi}"

printf '%s\n' "[titan-pi] Installing Titan Pi extension from: $ROOT_DIR"

command -v pi >/dev/null 2>&1 || {
  printf '%s\n' "[titan-pi] Error: pi command not found on PATH." >&2
  exit 1
}

pi install "$ROOT_DIR"

mkdir -p "$TITAN_PI_HOME/config" "$TITAN_PI_HOME/traces"

if [ ! -f "$TITAN_PI_HOME/config/extraction_models.yaml" ] && [ -f "$ROOT_DIR/config/extraction_models.yaml" ]; then
  cp "$ROOT_DIR/config/extraction_models.yaml" "$TITAN_PI_HOME/config/extraction_models.yaml"
  printf '%s\n' "[titan-pi] Copied extraction model config."
fi

if [ ! -f "$TITAN_PI_HOME/config/embedding_models.yaml" ] && [ -f "$ROOT_DIR/config/embedding_models.yaml" ]; then
  cp "$ROOT_DIR/config/embedding_models.yaml" "$TITAN_PI_HOME/config/embedding_models.yaml"
  printf '%s\n' "[titan-pi] Copied embedding model config."
fi

if [ ! -f "$TITAN_PI_HOME/.env" ]; then
  cat > "$TITAN_PI_HOME/.env" <<'EOF'
# Titan Pi workspace secrets
# Add the key for your configured extraction model, for example:
# GEMINI_API_KEY=your_key_here
# OPENAI_API_KEY=your_key_here
EOF
  printf '%s\n' "[titan-pi] Created $TITAN_PI_HOME/.env"
fi

printf '%s\n' "[titan-pi] Done."
printf '%s\n' "[titan-pi] Workspace: $TITAN_PI_HOME"
printf '%s\n' "[titan-pi] Next: add your API key to $TITAN_PI_HOME/.env if needed, then start pi and run /titan-status."
