#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const { launchPython } = require('./titan_claude_runtime');

const input = fs.readFileSync(0, 'utf8');
try {
  const payload = JSON.parse(input || '{}');
  const event = String(payload.hook_event_name || '');
  const captureMode = String(
    process.env.CLAUDE_PLUGIN_OPTION_capture_mode
      || process.env.CLAUDE_PLUGIN_OPTION_CAPTURE_MODE
      || process.env.TITAN_CLAUDE_CAPTURE_MODE
      || 'messages',
  ).toLowerCase();
  if ((event === 'PostToolUse' || event === 'PostToolUseFailure') && captureMode !== 'full') {
    process.exit(0);
  }
} catch (_) {
  // Let the Python hook apply its normal fail-open parsing behavior.
}

launchPython('titan_claude_hook.py', { failOpen: true, input });
