---
name: titan-setup
description: Create or verify Titan Memory setup for Grok. Use when the user runs /titan-setup, first enables Titan, or needs agent home, env keys, plugin, or MCP wiring.
---

# Titan Setup

Prepare and verify Titan Memory for the Grok agent.

## Steps

1. Create/verify the agent home:

```text
~/.titan/agents/grok/
  .env
  config/
  traces/
  out/memories/memory_store.db
```

You may run:

```bash
python3 integrations/grok_titan_plugin/scripts/titan_first_run.py --prepare
```

2. Ensure a provider key exists in `~/.titan/agents/grok/.env` (for example `GEMINI_API_KEY` or another supported key). Never print secret values.
3. Confirm the `titan-memory` plugin is installed under `~/.grok/plugins/titan-memory` (or loaded via plugin path) and listed in `[plugins].enabled` if needed.
4. Confirm MCP: `/mcps` should show `titan-memory` connected. Plugin servers under `~/.grok/plugins/` are trusted automatically.
5. Confirm health with `/titan-status` or by calling `doctor`.

## First prompts after setup

- Recall prior work with `/titan-query`
- Save a decision with `/titan-save`
- Show recent memories with `/titan-recent`
- Check doctor with `/titan-status`
