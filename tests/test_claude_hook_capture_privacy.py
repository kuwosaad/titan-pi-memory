import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "integrations" / "claude_titan_plugin" / "scripts" / "titan_claude_hook.py"
spec = importlib.util.spec_from_file_location("titan_claude_hook_capture", HOOK_PATH)
hook = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hook)


class ClaudeHookCapturePrivacyTests(unittest.TestCase):
    def test_direct_defaults_match_manifest_safety_defaults(self):
        self.assertEqual(hook.capture_mode({}), "messages")
        self.assertFalse(hook.capture_tool_io({}))
        self.assertEqual(
            hook.capture_mode({
                "CLAUDE_PLUGIN_OPTION_CAPTURE_MODE": "off",
                "CLAUDE_PLUGIN_OPTION_capture_mode": "full",
            }),
            "off",
        )
        self.assertEqual(hook.capture_mode({"CLAUDE_PLUGIN_OPTION_capture_mode": "full"}), "full")
        self.assertEqual(
            hook.build_trace_events(
                {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Read"}
            ),
            [],
        )

    def test_current_stop_contract_captures_last_assistant_message(self):
        events = hook.build_trace_events(
            {
                "hook_event_name": "Stop",
                "session_id": "s1",
                "last_assistant_message": "Implemented the fix and ran 27 tests.",
                "stop_hook_active": False,
                "transcript_path": "/tmp/session.jsonl",
                "cwd": "/repo",
            }
        )

        self.assertEqual([event["event_type"] for event in events], ["assistant_message", "turn_complete"])
        payload = events[0]["payload"]
        self.assertEqual(payload["content"], "Implemented the fix and ran 27 tests.")
        self.assertTrue(payload["content_complete"])
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["transcript_path"], "/tmp/session.jsonl")

    def test_post_tool_failure_is_a_failed_tool_execution(self):
        events = hook.build_trace_events(
            {
                "hook_event_name": "PostToolUseFailure",
                "session_id": "s1",
                "tool_name": "Bash",
                "tool_use_id": "call-1",
                "tool_input": {"command": "false"},
                "error": "exit 1",
            },
            mode="full",
        )

        self.assertEqual(events[0]["event_type"], "tool_execution")
        self.assertTrue(events[0]["payload"]["failed"])
        self.assertEqual(events[0]["payload"]["error"], "exit 1")

    def test_subagent_stop_preserves_parent_and_subagent_metadata(self):
        events = hook.build_trace_events(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "child-session",
                "parent_session_id": "parent-session",
                "agent_id": "worker-1",
                "agent_type": "Explore",
                "last_assistant_message": "Found the integration path.",
            }
        )

        self.assertEqual([event["event_type"] for event in events], ["tool_execution"])
        payload = events[0]["payload"]
        self.assertEqual(payload["tool"], "Subagent")
        self.assertEqual(payload["parent_session_id"], "parent-session")
        self.assertEqual(payload["agent_id"], "worker-1")
        self.assertEqual(payload["agent_type"], "Explore")
        self.assertEqual(payload["subagent_id"], "worker-1")
        self.assertEqual(payload["subagent_type"], "Explore")
        self.assertTrue(payload["content_omitted"])
        self.assertNotIn("output", payload)

    def test_subagent_output_requires_full_mode_and_tool_io_opt_in(self):
        event = {
            "hook_event_name": "SubagentStop",
            "session_id": "child-session",
            "agent_id": "worker-1",
            "last_assistant_message": "private worker output",
        }
        without_io = hook.build_trace_events(event, mode="full", include_tool_io=False)[0]["payload"]
        with_io = hook.build_trace_events(event, mode="full", include_tool_io=True)[0]["payload"]

        self.assertNotIn("output", without_io)
        self.assertEqual(with_io["output"], "private worker output")

    def test_structured_secrets_are_redacted_before_serialization(self):
        events = hook.build_trace_events(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "tool_name": "HTTP",
                "tool_input": {
                    "api_key": "abc123456789",
                    "headers": {"Authorization": "Bearer supersecrettoken123"},
                    "password": "hunter2",
                },
                "tool_response": {"cookie": "session=private", "ok": True},
            },
            mode="full",
            include_tool_io=True,
        )

        serialized = json.dumps(events)
        self.assertNotIn("abc123456789", serialized)
        self.assertNotIn("supersecrettoken123", serialized)
        self.assertNotIn("hunter2", serialized)
        self.assertNotIn("session=private", serialized)
        self.assertIn("REDACTED", serialized)

    def test_tool_io_can_be_disabled_without_losing_metadata(self):
        events = hook.build_trace_events(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "tool_name": "Read",
                "tool_use_id": "call-1",
                "tool_input": {"file_path": "secret.txt"},
                "tool_response": "contents",
            },
            mode="full",
            include_tool_io=False,
        )

        payload = events[0]["payload"]
        self.assertEqual(payload["tool"], "Read")
        self.assertEqual(payload["call_id"], "call-1")
        self.assertNotIn("args", payload)
        self.assertNotIn("output", payload)

    def test_message_mode_skips_tools_and_metadata_mode_omits_content(self):
        tool_events = hook.build_trace_events(
            {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Read"},
            mode="messages",
        )
        message_events = hook.build_trace_events(
            {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "private message"},
            mode="metadata",
        )

        self.assertEqual(tool_events, [])
        self.assertTrue(message_events[0]["payload"]["content_omitted"])
        self.assertNotIn("content", message_events[0]["payload"])

    def test_message_text_preserves_formatting_and_has_defensive_cap(self):
        formatted = "line one\n```python\nprint('ok')\n```"
        normal = hook.build_trace_events(
            {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": formatted}
        )[0]["payload"]
        large = hook.build_trace_events(
            {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "é" * 40000}
        )[0]["payload"]

        self.assertEqual(normal["content"], formatted)
        self.assertTrue(normal["content_complete"])
        self.assertLessEqual(len(large["content"].encode("utf-8")), hook.MESSAGE_LIMIT_BYTES)
        self.assertTrue(large["truncated"])
        self.assertFalse(large["content_complete"])

    def test_do_not_remember_suppresses_prompt_and_matching_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TITAN_SPOOL_DIR": tmp, "TITAN_AGENT_NAME": "claude-code"}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(
                    hook.main(
                        io.StringIO(json.dumps({
                            "hook_event_name": "UserPromptSubmit",
                            "session_id": "s1",
                            "prompt": "Do not remember this: my temporary note",
                        })),
                        io.StringIO(),
                    ),
                    0,
                )
                self.assertEqual(
                    hook.main(
                        io.StringIO(json.dumps({
                            "hook_event_name": "Stop",
                            "session_id": "s1",
                            "last_assistant_message": "Acknowledged.",
                        })),
                        io.StringIO(),
                    ),
                    0,
                )

            target = Path(tmp) / "s1.jsonl"
            records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["event_type"] for record in records], ["turn_complete"])
            self.assertTrue(records[0]["payload"]["capture_suppressed"])
            serialized = target.read_text(encoding="utf-8")
            self.assertNotIn("temporary note", serialized)
            self.assertNotIn("Acknowledged", serialized)

    def test_excluded_project_and_off_mode_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_env = {"TITAN_SPOOL_DIR": tmp, "TITAN_AGENT_NAME": "claude-code"}
            with patch.dict(
                os.environ,
                {**base_env, "CLAUDE_PLUGIN_OPTION_EXCLUDED_PROJECTS": "/private/**"},
                clear=False,
            ):
                hook.main(
                    io.StringIO(json.dumps({
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "excluded",
                        "cwd": "/private/project",
                        "prompt": "secret",
                    })),
                    io.StringIO(),
                )
            with patch.dict(
                os.environ,
                {**base_env, "CLAUDE_PLUGIN_OPTION_CAPTURE_MODE": "off"},
                clear=False,
            ):
                hook.main(
                    io.StringIO(json.dumps({
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "off",
                        "prompt": "secret",
                    })),
                    io.StringIO(),
                )

            self.assertFalse((Path(tmp) / "excluded.jsonl").exists())
            self.assertFalse((Path(tmp) / "off.jsonl").exists())

    def test_concurrent_hook_processes_leave_complete_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "TITAN_SPOOL_DIR": tmp, "TITAN_AGENT_NAME": "claude-code"}
            processes = []
            for index in range(12):
                payload = json.dumps({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "shared",
                    "prompt": f"message {index}",
                })
                processes.append(
                    subprocess.Popen(
                        [sys.executable, str(HOOK_PATH)],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=env,
                    )
                )
                processes[-1].communicate(payload, timeout=10)

            self.assertTrue(all(process.returncode == 0 for process in processes))
            lines = (Path(tmp) / "shared.jsonl").read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            self.assertEqual(len(records), 12)
            self.assertEqual(len({record["event_id"] for record in records}), 12)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not authoritative on Windows")
    def test_trace_directory_and_files_are_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_dir = Path(tmp) / "traces"
            hook.append_trace_events(
                hook.build_trace_events({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "s1",
                    "prompt": "hello",
                }),
                trace_dir,
            )

            self.assertEqual(stat.S_IMODE(trace_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((trace_dir / "s1.jsonl").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((trace_dir / ".claude-hook.lock").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
