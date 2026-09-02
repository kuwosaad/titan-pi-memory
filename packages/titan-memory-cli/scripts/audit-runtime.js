'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageRoot = path.resolve(__dirname, '..');
const runtimeRoot = path.join(packageRoot, 'runtime');

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
  /\.(?:pem|key|p12|pfx)$/i,
];

const forbiddenTextPatterns = [
  { label: 'macOS home path', pattern: /\/Users\/[A-Za-z0-9._-]+\// },
  { label: 'Linux home path', pattern: /\/home\/[A-Za-z0-9._-]+\// },
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
];

const founderTermPattern = /\b(?:Kuwo|Karu|Saad|Mohammad)\b/i;
const allowedLegacyReferences = new Map([
  ['app/graph/clusters.py', [/"karu"/i]],
  ['app/save_pipeline/pipeline.py', [/openclaw-hook:titan-karu-bridge/i]],
  ['app/storage/memories.py', [/_ALLOWED_SPEAKER_FOCUS.*"kuwo".*"karu"/i]],
  ['app/storage/models.py', [/speaker_focus:.*Literal.*"kuwo".*"karu"/i]],
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

if (!fs.existsSync(runtimeRoot)) {
  console.error('[titan-memory-cli] runtime audit failed: runtime/ does not exist');
  process.exit(1);
}

const violations = [];
const runtimeFiles = collectFiles(runtimeRoot);
for (const file of runtimeFiles) {
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
  const text = readableText(absolute);
  if (text === null) continue;
  for (const check of forbiddenTextPatterns) {
    if (check.pattern.test(text)) violations.push(`${relative}: ${check.label}`);
  }
}

if (violations.length > 0) {
  console.error('[titan-memory-cli] runtime audit rejected the npm package:');
  for (const violation of [...new Set(violations)].sort()) console.error(`- ${violation}`);
  process.exit(1);
}

console.log(`[titan-memory-cli] runtime audit passed (${runtimeFiles.length} files)`);
