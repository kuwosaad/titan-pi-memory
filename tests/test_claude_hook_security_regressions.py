import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "integrations" / "claude_titan_plugin"
HOOK_PATH = PLUGIN_ROOT / "scripts" / "titan_claude_hook.py"
spec = importlib.util.spec_from_file_location("titan_claude_hook_security", HOOK_PATH)
hook = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hook)


class ClaudeHookSecurityRegressionTests(unittest.TestCase):
    def test_direct_hook_agent_names_cannot_escape_namespace(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(Path, "home", return_value=Path(tmp)):
            root = Path(tmp) / ".titan" / "agents"
            for name in ("../../outside", "/absolute", "C:\\Users\\other", "claude-code"):
                with self.subTest(name=name):
                    resolved = hook.resolve_trace_dir(name, {"UNRELATED": "1"})
                    self.assertTrue(resolved.is_relative_to(root))
                    self.assertEqual(len(resolved.relative_to(root).parts), 2)
            self.assertEqual(hook.resolve_agent_name({"TITAN_AGENT_NAME": "../../outside"}), "outside")

    def test_retention_prunes_only_adapter_fallback_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            titan_traces = root / "titan" / "traces"
            titan_traces.mkdir(parents=True)
            core_files = [titan_traces / "events.jsonl", titan_traces / "retry_queue.jsonl"]
            for path in core_files:
                path.write_text("{}\n", encoding="utf-8")
                os.utime(path, (1, 1))

            env = {"CLAUDE_PLUGIN_DATA": str(root / "plugin"), "CLAUDE_PLUGIN_OPTION_RETENTION_DAYS": "1"}
            fallback = hook._adapter_retention_dir("claude-code", env)
            assert fallback is not None
            fallback.mkdir(parents=True)
            old_adapter_file = fallback / "old.jsonl"
            old_adapter_file.write_text("{}\n", encoding="utf-8")
            os.utime(old_adapter_file, (1, 1))

            hook._prune_expired_traces(fallback, env)

            self.assertFalse(old_adapter_file.exists())
            self.assertTrue(all(path.exists() for path in core_files))

    def test_subagent_stop_does_not_consume_do_not_remember_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TITAN_SPOOL_DIR": tmp, "TITAN_AGENT_NAME": "claude-code"}
            payloads = [
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "s1",
                    "prompt": "Do not remember this: private work",
                },
                {
                    "hook_event_name": "SubagentStop",
                    "session_id": "s1",
                    "last_assistant_message": "private subagent result",
                },
                {
                    "hook_event_name": "Stop",
                    "session_id": "s1",
                    "last_assistant_message": "private parent result",
                },
            ]
            with patch.dict(os.environ, env, clear=False):
                for payload in payloads:
                    self.assertEqual(hook.main(io.StringIO(json.dumps(payload)), io.StringIO()), 0)

            target = Path(tmp) / "s1.jsonl"
            records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["event_type"] for record in records], ["turn_complete"])
            serialized = target.read_text(encoding="utf-8")
            self.assertNotIn("private subagent", serialized)
            self.assertNotIn("private parent", serialized)

    @unittest.skipIf(os.name == "nt", "symlink creation requires platform-specific privileges on Windows")
    def test_excluded_project_resolves_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            excluded = root / "excluded"
            project = excluded / "project"
            project.mkdir(parents=True)
            alias = root / "alias"
            alias.symlink_to(excluded, target_is_directory=True)
            env = {"CLAUDE_PLUGIN_OPTION_EXCLUDED_PROJECTS": str(excluded / "**")}

            self.assertTrue(hook._project_excluded({"cwd": str(alias / "project")}, env))

    def test_session_start_uses_existing_owner_before_starting_one(self):
        calls = []

        def recall_context(query, **kwargs):
            calls.append((query, kwargs))
            return {
                "ok": True,
                "brief": "- [source:pi scene:s1 status:verified] remembered decision",
            }

        env = {
            "CLAUDE_PLUGIN_DATA": "/tmp/plugin-data",
            "CLAUDE_PLUGIN_OPTION_RECALL_MODE": "automatic",
            "CLAUDE_PLUGIN_OPTION_RECALL_TIMEOUT_MS": "1000",
        }
        with patch.object(hook, "_runtime_available", return_value=True), patch.object(
            hook, "_import_runtime", return_value=(recall_context, object())
        ):
            context = hook.automatic_recall(
                {"hook_event_name": "SessionStart", "session_id": "s1"},
                agent_name="claude-code",
                env=env,
            )

        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][1]["session_id"])
        self.assertIn("untrusted historical evidence", context)
        self.assertIn("remembered decision", context)

    def test_new_normal_prompt_clears_stale_suppression_from_interrupted_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TITAN_SPOOL_DIR": tmp, "TITAN_AGENT_NAME": "claude-code"}
            payloads = [
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "s1",
                    "prompt": "Do not remember this: interrupted private work",
                },
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "s1",
                    "prompt": "This normal turn should be remembered.",
                },
                {
                    "hook_event_name": "Stop",
                    "session_id": "s1",
                    "last_assistant_message": "Normal public answer.",
                },
            ]
            with patch.dict(os.environ, env, clear=False):
                for payload in payloads:
                    self.assertEqual(hook.main(io.StringIO(json.dumps(payload)), io.StringIO()), 0)

            target = Path(tmp) / "s1.jsonl"
            records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [record["event_type"] for record in records],
                ["user_message", "assistant_message", "turn_complete"],
            )
            serialized = target.read_text(encoding="utf-8")
            self.assertNotIn("interrupted private", serialized)
            self.assertIn("Normal public answer", serialized)

    def test_subagent_evidence_does_not_consume_parent_assistant_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TITAN_SPOOL_DIR": tmp, "TITAN_AGENT_NAME": "claude-code"}
            payloads = [
                {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "Parent request"},
                {
                    "hook_event_name": "SubagentStop",
                    "session_id": "s1",
                    "agent_id": "worker-1",
                    "agent_type": "Explore",
                    "last_assistant_message": "Worker evidence",
                },
                {
                    "hook_event_name": "Stop",
                    "session_id": "s1",
                    "last_assistant_message": "Parent final answer",
                },
            ]
            with patch.dict(os.environ, env, clear=False):
                for payload in payloads:
                    self.assertEqual(hook.main(io.StringIO(json.dumps(payload)), io.StringIO()), 0)

            records = [
                json.loads(line)
                for line in (Path(tmp) / "s1.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [record["event_type"] for record in records],
                ["user_message", "tool_execution", "assistant_message", "turn_complete"],
            )
            self.assertEqual(records[1]["payload"]["tool"], "Subagent")
            self.assertEqual(records[2]["payload"]["content"], "Parent final answer")

    def test_retention_scan_is_throttled_to_once_per_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "runtime" / "claude-code" / "fallback"
            fallback.mkdir(parents=True)
            env = {"CLAUDE_PLUGIN_OPTION_RETENTION_DAYS": "1"}
            first = fallback / "first.jsonl"
            first.write_text("{}\n", encoding="utf-8")
            os.utime(first, (1, 1))

            hook._prune_expired_traces(fallback, env, now=200000.0)
            self.assertFalse(first.exists())

            second = fallback / "second.jsonl"
            second.write_text("{}\n", encoding="utf-8")
            os.utime(second, (1, 1))
            hook._prune_expired_traces(fallback, env, now=200100.0)
            self.assertTrue(second.exists())

            hook._prune_expired_traces(
                fallback,
                env,
                now=200000.0 + hook.RETENTION_PRUNE_INTERVAL_SECONDS + 1,
            )
            self.assertFalse(second.exists())
            self.assertTrue((fallback.parent / ".retention-last-pruned").is_file())


if __name__ == "__main__":
    unittest.main()
