---
name: titan-key
description: Set the Titan extraction API key for Grok. Use when the user runs /titan-key or extraction/save is failing for missing keys.
---

# Titan Key

Pi walks this interactively. In Grok, write the key into `~/.titan/agents/grok/.env` without echoing the secret.

Supported names: `OPENCODE_GO_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`.

After writing, run `titan-grok doctor` and confirm the key name exists (not the value).
