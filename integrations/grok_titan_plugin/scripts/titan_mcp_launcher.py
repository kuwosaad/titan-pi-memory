#!/usr/bin/env python3
"""Run Grok’s MCP server or setup and graph commands in its agent workspace."""
import argparse
import os
from pathlib import Path
import sys

from titan_grok_env import DEFAULT_AGENT, runtime_env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("command", choices=["mcp", "configure", "graph"], default="mcp", nargs="?")
    args, options = parser.parse_known_args()
    helper = Path(__file__).with_name("titan_plugin_runtime.py")
    argv = [sys.executable, str(helper), args.command, "--agent", args.agent, *options]
    os.execve(sys.executable, argv, runtime_env(args.agent))


if __name__ == "__main__":
    main()
