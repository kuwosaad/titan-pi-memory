from pathlib import Path
import os
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


def test_default_launch_runs_a_child_process_on_windows(tmp_path, monkeypatch):
    calls, execs = [], []

    class WindowsOs:
        # Only the launcher sees Windows; pathlib keeps the real platform.
        name = "nt"

        def __getattr__(self, attr):
            return getattr(os, attr)

        @staticmethod
        def execvpe(*args):
            execs.append(args)

    monkeypatch.setattr(titan_mcp_launcher, "os", WindowsOs())
    monkeypatch.setattr(titan_mcp_launcher.subprocess, "call", lambda argv, env: calls.append((argv, env)) or 7)
    env = {"PATH": "", "TITAN_HOME": str(tmp_path / "codex memory")}

    # The real default is os.execvpe; pass the patched one so the identity check still applies.
    result = titan_mcp_launcher.run(["--agent", "codex"], base_env=env, exec_fn=titan_mcp_launcher.os.execvpe)

    helper = Path(titan_mcp_launcher.__file__).with_name("titan_plugin_runtime.py")
    assert result == 7
    assert execs == []
    assert calls[0][0] == [sys.executable, str(helper), "mcp", "--agent", "codex"]
    assert calls[0][1]["TITAN_AGENT_NAME"] == "codex"


def test_forked_session_records_codex_source_in_custom_home(tmp_path):
    _, events = titan_codex_hook.build_trace_events({
        "hook_event_name": "SessionStart", "session_id": "forked-session", "source": "fork"
    })
    assert events[0]["payload"]["reason"] == "fork"
    assert titan_codex_hook.resolve_trace_dir({"TITAN_HOME": str(tmp_path)}) == tmp_path / "traces"
