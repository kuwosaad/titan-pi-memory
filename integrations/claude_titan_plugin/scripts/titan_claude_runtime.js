'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const pluginRoot = path.resolve(__dirname, '..');
const pluginPackage = require(path.join(pluginRoot, 'package.json'));
const RUNTIME_PACKAGE = 'titan-memory-cli';
const RUNTIME_PACKAGE_VERSION = pluginPackage.dependencies[RUNTIME_PACKAGE];
const BOOTSTRAP_LOCK_STALE_MS = 10 * 60 * 1000;
const DEFAULT_BOOTSTRAP_TIMEOUT_MS = 120000;
const PACKAGE_METADATA_FILES = ['package.json', 'package-lock.json'];

function pluginDataRoot() {
  return path.resolve(
    process.env.CLAUDE_PLUGIN_DATA
      || process.env.TITAN_CLAUDE_DATA
      || path.join(os.homedir(), '.titan', 'claude-plugin'),
  );
}

function managedRuntimeHome() {
  return path.join(pluginDataRoot(), 'managed-runtime');
}

function packagedDependenciesRoot() {
  return path.join(managedRuntimeHome(), 'package');
}

function managedManifestPath() {
  return path.join(managedRuntimeHome(), 'current.json');
}

function pythonInVenv(root) {
  return process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python');
}

function developmentRuntime() {
  const repoRoot = path.resolve(pluginRoot, '..', '..');
  const python = pythonInVenv(repoRoot);
  if (!fs.existsSync(python)) return null;
  return { python, runtimeRoot: repoRoot };
}

function readManagedRuntime() {
  const manifestPath = managedManifestPath();
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    if (manifest.package !== RUNTIME_PACKAGE || manifest.version !== RUNTIME_PACKAGE_VERSION) {
      return null;
    }
    const python = path.resolve(String(manifest.python || ''));
    const runtimeRoot = path.resolve(String(manifest.runtime_root || ''));
    if (!python || !runtimeRoot || !fs.statSync(python).isFile() || !fs.statSync(runtimeRoot).isDirectory()) {
      return null;
    }
    return { python, runtimeRoot };
  } catch (_) {
    return null;
  }
}

function sleepSync(milliseconds) {
  if (milliseconds <= 0) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function processAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error && error.code === 'EPERM';
  }
}

function bootstrapLockDir() {
  return path.join(managedRuntimeHome(), '.bootstrap.lock');
}

function readLockOwner(lockDir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(lockDir, 'owner.json'), 'utf8'));
  } catch (_) {
    return null;
  }
}

function staleBootstrapLock(lockDir, now = Date.now()) {
  const owner = readLockOwner(lockDir);
  if (owner && processAlive(Number(owner.pid))) return false;
  try {
    const timestamp = owner && Number(owner.started_at_ms)
      ? Number(owner.started_at_ms)
      : fs.statSync(lockDir).mtimeMs;
    return now - timestamp >= BOOTSTRAP_LOCK_STALE_MS || Boolean(owner);
  } catch (_) {
    return true;
  }
}

function tryAcquireBootstrapLock() {
  const runtimeHome = managedRuntimeHome();
  const lockDir = bootstrapLockDir();
  fs.mkdirSync(runtimeHome, { recursive: true, mode: 0o700 });
  try {
    fs.mkdirSync(lockDir, { mode: 0o700 });
  } catch (error) {
    if (!error || error.code !== 'EEXIST') throw error;
    if (!staleBootstrapLock(lockDir)) return null;
    fs.rmSync(lockDir, { recursive: true, force: true });
    try {
      fs.mkdirSync(lockDir, { mode: 0o700 });
    } catch (retryError) {
      if (retryError && retryError.code === 'EEXIST') return null;
      throw retryError;
    }
  }
  const ownerPath = path.join(lockDir, 'owner.json');
  fs.writeFileSync(
    ownerPath,
    `${JSON.stringify({ pid: process.pid, started_at_ms: Date.now() })}\n`,
    { encoding: 'utf8', mode: 0o600 },
  );
  return {
    release() {
      const owner = readLockOwner(lockDir);
      if (owner && Number(owner.pid) === process.pid) {
        fs.rmSync(lockDir, { recursive: true, force: true });
      }
    },
  };
}

function waitForManagedRuntime(timeoutMs = 1000) {
  const deadline = Date.now() + Math.max(0, timeoutMs);
  do {
    const runtime = readManagedRuntime();
    if (runtime) return runtime;
    sleepSync(50);
  } while (Date.now() < deadline);
  return readManagedRuntime();
}

function timeoutDeadline(timeoutMs) {
  const value = Number(timeoutMs);
  return Date.now() + (Number.isFinite(value) ? Math.max(0, value) : DEFAULT_BOOTSTRAP_TIMEOUT_MS);
}

function remainingTimeout(deadline, operation) {
  const remaining = Math.floor(deadline - Date.now());
  if (remaining <= 0) throw new Error(`Timed out ${operation}`);
  return remaining;
}

