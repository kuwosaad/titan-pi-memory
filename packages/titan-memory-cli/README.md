# titan-memory-cli

Install the `titan` command with npm.

Titan Memory is a local-first memory runtime for coding agents. This npm package gives Codex and other agents a `titan` command without requiring a PyPI install.

## Install for Codex

Run one command:

```bash
npx -y titan-memory-cli@latest setup codex
```

That command prepares Titan, creates the Codex memory folder, asks which extraction model to use, configures the required `nomic-embed-text:v1.5` embedding model, installs the Codex plugin, patches Codex MCP config, and runs a health check.

Then do the one manual safety step Codex requires:

```text
open Codex
/hooks
approve Titan Memory
```

## Install the CLI globally

Optional, if you want `titan` available on your PATH:

```bash
npm install -g titan-memory-cli
```

Verify:

```bash
titan --help
titan codex list-tools
```

On first run, the wrapper creates a Python virtual environment at:

```text
~/.titan/npm-python
```

and installs Titan's Python dependencies there.

Codex requires manual hook trust. Titan does not bypass Codex's `/hooks` safety gate.

## Storage

Codex memory is isolated by default under:

```text
~/.titan/agents/codex
```

## Environment variables

- `TITAN_NPM_VENV`: override the Python venv path
- `TITAN_NPM_NO_VENV=1`: run with system Python instead of the managed venv
- `PYTHON`: choose the Python executable used to create the venv

## Links

- Codex plugin: https://github.com/kuwosaad/titan-memory-codex
- CLI source: https://github.com/kuwosaad/titan-memory-cli
- License: Apache-2.0
