import errno
import importlib
import json
import multiprocessing
import os
import queue
import tempfile
import time
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


def _record_locked_section(lock_path: str, events_path: str, ready) -> None:
    """Record a critical section from an independently spawned process."""

    import app.storage.sessions as sessions_module

    ready.wait(timeout=20)
    with sessions_module.interprocess_lock(Path(lock_path)):
        with open(events_path, "a", encoding="utf-8") as handle:
            handle.write("enter\n")
            handle.flush()
        time.sleep(0.1)
        with open(events_path, "a", encoding="utf-8") as handle:
            handle.write("exit\n")
            handle.flush()


class StorageDirTests(unittest.TestCase):
    def test_interprocess_lock_serializes_spawned_processes_on_all_platforms(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "ingest.lock"
            events_path = Path(tmp_dir) / "events.log"
            ready = context.Event()
            processes = [
                context.Process(target=_record_locked_section, args=(str(lock_path), str(events_path), ready)),
                context.Process(target=_record_locked_section, args=(str(lock_path), str(events_path), ready)),
            ]
            for process in processes:
                process.start()
            ready.set()
            for process in processes:
                process.join(timeout=20)

            self.assertTrue(all(not process.is_alive() for process in processes))
            self.assertEqual([process.exitcode for process in processes], [0, 0])
            self.assertEqual(events_path.read_text(encoding="utf-8").splitlines(), [
                "enter", "exit", "enter", "exit",
            ])

    def test_interprocess_lock_uses_windows_file_lock_when_fcntl_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            import app.storage.sessions as sessions_module

            calls = []

            class FakeMsvcrt:
                LK_NBLCK = 2
                LK_UNLCK = 0

                @staticmethod
                def locking(fd, mode, size):
                    calls.append((fd, mode, size))

            lock_path = Path(tmp_dir) / "nested" / "ingest.lock"
            with patch.object(sessions_module, "fcntl", None), patch.object(sessions_module, "msvcrt", FakeMsvcrt):
                with sessions_module.interprocess_lock(lock_path):
                    self.assertTrue(lock_path.exists())

            self.assertEqual([mode for _fd, mode, _size in calls], [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK])

    def test_windows_lock_propagates_non_contention_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            import app.storage.sessions as sessions_module

            class FakeMsvcrt:
                LK_NBLCK = 2
                LK_UNLCK = 0

                @staticmethod
                def locking(_fd, _mode, _size):
                    raise OSError(22, "invalid argument")

            lock_path = Path(tmp_dir) / "ingest.lock"
            with patch.object(sessions_module, "fcntl", None), patch.object(sessions_module, "msvcrt", FakeMsvcrt):
                with self.assertRaises(OSError):
                    with sessions_module.interprocess_lock(lock_path):
                        pass

    def test_windows_lock_retries_contention_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            import app.storage.sessions as sessions_module

            calls = []

            class FakeMsvcrt:
                LK_NBLCK = 2
                LK_UNLCK = 0

                @staticmethod
                def locking(_fd, mode, _size):
                    calls.append(mode)
                    if len(calls) == 1:
                        busy = OSError(errno.EINVAL, "lock is busy")
                        busy.winerror = 33
                        raise busy

            lock_path = Path(tmp_dir) / "ingest.lock"
            with patch.object(sessions_module, "fcntl", None), patch.object(sessions_module, "msvcrt", FakeMsvcrt), patch.object(
                sessions_module.time, "sleep"
            ) as sleep_mock:
                with sessions_module.interprocess_lock(lock_path):
                    pass

            self.assertEqual(calls, [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK])
            sleep_mock.assert_called_once_with(0.05)

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
