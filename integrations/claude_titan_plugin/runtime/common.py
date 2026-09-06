from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from . import RUNTIME_VERSION

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


if os.name == "nt":  # pragma: no cover - Windows CI
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WINDOWS_PROCESS_MISSING_ERRORS = {6, 87, 1168}  # invalid handle/parameter, not found

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _open_process = _kernel32.OpenProcess
    _open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _open_process.restype = wintypes.HANDLE
    _get_exit_code_process = _kernel32.GetExitCodeProcess
    _get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _get_exit_code_process.restype = wintypes.BOOL
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL

    def _windows_process_alive(pid: int) -> bool:
        # os.kill(pid, 0) is not a safe Windows liveness probe: it can
        # send a console control event or fall through to TerminateProcess.
        if pid > 0xFFFFFFFF:
            return False
        handle = _open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Access/query failures are deliberately treated as alive. Only
            # errors proving that the process object is gone may report dead.
            return ctypes.get_last_error() not in _WINDOWS_PROCESS_MISSING_ERRORS
        try:
            exit_code = wintypes.DWORD()
            if not _get_exit_code_process(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            _close_handle(handle)
else:

    def _windows_process_alive(pid: int) -> bool:  # pragma: no cover - defensive
        raise AssertionError("Windows process probe called on POSIX")


@dataclass(frozen=True)
class RuntimeState:
    pid: int
    port: int
    token: str
    version: str
    agent_name: str
    owner_nonce: str = ""
    started_at: float = 0.0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


def agent_name() -> str:
    return (
        os.getenv("TITAN_AGENT_NAME")
        or os.getenv("CLAUDE_PLUGIN_OPTION_agent_name")
        or "claude-code"
    ).strip()


def data_root() -> Path:
    raw = (
        os.getenv("TITAN_CLAUDE_DATA")
        or os.getenv("CLAUDE_PLUGIN_DATA")
        or str(Path.home() / ".titan" / "claude-plugin")
    )
    return Path(raw).expanduser().resolve()


def runtime_dir(name: str | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or agent_name()))
    path = data_root() / "runtime" / (safe or "claude-code")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_private(path, directory=True)
    return path


def state_path(name: str | None = None) -> Path:
    return runtime_dir(name) / "daemon.json"


def fallback_dir(name: str | None = None) -> Path:
    path = runtime_dir(name) / "fallback"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_private(path, directory=True)
    return path


def _chmod_private(path: Path, *, directory: bool = False) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass


def atomic_write_state(state: RuntimeState) -> None:
    target = state_path(state.agent_name)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = Path(temporary)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        _chmod_private(target)
    finally:
        tmp.unlink(missing_ok=True)


def read_state(name: str | None = None) -> RuntimeState | None:
    target = state_path(name)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return RuntimeState(
            pid=int(payload["pid"]),
            port=int(payload["port"]),
            token=str(payload["token"]),
            version=str(payload["version"]),
            agent_name=str(payload["agent_name"]),
            owner_nonce=str(payload.get("owner_nonce") or ""),
            started_at=float(payload.get("started_at") or 0.0),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _lock_file(handle, *, blocking: bool) -> None:
    if fcntl is not None:
        mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), mode)
        return
    if msvcrt is None:  # pragma: no cover
        raise RuntimeError("No interprocess locking primitive is available")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    while True:  # pragma: no cover - Windows
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not blocking:
                raise BlockingIOError("runtime lease is already held")
            time.sleep(0.05)


def _unlock_file(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def ownership_lock(name: str | None = None) -> Iterator[None]:
    path = runtime_dir(name) / "owner.lock"
    with path.open("a+b") as handle:
        _chmod_private(path)
        _lock_file(handle, blocking=True)
        try:
            yield
        finally:
            _unlock_file(handle)


@contextmanager
def daemon_lease(name: str | None = None) -> Iterator[None]:
    """Hold the one-mutating-owner lease for the daemon's full lifetime."""

    path = runtime_dir(name) / "daemon.lease"
    with path.open("a+b") as handle:
        _chmod_private(path)
        _lock_file(handle, blocking=False)
        try:
            yield
        finally:
            _unlock_file(handle)


def daemon_command(port: int, token: str, name: str, owner_nonce: str, started_at: float) -> list[str]:
    return [
        sys.executable,
        "-m",
        "runtime.daemon",
        "--port",
        str(port),
        "--token",
        token,
        "--agent",
        name,
        "--version",
        RUNTIME_VERSION,
        "--owner-nonce",
        owner_nonce,
        "--started-at",
        str(started_at),
    ]


def spawn_daemon(
    port: int,
    token: str,
    name: str,
    owner_nonce: str,
    started_at: float,
) -> subprocess.Popen[bytes]:
    root = runtime_dir(name)
    log_path = root / "daemon.log"
    log = log_path.open("ab", buffering=0)
    _chmod_private(log_path)
    env = os.environ.copy()
    env["TITAN_AGENT_NAME"] = name
    env["TITAN_CLAUDE_DATA"] = str(data_root())
    plugin_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (plugin_root, env.get("PYTHONPATH"))))
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": log,
        "env": env,
        "close_fds": True,
    }
    if os.name == "nt":  # pragma: no cover - Windows CI
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        daemon_command(port, token, name, owner_nonce, started_at),
        **kwargs,
    )
    log.close()
    return process
