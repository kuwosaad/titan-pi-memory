import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cli.titan import (
    CLAUDE_AGENT_NAME,
    _CLAUDE_PLUGIN_RELEASE_FILES,
    _stop_verified_claude_runtime,
    ensure_claude_marketplace_snapshot,
    run_claude_purge,
)


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ClaudeDistributionSafetyTests(unittest.TestCase):
    def setUp(self):
        # Purge tests must never discover the developer's live plugin state.
        paths = patch("tools.cli.titan._claude_runtime_state_paths", return_value=[])
        paths.start()
        self.addCleanup(paths.stop)

    def test_marketplace_snapshot_copies_only_production_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "marketplace"
            for relative in _CLAUDE_PLUGIN_RELEASE_FILES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n" if path.suffix == ".json" else "safe\n", encoding="utf-8")
            polluted = (
                source / "node_modules" / "package" / "index.js",
                source / "runtime" / "__pycache__" / "cache.pyc",
                source / "daemon.log",
                source / "tests" / "secret-fixture.json",
                source / ".pytest_cache" / "state",
            )
            for path in polluted:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private", encoding="utf-8")

            with patch("tools.cli.titan.CLAUDE_PLUGIN_DIR", source):
                ok, detail = ensure_claude_marketplace_snapshot(target=target)

            self.assertTrue(ok, detail)
            snapshot = target / "plugins" / "titan-memory"
            for relative in _CLAUDE_PLUGIN_RELEASE_FILES:
                self.assertTrue((snapshot / relative).is_file(), relative)
            for path in polluted:
                self.assertFalse((snapshot / path.relative_to(source)).exists())
            actual = {
                path.relative_to(snapshot).as_posix()
                for path in snapshot.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, set(_CLAUDE_PLUGIN_RELEASE_FILES))

    def test_runtime_shutdown_refuses_identity_mismatch_without_signaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "daemon.json"
            state_path.write_text(json.dumps({
                "pid": 123,
                "port": 4567,
                "token": "secret",
                "owner_nonce": "expected",
                "started_at": 10.0,
            }), encoding="utf-8")
            with patch("tools.cli.titan._claude_runtime_state_paths", return_value=[state_path]), patch(
                "tools.cli.titan._process_alive", return_value=True
            ), patch(
                "urllib.request.urlopen",
                return_value=_Response({"ok": True, "pid": 123, "owner_nonce": "different", "started_at": 10.0}),
            ) as urlopen:
                ok, detail = _stop_verified_claude_runtime()

            self.assertFalse(ok)
            self.assertIn("does not match", detail)
            self.assertEqual(urlopen.call_count, 1)

    def test_runtime_shutdown_requires_authenticated_graceful_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "daemon.json"
            state_path.write_text(json.dumps({
                "pid": 123,
                "port": 4567,
                "token": "secret",
                "owner_nonce": "owner",
                "started_at": 10.0,
            }), encoding="utf-8")
            responses = [
                _Response({"ok": True, "pid": 123, "owner_nonce": "owner", "started_at": 10.0}),
                _Response({"ok": True}),
            ]
            with patch("tools.cli.titan._claude_runtime_state_paths", return_value=[state_path]), patch(
                "tools.cli.titan._process_alive", side_effect=[True, False, False]
            ), patch("urllib.request.urlopen", side_effect=responses) as urlopen:
                ok, detail = _stop_verified_claude_runtime(timeout=0.1)

            self.assertTrue(ok, detail)
            self.assertEqual(urlopen.call_count, 2)
            shutdown_request = urlopen.call_args_list[1].args[0]
            self.assertEqual(shutdown_request.full_url, "http://127.0.0.1:4567/shutdown")
            self.assertEqual(shutdown_request.get_method(), "POST")

    def test_purge_refuses_before_deletion_when_runtime_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_home = root / "agents" / CLAUDE_AGENT_NAME
            traces = agent_home / "traces"
            traces.mkdir(parents=True)
            with patch("tools.cli.titan.TITAN_HOME", root), patch(
                "tools.cli.titan.resolve_agent_titan_home", return_value=agent_home
            ), patch(
                "tools.cli.titan.resolve_effective_spool_dir", return_value=traces
            ), patch(
                "tools.cli.titan._stop_verified_claude_runtime",
                return_value=(False, "runtime active"),
            ), patch("tools.cli.titan.shutil.rmtree") as remove:
                result = run_claude_purge(apply=True, yes=True, traces_only=True)

            self.assertEqual(result, 1)
            remove.assert_not_called()

    def test_purge_removes_all_declared_runtime_targets(self):
        for traces_only in (True, False):
            with self.subTest(traces_only=traces_only), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                agent_home = root / "agents" / CLAUDE_AGENT_NAME
                traces = agent_home / "traces"
                traces.mkdir(parents=True)
                runtime = root / "plugin" / "runtime" / CLAUDE_AGENT_NAME
                fallback = runtime / "fallback"
                fallback.mkdir(parents=True)
                (fallback / "event.jsonl").write_text("{}\n", encoding="utf-8")
                with (
                    patch("tools.cli.titan.TITAN_HOME", root),
                    patch("tools.cli.titan.resolve_agent_titan_home", return_value=agent_home),
                    patch("tools.cli.titan.resolve_effective_spool_dir", return_value=traces),
                    patch("tools.cli.titan._claude_runtime_state_paths", return_value=[runtime / "state.json"]),
                    patch("tools.cli.titan._stop_verified_claude_runtime", return_value=(True, "stopped")),
                ):
                    self.assertEqual(run_claude_purge(apply=True, yes=True, traces_only=traces_only), 0)
                self.assertFalse(fallback.exists())
                self.assertFalse(traces.exists())
                self.assertEqual(runtime.exists(), traces_only)

    def test_purge_deletes_only_after_runtime_stop_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_home = root / "agents" / CLAUDE_AGENT_NAME
            traces = agent_home / "traces"
            traces.mkdir(parents=True)
            with patch("tools.cli.titan.TITAN_HOME", root), patch(
                "tools.cli.titan.resolve_agent_titan_home", return_value=agent_home
            ), patch(
                "tools.cli.titan.resolve_effective_spool_dir", return_value=traces
            ), patch(
                "tools.cli.titan._stop_verified_claude_runtime",
                return_value=(True, "stopped"),
            ) as stop, patch("tools.cli.titan.shutil.rmtree") as remove:
                result = run_claude_purge(apply=True, yes=True, traces_only=True)

            self.assertEqual(result, 0)
            stop.assert_called_once_with()
            remove.assert_called_once_with(traces.resolve())


if __name__ == "__main__":
    unittest.main()
