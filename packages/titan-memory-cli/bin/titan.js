#!/usr/bin/env node
'use strict';

// This wrapper owns installation and activation of the Python runtime. Codex
// MCP does not invoke this file on its hot path; it invokes the plugin-local
// launcher, which reads the manifest written here.
const { spawnSync, spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const packageRoot = path.resolve(__dirname, '..');
const bundledRuntime = path.join(packageRoot, 'runtime');
const packageVersion = require(path.join(packageRoot, 'package.json')).version;
const runtimeHome = path.resolve(process.env.TITAN_RUNTIME_HOME || process.env.TITAN_RUNTIME_DIR || path.join(os.homedir(), '.titan', 'runtime'));
const versionsDir = path.join(runtimeHome, 'versions');
const versionDir = path.join(versionsDir, packageVersion);
const runtimeScript = path.join(versionDir, 'tools', 'cli', 'titan.py');
const requirementsPath = path.join(versionDir, 'requirements.txt');
const venvDir = path.join(versionDir, '.venv');
const marketplaceDir = path.join(path.dirname(runtimeHome), 'codex-marketplace');
const marketplaceSource = path.join(bundledRuntime, 'integrations', 'codex_titan_plugin');
const marketplaceManifestSource = path.join(marketplaceSource, '.agents', 'plugins', 'marketplace.json');
const manifestPath = process.env.TITAN_RUNTIME_MANIFEST || path.join(runtimeHome, 'current.json');

function fail(message, error) {
  console.error(`[titan-memory-cli] ${message}`);
  if (error && error.message) console.error(`[titan-memory-cli] ${error.message}`);
  process.exit(1);
}

function commandExists(command) {
  const result = spawnSync(command, ['--version'], { stdio: 'ignore' });
  return result.status === 0;
}

function findPython() {
  const candidates = process.env.PYTHON ? [process.env.PYTHON, 'python3', 'python'] : ['python3', 'python'];
  for (const candidate of candidates) {
    if (!commandExists(candidate)) continue;
    const resolved = spawnSync(candidate, ['-c', 'import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)'], { encoding: 'utf8' });
    const executable = (resolved.stdout || '').trim();
    if (resolved.status === 0 && executable) return path.resolve(executable);
  }
  fail('Python 3.10+ is required. Install Python, then rerun the setup command.');
}

function pythonPath() {
  return process.platform === 'win32' ? path.join(venvDir, 'Scripts', 'python.exe') : path.join(venvDir, 'bin', 'python');
}

function copyRecursive(source, destination) {
  const stat = fs.statSync(source);
  if (stat.isDirectory()) {
    fs.mkdirSync(destination, { recursive: true });
    for (const entry of fs.readdirSync(source)) copyRecursive(path.join(source, entry), path.join(destination, entry));
  } else {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
  }
}

function runChecked(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', env: process.env });
  if (result.error) fail(`Failed to run ${command}`, result.error);
  if (result.status !== 0) process.exit(result.status || 1);
}

function ensureBundledRuntime() {
  const required = [path.join(bundledRuntime, 'tools', 'cli', 'titan.py'), requirementsPath.replace(versionDir, bundledRuntime)];
  if (!fs.existsSync(required[0]) || !fs.existsSync(required[1])) fail(`Titan runtime is missing from npm package: ${bundledRuntime}`);
  fs.mkdirSync(versionsDir, { recursive: true });
  const runtimeCurrent = fs.existsSync(runtimeScript)
    && fs.existsSync(requirementsPath)
    && directoryDigest(versionDir, new Set(['.venv'])) === directoryDigest(bundledRuntime);
  if (!runtimeCurrent) {
    const temporary = path.join(versionsDir, `.${packageVersion}.tmp-${process.pid}`);
    fs.rmSync(temporary, { recursive: true, force: true });
    copyRecursive(bundledRuntime, temporary);
    try {
      if (fs.existsSync(versionDir)) fs.renameSync(versionDir, `${versionDir}.previous-${process.pid}`);
      fs.renameSync(temporary, versionDir);
    } catch (error) {
      if (!fs.existsSync(versionDir)) throw error;
      fs.rmSync(temporary, { recursive: true, force: true });
    }
  }
  return versionDir;
}

function ensureVenv() {
  if (process.env.TITAN_NPM_NO_VENV === '1') return findPython();
  const systemPython = findPython();
  const py = pythonPath();
  if (!fs.existsSync(py)) {
    fs.mkdirSync(versionDir, { recursive: true });
    console.error(`[titan-memory-cli] setting up Python runtime in ${venvDir}`);
    runChecked(systemPython, ['-m', 'venv', venvDir]);
  }
  const stamp = path.join(venvDir, `.titan-memory-cli-${packageVersion}`);
  if (!fs.existsSync(stamp)) {
    console.error('[titan-memory-cli] installing Python dependencies');
    runChecked(py, ['-m', 'pip', 'install', '--quiet', '-r', requirementsPath]);
    fs.writeFileSync(stamp, `${new Date().toISOString()}\n`, 'utf8');
  }
  return py;
}

function writeAtomicJson(target, payload) {
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temporary = `${target}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(temporary, target);
  try { fs.chmodSync(target, 0o600); } catch (_) { /* best effort on Windows */ }
}

function directoryDigest(root, ignored = new Set()) {
  const hash = crypto.createHash('sha256');
  function visit(current, relative) {
    for (const entry of fs.readdirSync(current).sort()) {
      if (ignored.has(entry)
        || entry === '__pycache__'
        || entry === '.pytest_cache'
        || entry === '.mypy_cache'
        || entry === '.DS_Store'
        || entry.endsWith('.pyc')
        || entry.endsWith('.pyo')) continue;
      const source = path.join(current, entry);
      const childRelative = relative ? path.join(relative, entry) : entry;
      const stat = fs.statSync(source);
      if (stat.isDirectory()) {
        visit(source, childRelative);
      } else if (stat.isFile()) {
        hash.update(childRelative.replaceAll(path.sep, '/'));
        hash.update('\0');
        hash.update(fs.readFileSync(source));
        hash.update('\0');
      }
    }
  }
  visit(root, '');
  return hash.digest('hex');
}

function ensureMarketplace() {
  if (!fs.existsSync(marketplaceManifestSource)) fail('Bundled Codex marketplace snapshot is missing from npm package.');
  const sourceDigest = directoryDigest(marketplaceSource);
  const targetDigest = fs.existsSync(marketplaceDir)
    ? directoryDigest(marketplaceDir, new Set(['.titan-marketplace-digest']))
    : null;
  if (targetDigest === sourceDigest && fs.existsSync(path.join(marketplaceDir, '.codex-plugin', 'plugin.json'))) {
    return marketplaceDir;
  }
  fs.mkdirSync(path.dirname(marketplaceDir), { recursive: true, mode: 0o700 });
  const temporary = `${marketplaceDir}.tmp-${process.pid}`;
  fs.rmSync(temporary, { recursive: true, force: true });
  copyRecursive(marketplaceSource, temporary);
  if (fs.existsSync(marketplaceDir)) fs.renameSync(marketplaceDir, `${marketplaceDir}.previous-${process.pid}`);
  fs.renameSync(temporary, marketplaceDir);
  return marketplaceDir;
}

function activateManagedRuntime() {
  const stableRoot = ensureBundledRuntime();
  const py = ensureVenv();
  const marketplace = ensureMarketplace();
  const manifest = {
    schema_version: 1,
    package: 'titan-memory-cli',
    version: packageVersion,
    runtime_root: stableRoot,
    python: py,
    entrypoint: runtimeScript,
    marketplace,
  };
  const expectedCurrent = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  if (fs.existsSync(manifestPath)) {
    const previous = `${runtimeHome}/previous.json`;
    const current = fs.readFileSync(manifestPath);
    // Only rotate the pointer when activation changes the current runtime.
    // Re-running the same package must preserve the older rollback target.
    if (!current.equals(expectedCurrent)
      && (!fs.existsSync(previous) || !current.equals(fs.readFileSync(previous)))) {
      try {
        writeAtomicJson(previous, JSON.parse(current.toString('utf8')));
      } catch (_) {
        // A damaged pointer should not prevent repair; the versioned runtime
        // directories remain available for manual rollback.
      }
    }
  }
  writeAtomicJson(manifestPath, manifest);
  return { python: py, entrypoint: runtimeScript, runtimeRoot: stableRoot };
}

function runTitan() {
  const managed = activateManagedRuntime();
  const env = { ...process.env };
  env.PYTHONPATH = env.PYTHONPATH ? `${managed.runtimeRoot}${path.delimiter}${env.PYTHONPATH}` : managed.runtimeRoot;
  const child = spawn(managed.python, [managed.entrypoint, ...process.argv.slice(2)], { stdio: 'inherit', env });
  child.on('error', (error) => fail('Failed to launch Titan', error));
  child.on('exit', (code, signal) => {
    if (signal) { process.kill(process.pid, signal); return; }
    process.exit(code || 0);
  });
}

runTitan();
