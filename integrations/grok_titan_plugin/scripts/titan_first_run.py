#!/usr/bin/env python3
"""First-run onboarding for Titan Memory for Grok.

This script is intentionally informational. It prepares the agent home if
needed and prints the manual Grok steps to enable the plugin and MCP server.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence, TextIO

from titan_grok_env import DEFAULT_AGENT, agent_home as grok_agent_home
HOOK_COMMAND = 'python3 "${GROK_PLUGIN_ROOT}/scripts/titan_grok_hook.py"'
FIRST_PROMPTS = [
    "Use Titan Memory to find prior decisions about this repo.",
    "Remember the outcome of this work in Titan.",
    "Show recent Titan memories for this Grok agent.",
    "Check whether Titan is healthy in this Grok session.",
    "Use Titan clusters to summarize related memories.",
]


def ensure_agent_home(agent: str = DEFAULT_AGENT) -> Path:
    safe_agent = (agent or DEFAULT_AGENT).strip() or DEFAULT_AGENT
    agent_home = grok_agent_home(safe_agent)
    for sub in ("config", "traces", "out/memories"):
        (agent_home / sub).mkdir(parents=True, exist_ok=True)
    env_path = agent_home / ".env"
    try:
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("# Titan provider settings for Grok. Use /titan-setup to configure.\n")
    return agent_home


def onboarding_payload(agent: str = DEFAULT_AGENT) -> dict:
    safe_agent = (agent or DEFAULT_AGENT).strip() or DEFAULT_AGENT
    agent_home = grok_agent_home(safe_agent)
    return {
        "title": "Titan Memory is ready for Grok.",
        "agent": safe_agent,
        "agent_home": str(agent_home),
        "trace_dir": str(agent_home / "traces"),
        "manual_steps": [
            {
                "command": "restart or /plugins",
                "description": "Restart Grok, or press r in /plugins to reload plugins.",
            },
            {
                "command": "/plugins",
                "description": "Confirm titan-memory is enabled (add to [plugins].enabled in ~/.grok/config.toml if needed).",
            },
            {
                "command": "/mcps",
                "description": "Confirm the titan-memory MCP server is connected.",
            },
            {
                "command": "/titan-setup",
                "description": "Select extraction and embedding providers in the local form, then restart Grok.",
            },
        ],
        "first_prompts": FIRST_PROMPTS,
        "hook_command": HOOK_COMMAND,
        "privacy_note": (
            "Titan stores Grok memory locally under ~/.titan/agents/grok. "
            "Passive capture writes redacted hook traces under traces/."
        ),
    }


def render_text(payload: dict) -> str:
    lines = [
        payload["title"],
        "",
        "Next steps inside Grok:",
    ]
    for idx, step in enumerate(payload["manual_steps"], start=1):
        lines.append(f"{idx}. Run {step['command']} — {step['description']}")
    lines.extend([
        "",
        "Try one of these prompts:",
    ])
    for prompt in payload["first_prompts"]:
        lines.append(f"- {prompt}")
    lines.extend([
        "",
        f"Local storage: {payload['agent_home']}",
        payload["privacy_note"],
        "",
        f"Passive hook command: {payload['hook_command']}",
    ])
    return "\n".join(lines) + "\n"


def run(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show Titan Memory for Grok first-run instructions.")
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable onboarding details.")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Create ~/.titan/agents/<agent> layout if missing.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    stream = stdout or os.sys.stdout
    if args.prepare:
        ensure_agent_home(args.agent)
    payload = onboarding_payload(args.agent)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
    else:
        print(render_text(payload), end="", file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
