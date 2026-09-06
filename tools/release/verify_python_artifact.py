#!/usr/bin/env python3
"""Install Python distributions into fresh environments and exercise their CLI/MCP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

# Reuse the protocol check and environment isolation used by the npm verifier.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from verify_packed_artifact import REQUIRED_MCP_TOOLS, clean_environment, mcp_stdio_handshake, run_checked


def verify(artifact: Path) -> None:
    artifact = artifact.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="titan-python-artifact-") as directory:
        root = Path(directory)
        env = clean_environment(root)
        venv = root / "venv"
        run_checked([sys.executable, "-m", "venv", str(venv)], cwd=root, env=env)
        binary = venv / ("Scripts" if sys.platform == "win32" else "bin")
        python = binary / ("python.exe" if sys.platform == "win32" else "python")
        run_checked(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(artifact)],
            cwd=root, env=env, timeout=180,
        )
        cli = binary / ("titan.exe" if sys.platform == "win32" else "titan")
        run_checked([str(cli), "--help"], cwd=root, env=env)
        result = run_checked([str(cli), "codex", "list-tools", "--json"], cwd=root, env=env)
        names = set(json.loads(result.stdout)["tools"])
        if not REQUIRED_MCP_TOOLS <= names:
            raise RuntimeError("Installed Python CLI is missing required tools")
        names = set(mcp_stdio_handshake([str(cli), "mcp", "--agent", "codex"], cwd=root, env=env))
        if not REQUIRED_MCP_TOOLS <= names:
            raise RuntimeError("Installed Python MCP is missing required tools")
        print(f"Python artifact verified: {artifact.name}; MCP {len(names)} tools")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="wheel, source archive, or directory containing both")
    path = parser.parse_args().path
    artifacts = sorted(path.glob("*.whl")) + sorted(path.glob("*.tar.gz")) if path.is_dir() else [path]
    if not artifacts:
        parser.error("no Python distributions found")
    for artifact in artifacts:
        verify(artifact)


if __name__ == "__main__":
    main()
