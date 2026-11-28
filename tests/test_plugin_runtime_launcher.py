"""The shared plugin runtime launcher: health checks, rebuilds and reclaiming old runtimes."""
import hashlib
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "tools" / "plugin_runtime" / "launcher.py"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="fake runtimes use POSIX interpreter layout and flock")


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """A copy of the launcher beside its own engine archive, with a private cache."""
    directory = tmp_path / "plugin dir"
    directory.mkdir()
    shutil.copyfile(LAUNCHER, directory / "titan_runtime.py")
    (directory / "titan_engine.zip").write_bytes(b"engine v2")
    spec = importlib.util.spec_from_file_location("titan_runtime_under_test", directory / "titan_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cache = tmp_path / "runtimes"
    monkeypatch.setenv("TITAN_PLUGIN_RUNTIME_HOME", str(cache))
    module.cache = cache
    module.key = f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}-{hashlib.sha256(b'engine v2').hexdigest()}"
    return module


def make_runtime(cache, key, suffix="abcd1234", *, runs=True, publish=True, owners=None, idle=0):
    runtime = cache / f"{key}-{suffix}"
    (runtime / "bin").mkdir(parents=True)
    python = runtime / "bin" / "python"
    if runs:
        python.symlink_to(sys.executable)
    else:
        # What a Homebrew patch upgrade leaves: dyld cannot load the old keg.
        python.write_text("#!/bin/sh\necho 'dyld: Library not loaded' >&2\nexit 134\n")
        python.chmod(0o755)
    (cache / (key + ".lock")).touch()
    if publish:
        (cache / (key + ".ready")).write_text(runtime.name)
    if owners is not None:
        record = runtime / "titan-owners"
        record.write_text("".join(f"{path}\n" for path in owners))
        stamp = time.time() - idle
        os.utime(record, (stamp, stamp))
    return runtime


def fake_install(module, calls):
    def install(cache, key, archive, marker):
        calls.append(key)
        runtime = make_runtime(cache, key, suffix=f"new{len(calls):05d}", publish=False)
        marker.write_text(runtime.name)
        return runtime
    return install


def test_rebuilds_a_published_runtime_whose_interpreter_no_longer_starts(plugin, monkeypatch):
    plugin.cache.mkdir()
    broken = make_runtime(plugin.cache, plugin.key, runs=False, owners=[])
    calls = []
    monkeypatch.setattr(plugin, "_install", fake_install(plugin, calls))

    python = plugin.managed_python()

    assert calls == [plugin.key]
    assert python != broken / "bin" / "python"
    assert subprocess.run([str(python), "-I", "-c", "pass"]).returncode == 0
    assert (plugin.cache / (plugin.key + ".ready")).read_text() == python.parent.parent.name
    # A second launch trusts the healthy rebuild and does not install again.
    assert plugin.managed_python() == python
    assert calls == [plugin.key]


def test_healthy_runtime_is_reused_and_records_its_archive(plugin, monkeypatch):
    plugin.cache.mkdir()
    runtime = make_runtime(plugin.cache, plugin.key)
    monkeypatch.setattr(plugin, "_install", lambda *args: pytest.fail("reinstalled a healthy runtime"))

    assert plugin.managed_python() == runtime / "bin" / "python"
    archive = Path(plugin.__file__).with_name("titan_engine.zip")
    assert (runtime / "titan-owners").read_text().splitlines() == [str(archive)]


def test_reclaims_interrupted_installs_but_not_one_in_progress(plugin):
    plugin.cache.mkdir()
    killed = make_runtime(plugin.cache, plugin.key, "killed01", publish=False)
    other_key = plugin.key.replace(plugin.key[-64:], "1" * 64)
    building = make_runtime(plugin.cache, other_key, "build001", publish=False)

    with open(plugin.cache / (other_key + ".lock"), "a+b") as handle:
        assert plugin._lock(handle, blocking=False)  # another installer holds this key
        plugin.reclaim(plugin.cache)

    assert not killed.exists()
    assert building.exists()
    assert not list(plugin.cache.glob("*.trash"))


