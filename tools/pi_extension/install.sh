#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
printf '%s\n' "[titan-pi] Installing Titan Pi extension from: $ROOT_DIR"

command -v pi >/dev/null 2>&1 || {
  printf '%s\n' "[titan-pi] Error: pi command not found on PATH." >&2
  exit 1
}

pi install "$ROOT_DIR"
printf '%s\n' "[titan-pi] Start Pi and run /titan-setup to configure extraction and embedding."