function copyPackageMetadata(packageRoot) {
  fs.mkdirSync(packageRoot, { recursive: true, mode: 0o700 });
  for (const name of PACKAGE_METADATA_FILES) {
    const source = path.join(pluginRoot, name);
    const sourceStat = fs.lstatSync(source);
    if (!sourceStat.isFile() || sourceStat.isSymbolicLink()) {
      throw new Error(`Packaged Claude runtime metadata is missing or unsafe: ${source}`);
    }
    const destination = path.join(packageRoot, name);
    fs.copyFileSync(source, destination);
    try { fs.chmodSync(destination, 0o600); } catch (_) { /* best effort on Windows */ }
  }
}

function installedPackagedPackage(packageRoot = packagedDependenciesRoot()) {
  // Do not use require.resolve with a search path here. Node walks parent
  // directories for that form, which can accidentally activate a developer's
  // checkout node_modules instead of the persistent plugin-data install.
  const packageJson = path.join(packageRoot, 'node_modules', RUNTIME_PACKAGE, 'package.json');
  try {
    const installed = JSON.parse(fs.readFileSync(packageJson, 'utf8'));
    if (installed.name !== RUNTIME_PACKAGE || installed.version !== RUNTIME_PACKAGE_VERSION) return null;
  } catch (_) {
    return null;
  }
  return path.dirname(packageJson);
}

