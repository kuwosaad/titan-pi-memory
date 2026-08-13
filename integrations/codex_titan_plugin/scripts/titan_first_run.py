#!/usr/bin/env python3
"""First-run onboarding for Titan Memory for Codex.

This script is intentionally informational. It does not trust hooks or mutate
Codex security settings because Codex requires users to review hooks manually.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence, TextIO

DEFAULT_AGENT = "codex"
HOOK_COMMAND = "python3 ${PLUGIN_ROOT}/scripts/titan_codex_hook.py"
FIRST_PROMPTS = [
    "Use Titan Memory to find prior decisions about this repo.",
    "Remember the outcome of this work in Titan.",
    "Show recent Titan memories for this Codex agent.",
    "Check whether Titan is healthy in this Codex session.",
    "Use Titan patterns workflow to inspect candidate patterns.",
]


def onboarding_payload(agent: str = DEFAULT_AGENT) -> dict:
    safe_agent = (agent or DEFAULT_AGENT).strip() or DEFAULT_AGENT
    agent_home = Path.home() / ".titan" / "agents" / safe_agent
    return {
        "title": "Titan Memory is installed.",
        "agent": safe_agent,
        "agent_home": str(agent_home),
        "trace_dir": str(agent_home / "traces"),
        "manual_steps": [
            {"command": "/hooks", "description": f"Review and trust: {HOOK_COMMAND}"},
            {"command": "/mcp", "description": "Confirm the titan-memory MCP server is enabled."},
            {"command": "/plugins", "description": "Confirm the Titan Memory plugin is installed."},
        ],
        "first_prompts": FIRST_PROMPTS,
        "privacy_note": "Titan stores Codex memory locally under ~/.titan/agents/codex. Passive capture only starts after you trust hooks in /hooks.",
    }


def render_text(payload: dict) -> str:
    lines = [
        payload["title"],
        "",
        "Next steps inside Codex:",
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
    ])
    return "\n".join(lines) + "\n"


def run(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show Titan Memory for Codex first-run instructions.")
    parser.add_argument("--agent", default=os.getenv("TITAN_AGENT_NAME", DEFAULT_AGENT))
    parser.add_argument("--json", action="store_true", help="Print machine-readable onboarding details.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    stream = stdout or os.sys.stdout
    payload = onboarding_payload(args.agent)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
    else:
        print(render_text(payload), end="", file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
