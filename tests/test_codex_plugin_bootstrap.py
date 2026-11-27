from pathlib import Path
import sys

from integrations.codex_titan_plugin.scripts import titan_codex_hook, titan_mcp_launcher


def test_marketplace_launcher_bootstraps_from_installed_plugin_with_no_cli(tmp_path):
    calls = []
    home = tmp_path / "codex-memory"
    env = {"PATH": "", "TITAN_HOME": str(home)}
    result = titan_mcp_launcher.run([], base_env=env, exec_fn=lambda *args: calls.append(args))

    assert result == 0
    executable, argv, runtime_env = calls[0]
    helper = Path(titan_mcp_launcher.__file__).with_name("titan_plugin_runtime.py")
    assert executable == sys.executable
    assert argv == [sys.executable, str(helper), "mcp", "--agent", "codex"]
    assert runtime_env["TITAN_HOME"] == runtime_env["TITAN_BASE_DIR"] == str(home)
    assert runtime_env["TITAN_AGENT_NAME"] == "codex"
    assert runtime_env["PATH"] == ""


def test_forked_session_records_codex_source_in_custom_home(tmp_path):
    _, events = titan_codex_hook.build_trace_events({
        "hook_event_name": "SessionStart", "session_id": "forked-session", "source": "fork"
    })
    assert events[0]["payload"]["reason"] == "fork"
    assert titan_codex_hook.resolve_trace_dir({"TITAN_HOME": str(tmp_path)}) == tmp_path / "traces"
