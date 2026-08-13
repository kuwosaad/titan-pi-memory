import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT_DIR / "integrations" / "codex_titan_plugin" / "scripts" / "titan_codex_hook.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("titan_codex_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


hook = _load_hook_module()
SUPPORTED_HOOK_STDOUT = {"continue": True}


class CodexHookTests(unittest.TestCase):
    def _run_hook(self, payload: dict, trace_dir: Path) -> tuple[str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO(json.dumps(payload))
        with patch.dict(hook.os.environ, {"TITAN_SPOOL_DIR": str(trace_dir), "TITAN_AGENT_NAME": "codex"}, clear=False):
            result = hook.run(stdin=stdin, stdout=stdout, stderr=stderr)
        self.assertEqual(result, 0)
        return stdout.getvalue(), stderr.getvalue()

    def _read_records(self, trace_dir: Path, session_id: str) -> list[dict]:
        path = trace_dir / f"{session_id}.jsonl"
        self.assertTrue(path.exists())
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def _assert_supported_stdout(self, stdout: str) -> None:
        payload = json.loads(stdout)
        self.assertEqual(payload, SUPPORTED_HOOK_STDOUT)
        self.assertNotIn("suppressOutput", payload)

    def test_supported_codex_events_return_supported_stdout_contract(self):
        event_payloads = [
            {"hook_event_name": "SessionStart", "session_id": "session-start"},
            {"hook_event_name": "UserPromptSubmit", "session_id": "user-prompt", "prompt": "hello"},
            {"hook_event_name": "PostToolUse", "session_id": "post-tool", "tool_name": "bash"},
            {"hook_event_name": "PostCompact", "session_id": "post-compact", "trigger": "manual"},
            {"hook_event_name": "Stop", "session_id": "stop", "last_assistant_message": "done"},
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            for payload in event_payloads:
                with self.subTest(event=payload["hook_event_name"]):
                    stdout, stderr = self._run_hook(payload, trace_dir)

                self.assertEqual(stderr, "")
                self._assert_supported_stdout(stdout)

    def test_session_start_payload_creates_jsonl_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            stdout, stderr = self._run_hook(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "codex-session-1",
                    "cwd": str(ROOT_DIR),
                    "transcript_path": "/tmp/transcript.jsonl",
                    "model": "gpt-5.5",
                },
                trace_dir,
            )
            records = self._read_records(trace_dir, "codex-session-1")

        self.assertEqual(stderr, "")
        self._assert_supported_stdout(stdout)
        self.assertEqual(records[0]["event_type"], "session_created")
        self.assertEqual(records[0]["payload"]["source"], "codex")

    def test_user_prompt_submit_redacts_obvious_secrets(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            self._run_hook(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "secret-session",
                    "prompt": "OPENAI_API_KEY=sk-thisshouldnotbevisible123456789 Authorization: Bearer ghp_thisshouldnotbevisible123456789",
                },
                trace_dir,
            )
            records = self._read_records(trace_dir, "secret-session")

        rendered = json.dumps(records)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotIn("thisshouldnotbevisible", rendered)
        self.assertEqual(records[0]["payload"]["content"].split()[0], "OPENAI_API_KEY=[REDACTED]")

    def test_user_prompt_submit_redacts_bare_token_shaped_secrets(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            self._run_hook(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "bare-secret-session",
                    "prompt": "tokens sk-thisshouldnotbevisible123456789 ghp_thisshouldnotbevisible123456789 github_pat_thisshouldnotbevisible123456789 AIzathisshouldnotbevisible123456789",
                },
                trace_dir,
            )
            records = self._read_records(trace_dir, "bare-secret-session")

        rendered = json.dumps(records)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotIn("thisshouldnotbevisible", rendered)

    def test_post_tool_use_compacts_large_outputs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            self._run_hook(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "tool-session",
                    "tool_name": "bash",
                    "tool_use_id": "tool-1",
                    "tool_input": {"command": "pytest"},
                    "tool_response": "x" * 3000,
                },
                trace_dir,
            )
            records = self._read_records(trace_dir, "tool-session")

        self.assertEqual(records[0]["event_type"], "tool_execution")
        self.assertEqual(records[0]["payload"]["tool"], "bash")
        self.assertEqual(records[0]["payload"]["call_id"], "tool-1")
        self.assertEqual(records[0]["payload"]["args"], {"command": "pytest"})
        self.assertIn("[TRUNCATED]", records[0]["payload"]["output"])
        self.assertLess(len(records[0]["payload"]["output"]), 1300)

    def test_stop_writes_assistant_message_and_turn_end(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_dir = Path(tmp_dir)
            stdout, _stderr = self._run_hook(
                {
                    "hook_event_name": "Stop",
                    "session_id": "stop-session",
                    "turn_id": "turn-1",
                    "last_assistant_message": "done",
                },
                trace_dir,
            )
            records = self._read_records(trace_dir, "stop-session")

        self._assert_supported_stdout(stdout)
        self.assertEqual([record["event_type"] for record in records], ["assistant_message", "turn_complete"])
        self.assertEqual(records[0]["payload"]["content"], "done")

    def test_invalid_input_does_not_crash_and_stdout_is_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.dict(hook.os.environ, {"TITAN_SPOOL_DIR": tmp_dir}, clear=False):
                result = hook.run(stdin=io.StringIO("not-json"), stdout=stdout, stderr=stderr)

        self.assertEqual(result, 0)
        self._assert_supported_stdout(stdout.getvalue())
        self.assertIn("capture skipped", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
