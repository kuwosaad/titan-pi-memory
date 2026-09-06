import io
import json
import os
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from types import ModuleType
from unittest.mock import Mock, patch
from pathlib import Path

from tools.cli.titan import main


def _fake_recovery_module(recover_pending_sessions: Mock) -> ModuleType:
    module = ModuleType("integrations.codex_titan_plugin.pending_recovery")
    module.recover_pending_sessions = recover_pending_sessions
    return module


@contextmanager
def _isolated_cli_runtime():
    """Yield a temporary Titan home while preserving the default agent layout."""
    temporary = tempfile.TemporaryDirectory(prefix="titan-codex-recovery-test-")
    root = Path(temporary.name)
    titan_home = root / "titan-home"
    runtime = root / "runtime"
    patches = (
        patch.dict(
            os.environ,
            {
                "TITAN_HOME": str(titan_home),
                "TITAN_BASE_DIR": str(root / "titan-base"),
                "TITAN_RUNTIME_HOME": str(runtime),
                "TITAN_RUNTIME_DIR": str(runtime),
                "TITAN_RUNTIME_MANIFEST": str(runtime / "current.json"),
            },
            clear=False,
        ),
        patch("tools.cli.titan._explicit_titan_home", None),
        patch("tools.cli.titan.TITAN_HOME", titan_home),
    )
    try:
        for item in patches:
            item.start()
        yield titan_home
    finally:
        for item in reversed(patches):
            item.stop()
        temporary.cleanup()


def test_recover_pending_cli_is_dry_run_by_default_and_hides_recovered_text() -> None:
    recover = Mock(
        return_value={
            "dry_run": True,
            "pending_sessions": 2,
            "pending_events": 7,
            "recoverable_completed_turns": 2,
            "partial_turns": 1,
            "active_turns": 0,
            "missing_transcripts": 0,
            "recovered_text": "private answer must not be printed",
        }
    )
    stdout = io.StringIO()
    fake_module = _fake_recovery_module(recover)

    with _isolated_cli_runtime() as titan_home:
        with patch.dict(sys.modules, {"integrations.codex_titan_plugin.pending_recovery": fake_module}), patch(
            "tools.cli.titan.configure_runtime_for_agent"
        ), redirect_stdout(stdout):
            assert main(["codex", "recover-pending"]) == 0

    recover.assert_called_once_with(
        apply=False,
        session_ids=None,
        pending_file=titan_home / "agents" / "codex" / "traces" / "pending_user_messages.json",
    )
    output = stdout.getvalue()
    assert "private answer" not in output
    assert json.loads(output) == {
        "active_turns": 0,
        "dry_run": True,
        "missing_transcripts": 0,
        "partial_turns": 1,
        "pending_events": 7,
        "pending_sessions": 2,
        "recoverable_completed_turns": 2,
    }


def test_recover_pending_cli_passes_apply_and_repeated_session_ids() -> None:
    recover = Mock(return_value={"dry_run": False, "applied_turns": 4, "stored_memories": 2})
    stdout = io.StringIO()
    fake_module = _fake_recovery_module(recover)

    with _isolated_cli_runtime() as titan_home:
        with patch.dict(sys.modules, {"integrations.codex_titan_plugin.pending_recovery": fake_module}), patch(
            "tools.cli.titan.configure_runtime_for_agent"
        ), redirect_stdout(stdout):
            assert main(
                [
                    "codex",
                    "recover-pending",
                    "--apply",
                    "--session-id",
                    "session-a",
                    "--session-id",
                    "session-b",
                ]
            ) == 0

    recover.assert_called_once_with(
        apply=True,
        session_ids=["session-a", "session-b"],
        pending_file=titan_home / "agents" / "codex" / "traces" / "pending_user_messages.json",
    )
    assert json.loads(stdout.getvalue()) == {"applied_turns": 4, "dry_run": False, "stored_memories": 2}
