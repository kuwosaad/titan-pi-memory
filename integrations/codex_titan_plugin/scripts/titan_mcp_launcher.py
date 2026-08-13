#!/usr/bin/env python3
"""Launch Titan MCP for the Codex plugin.

The public plugin should not silently depend on a random broken `titan` binary.
This launcher prefers a healthy local Titan CLI, falls back to npm package runners
when available, and prints actionable setup errors without echoing secrets.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

PACKAGE_NAME = "titan-memory-cli"
DEFAULT_AGENT = "codex"
PROBE_TIMEOUT_SEC = 10

SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key|authorization|token|secret|password|credential)=([^\s]+)", re.IGNORECASE),
    re.compile(r"(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
]


@dataclass(frozen=True)
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    cwd: Path | None = None
    source: str = "local"


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def build_runtime_env(agent: str, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    safe_agent = agent.strip() or DEFAULT_AGENT
    default_home = Path.home() / ".titan" / "agents" / safe_agent
    env["TITAN_AGENT_NAME"] = safe_agent
    env.setdefault("TITAN_HOME", str(default_home))
    env.setdefault("TITAN_BASE_DIR", env["TITAN_HOME"])
    return env


def _repo_root_from_launcher() -> Path | None:
    # integrations/codex_titan_plugin/scripts/titan_mcp_launcher.py -> repo root
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").exists() and (candidate / "tools" / "cli" / "titan.py").exists():
        return candidate
    return None


def _probe_candidate(
    base_argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path | None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[bool, str]:
    try:
        result = run_fn(
            [*base_argv, "mcp", "--help"],
            cwd=str(cwd) if cwd else None,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SEC,
        )
    except Exception as exc:  # pragma: no cover - exact subprocess exceptions vary by platform
        return False, redact_text(exc)

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode == 0 and "Traceback" not in output and "ImportError" not in output:
        return True, "ok"
    first_line = output.splitlines()[0] if output else "no output"
    return False, f"exit {result.returncode}: {redact_text(first_line)}"


def _healthy_local_plan(
    *,
    agent: str,
    env: dict[str, str],
    which_fn: Callable[[str], str | None],
    run_fn: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[LaunchPlan | None, list[str]]:
    diagnostics: list[str] = []
    titan_command = env.get("TITAN_CLI_COMMAND") or "titan"
    titan_path = which_fn(titan_command) if os.path.basename(titan_command) == titan_command else titan_command

    if titan_path:
        ok, reason = _probe_candidate([titan_path], env=env, cwd=None, run_fn=run_fn)
        if ok:
            return LaunchPlan([titan_path, "mcp", "--agent", agent], env, source="local-titan"), diagnostics
        diagnostics.append(f"local Titan CLI is not healthy ({titan_path}): {reason}")
    else:
        diagnostics.append("local Titan CLI was not found on PATH")

    repo_root = _repo_root_from_launcher()
    if repo_root:
        module_env = dict(env)
        existing_pythonpath = module_env.get("PYTHONPATH")
        module_env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(repo_root)
        base_argv = [sys.executable, "-m", "tools.cli.titan"]
        ok, reason = _probe_candidate(base_argv, env=module_env, cwd=repo_root, run_fn=run_fn)
        if ok:
            return LaunchPlan([*base_argv, "mcp", "--agent", agent], module_env, cwd=repo_root, source="repo-module"), diagnostics
        diagnostics.append(f"repo Titan module is not healthy ({repo_root}): {reason}")

    return None, diagnostics


def _package_fallback_plan(
    *,
    agent: str,
    env: dict[str, str],
    which_fn: Callable[[str], str | None],
) -> tuple[LaunchPlan | None, str | None]:
    npx = which_fn("npx")
    if npx:
        return LaunchPlan([npx, "-y", PACKAGE_NAME, "mcp", "--agent", agent], env, source="npx"), None

    npm = which_fn("npm")
    if npm:
        return LaunchPlan(
            [npm, "exec", "--yes", "--package", PACKAGE_NAME, "--", "titan", "mcp", "--agent", agent],
            env,
            source="npm-exec",
        ), None

    return None, "Neither `npx` nor `npm` is available for package fallback."


def build_launch_plan(
    *,
    agent: str = DEFAULT_AGENT,
    base_env: Mapping[str, str] | None = None,
    allow_package_fallback: bool = True,
    which_fn: Callable[[str], str | None] = shutil.which,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[LaunchPlan | None, list[str]]:
    env = build_runtime_env(agent, base_env)
    plan, diagnostics = _healthy_local_plan(agent=agent, env=env, which_fn=which_fn, run_fn=run_fn)
    if plan:
        return plan, diagnostics

    if allow_package_fallback:
        fallback, reason = _package_fallback_plan(agent=agent, env=env, which_fn=which_fn)
        if fallback:
            diagnostics.append(f"falling back to {fallback.source} package runner for {PACKAGE_NAME}")
            return fallback, diagnostics
        if reason:
            diagnostics.append(reason)

    return None, diagnostics


def _print_failure(diagnostics: Sequence[str], stderr: TextIO) -> None:
    print("Titan Memory MCP could not start.", file=stderr)
    for item in diagnostics:
        print(f"- {redact_text(item)}", file=stderr)
    print("", file=stderr)
    print("Install or repair Titan, then restart Codex:", file=stderr)
    print("  npm install -g titan-memory-cli", file=stderr)
    print("  # or, from the Titan repo for local dogfood:", file=stderr)
    print("  pip install -e .", file=stderr)
    print("", file=stderr)
    print("Then verify:", file=stderr)
    print("  titan codex list-tools", file=stderr)
    print("  titan setup codex --verify", file=stderr)


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    base_env: Mapping[str, str] | None = None,
    which_fn: Callable[[str], str | None] = shutil.which,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    exec_fn: Callable[[str, Sequence[str], Mapping[str, str]], None] = os.execvpe,
) -> int:
    parser = argparse.ArgumentParser(description="Launch Titan MCP for Codex over stdio.")
    parser.add_argument("--agent", default=(base_env or os.environ).get("TITAN_AGENT_NAME", DEFAULT_AGENT))
    parser.add_argument(
        "--no-package-fallback",
        action="store_true",
        help="Do not fall back to npm package runners when a local Titan CLI is unavailable.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan, diagnostics = build_launch_plan(
        agent=args.agent,
        base_env=base_env,
        allow_package_fallback=not args.no_package_fallback,
        which_fn=which_fn,
        run_fn=run_fn,
    )
    if not plan:
        _print_failure(diagnostics, stderr)
        return 127

    if diagnostics:
        for item in diagnostics:
            print(f"[titan-mcp-launcher] {redact_text(item)}", file=stderr)
    print(f"[titan-mcp-launcher] launching Titan MCP via {plan.source}", file=stderr)
    if plan.cwd:
        os.chdir(plan.cwd)
    exec_fn(plan.argv[0], plan.argv, plan.env)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
