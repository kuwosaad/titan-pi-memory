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
    home = tmp_path / "titan home"
    result = subprocess.run(
        [sys.executable, str(scripts / "titan_claude_launcher.py")],
        input="MCP frame", text=True, capture_output=True,
        env={**os.environ, "TITAN_HOME": str(home), "TITAN_CLAUDE_EXTRACTION_KEY": "synthetic-key",
             "PATH": _path_without_tools(tmp_path)},
        check=True, timeout=10,
    )
    assert json.loads(result.stdout) == ["synthetic-key", "MCP frame"]
    assert "synthetic-key" not in result.stderr
    # Provider setup learns the key's source, never its value.
    record = (home / "plugin_managed_keys.json").read_text(encoding="utf-8")
    assert json.loads(record) == {"OPENCODE_GO_API_KEY": "/plugin configure"}
    assert "synthetic-key" not in record


def test_plugin_configure_key_overrides_the_saved_key_and_clearing_it_restores_the_saved_one(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(PLUGIN / "scripts/titan_claude_launcher.py", scripts / "titan_claude_launcher.py")
    (scripts / "titan_runtime.py").write_text("import sys\nprint(sys.executable)\n", encoding="utf-8")
    (scripts / "titan_claude_mcp.py").write_text(
        "import os\nprint(os.environ.get('OPENCODE_GO_API_KEY', ''))\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("OPENCODE_GO_API_KEY=saved-by-setup\n", encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if k != "OPENCODE_GO_API_KEY"}

    def launch(key):
        env = {**base, "TITAN_HOME": str(home), "TITAN_CLAUDE_EXTRACTION_KEY": key}
        return subprocess.run([sys.executable, str(scripts / "titan_claude_launcher.py")], text=True,
                              capture_output=True, env=env, check=True, timeout=10).stdout.strip()

    assert launch("from-plugin-configure") == "from-plugin-configure"
    assert (home / "plugin_managed_keys.json").exists()
    # Blank field: the launcher leaves the engine to load the key saved by setup.
    assert launch("${user_config.opencode_go_api_key}") == ""
    assert not (home / "plugin_managed_keys.json").exists()


@pytest.mark.parametrize("helper", ["raise SystemExit(7)", "print('/missing/titan/python')"])
def test_mcp_propagates_bootstrap_failures(tmp_path, helper):
    launcher = tmp_path / "titan_claude_launcher.py"
    shutil.copyfile(PLUGIN / "scripts/titan_claude_launcher.py", launcher)
    (tmp_path / "titan_runtime.py").write_text(helper, encoding="utf-8")
    result = subprocess.run([sys.executable, str(launcher)], capture_output=True, timeout=10)
    assert result.returncode != 0
    assert result.stdout == b""
