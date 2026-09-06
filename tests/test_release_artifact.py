from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "titan-memory-cli"
GATE = ROOT / "scripts" / "package_gate.py"
VERIFIER = ROOT / "scripts" / "verify_packed_artifact.py"
TIMEOUT = 30
NPM = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm") or "npm"
REQUIRED_TOOLS = {
    "store_trace_packet", "store_trace_event", "query_memories", "get_scene_context",
    "get_recent_memories", "doctor", "inspect_clusters", "analyze_clusters",
    "patterns_status", "patterns_list", "pattern_get", "pattern_create", "pattern_accept",
    "pattern_reject", "patterns_evidence_packet", "patterns_mark_processed",
    "patterns_export_bundle", "patterns_import_bundle",
}


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=TIMEOUT
    )


def prepare_candidate(state: Path) -> Path:
    """Copy only npm-generation inputs; never copy local .claude/venvs/runtime."""
    candidate = state / "candidate"
    candidate.mkdir(parents=True)
    for relative in ("app", "entrypoints", "integrations", "tools/cli"):
        shutil.copytree(
            ROOT / relative,
            candidate / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", ".venv"),
        )
    for relative in (
        "config/.env.example", "config/embedding_models.yaml", "config/extraction_models.yaml",
        "config/settings.yaml", "config/visual_config.yaml", "tools/__init__.py",
        "tools/opencode/__init__.py", "tools/opencode/install_plugin.py", "requirements.txt",
        "LICENSE", "package.json", "scripts/package_gate.py", "scripts/verify_packed_artifact.py",
    ):
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    package_candidate = candidate / "packages" / "titan-memory-cli"
    package_candidate.mkdir(parents=True)
    for child in PACKAGE.iterdir():
        if child.name in {"runtime", "node_modules"}:
            continue
        target = package_candidate / child.name
        if child.is_dir():
            shutil.copytree(child, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
        else:
            shutil.copy2(child, target)
    prepared = run(
        [NPM, "--prefix", str(candidate / "packages" / "titan-memory-cli"), "run", "prepack"],
        cwd=candidate,
        env={**os.environ, "npm_config_cache": str(state / "prepare-npm-cache")},
    )
    if prepared.returncode:
        raise AssertionError(prepared.stderr or prepared.stdout)
    return candidate


def pack_existing(destination: Path, package_root: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["npm_config_cache"] = str(destination / "npm-cache")
    result = run(
        [NPM, "pack", "--ignore-scripts", "--json", "--pack-destination", str(destination)],
        cwd=package_root,
        env=env,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    archive = destination / json.loads(result.stdout)[0]["filename"]
    assert archive.is_file()
    return archive


def remove_archive_member(source: Path, destination: Path, relative: str) -> None:
    removed = f"package/{relative}"
    with tarfile.open(source, "r:gz") as source_tar, tarfile.open(destination, "w:gz") as target_tar:
        for member in source_tar.getmembers():
            if member.name == removed:
                continue
            clone = copy.copy(member)
            if member.isfile():
                extracted = source_tar.extractfile(member)
                assert extracted is not None
                target_tar.addfile(clone, extracted)
            else:
                target_tar.addfile(clone)


class ReleaseArtifactTests(unittest.TestCase):
    def test_gate_scans_source_packlist_and_actual_tarball(self):
        with tempfile.TemporaryDirectory(prefix="titan-release-pack-") as directory:
            state = Path(directory)
            candidate = prepare_candidate(state)
            package_root = candidate / "packages" / "titan-memory-cli"
            source = run([sys.executable, str(GATE), "--source", "--root", str(candidate)], cwd=ROOT)
            self.assertEqual(source.returncode, 0, source.stderr or source.stdout)
            archive = pack_existing(state / "artifact", package_root)
            checked = run([sys.executable, str(GATE), "--artifact", str(archive), "--root", str(candidate)], cwd=ROOT)
            self.assertEqual(checked.returncode, 0, checked.stderr or checked.stdout)
            with tarfile.open(archive, "r:gz") as handle:
                names = [member.name for member in handle.getmembers() if member.isfile()]
            self.assertFalse(any(name.endswith("_agents.md") for name in names))

            # The preparation step must omit exact internal-note names, not just
            # the historical *_agents.md suffix.
            for note_name in ("AGENTS.md", "CONTEXT.md", "release_agents.md"):
                source_note = candidate / "app" / note_name
                source_note.write_text("internal release note\n", encoding="utf-8")
            prepared = run(
                [NPM, "--prefix", str(package_root), "run", "prepack"],
                cwd=candidate,
                env={**os.environ, "npm_config_cache": str(state / "reprepare-npm-cache")},
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr or prepared.stdout)
            for note_name in ("AGENTS.md", "CONTEXT.md", "release_agents.md"):
                self.assertFalse((package_root / "runtime" / "app" / note_name).exists())

            agent_note = package_root / "runtime" / "app" / "release_agents.md"
            agent_note.write_text("internal release note\n", encoding="utf-8")
            rejected = run(
                ["node", str(package_root / "scripts" / "audit-runtime.js"), str(package_root)], cwd=candidate
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("internal agent notes", rejected.stderr)

    def test_gate_rejects_privacy_content_in_the_exact_source_packlist(self):
        with tempfile.TemporaryDirectory(prefix="titan-release-fixture-") as directory:
            candidate = prepare_candidate(Path(directory))
            fixture = candidate / "packages" / "titan-memory-cli"
            (fixture / "runtime" / "app" / "seeded_leak.py").write_text(
                'LOCAL_PROJECT = "/Users/example-user/private-project"\n', encoding="utf-8"
            )
            result = run([sys.executable, str(GATE), "--source", "--root", str(fixture)], cwd=ROOT)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("local home path", result.stderr)

    def test_incomplete_installed_artifact_cannot_import_cortex_from_source_checkout(self):
        with tempfile.TemporaryDirectory(prefix="titan-release-malformed-") as directory:
            state = Path(directory)
            candidate = prepare_candidate(state)
            archive = pack_existing(state / "valid", candidate / "packages" / "titan-memory-cli")
            malformed = state / "missing-cortex.tgz"
            remove_archive_member(archive, malformed, "runtime/app/graph/cortex_analysis.py")

            # These are deliberately hostile inherited values. The installed
            # process must run from a temp cwd and must not use this checkout.
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("TITAN_") or key.startswith("PYTHON"):
                    env.pop(key, None)
            env.update(
                {
                    "TITAN_NPM_NO_VENV": "1",
                    "TITAN_RUNTIME_HOME": str(state / "runtime-home"),
                    "TITAN_RUNTIME_MANIFEST": str(state / "runtime-home" / "current.json"),
                    "TITAN_HOME": str(state / "titan-home"),
                    "TITAN_BASE_DIR": str(state / "titan-base"),
                    "TITAN_SPOOL_DIR": str(state / "spool"),
                    # Keep the venv launcher path.  Resolving it points at the
                    # base interpreter, which does not have the runtime deps.
                    "PYTHON": sys.executable,
                }
            )
            install = state / "install"
            installed = run(
                [NPM, "install", "--ignore-scripts", "--no-audit", "--no-fund", "--prefix", str(install), str(malformed)],
                cwd=state,
                env=env,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            cli = install / "node_modules" / "titan-memory-cli" / "bin" / "titan.js"
            isolated_cwd = state / "cwd"
            isolated_cwd.mkdir()
            result = run(["node", str(cli), "codex", "list-tools", "--json"], cwd=isolated_cwd, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("PYTHONHOME", env)
            self.assertIn("cortex_analysis", result.stderr)

            gate = run([sys.executable, str(GATE), "--artifact", str(malformed)], cwd=ROOT)
            self.assertNotEqual(gate.returncode, 0)
            self.assertIn("cortex_analysis.py", gate.stderr)

    def test_verifier_binds_real_python_instead_of_inherited_fake_python(self):
        with tempfile.TemporaryDirectory(prefix="titan-release-fake-python-") as directory:
            state = Path(directory)
            candidate = prepare_candidate(state)
            artifact = pack_existing(state / "artifact", candidate / "packages" / "titan-memory-cli")
            marker = state / "fake-python-used"
            fake = state / "fake-python.py"
            tools = json.dumps(sorted(REQUIRED_TOOLS))
            fake.write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env python3
                    import json, os, shutil, sys
                    from pathlib import Path
                    Path(os.environ['FAKE_PYTHON_MARKER']).touch()
                    args = sys.argv[1:]
                    if args == ['--version']:
                        raise SystemExit(0)
                    if args[:2] == ['-c', args[1] if len(args) > 1 else '']:
                        print(Path(__file__).resolve())
                        raise SystemExit(0)
                    if args[:2] == ['-m', 'venv']:
                        target = Path(args[2]); python = target / 'bin' / 'python'
                        python.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(__file__, python)
                        python.chmod(0o755); raise SystemExit(0)
                    if args[:2] == ['-m', 'pip']:
                        raise SystemExit(0)
                    if 'mcp' in args:
                        for line in sys.stdin:
                            request = json.loads(line)
                            if request.get('id') == 1:
                                print(json.dumps({{'jsonrpc':'2.0','id':1,'result':{{}}}}), flush=True)
                            elif request.get('id') == 2:
                                print(json.dumps({{'jsonrpc':'2.0','id':2,'result':{{'tools':[{{'name': name}} for name in {tools}]}}}}), flush=True)
                    else:
                        print(json.dumps({{'tools': {tools}}}))
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            # PYTHONHOME is read by Python before user code starts.  Put the
            # hostile values in a bootstrap that is started with a clean
            # environment, so the verifier gets a chance to remove them.
            bootstrap = state / "run-verifier.py"
            bootstrap.write_text(
                textwrap.dedent(
                    f"""
                    import os
                    import runpy
                    import sys

                    os.environ.update({{
                        "PYTHON": {str(fake)!r},
                        "PYTHONPATH": {str(ROOT)!r},
                        "PYTHONHOME": {sys.prefix!r},
                        "FAKE_PYTHON_MARKER": {str(marker)!r},
                        "TITAN_HOME": {str(state / "ambient-home")!r},
                        "TITAN_RUNTIME_HOME": {str(state / "ambient-runtime")!r},
                    }})
                    sys.argv = [{str(VERIFIER)!r}, "--artifact", {str(artifact)!r}, "--root", {str(ROOT)!r}]
                    runpy.run_path({str(VERIFIER)!r}, run_name="__main__")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("TITAN_") or key.startswith("PYTHON"):
                    env.pop(key, None)
            isolated_cwd = state / "cwd"
            isolated_cwd.mkdir()
            result = run(
                [sys.executable, str(bootstrap)],
                cwd=isolated_cwd,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertFalse(marker.exists(), "verifier allowed inherited PYTHON to run the artifact")
            self.assertIn("handshake", result.stdout)


if __name__ == "__main__":
    unittest.main()
