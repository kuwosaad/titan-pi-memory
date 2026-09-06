from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "integrations" / "claude_titan_plugin"
RUNTIME_JS = PLUGIN_ROOT / "scripts" / "titan_claude_runtime.js"
RUNTIME_PACKAGE = "titan-memory-cli"
RUNTIME_VERSION = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))["dependencies"]["titan-memory-cli"]


def _runtime_fixture(root: Path) -> tuple[Path, Path]:
    runtime_root = root / "runtime-root"
    runtime_root.mkdir(parents=True)
    python = root / ("python.exe" if os.name == "nt" else "python")
    python.write_text("", encoding="utf-8")
    return python, runtime_root


def _manifest(data_root: Path, python: Path, runtime_root: Path) -> Path:
    manifest = data_root / "managed-runtime" / "current.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({
            "package": "titan-memory-cli",
            "version": RUNTIME_VERSION,
            "python": str(python),
            "runtime_root": str(runtime_root),
        }),
        encoding="utf-8",
    )
    return manifest


def _clean_plugin_fixture(root: Path, python: Path, runtime_root: Path) -> Path:
    """Build a copied plugin whose pinned runtime dependency is local to the fixture."""
    plugin = root / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    shutil.copy2(RUNTIME_JS, plugin / "scripts" / "titan_claude_runtime.js")

    dependency = root / "fixture-runtime"
    (dependency / "bin").mkdir(parents=True)
    dependency_version = "9.9.9"
    dependency_bin = f"""
const fs = require('node:fs');
const path = require('node:path');
const runtimeHome = path.resolve(process.env.TITAN_RUNTIME_HOME);
fs.mkdirSync(runtimeHome, {{ recursive: true }});
fs.writeFileSync(path.join(runtimeHome, 'current.json'), JSON.stringify({{
  package: 'titan-memory-cli', version: {json.dumps(dependency_version)},
  python: {json.dumps(str(python))}, runtime_root: {json.dumps(str(runtime_root))}
}}));
"""
    (dependency / "package.json").write_text(
        json.dumps({
            "name": "titan-memory-cli",
            "version": dependency_version,
            "bin": {"titan": "bin/titan.js"},
        }),
        encoding="utf-8",
    )
    (dependency / "bin" / "titan.js").write_text(dependency_bin, encoding="utf-8")

    dependency_spec = "9.9.9"
    (plugin / "package.json").write_text(
        json.dumps({
            "name": "titan-memory-claude-plugin",
            "version": "0.0.0",
            "private": True,
            "dependencies": {RUNTIME_PACKAGE: dependency_spec},
        }),
        encoding="utf-8",
    )
    (plugin / "package-lock.json").write_text(
        json.dumps({
            "name": "titan-memory-claude-plugin",
            "version": "0.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {
                    "name": "titan-memory-claude-plugin",
                    "version": "0.0.0",
                    "dependencies": {RUNTIME_PACKAGE: dependency_spec},
                },
                "../fixture-runtime": {
                    "name": RUNTIME_PACKAGE,
                    "version": dependency_version,
                    "bin": {"titan": "bin/titan.js"},
                },
                "node_modules/titan-memory-cli": {
                    "resolved": "../fixture-runtime",
                    "link": True,
                },
            },
        }),
        encoding="utf-8",
    )
    return plugin


shutil_which = shutil.which("node")


