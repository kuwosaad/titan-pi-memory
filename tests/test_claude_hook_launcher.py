import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "integrations/claude_titan_plugin/scripts/titan_claude_hook.py"


def run_cold_hook(tmp_path, payload, mode="messages"):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(HOOK)],
        input=json.dumps(payload), text=True, capture_output=True, timeout=5,
        env={
            **os.environ,
            "TITAN_AGENT_NAME": "claude-test",
            "TITAN_SPOOL_DIR": str(tmp_path / "spool"),
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
            "TITAN_CLAUDE_DATA": str(tmp_path / "data"),
            "CLAUDE_PLUGIN_OPTION_CAPTURE_MODE": mode,
            "PATH": str(tmp_path),
        },
        check=True,
    )
    assert result.stdout == ""
    files = list((tmp_path / "data").rglob("*.jsonl"))
    return [json.loads(line) for file in files for line in file.read_text().splitlines()]


def test_cold_hook_saves_prompt_before_mcp_is_ready(tmp_path):
    events = run_cold_hook(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "Cold prompt",
    })
    assert events[0]["payload"]["content"] == "Cold prompt"
    assert not (tmp_path / "spool").exists()


def test_messages_mode_skips_tool_events(tmp_path):
    events = run_cold_hook(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Read",
    })
    assert events == []


def test_full_mode_records_tool_event(tmp_path):
    events = run_cold_hook(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Read",
    }, mode="full")
    assert events[0]["event_type"] == "tool_execution"
