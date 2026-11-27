#!/usr/bin/env python3
"""Start the Claude MCP bridge with the plugin's managed Python interpreter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


KEY_NAME = "OPENCODE_GO_API_KEY"
# Non-secret record of credentials supplied outside Titan's own .env. Provider
# setup reads it to show that /plugin configure takes precedence.
MANAGED_KEYS_FILE = "plugin_managed_keys.json"


def agent_home(env: dict[str, str]) -> Path:
    name = re.sub(r"[^a-z0-9]+", "-", env.get("TITAN_AGENT_NAME", "claude-code").lower()).strip("-")
    return Path(env.get("TITAN_HOME") or Path.home() / ".titan" / "agents" / name).expanduser()


def record_managed_key(home: Path, present: bool) -> None:
    path = home / MANAGED_KEYS_FILE
    try:
        if not present:
            path.unlink(missing_ok=True)
            return
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged = path.with_name(path.name + f".{os.getpid()}.tmp")
        staged.write_text(json.dumps({KEY_NAME: "/plugin configure"}) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        print(f"Titan could not record the /plugin configure key source: {exc}", file=sys.stderr)


def hold_runtime(python: Path) -> None:
    """Keep a shared lock on the private runtime for the server's lifetime (POSIX).

    titan_runtime.py does not delete a runtime while any process holds this lock.
    The descriptor is inheritable, so the lock survives exec.
    """
    import fcntl
    runtime = python.parent.parent
    if not (runtime / "pyvenv.cfg").is_file():
        return  # not a private runtime
    try:
        handle = open(runtime / "titan-in-use.lock", "a+b")
        fcntl.flock(handle, fcntl.LOCK_SH)
        os.set_inheritable(handle.fileno(), True)
    except OSError:
        return
    _HELD.append(handle)


_HELD: list = []


def config_revision(key: str, env: dict[str, str]) -> str:
    home = agent_home(env)
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
    # A key set in /plugin configure takes precedence over one saved by provider
    # setup in Titan's .env; setup reads this record and says so.
    record_managed_key(agent_home(env), bool(key))
    if key:
        env[KEY_NAME] = key
    argv = [str(python), str(scripts / "titan_claude_mcp.py")]
    # Windows has no real exec: os.exec* there neither quotes arguments containing
    # spaces nor keeps the caller's stdio attached, which breaks MCP servers.
    if os.name == "nt":
        return subprocess.call(argv, env=env)
    hold_runtime(python)
    os.execve(python, argv, env)
    return 0  # unreachable: execve does not return


if __name__ == "__main__":
    raise SystemExit(main())
