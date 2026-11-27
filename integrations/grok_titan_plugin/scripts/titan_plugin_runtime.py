"""Start the engine shipped beside a Titan plugin, without a global Titan CLI."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import venv
import zipfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def installation_lock(path):
    # OS locks are released if an installer is killed. A later launch retries.
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno != 13:
                        raise
                    time.sleep(0.2)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def managed_python():
    archive = Path(__file__).resolve().with_name("titan_engine.zip")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    cache = Path(os.environ.get("TITAN_PLUGIN_RUNTIME_HOME") or Path.home() / ".titan" / "plugin-runtimes").expanduser().resolve()
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}-{digest}"
    marker = cache / (key + ".ready")
    def installed_python():
        try:
            name = marker.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if not name.startswith(key + "-") or Path(name).name != name:
            return None
        candidate = cache / name / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        return candidate if candidate.is_file() else None
    python = installed_python()
    if python:
        return python
    with installation_lock(cache / (key + ".lock")):
        python = installed_python()
        if python:
            return python
        # Virtual environments contain absolute paths, so keep their original
        # location and publish the pointer only after installation succeeds.
        location = Path(tempfile.mkdtemp(prefix=key + "-", dir=cache))
        try:
            print("Titan is preparing its private Python environment.", file=sys.stderr, flush=True)
            venv.EnvBuilder(with_pip=True).create(location)
            python = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
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
        return python


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
        return _hand_off([python, "-I", "-m", *args])
    except (OSError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Titan could not start its private runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
