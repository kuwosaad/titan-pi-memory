---
name: setup
description: Open Titan’s local provider setup for this Claude Code namespace.
disable-model-invocation: true
allowed-tools: Bash
---

Open the bundled provider setup when the user invokes this skill. The active namespace is `${user_config.agent_name}` (default `claude-code`). Normalize the value to lower case and replace each run of non-alphanumeric characters with a hyphen. Trim leading and trailing hyphens. Stop if nothing remains. Pass only that normalized namespace as the final argument.

Use Bash to start the following command with `run_in_background: true`. Replace `claude-code` with the normalized namespace when different. Claude expands the plugin path in this skill when it loads.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/titan_runtime.py" configure --agent claude-code
```

Tell the user to select their extraction and embedding models and enter any keys in the local browser form. Leave the process running while they use it. Once they save, ask them to reload the plugin by typing `/reload-plugins`. Keep credentials out of chat and shell arguments.
