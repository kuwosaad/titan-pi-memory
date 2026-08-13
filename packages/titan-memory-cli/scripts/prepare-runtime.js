'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(packageRoot, '..', '..');
const runtimeDir = path.join(packageRoot, 'runtime');

const copyDirs = ['app', 'config', 'entrypoints', 'integrations', 'tools'];
const copyFiles = ['requirements.txt', 'LICENSE'];
const ignoredNames = new Set([
  '__pycache__',
  '.pytest_cache',
  '.mypy_cache',
  '.DS_Store',
  'node_modules',
  'dist',
  'build',
]);

function shouldSkip(src) {
  const base = path.basename(src);
  if (ignoredNames.has(base)) return true;
  if (base.endsWith('.pyc')) return true;
  if (base.endsWith('.pyo')) return true;
  if (base.endsWith('.egg-info')) return true;
  return false;
}

function copyRecursive(src, dest) {
  if (shouldSkip(src)) return;
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      copyRecursive(path.join(src, entry), path.join(dest, entry));
    }
    return;
  }
  if (stat.isFile()) {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
  }
}

fs.rmSync(runtimeDir, { recursive: true, force: true });
fs.mkdirSync(runtimeDir, { recursive: true });

for (const dir of copyDirs) {
  copyRecursive(path.join(repoRoot, dir), path.join(runtimeDir, dir));
}

for (const file of copyFiles) {
  copyRecursive(path.join(repoRoot, file), path.join(runtimeDir, file));
}

fs.copyFileSync(path.join(repoRoot, 'LICENSE'), path.join(packageRoot, 'LICENSE'));

console.log(`[titan-memory-cli] prepared runtime at ${runtimeDir}`);