@unittest.skipUnless(shutil_which, "Node.js is not installed")
class ClaudeRuntimeBootstrapTests(unittest.TestCase):
    def _run_node(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [shutil_which, "-e", script],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_clean_copied_plugin_installs_pinned_runtime_in_plugin_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python, runtime_root = _runtime_fixture(root)
            plugin = _clean_plugin_fixture(root, python, runtime_root)
            data = root / "plugin-data"
            # The copied lockfile's local fixture is staged beside the persistent
            # install root, so npm ci never needs a registry or the source tree.
            shutil.copytree(root / "fixture-runtime", data / "managed-runtime" / "fixture-runtime")
            env = {
                **os.environ,
                "CLAUDE_PLUGIN_DATA": str(data),
                # Keep npm away from user config, credentials, registry, or
                # real Titan state without changing the host HOME.
                "NPM_CONFIG_USERCONFIG": str(root / "npmrc"),
                "NPM_CONFIG_CACHE": str(root / "npm-cache"),
                "NPM_CONFIG_UPDATE_NOTIFIER": "false",
                # Launchers may inherit this from their host environment. The
                # runtime must still write its own managed-data manifest.
                "TITAN_RUNTIME_MANIFEST": str(root / "host-runtime" / "current.json"),
            }
            script = f"""
const runtime = require({json.dumps(str(plugin / 'scripts' / 'titan_claude_runtime.js'))});
const result = runtime.ensurePackagedRuntime(process.env, {{ timeoutMs: 10000 }});
if (!result) process.exit(2);
console.log(JSON.stringify(result));
"""
            result = self._run_node(script, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["runtimeRoot"], str(runtime_root))
            self.assertTrue((data / "managed-runtime" / "current.json").is_file())
            self.assertFalse((root / "host-runtime" / "current.json").exists())
            self.assertTrue(
                (data / "managed-runtime" / "package" / "node_modules" / RUNTIME_PACKAGE).exists()
            )
            self.assertFalse((plugin / "node_modules").exists())

    def test_failed_installer_releases_lock_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python, runtime_root = _runtime_fixture(root)
            data = root / "plugin-data"
            fixture_package = json.dumps({"name": RUNTIME_PACKAGE, "version": RUNTIME_VERSION})
            fixture_bin = """
const fs = require('node:fs');
const path = require('node:path');
const home = path.resolve(process.env.TITAN_RUNTIME_HOME);
fs.mkdirSync(home, { recursive: true });
fs.writeFileSync(path.join(home, 'current.json'), JSON.stringify({
  package: 'titan-memory-cli', version: %s, python: %s, runtime_root: %s
}));
""" % (json.dumps(RUNTIME_VERSION), json.dumps(str(python)), json.dumps(str(runtime_root)))
            script = f"""
const fs = require('node:fs');
const path = require('node:path');
const runtime = require({json.dumps(str(RUNTIME_JS))});
let attempts = 0;
const installDependencies = ({{ packageRoot }}) => {{
  attempts += 1;
  const packageDir = path.join(packageRoot, 'node_modules', {json.dumps(RUNTIME_PACKAGE)});
  if (attempts === 1) {{
    fs.mkdirSync(packageDir, {{ recursive: true }});
    fs.writeFileSync(path.join(packageDir, 'package.json'), {json.dumps(fixture_package)});
    throw new Error('synthetic installer failure');
  }}
  fs.mkdirSync(path.join(packageDir, 'bin'), {{ recursive: true }});
  fs.writeFileSync(path.join(packageDir, 'package.json'), {json.dumps(fixture_package)});
  fs.writeFileSync(path.join(packageDir, 'bin', 'titan.js'), {json.dumps(fixture_bin)});
}};
const activate = (env, options) => runtime.activatePackagedRuntime(env, {{
  ...options, installDependencies
}});
let firstError = '';
try {{
  runtime.ensurePackagedRuntime(process.env, {{ timeoutMs: 5000, activate }});
}} catch (error) {{
  firstError = error.message;
}}
const packageDir = path.join(runtime.managedManifestPath(), '..', 'package', 'node_modules', {json.dumps(RUNTIME_PACKAGE)});
if (fs.existsSync(packageDir)) process.exit(4);
const result = runtime.ensurePackagedRuntime(process.env, {{ timeoutMs: 5000, activate }});
if (!result || !fs.existsSync(packageDir) || attempts !== 2 || !firstError.includes('synthetic installer failure')) process.exit(2);
console.log(JSON.stringify({{ attempts, firstError }}));
"""
            result = self._run_node(
                script,
                {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["attempts"], 2)
            self.assertIn("synthetic installer failure", payload["firstError"])
            self.assertFalse((data / "managed-runtime" / ".bootstrap.lock").exists())

    def test_activation_failure_reports_child_diagnostics_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python, runtime_root = _runtime_fixture(root)
            data = root / "plugin-data"
            fixture_package = json.dumps({"name": RUNTIME_PACKAGE, "version": RUNTIME_VERSION})
            script = f"""
const fs = require('node:fs');
const path = require('node:path');
const runtime = require({json.dumps(str(RUNTIME_JS))});
const installDependencies = ({{ packageRoot }}) => {{
  const packageDir = path.join(packageRoot, 'node_modules', {json.dumps(RUNTIME_PACKAGE)});
  fs.mkdirSync(path.join(packageDir, 'bin'), {{ recursive: true }});
  fs.writeFileSync(path.join(packageDir, 'package.json'), {json.dumps(fixture_package)});
  fs.writeFileSync(path.join(packageDir, 'bin', 'titan.js'), 'console.error("synthetic activation failure"); process.exit(7);');
}};
try {{
  runtime.ensurePackagedRuntime(process.env, {{ timeoutMs: 5000, installDependencies }});
  process.exit(2);
}} catch (error) {{
  if (!error.message.includes('synthetic activation failure')) process.exit(3);
  console.log(error.message);
}}
"""
            result = self._run_node(
                script,
                {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("synthetic activation failure", result.stdout)
            self.assertFalse((data / "managed-runtime" / ".bootstrap.lock").exists())

    def test_installer_timeout_is_bounded_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_npm = root / "slow-npm.js"
            fake_npm.write_text(
                "while (Date.now() < %d) {}\n" % (int(time.time() * 1000) + 5000),
                encoding="utf-8",
            )
            data = root / "plugin-data"
            script = f"""
const runtime = require({json.dumps(str(RUNTIME_JS))});
try {{
  runtime.ensurePackagedRuntime(process.env, {{
    timeoutMs: 100,
    npmCommand: process.execPath,
    npmArgs: [{json.dumps(str(fake_npm))}],
  }});
  process.exit(2);
}} catch (error) {{
  if (!error.message.includes('Timed out installing')) process.exit(3);
  console.log(error.message);
}}
"""
            started = time.monotonic()
            result = self._run_node(script, {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)})
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Timed out installing", result.stdout)
            self.assertLess(elapsed, 3.0)
            self.assertFalse((data / "managed-runtime" / ".bootstrap.lock").exists())

    def test_valid_manifest_bypasses_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "plugin-data"
            python, runtime_root = _runtime_fixture(root)
            _manifest(data, python, runtime_root)
            counter = root / "activations"
            env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
            script = f"""
const fs = require('node:fs');
const runtime = require({json.dumps(str(RUNTIME_JS))});
const result = runtime.ensurePackagedRuntime(process.env, {{
  timeoutMs: 1000,
  activate: () => {{ fs.appendFileSync({json.dumps(str(counter))}, 'x'); throw new Error('unexpected'); }},
}});
console.log(JSON.stringify(result));
"""
            result = self._run_node(script, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(counter.exists())
            self.assertEqual(json.loads(result.stdout)["runtimeRoot"], str(runtime_root))

    def test_concurrent_bootstrap_has_exactly_one_activator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "plugin-data"
            python, runtime_root = _runtime_fixture(root)
            manifest = data / "managed-runtime" / "current.json"
            counter = root / "activations"
            env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
            script = f"""
const fs = require('node:fs');
const runtime = require({json.dumps(str(RUNTIME_JS))});
function sleep(ms) {{ Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }}
const result = runtime.ensurePackagedRuntime(process.env, {{
  timeoutMs: 5000,
  activate: () => {{
    fs.appendFileSync({json.dumps(str(counter))}, `${{process.pid}}\\n`);
    sleep(250);
    fs.mkdirSync({json.dumps(str(manifest.parent))}, {{ recursive: true }});
    fs.writeFileSync({json.dumps(str(manifest))}, JSON.stringify({{
      package: 'titan-memory-cli', version: {json.dumps(RUNTIME_VERSION)},
      python: {json.dumps(str(python))}, runtime_root: {json.dumps(str(runtime_root))}
    }}));
    return runtime.readManagedRuntime();
  }},
}});
if (!result) process.exit(2);
"""
            processes = [
                subprocess.Popen(
                    [shutil_which, "-e", script],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for _ in range(6)
            ]
            outputs = [process.communicate(timeout=10) for process in processes]

            for process, (_stdout, stderr) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(len(counter.read_text(encoding="utf-8").splitlines()), 1)
            self.assertTrue(manifest.is_file())

    def test_dead_owner_bootstrap_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "plugin-data"
            python, runtime_root = _runtime_fixture(root)
            runtime_home = data / "managed-runtime"
            lock = runtime_home / ".bootstrap.lock"
            lock.mkdir(parents=True)
            (lock / "owner.json").write_text(
                json.dumps({"pid": 99999999, "started_at_ms": int(time.time() * 1000)}),
                encoding="utf-8",
            )
            manifest = runtime_home / "current.json"
            env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
            script = f"""
const fs = require('node:fs');
const runtime = require({json.dumps(str(RUNTIME_JS))});
runtime.ensurePackagedRuntime(process.env, {{
  timeoutMs: 1000,
  activate: () => {{
    fs.writeFileSync({json.dumps(str(manifest))}, JSON.stringify({{
      package: 'titan-memory-cli', version: {json.dumps(RUNTIME_VERSION)},
      python: {json.dumps(str(python))}, runtime_root: {json.dumps(str(runtime_root))}
    }}));
    return runtime.readManagedRuntime();
  }},
}});
"""
            result = self._run_node(script, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(manifest.is_file())
            self.assertFalse(lock.exists())

    def test_stale_runtime_version_forces_single_reactivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "plugin-data"
            python, runtime_root = _runtime_fixture(root)
            manifest = _manifest(data, python, runtime_root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["version"] = "0.0.0-stale"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            counter = root / "activations"
            env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
            script = f"""
const fs = require('node:fs');
const runtime = require({json.dumps(str(RUNTIME_JS))});
const result = runtime.ensurePackagedRuntime(process.env, {{
  timeoutMs: 1000,
  activate: () => {{
    fs.appendFileSync({json.dumps(str(counter))}, 'x');
    fs.writeFileSync({json.dumps(str(manifest))}, JSON.stringify({{
      package: 'titan-memory-cli', version: {json.dumps(RUNTIME_VERSION)},
      python: {json.dumps(str(python))}, runtime_root: {json.dumps(str(runtime_root))}
    }}));
    return runtime.readManagedRuntime();
  }},
}});
if (!result) process.exit(2);
"""
            result = self._run_node(script, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8"), "x")
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8"))["version"],
                RUNTIME_VERSION,
            )

    def test_fail_open_hook_does_not_bootstrap_when_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "plugin-data"
            env = {
                **os.environ,
                "CLAUDE_PLUGIN_DATA": str(data),
                "TITAN_CLAUDE_HOOK_BOOTSTRAP_WAIT_MS": "50",
            }
            script = f"""
const childProcess = require('node:child_process');
let activations = 0;
childProcess.spawnSync = () => {{ activations += 1; return {{ status: 0 }}; }};
const runtime = require({json.dumps(str(RUNTIME_JS))});
runtime.launchPython('does-not-run.py', {{ failOpen: true }});
console.log(`activations=${{activations}}`);
"""
            started = time.monotonic()
            result = self._run_node(script, env)
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("activations=0", result.stdout)
            self.assertLess(elapsed, 2.0)
            self.assertFalse((data / "managed-runtime" / "current.json").exists())
            self.assertFalse((data / "managed-runtime" / ".bootstrap.lock").exists())


if __name__ == "__main__":
    unittest.main()
