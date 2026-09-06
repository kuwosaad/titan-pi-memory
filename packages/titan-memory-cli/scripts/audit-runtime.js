'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageRoot = path.resolve(process.argv[2] || path.resolve(__dirname, '..'));
const runtimeRoot = path.join(packageRoot, 'runtime');

const requiredRuntimeFiles = [
  'app/api/routes.py',
  'app/graph/cortex_analysis.py',
  'entrypoints/main.py',
  'entrypoints/mcp_server.py',
  'integrations/codex_titan_plugin/.agents/plugins/marketplace.json',
  'integrations/codex_titan_plugin/.codex-plugin/plugin.json',
  'integrations/codex_titan_plugin/.mcp.json',
  'integrations/codex_titan_plugin/hooks/hooks.json',
  'integrations/claude_titan_plugin/.claude-plugin/plugin.json',
  'integrations/claude_titan_plugin/.mcp.json',
  'tools/cli/titan.py',
];
const allowedSuffixes = new Set([
  '.css', '.html', '.js', '.json', '.md', '.png', '.py', '.sh', '.ts', '.yaml', '.yml',
]);
const forbiddenPathPrefixes = [
  'entrypoints/overnight/',
  'tools/benchmarks/',
  'tools/dev/',
  'tools/pi_extension/',
  'tools/presentations/',
  'tools/scripts/',
];

const forbiddenFilePatterns = [
  /(^|\/)(?:memory_store|memories|scenes|sessions|traces?)\.(?:db|sqlite3?|json|jsonl)$/i,
  /(^|\/)(?:credentials?|secrets?|auth(?:entication)?|private[-_]?keys?)(?:[-_.][^/]*)?$/i,
  /\.(?:pem|key|p12|pfx)$/i,
];

