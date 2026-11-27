#!/usr/bin/env python3
"""Start the Claude MCP bridge with the plugin's managed Python interpreter."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path


def config_revision(key: str, env: dict[str, str]) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", env.get("TITAN_AGENT_NAME", "claude-code").lower()).strip("-")
    home = Path(env.get("TITAN_HOME") or Path.home() / ".titan" / "agents" / name).expanduser()
    digest = hashlib.sha256(key.encode())
    for path in (home / ".env", home / "config/extraction_models.yaml", home / "config/embedding_models.yaml"):
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            continue
    return digest.hexdigest()


def main() -> int:
    scripts = Path(__file__).resolve().parent
    env = os.environ.copy()
    key = env.pop("TITAN_CLAUDE_EXTRACTION_KEY", "")
    result = subprocess.run(
        [sys.executable, str(scripts / "titan_runtime.py"), "--runtime-python"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        return result.returncode
    python = Path(result.stdout.strip())
    if not python.is_absolute() or not python.is_file():
        print("Titan's runtime helper did not return an installed Python interpreter.", file=sys.stderr)
        return 1

    if key == "${user_config.opencode_go_api_key}":
        key = ""
    # Reconnect to a new daemon after a native key or saved provider setting changes.
    env["TITAN_CLAUDE_CONFIG_REVISION"] = config_revision(key, env)
    if key:
        env["OPENCODE_GO_API_KEY"] = key
    argv = [str(python), str(scripts / "titan_claude_mcp.py")]
    # Windows has no real exec: os.exec* there neither quotes arguments containing
    # spaces nor keeps the caller's stdio attached, which breaks MCP servers.
    if os.name == "nt":
        return subprocess.call(argv, env=env)
    os.execve(python, argv, env)
    return 0  # unreachable: execve does not return


if __name__ == "__main__":
    raise SystemExit(main())
