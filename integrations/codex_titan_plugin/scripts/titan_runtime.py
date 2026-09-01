#!/usr/bin/env python3
"""Stable managed-runtime resolution for the Titan Codex connector.

The Codex MCP process is deliberately tiny: it reads the manifest written by
the package installer and replaces itself with the managed Python process.  No
package manager, PATH lookup, or health probe belongs on this hot path.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, NamedTuple


DEFAULT_AGENT = "codex"
MANIFEST_NAME = "current.json"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class ManagedRuntime(NamedTuple):
    version: str
    runtime_root: Path
    python: Path
    entrypoint: Path
    marketplace: Path | None = None


def runtime_home(env: Mapping[str, str] | None = None) -> Path:
    values = env if env is not None else os.environ
    configured = values.get("TITAN_RUNTIME_HOME") or values.get("TITAN_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".titan" / "runtime"


def manifest_path(env: Mapping[str, str] | None = None) -> Path:
    values = env if env is not None else os.environ
    configured = values.get("TITAN_RUNTIME_MANIFEST")
    return Path(configured).expanduser() if configured else runtime_home(values) / MANIFEST_NAME


def _absolute(path_value: object, field: str) -> Path:
    if not isinstance(path_value, str) or not path_value.strip():
        raise ValueError(f"managed runtime manifest field '{field}' is missing")
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"managed runtime manifest field '{field}' must be an absolute path")
    return path


def _normalize_agent_name(agent: str) -> str:
    """Return the safe namespace used beneath the Titan agent directory."""
    raw = str(agent).strip().lower()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError("Agent name must not contain path separators or '..'.")
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if not normalized:
        raise ValueError("Agent name must contain at least one letter or number.")
    return normalized


def load_manifest(path: Path | None = None, *, env: Mapping[str, str] | None = None) -> ManagedRuntime:
    target = path or manifest_path(env)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"managed Titan runtime is not installed (manifest missing: {target}); "
            "run `titan setup codex` to install or repair it"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"managed Titan runtime manifest is unreadable ({target}: {exc}); "
            "run `titan setup codex` to repair it"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"managed Titan runtime manifest is not a JSON object: {target}")
    if payload.get("schema_version", 1) != 1 or payload.get("package", "titan-memory-cli") != "titan-memory-cli":
        raise RuntimeError(f"managed Titan runtime manifest has an unsupported schema or package: {target}")
    version = payload.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise RuntimeError(f"managed Titan runtime manifest has an invalid version: {target}")
    try:
        runtime_root = _absolute(payload.get("runtime_root"), "runtime_root")
        python = _absolute(payload.get("python"), "python")
        entrypoint = _absolute(payload.get("entrypoint"), "entrypoint")
        marketplace_value = payload.get("marketplace")
        marketplace = _absolute(marketplace_value, "marketplace") if marketplace_value else None
    except ValueError as exc:
        raise RuntimeError(f"managed Titan runtime manifest is invalid ({target}): {exc}") from exc
    if runtime_root.name != version:
        raise RuntimeError(f"managed Titan runtime version does not match its directory: {runtime_root}")

    if not runtime_root.is_dir():
        raise RuntimeError(f"managed Titan runtime directory is missing: {runtime_root}; run the setup command to repair it")
    if not python.is_file():
        raise RuntimeError(f"managed Titan Python is missing: {python}; run the setup command to repair it")
    if not entrypoint.is_file():
        raise RuntimeError(f"managed Titan MCP entrypoint is missing: {entrypoint}; run the setup command to repair it")
    resolved_root = runtime_root.resolve()
    try:
        entrypoint.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(
            f"managed Titan MCP entrypoint must be inside its runtime directory: {entrypoint}"
        ) from exc
    return ManagedRuntime(version, runtime_root, python, entrypoint, marketplace)


def build_environment(agent: str = DEFAULT_AGENT, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env if base_env is not None else os.environ)
    safe_agent = _normalize_agent_name(agent or DEFAULT_AGENT)
    agent_home = Path(env.get("TITAN_HOME", str(Path.home() / ".titan" / "agents" / safe_agent))).expanduser()
    env["TITAN_AGENT_NAME"] = safe_agent
    env.setdefault("TITAN_HOME", str(agent_home))
    env.setdefault("TITAN_BASE_DIR", env["TITAN_HOME"])
    return env


def build_launch_argv(runtime: ManagedRuntime, agent: str) -> list[str]:
    return [str(runtime.python), str(runtime.entrypoint), "mcp", "--agent", agent]


def exec_managed_runtime(
    agent: str = DEFAULT_AGENT,
    *,
    env: Mapping[str, str] | None = None,
    manifest: Path | None = None,
    exec_fn=os.execvpe,
) -> ManagedRuntime:
    runtime = load_manifest(manifest, env=env)
    launch_env = build_environment(agent, env)
    existing_path = launch_env.get("PYTHONPATH")
    launch_env["PYTHONPATH"] = (
        f"{runtime.runtime_root}{os.pathsep}{existing_path}" if existing_path else str(runtime.runtime_root)
    )
    argv = build_launch_argv(runtime, agent)
    exec_fn(argv[0], argv, launch_env)
    return runtime


__all__ = [
    "ManagedRuntime",
    "build_environment",
    "build_launch_argv",
    "exec_managed_runtime",
    "load_manifest",
    "manifest_path",
    "runtime_home",
]
