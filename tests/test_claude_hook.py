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
FIXTURES = ROOT / "tests" / "fixtures" / "claude_hooks"

spec = importlib.util.spec_from_file_location("titan_claude_hook", HOOK_PATH)
titan_claude_hook = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(titan_claude_hook)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ClaudeHookTests(unittest.TestCase):
    def _run_hook(self, tmp_dir: Path, payload: dict) -> list[dict]:
        with patch.dict(
            os.environ,
            {
                "TITAN_SPOOL_DIR": str(tmp_dir),
                "CLAUDE_PLUGIN_OPTION_CAPTURE_MODE": "full",
                "CLAUDE_PLUGIN_OPTION_CAPTURE_TOOL_IO": "true",
            },
            clear=False,
        ):
            code = titan_claude_hook.main(io.StringIO(json.dumps(payload)), io.StringIO())
        self.assertEqual(code, 0)
        target = tmp_dir / f"{payload.get('session_id', 'default')}.jsonl"
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]

    def test_current_claude_hook_fixtures_parse(self):
        expected_events = {
            "session_start": "SessionStart",
            "user_prompt_submit": "UserPromptSubmit",
            "post_tool_use": "PostToolUse",
            "post_tool_use_failure": "PostToolUseFailure",
            "post_compact": "PostCompact",
            "stop": "Stop",
            "subagent_stop": "SubagentStop",
            "session_end": "SessionEnd",
        }
        for name, event_name in expected_events.items():
            with self.subTest(fixture=name):
                payload = load_fixture(name)
                self.assertEqual(payload["hook_event_name"], event_name)
                self.assertEqual(payload["session_id"], "fixture-session")
                self.assertIn("transcript_path", payload)
                self.assertIn("cwd", payload)

    def test_session_start_creates_trace_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), load_fixture("session_start"))

        self.assertEqual(events[0]["event_type"], "session_created")
        self.assertEqual(events[0]["payload"]["cwd"], "/tmp/titan-project")
        self.assertEqual(events[0]["payload"]["transcript_path"], "/tmp/claude/fixture-session.jsonl")
        self.assertEqual(events[0]["schema_version"], "v1")

    def test_user_prompt_submit_writes_user_message_and_redacts_secret(self):
        payload = load_fixture("user_prompt_submit")
        payload["prompt"] = "use OPENAI_API_KEY=sk-supersecretvalue12345 for this"
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), payload)

        self.assertEqual(events[0]["event_type"], "user_message")
        self.assertIn("OPENAI_API_KEY=[REDACTED]", events[0]["payload"]["content"])
        self.assertNotIn("sk-supersecret", events[0]["payload"]["content"])

    def test_post_tool_use_writes_compacted_tool_execution(self):
        payload = load_fixture("post_tool_use")
        payload["tool_response"] = "x" * 1500
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), payload)

        event_payload = events[0]["payload"]
        self.assertEqual(events[0]["event_type"], "tool_execution")
        self.assertEqual(event_payload["tool"], "Read")
        self.assertEqual(event_payload["call_id"], "tool-read-1")
        self.assertLessEqual(len(event_payload["output"]), 1000)

    def test_post_tool_use_failure_writes_failed_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), load_fixture("post_tool_use_failure"))

        self.assertEqual([event["event_type"] for event in events], ["tool_execution"])
        self.assertEqual(events[0]["payload"]["raw_type"], "PostToolUseFailure")
        self.assertEqual(events[0]["payload"]["tool"], "Bash")
        self.assertIn("status 1", events[0]["payload"]["error"])

    def test_post_compact_writes_lifecycle_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), load_fixture("post_compact"))

        self.assertEqual([event["event_type"] for event in events], ["session_compacted"])
        self.assertEqual(events[0]["payload"]["raw_type"], "PostCompact")

    def test_stop_uses_current_last_assistant_message_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), load_fixture("stop"))

        self.assertEqual([event["event_type"] for event in events], ["assistant_message", "turn_complete"])
        self.assertEqual(
            events[0]["payload"]["content"],
            "The Claude adapter contract fixtures are complete.",
        )

    def test_subagent_stop_captures_final_response_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), load_fixture("subagent_stop"))

        self.assertEqual([event["event_type"] for event in events], ["tool_execution"])
        self.assertEqual(events[0]["payload"]["raw_type"], "SubagentStop")
        self.assertEqual(events[0]["payload"]["tool"], "Subagent")
        self.assertEqual(events[0]["payload"]["agent_id"], "agent-contract-1")
        self.assertEqual(events[0]["payload"]["agent_type"], "Explore")
        self.assertIn("current Claude Stop schema", events[0]["payload"]["output"])

    def test_session_end_writes_session_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = self._run_hook(Path(tmp), load_fixture("session_end"))

        self.assertEqual(events[0]["event_type"], "session_closed")
        self.assertEqual(events[0]["payload"]["reason"], "prompt_input_exit")

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