def test_reclaims_old_digests_only_when_unshipped_and_idle(plugin, tmp_path):
    plugin.cache.mkdir()
    old = plugin.RECLAIM_AFTER + 60
    gone = make_runtime(plugin.cache, plugin.key[:-64] + "a" * 64, owners=[tmp_path / "removed/titan_engine.zip"], idle=old)
    recent = make_runtime(plugin.cache, plugin.key[:-64] + "b" * 64, owners=[tmp_path / "removed/titan_engine.zip"], idle=60)
    shipped_archive = tmp_path / "older plugin" / "titan_engine.zip"
    shipped_archive.parent.mkdir()
    shipped_archive.write_bytes(b"engine v1")
    shipped = make_runtime(plugin.cache, plugin.key[:-64] + hashlib.sha256(b"engine v1").hexdigest(),
                           owners=[shipped_archive], idle=old)
    # Same archive, but built by a Python that has since been removed. Use a
    # version that is never the running one, or this collides with `shipped`.
    broken = make_runtime(plugin.cache, f"cpython-30-{sysconfig.get_platform()}-{hashlib.sha256(b'engine v1').hexdigest()}",
                          runs=False, owners=[shipped_archive], idle=old)
    replaced = make_runtime(plugin.cache, plugin.key, "replaced", publish=False, owners=[], idle=old)
    current = make_runtime(plugin.cache, plugin.key, "current1", owners=[], idle=old)

    plugin.reclaim(plugin.cache, keep=current.name)

    assert not gone.exists() and not (plugin.cache / (gone.name.rsplit("-", 1)[0] + ".ready")).exists()
    assert recent.exists()
    assert shipped.exists()
    assert not broken.exists()
    assert not replaced.exists()
    assert current.exists()


def test_keeps_a_runtime_a_running_server_holds(plugin, tmp_path):
    plugin.cache.mkdir()
    runtime = make_runtime(plugin.cache, plugin.key[:-64] + "d" * 64, owners=[tmp_path / "gone.zip"],
                           idle=plugin.RECLAIM_AFTER + 60)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time\n"
         "h = open(sys.argv[1], 'a+b'); fcntl.flock(h, fcntl.LOCK_SH); print('held', flush=True); time.sleep(30)",
         str(runtime / "titan-in-use.lock")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        plugin.reclaim(plugin.cache)
        assert runtime.exists()
    finally:
        holder.kill()
        holder.wait()
    plugin.reclaim(plugin.cache)
    assert not runtime.exists()


def test_in_use_lock_survives_exec(plugin, tmp_path):
    runtime = make_runtime(tmp_path, "cpython-x-y-" + "e" * 64)
    script = (
        "import importlib.util, os, sys\n"
        "spec = importlib.util.spec_from_file_location('l', sys.argv[1]); l = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(l); l._mark_in_use(sys.argv[2])\n"
        "os.execv(sys.executable, [sys.executable, '-c', 'import sys; sys.stdin.read()'])\n"
    )
    server = subprocess.Popen([sys.executable, "-c", script, plugin.__file__, str(runtime / "bin" / "python")],
                              stdin=subprocess.PIPE)
    try:
        lock = runtime / "titan-in-use.lock"
        for _ in range(100):
            if lock.exists():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        with open(lock, "a+b") as handle:
            assert not plugin._lock(handle, blocking=False)
    finally:
        server.stdin.close()
        server.wait(timeout=10)
    with open(runtime / "titan-in-use.lock", "a+b") as handle:
        assert plugin._lock(handle, blocking=False)


def test_terminated_install_removes_its_partial_environment(plugin, monkeypatch):
    plugin.cache.mkdir()
    created = []

    class Killed:
        def __init__(self, **_):
            pass

        def create(self, location):
            created.append(Path(location))
            (Path(location) / "lib").mkdir()
            os.kill(os.getpid(), signal.SIGTERM)  # the host gives up on startup
            time.sleep(5)

    monkeypatch.setattr(plugin.venv, "EnvBuilder", Killed)
    before = signal.getsignal(signal.SIGTERM)
    archive = Path(plugin.__file__).with_name("titan_engine.zip")
    with pytest.raises(SystemExit) as stopped:
        plugin._install(plugin.cache, plugin.key, archive, plugin.cache / (plugin.key + ".ready"))

    assert stopped.value.code == 128 + signal.SIGTERM
    assert created and not created[0].exists()
    assert signal.getsignal(signal.SIGTERM) == before