function npmCliCandidates(environment = process.env) {
  const nodeDirectory = path.dirname(process.execPath);
  const candidates = [
    path.join(nodeDirectory, 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    path.join(nodeDirectory, '..', 'node_modules', 'npm', 'bin', 'npm-cli.js'),
  ];
  if (process.platform === 'win32') {
    const appData = environment.APPDATA;
    if (appData) candidates.push(path.join(appData, 'npm', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
  } else {
    candidates.push(path.join(nodeDirectory, '..', 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
  }
  return candidates;
}

function npmInvocation(options = {}) {
  if (options.npmCommand) {
    return {
      command: options.npmCommand,
      args: options.npmArgs || [],
      ...(options.npmShell === undefined ? {} : { shell: options.npmShell }),
    };
  }
  const environment = options.env || process.env;
  const npmExecPath = environment.npm_execpath;
  if (npmExecPath && fs.existsSync(npmExecPath) && !(/\.cmd$/i.test(npmExecPath) && process.platform === 'win32')) {
    return { command: process.execPath, args: [npmExecPath] };
  }
  for (const candidate of npmCliCandidates(environment)) {
    if (fs.existsSync(candidate)) return { command: process.execPath, args: [candidate] };
  }
  if (process.platform === 'win32') {
    // npm.cmd is a Windows batch file and cannot be spawned directly without
    // a shell. Keep the cmd.exe arguments fixed; never interpolate a path or
    // other user-controlled value into the /c command string.
    const commandShell = environment.ComSpec || environment.COMSPEC || 'cmd.exe';
    return { command: commandShell, args: ['/d', '/s', '/c', 'npm.cmd'] };
  }
  return { command: 'npm', args: [] };
}

function childDiagnostics(result) {
  return [result && result.stderr, result && result.stdout]
    .map((value) => String(value || '').trim())
    .filter(Boolean)
    .join('\n')
    .split(/\r?\n/)
    .slice(-6)
    .join('\n');
}

function removeFailedDependencyInstall(packageRoot) {
  try {
    fs.rmSync(path.join(packageRoot, 'node_modules'), { recursive: true, force: true });
  } catch (error) {
    throw new Error(`failed to clean incomplete Claude runtime dependency install: ${error.message}`);
  }
}

function installPackagedDependencies({ packageRoot, deadline, options = {} }) {
  try {
    copyPackageMetadata(packageRoot);
    const invocation = npmInvocation(options);
    const installed = spawnSync(
      invocation.command,
      [...invocation.args, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
      {
        cwd: packageRoot,
        env: { ...process.env, ...(options.env || {}) },
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        ...(invocation.shell === undefined ? {} : { shell: invocation.shell }),
        timeout: remainingTimeout(deadline, 'installing the Claude runtime dependency'),
      },
    );
    if (installed.error) {
      if (installed.error.code === 'ETIMEDOUT') {
        throw new Error('Timed out installing the Claude runtime dependency');
      }
      const detail = childDiagnostics(installed);
      throw new Error(`Failed to run npm ci: ${installed.error.message}${detail ? `: ${detail}` : ''}`);
    }
    if (installed.status !== 0) {
      const output = childDiagnostics(installed);
      const status = installed.signal
        ? `signal ${installed.signal}`
        : `exit code ${installed.status}`;
      throw new Error(`npm ci failed with ${status}${output ? `: ${output}` : ''}`);
    }
  } catch (error) {
    removeFailedDependencyInstall(packageRoot);
    throw error;
  }
}

function activatePackagedRuntime(env, options = {}) {
  const deadline = options.deadline === undefined
    ? timeoutDeadline(options.timeoutMs === undefined ? DEFAULT_BOOTSTRAP_TIMEOUT_MS : options.timeoutMs)
    : options.deadline;
  const packageRoot = packagedDependenciesRoot();
  let installedPackageRoot = installedPackagedPackage(packageRoot);
  if (!installedPackageRoot) {
    const installer = options.installDependencies || installPackagedDependencies;
    try {
      installer({ packageRoot, deadline, options: { ...options, env } });
    } catch (error) {
      // npm normally removes node_modules itself, but a killed process or a
      // test/custom installer can leave a misleading partial package behind.
      // Never let that partial state make a later retry skip installation.
      try {
        removeFailedDependencyInstall(packageRoot);
      } catch (cleanupError) {
        throw new Error(`${error.message}; ${cleanupError.message}`);
      }
      throw error;
    }
    installedPackageRoot = installedPackagedPackage(packageRoot);
  }
  if (!installedPackageRoot) {
    throw new Error(`Installed Claude runtime dependency is missing or has the wrong version under ${packageRoot}`);
  }
  const packageJson = path.join(installedPackageRoot, 'package.json');
  const packageInstallRoot = path.dirname(packageJson);
  const bin = path.join(packageInstallRoot, 'bin', 'titan.js');
  const activationEnv = {
    ...env,
    TITAN_RUNTIME_HOME: managedRuntimeHome(),
    TITAN_RUNTIME_MANIFEST: managedManifestPath(),
    TITAN_CLAUDE_DATA: pluginDataRoot(),
  };
  const activated = spawnSync(process.execPath, [bin, '--help'], {
    env: activationEnv,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: remainingTimeout(deadline, 'activating the Claude runtime'),
  });
  const detail = childDiagnostics(activated);
  if (activated.error && activated.error.code === 'ETIMEDOUT') {
    throw new Error(`Timed out activating the Claude runtime${detail ? `: ${detail}` : ''}`);
  }
  if (activated.error) {
    throw new Error(`Failed to activate the Claude runtime: ${activated.error.message}${detail ? `: ${detail}` : ''}`);
  }
  if (activated.status !== 0) {
    const status = activated.signal ? `signal ${activated.signal}` : `exit code ${activated.status}`;
    throw new Error(`Titan managed runtime activation failed with ${status}${detail ? `: ${detail}` : ''}`);
  }
  const runtime = readManagedRuntime();
  if (!runtime) {
    throw new Error(`Titan managed runtime manifest is incomplete: ${managedManifestPath()}`);
  }
  return runtime;
}

function ensurePackagedRuntime(env, options = {}) {
  const existing = readManagedRuntime();
  if (existing) return existing;
  const activate = options.activate || activatePackagedRuntime;
  const deadline = timeoutDeadline(options.timeoutMs === undefined
    ? DEFAULT_BOOTSTRAP_TIMEOUT_MS
    : options.timeoutMs);

  while (Date.now() <= deadline) {
    const lock = tryAcquireBootstrapLock();
    if (lock) {
      try {
        const current = readManagedRuntime();
        if (current) return current;
        return activate(env, { ...options, deadline });
      } finally {
        lock.release();
      }
    }
    const current = waitForManagedRuntime(Math.min(250, Math.max(0, deadline - Date.now())));
    if (current) return current;
  }
  throw new Error('Timed out waiting for Titan managed runtime bootstrap');
}

function launchPython(scriptName, { failOpen = false, input = null } = {}) {
  const env = {
    ...process.env,
    TITAN_CLAUDE_DATA: pluginDataRoot(),
  };
  let runtime;
  try {
    runtime = readManagedRuntime();
    if (!runtime && failOpen) {
      runtime = developmentRuntime() || waitForManagedRuntime(
        Number(process.env.TITAN_CLAUDE_HOOK_BOOTSTRAP_WAIT_MS || 1000),
      );
      if (!runtime) return;
    }
    if (!runtime) {
      runtime = ensurePackagedRuntime(env) || developmentRuntime();
    }
    if (!runtime) throw new Error('Titan managed runtime is unavailable; reinstall the plugin or run titan setup claude-code');
  } catch (error) {
    console.error(`[titan-memory] ${error.message}`);
    process.exit(failOpen ? 0 : 1);
    return;
  }

  env.PYTHONPATH = [pluginRoot, runtime.runtimeRoot, env.PYTHONPATH]
    .filter(Boolean)
    .join(path.delimiter);
  const script = path.join(pluginRoot, 'scripts', scriptName);
  const stdio = input === null ? 'inherit' : ['pipe', 'inherit', 'inherit'];
  const child = spawn(runtime.python, [script], { env, stdio });
  if (input !== null) child.stdin.end(input);
  child.on('error', (error) => {
    console.error(`[titan-memory] ${error.message}`);
    process.exit(failOpen ? 0 : 1);
  });
  child.on('exit', (code, signal) => {
    if (signal && !failOpen) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(failOpen ? 0 : (code || 0));
  });
}

module.exports = {
  activatePackagedRuntime,
  ensurePackagedRuntime,
  installPackagedDependencies,
  launchPython,
  managedManifestPath,
  packagedDependenciesRoot,
  pluginDataRoot,
  npmInvocation,
  readManagedRuntime,
  staleBootstrapLock,
  waitForManagedRuntime,
};
