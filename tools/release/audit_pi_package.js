'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const requiredPiPayloads = [
  'README.md',
  'LICENSE',
  'requirements.txt',
  'package.json',
  'app/api/routes.py',
  'app/graph/cortex_analysis.py',
  'app/graph/corpus_analysis.py',
  'app/graph/ui/client.js',
  'app/graph/ui/styles.css',
  'app/graph/ui/template.html',
  'app/patterns/memory.py',
  'app/runtime/context.py',
  'app/save_pipeline/trace_intake.py',
  'app/storage/sqlite.py',
  'config/__init__.py',
  'config/.env.example',
  'config/embedding_models.yaml',
  'config/extraction_models.yaml',
  'config/settings.yaml',
  'config/visual_config.yaml',
  'entrypoints/__init__.py',
  'entrypoints/main.py',
  'entrypoints/mcp_server.py',
  'tools/pi_extension/index.ts',
  'tools/pi_extension/install.sh',
  'tools/pi_extension/README.md',
  'tools/pi_extension/server.py',
  'tools/pi_extension/titan_dashboard.py',
  'tools/pi_extension/prompts/memory-sync.md',
  'tools/pi_extension/skills/memory-sync/SKILL.md',
  'tools/pi_extension/skills/titan-memory-workflow/SKILL.md',
  'assets/titan-pi-card.png',
  'assets/titan-pi-banner.png',
];

function existingFile(file) {
  try { return fs.statSync(file).isFile(); } catch (_) { return false; }
}

