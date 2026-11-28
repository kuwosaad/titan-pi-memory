"""Resolve the Grok plugin's storage independently of its parent shell."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


DEFAULT_AGENT = "grok"


def agent_home(agent: str = DEFAULT_AGENT, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if not agent or agent.startswith(".") or not all(c.isalnum() or c in "_-" for c in agent):
        raise ValueError("Expected an agent name containing letters, digits, hyphens, or underscores")
    override = env.get("GROK_TITAN_HOME")
    return Path(override).expanduser() if override else Path.home() / ".titan" / "agents" / agent


def runtime_env(agent: str = DEFAULT_AGENT, env: Mapping[str, str] | None = None) -> dict[str, str]:
    result = dict(os.environ if env is None else env)
    home = agent_home(agent, result)
    if result.get("TITAN_AGENT_NAME") not in (None, agent):
        for key in ("TITAN_SETTINGS_PATH", "TITAN_MEMORY_DB_PATH",
                    "TITAN_EXTRACTION_CONFIG_PATH", "TITAN_EMBEDDING_CONFIG_PATH"):
            result.pop(key, None)
    result.update(
        TITAN_AGENT_NAME=agent,
        TITAN_HOME=str(home),
        TITAN_BASE_DIR=str(home),
        TITAN_SPOOL_DIR=str(home / "traces"),
    )
    return result