const forbiddenTextPatterns = [
  { label: 'macOS home path', pattern: /\/Users\/[A-Za-z0-9._-]+(?:\/|$)/ },
  { label: 'Linux home path', pattern: /\/home\/[A-Za-z0-9._-]+(?:\/|$)/ },
  { label: 'Windows home path', pattern: /[A-Za-z]:\\Users\\[^\\\s]+\\/i },
  { label: 'email address', pattern: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i },
  { label: 'founder-specific retrieval actors', pattern: /PROFILE_ACTOR_TERMS\s*=/i },
  { label: 'founder-specific extraction policy', pattern: /when the exchange clearly refers to Kuwo and Karu/i },
  { label: 'personal preference example', pattern: /Kuwo is a beginner learning Python/i },
  { label: 'personal preference example', pattern: /Kuwo prefers direct instructions/i },
  { label: 'personal identity alias', pattern: /['"]karu['"]\s*:\s*['"]assistant['"]/i },
  { label: 'personal identity alias', pattern: /['"]kuwo['"]\s*:\s*['"]user['"]/i },
  { label: 'personal actor defaults', pattern: /['"]saad['"]\s*,\s*['"]kuwo['"]/i },
  { label: 'OpenAI-style secret', pattern: /\bsk-[A-Za-z0-9_-]{16,}\b/ },
  { label: 'Google-style secret', pattern: /\bAIza[0-9A-Za-z_-]{20,}\b/ },
  { label: 'credential assignment', pattern: /\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)\s*[:=]\s*(?!(?:[\"']?)(?:YOUR(?:_[A-Z0-9]+)*|<[^>]+>|\$\{[^}]+\}|\[REDACTED\])(?:[\"']?))(?:[\"'][^\"'\n]{12,}[\"']|[A-Za-z0-9][A-Za-z0-9./+=:-]{11,})/i },
  { label: 'email address', pattern: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i },
];

const founderTermPattern = /\b(?:Kuwo|Karu|Saad|Mohammad)\b/i;
const genericPersonalPathPattern = /(?<![A-Za-z0-9_.-])\/(?:Users|home)\/[A-Za-z0-9._-]+(?:\/|$)/i;
const windowsPersonalPathPattern = /\b[A-Z]:[\\/]Users[\\/][^\\/\s'\"<>]+/i;
const forbiddenAgentNoteNames = new Set(['AGENTS.md', 'CONTEXT.md']);
const forbiddenAgentNotesPattern = /(^|\/)(?:AGENTS|CONTEXT)\.md$/i;
const forbiddenAgentNotesSuffixPattern = /(^|\/)[^/]*_agents\.md$/i;
const allowedExtensionlessFiles = new Set(['LICENSE', 'requirements.txt']);
const allowedLegacyReferences = new Map([
  ['app/graph/clusters.py', [/"karu"/i]],
  ['app/save_pipeline/pipeline.py', [/openclaw-hook:titan-karu-bridge/i]],
  ['tools/cli/titan.py', [/titan-memory@titan-karu-lab/i, /"titan-karu-lab"/i]],
]);

function collectFiles(root, prefix = '') {
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...collectFiles(absolute, relative));
    else if (entry.isFile()) files.push({ absolute, relative });
  }
  return files;
}

function readableText(file) {
  const content = fs.readFileSync(file);
  if (content.includes(0)) return null;
  return content.toString('utf8');
}

function isFile(file) {
  try { return fs.statSync(file).isFile(); } catch (_) { return false; }
}

if (!fs.existsSync(runtimeRoot)) {
  console.error(`[titan-memory-cli] runtime audit failed: runtime/ does not exist (${runtimeRoot})`);
  process.exit(1);
}

const violations = [];
for (const relative of requiredRuntimeFiles) {
  if (!isFile(path.join(runtimeRoot, relative))) {
    violations.push(`runtime/${relative}: required release file is missing`);
  }
}
const runtimeFiles = collectFiles(runtimeRoot);
for (const file of runtimeFiles) {
  if (forbiddenAgentNoteNames.has(path.posix.basename(file.relative))
    || forbiddenAgentNotesPattern.test(file.relative)
    || forbiddenAgentNotesSuffixPattern.test(file.relative)) {
    violations.push(`${file.relative}: internal agent notes are not release content`);
  }
  const suffix = path.extname(file.relative).toLowerCase();
  if (!allowedExtensionlessFiles.has(file.relative)
    && file.relative !== 'config/.env.example'
    && suffix !== '' && !allowedSuffixes.has(suffix)) {
    violations.push(`${file.relative}: unexpected artifact file type`);
  }
  if (!allowedExtensionlessFiles.has(file.relative)
    && file.relative !== 'config/.env.example'
    && suffix === '') {
    violations.push(`${file.relative}: unexpected extensionless artifact file`);
  }
  if (forbiddenPathPrefixes.some((prefix) => file.relative.startsWith(prefix))) {
    violations.push(`${file.relative}: development-only path`);
  }
  if (forbiddenFilePatterns.some((pattern) => pattern.test(file.relative))) {
    violations.push(`${file.relative}: private-data file type`);
  }
  const text = readableText(file.absolute);
  if (text === null) continue;
  for (const check of forbiddenTextPatterns) {
    if (check.pattern.test(text)) violations.push(`${file.relative}: ${check.label}`);
  }
  if (genericPersonalPathPattern.test(text)) violations.push(`${file.relative}: local home path`);
  if (windowsPersonalPathPattern.test(text)) violations.push(`${file.relative}: local Windows user path`);
  const allowedLines = allowedLegacyReferences.get(file.relative) || [];
  text.split(/\r?\n/).forEach((line, index) => {
    if (!founderTermPattern.test(line)) return;
    if (!allowedLines.some((pattern) => pattern.test(line))) {
      violations.push(`${file.relative}:${index + 1}: founder-specific text`);
    }
  });
}

for (const relative of ['README.md', 'package.json', 'bin/titan.js']) {
  const absolute = path.join(packageRoot, relative);
  if (!isFile(absolute)) {
    violations.push(`${relative}: required package file is missing`);
    continue;
  }
  const text = readableText(absolute);
  if (text === null) continue;
  for (const check of forbiddenTextPatterns) {
    if (check.pattern.test(text)) violations.push(`${relative}: ${check.label}`);
  }
  if (genericPersonalPathPattern.test(text)) violations.push(`${relative}: local home path`);
  if (windowsPersonalPathPattern.test(text)) violations.push(`${relative}: local Windows user path`);
}

if (violations.length > 0) {
  console.error('[titan-memory-cli] runtime audit rejected the npm package:');
  for (const violation of [...new Set(violations)].sort()) console.error(`- ${violation}`);
  process.exit(1);
}

console.log(`[titan-memory-cli] runtime audit passed (${runtimeFiles.length} files)`);
