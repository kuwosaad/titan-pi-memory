"""Start the engine shipped beside a Titan plugin, without a global Titan CLI."""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
import venv
import zipfile
from contextlib import contextmanager
from pathlib import Path

# A runtime that is no longer published, or that no plugin copy still ships, is
# removed once nothing has launched it for this long. Some hosts (Pi) keep using
# the interpreter path they resolved at startup, which no lock can observe.
RECLAIM_AFTER = 3 * 24 * 3600
# Archives that launched a runtime, one path per line. Its mtime is the last launch.
OWNERS = "titan-owners"
# On POSIX every server started through main() holds a shared lock on this file.
IN_USE = "titan-in-use.lock"
_held = []


def _lock(handle, *, shared=False, blocking=True):
    """Lock an open file. Return False if blocking is off and the lock is taken."""
    if os.name == "nt":
        import msvcrt
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError as exc:
                if exc.errno != 13:
                    raise
                if not blocking:
                    return False
                time.sleep(0.2)
    import fcntl
    try:
        fcntl.flock(handle, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        return False
    return True


@contextmanager
def installation_lock(path, *, blocking=True):
    # OS locks are released if an installer is killed. A later launch retries.
    with path.open("a+b") as handle:
        yield handle if _lock(handle, blocking=blocking) else None


@contextmanager
def _terminate_as_exit():
    # Hosts stop MCP servers that miss their startup deadline, which is when the
    # first install runs. Turning SIGTERM into SystemExit lets subprocess.run stop
    # pip and the cleanup below delete the partial environment.
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    previous = {}
    for name in ("SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                previous[number] = signal.signal(number, stop)
            except (OSError, ValueError):
                pass
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _interpreter(runtime):
    return runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _runs(python):
    # A venv links to its base Python by a path that can include the patch
    # version (Homebrew Cellar), so an upgrade can leave it unable to start.
    try:
        return subprocess.run([str(python), "-I", "-c", "pass"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _published(cache, key):
    try:
        return (cache / (key + ".ready")).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def _still_shipped(key, owners):
    digest = key.rsplit("-", 1)[-1]
    for line in owners.read_text(encoding="utf-8").splitlines():
        try:
            if hashlib.sha256(Path(line).read_bytes()).hexdigest() == digest:
                return True
        except OSError:
            continue
    return False


def _remove_unused(runtime):
    trash = runtime.with_name(runtime.name + ".trash")
    if os.name == "nt":
        # Windows will not rename a directory while a program inside it runs.
        try:
            os.rename(runtime, trash)
        except OSError:
            return False
    else:
        with (runtime / IN_USE).open("a+b") as handle:
            if not _lock(handle, blocking=False):
                return False
            # Renaming first also stops a pip orphaned by a killed install: its paths vanish.
            os.rename(runtime, trash)
    shutil.rmtree(trash, ignore_errors=True)
    return True


def reclaim(cache, keep=None):
    """Delete runtimes that no launch can use any more.

    Candidates are interrupted installs, runtimes replaced by a rebuild, runtimes
    whose interpreter no longer starts, and runtimes that no plugin archive on
    disk still ships. A runtime is kept while another process is installing that
    key, while a server holds its in-use lock, or within RECLAIM_AFTER of its last
    launch.
    """
    now = time.time()
    for entry in cache.iterdir():
        try:
            name = entry.name
            if name.endswith(".trash"):
                shutil.rmtree(entry, ignore_errors=True)
                continue
            key = name.rsplit("-", 1)[0]
            if name == keep or "-" not in name or entry.is_symlink() or not entry.is_dir() \
                    or not (cache / (key + ".lock")).is_file():
                continue
            owners = entry / OWNERS
            published = _published(cache, key) == name
            try:
                last = owners.stat().st_mtime
            except FileNotFoundError:
                # Never launched; runtimes from earlier releases had no owners file.
                last = entry.stat().st_mtime if published else None
            if last is not None and now - last < RECLAIM_AFTER:
                continue
            with installation_lock(cache / (key + ".lock"), blocking=False) as lock:
                if lock is None:
                    continue  # being installed right now
                published = _published(cache, key) == name
                if published and _runs(_interpreter(entry)) and (not owners.is_file() or _still_shipped(key, owners)):
                    os.utime(owners if owners.is_file() else entry)  # checked; look again later
                    continue
                if _remove_unused(entry) and published:
                    (cache / (key + ".ready")).unlink(missing_ok=True)
        except OSError:
            continue


def _install(cache, key, archive, marker):
    # Virtual environments contain absolute paths, so keep their original
    # location and publish the pointer only after installation succeeds.
    location = Path(tempfile.mkdtemp(prefix=key + "-", dir=cache))
    try:
        with _terminate_as_exit():
            print("Titan is preparing its private Python environment.", file=sys.stderr, flush=True)
            venv.EnvBuilder(with_pip=True).create(location)
            python = _interpreter(location)
            code = location / "engine"
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(code)
            subprocess.run([str(python), "-I", "-m", "pip", "--disable-pip-version-check", "install", "--no-input",
                            "-r", str(code / "requirements.txt")], stdout=sys.stderr, check=True)
            site = subprocess.check_output([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
            (Path(site) / "titan_engine.pth").write_text(str(code) + "\n", encoding="utf-8")
            subprocess.run([str(python), "-I", "-c", "import app, tools.cli.titan, mcp.server.fastmcp"], stdout=sys.stderr, check=True)
            pointer = location / "ready.tmp"
            pointer.write_text(location.name, encoding="utf-8")
            os.replace(pointer, marker)
    except BaseException:
        shutil.rmtree(location, ignore_errors=True)
        raise
    return location


def _note_launch(runtime, archive):
    owners = runtime / OWNERS
    try:
        known = owners.read_text(encoding="utf-8").splitlines() if owners.is_file() else []
        if str(archive) not in known:
            with owners.open("a", encoding="utf-8") as handle:
                handle.write(str(archive) + "\n")
        os.utime(owners)
    except OSError:
        pass


def managed_python():
    archive = Path(__file__).resolve().with_name("titan_engine.zip")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    cache = Path(os.environ.get("TITAN_PLUGIN_RUNTIME_HOME") or Path.home() / ".titan" / "plugin-runtimes").expanduser().resolve()
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}-{digest}"
    marker = cache / (key + ".ready")

    def installed():
        name = _published(cache, key)
        if not name or not name.startswith(key + "-") or Path(name).name != name:
            return None
        runtime = cache / name
        python = _interpreter(runtime)
        # A runtime whose interpreter cannot start is rebuilt rather than trusted.
        return runtime if python.is_file() and _runs(python) else None

    reclaim(cache, keep=_published(cache, key))
    runtime = installed()
    if runtime is None:
        with installation_lock(cache / (key + ".lock")):
            runtime = installed() or _install(cache, key, archive, marker)
    _note_launch(runtime, archive)
    return _interpreter(runtime)


def _mark_in_use(python):
    # The descriptor survives exec, so the server holds this shared lock for its
    # whole life and reclaim() leaves its runtime alone.
    if os.name == "nt":
        return
    try:
        handle = (Path(python).parent.parent / IN_USE).open("a+b")
        _lock(handle, shared=True)
        os.set_inheritable(handle.fileno(), True)
    except OSError:
        return  # RECLAIM_AFTER still protects a recently launched runtime
    _held.append(handle)


def _hand_off(argv: list[str]) -> int:
    """Replace this process with argv, or run it as a child on Windows."""
    # Windows has no real exec: os.exec* there neither quotes arguments containing
    # spaces nor keeps the caller's stdio attached, which breaks MCP servers.
    if os.name == "nt":
        return subprocess.call(argv)
    os.execv(argv[0], argv)
    return 0  # unreachable: execv does not return

def main(argv):
    if sys.version_info < (3, 10):
        print("Titan requires Python 3.10 or later with venv and pip.", file=sys.stderr)
        return 1
    try:
        python = str(managed_python())
        if argv == ["--runtime-python"]:
            print(python)
            return 0
        if argv[:1] == ["configure"]:
            args = ["tools.cli.onboarding", "--providers-only", *argv[1:]]
        elif argv[:1] == ["--module"] and len(argv) > 1:
            args = argv[1:]
        else:
            args = ["tools.cli.titan", *argv]
        _mark_in_use(python)
        return _hand_off([python, "-I", "-m", *args])
    except (OSError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Titan could not start its private runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
