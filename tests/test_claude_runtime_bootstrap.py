import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1] / "integrations" / "claude_titan_plugin"


def _path_without_tools(directory):
    """A PATH with no user tools; Windows still needs System32 to load Python's DLLs."""
    if os.name != "nt":
        return str(directory)
    system32 = os.path.join(os.environ.get("SYSTEMROOT", r"C:\\Windows"), "System32")
    return os.pathsep.join([str(directory), system32, os.path.dirname(sys.executable)])


def test_mcp_uses_managed_interpreter_and_keeps_stdio(tmp_path):
    scripts = tmp_path / "plugin with spaces" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(PLUGIN / "scripts/titan_claude_launcher.py", scripts / "titan_claude_launcher.py")
    (scripts / "titan_runtime.py").write_text(
        "import sys\nassert sys.argv[1:] == ['--runtime-python']\nprint(sys.executable)\n",
        encoding="utf-8",
    )
    (scripts / "titan_claude_mcp.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps([os.environ['OPENCODE_GO_API_KEY'], sys.stdin.read()]))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(scripts / "titan_claude_launcher.py")],
        input="MCP frame", text=True, capture_output=True,
        env={**os.environ, "TITAN_CLAUDE_EXTRACTION_KEY": "synthetic-key", "PATH": _path_without_tools(tmp_path)},
        check=True, timeout=10,
    )
    assert json.loads(result.stdout) == ["synthetic-key", "MCP frame"]
    assert "synthetic-key" not in result.stderr


@pytest.mark.parametrize("helper", ["raise SystemExit(7)", "print('/missing/titan/python')"])
def test_mcp_propagates_bootstrap_failures(tmp_path, helper):
    launcher = tmp_path / "titan_claude_launcher.py"
    shutil.copyfile(PLUGIN / "scripts/titan_claude_launcher.py", launcher)
    (tmp_path / "titan_runtime.py").write_text(helper, encoding="utf-8")
    result = subprocess.run([sys.executable, str(launcher)], capture_output=True, timeout=10)
    assert result.returncode != 0
    assert result.stdout == b""
