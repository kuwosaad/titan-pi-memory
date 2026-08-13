import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "integrations" / "claude_titan_plugin" / "scripts" / "titan_claude_hook.py"

spec = importlib.util.spec_from_file_location("titan_claude_hook", HOOK_PATH)
titan_claude_hook = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(titan_claude_hook)


class ClaudeHookTests(unittest.TestCase):
    def _run_hook(self, tmp_dir: Path, payload: dict) -> list[dict]:
        with patch.dict(os.environ, {"TITAN_SPOOL_DIR": str(tmp_dir)}, clear=False):
            code = titan_claude_hook.main(io.StringIO(json.dumps(payload)), io.StringIO())
        self.assertEqual(code, 0)
        target = tmp_dir / f"{payload.get('session_id', 'default')}.jsonl"
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]

    def test_session_start_creates_trace_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(
                Path(tmp),
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "s1",
                    "cwd": "/repo",
                    "transcript_path": "/tmp/transcript.jsonl",
                    "model": "claude-sonnet",
                },
            )

        self.assertEqual(events[0]["event_type"], "session_created")
        self.assertEqual(events[0]["payload"]["cwd"], "/repo")
        self.assertEqual(events[0]["schema_version"], "v1")

    def test_user_prompt_submit_writes_user_message_and_redacts_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(
                Path(tmp),
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "s1",
                    "prompt": "use OPENAI_API_KEY=sk-supersecretvalue12345 for this",
                },
            )

        self.assertEqual(events[0]["event_type"], "user_message")
        self.assertIn("OPENAI_API_KEY=[REDACTED]", events[0]["payload"]["content"])
        self.assertNotIn("sk-supersecret", events[0]["payload"]["content"])

    def test_post_tool_use_writes_compacted_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(
                Path(tmp),
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "s1",
                    "tool_name": "Read",
                    "tool_use_id": "call-1",
                    "tool_input": {"file_path": "README.md"},
                    "tool_response": "x" * 1500,
                },
            )

        payload = events[0]["payload"]
        self.assertEqual(events[0]["event_type"], "tool_execution")
        self.assertEqual(payload["tool"], "Read")
        self.assertEqual(payload["call_id"], "call-1")
        self.assertLessEqual(len(payload["output"]), 1000)

    def test_stop_writes_assistant_message_and_turn_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(
                Path(tmp),
                {"hook_event_name": "Stop", "session_id": "s1", "assistant_message": "finished the work"},
            )

        self.assertEqual([event["event_type"] for event in events], ["assistant_message", "turn_complete"])

    def test_session_end_writes_session_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(
                Path(tmp),
                {"hook_event_name": "SessionEnd", "session_id": "s1", "reason": "exit"},
            )

        self.assertEqual(events[0]["event_type"], "session_closed")
        self.assertEqual(events[0]["payload"]["reason"], "exit")

    def test_unknown_fields_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), {"hook_event_name": "NewEvent", "session_id": "s1", "nested": {"ok": True}})

        self.assertEqual(events[0]["event_type"], "claude_hook_event")

    def test_hook_exits_zero_when_trace_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_a_dir = Path(tmp) / "trace-file"
            not_a_dir.write_text("already a file", encoding="utf-8")
            with patch.dict(os.environ, {"TITAN_SPOOL_DIR": str(not_a_dir)}, clear=False):
                code = titan_claude_hook.main(io.StringIO('{"hook_event_name":"SessionStart"}'), io.StringIO())

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
