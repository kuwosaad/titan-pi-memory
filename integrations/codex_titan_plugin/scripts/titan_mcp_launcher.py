#!/usr/bin/env python3
"""Small Codex MCP entrypoint for Titan's managed runtime."""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import Sequence, TextIO


def _load_runtime_module():
    path = Path(__file__).with_name("titan_runtime.py")
    spec = importlib.util.spec_from_file_location("titan_codex_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Titan runtime helper is missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_runtime_env(agent: str = "codex", base_env=None):
    """Compatibility forwarding seam for integrations that only need env setup."""
    return _load_runtime_module().build_environment(agent, base_env)


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    base_env=None,
    exec_fn=os.execvpe,
) -> int:
    parser = argparse.ArgumentParser(description="Launch Titan MCP for Codex over stdio.")
    environment = base_env if base_env is not None else os.environ
    parser.add_argument("--agent", default=environment.get("TITAN_AGENT_NAME", "codex"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    runtime = _load_runtime_module()
    try:
        managed = runtime.load_manifest(env=environment)
        runtime.exec_managed_runtime(
            args.agent,
            env=environment,
            exec_fn=exec_fn,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print("Titan Memory MCP could not start.", file=stderr)
        print(f"- {exc}", file=stderr)
        print("", file=stderr)
        print("Install or repair Titan, then restart Codex:", file=stderr)
        print("  titan setup codex", file=stderr)
        print("  titan codex verify", file=stderr)
        return 127

    # exec_managed_runtime replaces this process. This branch exists only for
    # test doubles that return from execvpe.
    print(f"[titan-mcp-launcher] launching managed runtime {managed.version}", file=stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
