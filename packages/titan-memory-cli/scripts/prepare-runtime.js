'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(packageRoot, '..', '..');
const runtimeDir = path.join(packageRoot, 'runtime');

// This is a public release boundary, not a repository mirror. Keep the list
// explicit so research artifacts, benchmarks, local paths, fixtures, and
// personal examples cannot enter npm merely because they live in a copied
// top-level directory.
const runtimePaths = [
  'app',
  'config/.env.example',
  'config/embedding_models.yaml',
  'config/extraction_models.yaml',
  'config/settings.yaml',
  'config/visual_config.yaml',
  'entrypoints/__init__.py',
  'entrypoints/main.py',
  'entrypoints/mcp_server.py',
  'integrations/__init__.py',
  'integrations/codex_titan_plugin',
  'integrations/opencode_titan_plugin/dist/titan_v2_spool_plugin.ts',
  'tools/__init__.py',
  'tools/cli',
  'tools/opencode/__init__.py',
  'tools/opencode/install_plugin.py',
  'requirements.txt',
  'LICENSE',
];
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

for (const relativePath of runtimePaths) {
  copyRecursive(path.join(repoRoot, relativePath), path.join(runtimeDir, relativePath));
}

fs.copyFileSync(path.join(repoRoot, 'LICENSE'), path.join(packageRoot, 'LICENSE'));

console.log(`[titan-memory-cli] prepared runtime at ${runtimeDir}`);
