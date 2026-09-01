import importlib
import json
import multiprocessing
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _write_json_from_process(path: str, payload: dict, barrier, errors) -> None:
    """Force two independent runtimes into the shared-temp-file window."""

    import app.storage.sessions as sessions_module

    original_replace = sessions_module.os.replace

    def synchronized_replace(source, target):
        barrier.wait(timeout=5)
        return original_replace(source, target)

    sessions_module.os.replace = synchronized_replace
    try:
        sessions_module.write_json(Path(path), payload)
    except Exception as exc:  # pragma: no cover - asserted by the parent
        errors.put(f"{type(exc).__name__}: {exc}")


def _set_pending_from_process(path: str, session_id: str, barrier, errors) -> None:
    """Make two pending-state read-modify-write calls overlap when unlocked."""

    import app.storage.traces as traces_module

    target = Path(path)
    traces_module.PENDING_USER_MESSAGES_FILE = target
    traces_module.refresh_trace_paths = lambda: None
    traces_module.ensure_dirs = lambda: None
    original_write = traces_module.write_json

    def synchronized_write(write_path, payload):
        if Path(write_path) == target:
            try:
                barrier.wait(timeout=1)
            except Exception:
                # With the fixed implementation the second process cannot
                # reach this point until the first has completed its update.
                pass
        return original_write(write_path, payload)

    traces_module.write_json = synchronized_write
    try:
        traces_module.set_pending_user_message(session_id, f"content-{session_id}")
    except Exception as exc:  # pragma: no cover - asserted by the parent
        errors.put(f"{type(exc).__name__}: {exc}")


class StorageDirTests(unittest.TestCase):
    def test_ensure_dirs_creates_missing_parents(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "missing" / "titan-home"
            with patch.dict(os.environ, {"TITAN_BASE_DIR": str(base_dir)}, clear=False):
                import app.storage.sessions as sessions_module

                importlib.reload(sessions_module)
                sessions_module.ensure_dirs()

                self.assertTrue((base_dir / "out").exists())
                self.assertTrue((base_dir / "out" / "sessions").exists())
                self.assertTrue((base_dir / "out" / "memories").exists())
                self.assertTrue((base_dir / "out" / "traces").exists())
                self.assertTrue((base_dir / "out" / "graphs").exists())

    def test_write_json_is_safe_when_hook_and_mcp_write_same_file(self):
        """Separate hook/MCP processes must not share one temporary pathname."""

        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires process isolation via fork")

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "pending_user_messages.json"
            context = multiprocessing.get_context("fork")
            barrier = context.Barrier(2)
            errors = context.Queue()
            processes = [
                context.Process(
                    target=_write_json_from_process,
                    args=(str(target), {"session-a": {"content": "one"}}, barrier, errors),
                ),
                context.Process(
                    target=_write_json_from_process,
                    args=(str(target), {"session-b": {"content": "two"}}, barrier, errors),
                ),
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)

            self.assertTrue(all(not process.is_alive() for process in processes))
            self.assertEqual([process.exitcode for process in processes], [0, 0])
            reported_errors = []
            while True:
                try:
                    reported_errors.append(errors.get_nowait())
                except queue.Empty:
                    break
            self.assertEqual(reported_errors, [])
            self.assertIn(json.loads(target.read_text(encoding="utf-8")), [
                {"session-a": {"content": "one"}},
                {"session-b": {"content": "two"}},
            ])

    def test_pending_updates_from_two_mcp_processes_are_merged(self):
        """A second MCP process must not erase the first process's session."""

        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires process isolation via fork")

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "pending_user_messages.json"
            context = multiprocessing.get_context("fork")
            barrier = context.Barrier(2)
            errors = context.Queue()
            processes = [
                context.Process(target=_set_pending_from_process, args=(str(target), "session-a", barrier, errors)),
                context.Process(target=_set_pending_from_process, args=(str(target), "session-b", barrier, errors)),
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=5)

            self.assertTrue(all(not process.is_alive() for process in processes))
            self.assertEqual([process.exitcode for process in processes], [0, 0])
            reported_errors = []
            while True:
                try:
                    reported_errors.append(errors.get_nowait())
                except queue.Empty:
                    break
            self.assertEqual(reported_errors, [])
            self.assertEqual(
                set(json.loads(target.read_text(encoding="utf-8"))),
                {"session-a", "session-b"},
            )


if __name__ == "__main__":
    unittest.main()