function windowsNpmCli() {
  const candidates = [];
  if (process.execPath) {
    const nodeDirectory = path.dirname(process.execPath);
    candidates.push(path.join(nodeDirectory, 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    candidates.push(path.join(nodeDirectory, '..', 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    candidates.push(path.join(nodeDirectory, '..', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
  }
  const delimiter = process.platform === 'win32' ? ';' : path.delimiter;
  for (const entry of (process.env.PATH || '').split(delimiter)) {
    if (!entry) continue;
    const directory = path.resolve(entry);
    candidates.push(path.join(directory, 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    candidates.push(path.join(directory, '..', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    candidates.push(path.join(directory, '..', 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
  }
  const npmCli = [...new Set(candidates)].find(existingFile);
  if (!npmCli) {
    throw new Error('could not locate npm-cli.js for the Windows npm fallback');
  }
  return npmCli;
}

function npmInvocation() {
  // npm_execpath points at npm-cli.js in npm lifecycle environments. Running
  // it through the current Node executable works on Windows as well as POSIX.
  if (process.env.npm_execpath && process.execPath) {
    return { command: process.execPath, args: [process.env.npm_execpath] };
  }
  if (process.platform === 'win32') {
    // spawnSync does not reliably resolve bare npm.cmd without a shell. Resolve
    // npm-cli.js and invoke it through Node instead.
    return { command: process.execPath, args: [windowsNpmCli()] };
  }
  return { command: 'npm', args: [] };
}

function packageRootFromArgs() {
  const index = process.argv.indexOf('--root');
  return path.resolve(index >= 0 ? process.argv[index + 1] : path.join(__dirname, '..', '..'));
}

const packageRoot = packageRootFromArgs();
if (!fs.existsSync(path.join(packageRoot, 'package.json'))) {
  console.error(`[titan-pi] privacy audit failed: package.json is missing from ${packageRoot}`);
  process.exit(1);
}

let npm;
try {
  npm = npmInvocation();
} catch (error) {
  console.error(`[titan-pi] privacy audit failed: ${error.message}`);
  process.exit(1);
}
const packed = spawnSync(
  npm.command,
  [...npm.args, 'pack', '--dry-run', '--json', '--ignore-scripts'],
  { cwd: packageRoot, encoding: 'utf8' },
);

if (packed.status !== 0) {
  console.error('[titan-pi] privacy audit failed: npm pack could not resolve the package contents');
  if (packed.stderr) console.error(packed.stderr.trim());
  process.exit(1);
}

let packageFiles;
try {
  const start = packed.stdout.indexOf('[');
  const end = packed.stdout.lastIndexOf(']');
  packageFiles = JSON.parse(packed.stdout.slice(start, end + 1))[0].files;
  if (!Array.isArray(packageFiles) || packageFiles.some((entry) => !entry || typeof entry.path !== 'string')) {
    throw new Error('npm pack metadata has no valid files list');
  }
} catch (error) {
  console.error(`[titan-pi] privacy audit failed: invalid npm pack output (${error.message})`);
  process.exit(1);
}

const forbiddenPrefixes = [
  'entrypoints/overnight/',
  'config/overnight',
  'tests/',
  'docs/research/',
  'traces/',
  'out/',
];

const forbiddenTextPatterns = [
  ['macOS home path', /\/Users\/[A-Za-z0-9._-]+(?:\/|$)/],
  ['Linux home path', /\/home\/[A-Za-z0-9._-]+(?:\/|$)/],
  ['Windows home path', /[A-Za-z]:\\Users\\[^\\\s]+\\/i],
  ['email address', /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i],
  ['personal preference example', /Kuwo is a beginner learning Python/i],
  ['personal preference example', /Kuwo prefers direct instructions/i],
  ['personal identity example', /arbitrary karu\.md/i],
  ['OpenAI-style secret', /\bsk-[A-Za-z0-9_-]{16,}\b/],
  ['GitHub-style secret', /\bgh[pousr]_[A-Za-z0-9]{20,}\b/],
  ['Google-style secret', /\bAIza[0-9A-Za-z_-]{20,}\b/],
  ['AWS access key', /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/],
  ['bearer token', /\bBearer\s+[A-Za-z0-9._~+/-]{16,}/i],
  ['private key', /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
  ['credential assignment', /\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)\s*[:=]\s*(?!(?:["']?)(?:YOUR(?:_[A-Z0-9]+)*|<[^>]+>|\$\{[^}]+\}|\[REDACTED\])(?:["']?))(?:["'][^"'\n]{12,}["']|[A-Za-z0-9][A-Za-z0-9./+=:-]{11,})/i],
];

const founderTermPattern = /\b(?:Kuwo|Karu|Saad|Mohammad|Ayanokoji)\b/i;
const allowedLegacyReferences = new Map([
  ['app/save_pipeline/pipeline.py', [/openclaw-hook:titan-karu-bridge/i]],
]);
const forbiddenAgentNotePattern = /(^|\/)(?:AGENTS|CONTEXT)\.md$/i;

const violations = [];
const packagedPaths = new Set();
for (const entry of packageFiles) {
  const relative = entry.path.replaceAll('\\', '/');
  packagedPaths.add(relative);
  if (forbiddenAgentNotePattern.test(relative)) {
    violations.push(`${relative}: internal agent notes are not release content`);
  }
  if (forbiddenPrefixes.some((prefix) => relative.startsWith(prefix))) {
    violations.push(`${relative}: development-only path`);
  }

  const file = path.join(packageRoot, relative);
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) continue;
  const data = fs.readFileSync(file);
  if (data.includes(0) || data.length > 2_000_000) continue;

  let text;
  try {
    text = data.toString('utf8');
    if (Buffer.from(text, 'utf8').compare(data) !== 0) continue;
  } catch {
    continue;
  }

  for (const [label, pattern] of forbiddenTextPatterns) {
    if (pattern.test(text)) violations.push(`${relative}: ${label}`);
  }
  const allowedLines = allowedLegacyReferences.get(relative) || [];
  text.split(/\r?\n/).forEach((line, index) => {
    if (!founderTermPattern.test(line)) return;
    if (!allowedLines.some((pattern) => pattern.test(line))) {
      violations.push(`${relative}:${index + 1}: founder-specific text`);
    }
  });
}

for (const relative of requiredPiPayloads) {
  if (!packagedPaths.has(relative)) {
    violations.push(`${relative}: required Pi payload is missing from npm pack`);
  }
  const absolute = path.join(packageRoot, relative);
  if (!fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
    violations.push(`${relative}: required Pi payload is missing from the package tree`);
  }
}

if (violations.length > 0) {
  console.error('[titan-pi] privacy audit rejected the npm package:');
  for (const violation of [...new Set(violations)].sort()) console.error(`- ${violation}`);
  process.exit(1);
}

console.error(`[titan-pi] privacy audit passed (${packageFiles.length} packaged files)`);
