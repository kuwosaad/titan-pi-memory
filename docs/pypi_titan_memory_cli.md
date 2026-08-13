# titan-memory-cli

Titan Memory CLI and local-first memory runtime for coding agents.

This package installs the `titan` command used by Titan integrations, including the Codex plugin.

## Install

```bash
pip install titan-memory-cli
```

Verify:

```bash
titan --help
titan codex list-tools
```

## Codex plugin install

After installing the CLI, install the public Codex plugin:

```bash
npx codex-marketplace add kuwosaad/titan-memory-codex --plugin --global
```

Then open Codex and check:

```text
/plugins
/mcp
/hooks
```

Codex requires manual hook trust. Titan does not bypass Codex's `/hooks` safety gate.

## Local storage

By default, Codex memory is isolated under:

```text
~/.titan/agents/codex
```

Passive hook traces are stored under:

```text
~/.titan/agents/codex/traces
```

Cross-agent memory search/import is explicit. Codex memory does not automatically mix with Pi, Claude Code, Aider, or OpenCode memory.

## Useful commands

```bash
titan mcp --agent codex
titan setup codex --verify
titan codex doctor
titan codex reinstall-plugin
titan codex list-tools
```

## Links

- Codex plugin: https://github.com/kuwosaad/titan-memory-codex
- Titan repository: https://github.com/kuwosaad/titan-karu
- License: Apache-2.0
