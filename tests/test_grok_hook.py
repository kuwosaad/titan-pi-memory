import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "integrations" / "grok_titan_plugin" / "scripts" / "titan_grok_hook.py"

spec = importlib.util.spec_from_file_location("titan_grok_hook", HOOK_PATH)
titan_grok_hook = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(titan_grok_hook)


class GrokHookTests(unittest.TestCase):
    def _run_hook(self, tmp_dir: Path, payload: dict, extra_env: dict | None = None) -> tuple[list[dict], str, int]:
        env = {"TITAN_SPOOL_DIR": str(tmp_dir)}
        if extra_env:
            env.update(extra_env)
        stdout = io.StringIO()
        with patch.dict(os.environ, env, clear=False):
            code = titan_grok_hook.main(io.StringIO(json.dumps(payload)), stdout)
        session_id = (
            payload.get("sessionId")
            or payload.get("session_id")
            or "default"
        )
        target = tmp_dir / f"{session_id}.jsonl"
        events = []
        if target.exists():
            events = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
        return events, stdout.getvalue(), code

    def test_grok_camelcase_session_start_writes_session_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, stdout, code = self._run_hook(
                Path(tmp),
                {
                    "hookEventName": "session_start",
                    "sessionId": "gs1",
                    "cwd": "/repo",
                    "workspaceRoot": "/repo",
                },
            )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"continue": True})
        self.assertEqual(events[0]["event_type"], "session_created")
        self.assertEqual(events[0]["payload"]["source"], "grok")
        self.assertEqual(events[0]["payload"]["raw_type"], "session_start")
        self.assertEqual(events[0]["payload"]["cwd"], "/repo")
        self.assertEqual(events[0]["schema_version"], "v1")
        self.assertEqual(events[0]["session_id"], "gs1")

    def test_grok_user_prompt_submit_writes_user_message_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {
                    "hookEventName": "user_prompt_submit",
                    "sessionId": "gs1",
                    "prompt": "use OPENAI_API_KEY=sk-supersecretvalue12345 and ghp_secrettokenabc1234567890",
                },
            )

        self.assertEqual(code, 0)
        self.assertEqual(events[0]["event_type"], "user_message")
        content = events[0]["payload"]["content"]
        self.assertIn("OPENAI_API_KEY=[REDACTED]", content)
        self.assertNotIn("sk-supersecret", content)
        self.assertNotIn("ghp_secrettoken", content)
        self.assertEqual(events[0]["payload"]["source"], "grok")

    def test_grok_post_tool_use_camelcase_writes_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {
                    "hookEventName": "post_tool_use",
                    "sessionId": "gs1",
                    "toolName": "Read",
                    "toolInput": {"file_path": "README.md"},
                    "toolResult": "x" * 1500,
                },
            )

        self.assertEqual(code, 0)
        payload = events[0]["payload"]
        self.assertEqual(events[0]["event_type"], "tool_execution")
        self.assertEqual(payload["tool"], "Read")
        self.assertLessEqual(len(payload["output"]), 1000)
        self.assertEqual(payload["source"], "grok")
        self.assertEqual(payload["raw_type"], "post_tool_use")

    def test_grok_stop_with_last_assistant_message_writes_assistant_and_turn_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {
                    "hookEventName": "stop",
                    "sessionId": "gs1",
                    "lastAssistantMessage": "finished the work",
                },
            )

        self.assertEqual(code, 0)
        self.assertEqual([event["event_type"] for event in events], ["assistant_message", "turn_complete"])
        self.assertEqual(events[0]["payload"]["content"], "finished the work")
        self.assertEqual(events[0]["payload"]["source"], "grok")

    def test_grok_session_end_writes_session_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {
                    "hookEventName": "session_end",
                    "sessionId": "gs1",
                    "reason": "user_exit",
                },
            )

        self.assertEqual(code, 0)
        self.assertEqual(events[0]["event_type"], "session_closed")
        self.assertEqual(events[0]["payload"]["reason"], "user_exit")
        self.assertEqual(events[0]["payload"]["source"], "grok")

    def test_claude_style_snake_case_session_start_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "claude-style",
                    "cwd": "/repo",
                },
            )

        self.assertEqual(code, 0)
        self.assertEqual(events[0]["event_type"], "session_created")
        self.assertEqual(events[0]["session_id"], "claude-style")
        self.assertEqual(events[0]["payload"]["cwd"], "/repo")

    def test_titan_spool_dir_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            spool = Path(tmp) / "custom-spool"
            events, _, code = self._run_hook(
                spool,
                {
                    "hookEventName": "SessionStart",
                    "sessionId": "spool-check",
                },
            )
            self.assertEqual(code, 0)
            self.assertTrue((spool / "spool-check.jsonl").exists())
            self.assertEqual(events[0]["event_type"], "session_created")

    def test_stdout_is_continue_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, stdout, code = self._run_hook(
                Path(tmp),
                {"hookEventName": "session_start", "sessionId": "out"},
            )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.strip()), {"continue": True})

    def test_hook_never_raises_always_exits_zero_on_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with patch.dict(os.environ, {"TITAN_SPOOL_DIR": str(tmp)}, clear=False):
                code = titan_grok_hook.main(io.StringIO("not-json{{{"), stdout)

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"continue": True})

    def test_default_agent_path_is_grok_traces_when_no_spool_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            # Ensure TITAN_SPOOL_DIR is unset for this call.
            env = {k: v for k, v in os.environ.items() if k != "TITAN_SPOOL_DIR"}
            env["HOME"] = str(home)
            # Drop agent overrides so default "grok" is used.
            env.pop("TITAN_AGENT_NAME", None)
            env.pop("GROK_PLUGIN_OPTION_agent_name", None)

            stdout = io.StringIO()
            with patch.dict(os.environ, env, clear=True):
                # Path.home() reads HOME on Unix.
                code = titan_grok_hook.main(
                    io.StringIO(
                        json.dumps(
                            {
                                "hookEventName": "session_start",
                                "sessionId": "default-path",
                            }
                        )
                    ),
                    stdout,
                )

            expected = home / ".titan" / "agents" / "grok" / "traces" / "default-path.jsonl"
            self.assertEqual(code, 0)
            self.assertTrue(expected.exists(), f"missing {expected}")
            records = [json.loads(line) for line in expected.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["event_type"], "session_created")
            self.assertEqual(records[0]["payload"]["source"], "grok")

    def test_post_tool_use_failure_maps_to_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {
                    "hookEventName": "PostToolUseFailure",
                    "sessionId": "fail1",
                    "toolName": "Bash",
                    "toolInput": {"command": "false"},
                    "error": "exit 1",
                },
            )

        self.assertEqual(code, 0)
        self.assertEqual(events[0]["event_type"], "tool_execution")
        self.assertEqual(events[0]["payload"]["tool"], "Bash")
        self.assertIn("exit 1", events[0]["payload"]["error"])

    def test_pre_and_post_compact_map_to_session_compacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("pre_compact", "PostCompact"):
                events, _, code = self._run_hook(
                    Path(tmp),
                    {
                        "hookEventName": name,
                        "sessionId": f"c-{name}",
                        "trigger": "auto",
                    },
                )
                self.assertEqual(code, 0)
                self.assertEqual(events[0]["event_type"], "session_compacted")
                self.assertEqual(events[0]["payload"]["raw_type"], name)

    def test_unknown_event_writes_grok_hook_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, _, code = self._run_hook(
                Path(tmp),
                {"hookEventName": "WeirdNewThing", "sessionId": "u1", "nested": {"ok": True}},
            )

        self.assertEqual(code, 0)
        self.assertEqual(events[0]["event_type"], "grok_hook_event")
        self.assertEqual(events[0]["payload"]["raw_type"], "WeirdNewThing")

    def test_resolve_agent_name_prefers_env(self):
        with patch.dict(os.environ, {"TITAN_AGENT_NAME": "custom-agent"}, clear=False):
            self.assertEqual(titan_grok_hook.resolve_agent_name(), "custom-agent")
        with patch.dict(
            os.environ,
            {"GROK_PLUGIN_OPTION_agent_name": "from-plugin"},
            clear=False,
        ):
            # Clear TITAN_AGENT_NAME if present for isolation.
            env = {"GROK_PLUGIN_OPTION_agent_name": "from-plugin"}
            self.assertEqual(titan_grok_hook.resolve_agent_name(env), "from-plugin")
        self.assertEqual(titan_grok_hook.resolve_agent_name({}), "grok")


if __name__ == "__main__":
    unittest.main()
