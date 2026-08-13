#!/usr/bin/env node
'use strict';

const { spawnSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const packageRoot = path.resolve(__dirname, '..');
const runtimeDir = path.join(packageRoot, 'runtime');
const titanScript = path.join(runtimeDir, 'tools', 'cli', 'titan.py');
const requirementsPath = path.join(runtimeDir, 'requirements.txt');
const venvDir = process.env.TITAN_NPM_VENV || path.join(os.homedir(), '.titan', 'npm-python');
const stampPath = path.join(venvDir, '.titan-memory-cli-0.1.3');

function fail(message, error) {
  console.error(`[titan-memory-cli] ${message}`);
  if (error && error.message) {
    console.error(`[titan-memory-cli] ${error.message}`);
  }
  process.exit(1);
}

function commandExists(command) {
  const result = spawnSync(command, ['--version'], { stdio: 'ignore' });
  return result.status === 0;
}

function findPython() {
  const candidates = [];
  if (process.env.PYTHON) candidates.push(process.env.PYTHON);
  candidates.push('python3', 'python');
  for (const candidate of candidates) {
    if (commandExists(candidate)) return candidate;
  }
  fail('Python 3.10+ is required. Install Python, then rerun titan.');
}

function venvPythonPath() {
  if (process.platform === 'win32') {
    return path.join(venvDir, 'Scripts', 'python.exe');
  }
  return path.join(venvDir, 'bin', 'python');
}

function runChecked(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: options.stdio || 'inherit',
    env: options.env || process.env,
  });
  if (result.error) {
    fail(`Failed to run ${command}`, result.error);
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function ensureRuntimeExists() {
  if (!fs.existsSync(titanScript)) {
    fail(`Titan runtime is missing from npm package: ${titanScript}`);
  }
  if (!fs.existsSync(requirementsPath)) {
    fail(`Titan requirements are missing from npm package: ${requirementsPath}`);
  }
}

function ensureVenv() {
  const systemPython = findPython();
  const py = venvPythonPath();
  if (!fs.existsSync(py)) {
    fs.mkdirSync(venvDir, { recursive: true });
    console.error(`[titan-memory-cli] setting up Python runtime in ${venvDir}`);
    runChecked(systemPython, ['-m', 'venv', venvDir], { stdio: ['ignore', 'ignore', 'inherit'] });
  }

  if (!fs.existsSync(stampPath)) {
    console.error('[titan-memory-cli] installing Python dependencies');
    runChecked(py, ['-m', 'pip', 'install', '--quiet', '--upgrade', 'pip'], { stdio: ['ignore', 'ignore', 'inherit'] });
    runChecked(py, ['-m', 'pip', 'install', '--quiet', '-r', requirementsPath], { stdio: ['ignore', 'ignore', 'inherit'] });
    fs.mkdirSync(venvDir, { recursive: true });
    fs.writeFileSync(stampPath, new Date().toISOString() + '\n', 'utf8');
  }

  return py;
}

function runTitan() {
  ensureRuntimeExists();
  const py = process.env.TITAN_NPM_NO_VENV === '1' ? findPython() : ensureVenv();
  const env = { ...process.env };
  env.PYTHONPATH = env.PYTHONPATH ? `${runtimeDir}${path.delimiter}${env.PYTHONPATH}` : runtimeDir;

  const child = spawn(py, [titanScript, ...process.argv.slice(2)], {
    stdio: 'inherit',
    env,
  });

  child.on('error', (error) => fail('Failed to launch Titan', error));
  child.on('exit', (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code || 0);
  });
}

runTitan();
