# titan-memory-cli (canonical distribution)

Titan Memory CLI and local-first memory runtime for coding agents.

The canonical source for this CLI, the engine it launches, the Codex adapter,
tests, and release configuration is [`titan-pi-memory`](https://github.com/kuwosaad/titan-pi-memory).
Pi is one adapter/package in that source tree, not a separate ownership
boundary. Older Karu and standalone Codex/CLI repositories are compatibility
distributions.

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

## Codex plugin install and repair

After installing the CLI, use its setup entrypoint:

```bash
npx -y titan-memory-cli@latest setup codex
titan codex verify
```

If the local plugin registration needs repair, run:

```bash
titan codex reinstall-plugin
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

Codex writes only to its `codex` namespace. Its default recall path is a
read-only federation over `codex` plus `pi`; missing namespaces are skipped.
Claude Code, Aider, and OpenCode remain opt-in sources, and cross-agent imports
remain explicit operations. Codex retains write isolation even when it reads
Pi memories. In a live Codex session, the MCP recall tools use this federation
by default; writes and passive hook traces remain Codex-only.

## Useful commands

```bash
titan mcp --agent codex
titan setup codex --verify
titan codex doctor
titan codex reinstall-plugin
titan codex list-tools
```

Codex hook trust is intentionally manual. Open Codex, inspect `/hooks`, and
trust the Titan hook only if you want passive capture. CLI verification cannot
prove that the live Codex session has loaded MCP tools or trusted hooks; also
check `/mcp` and `/hooks` in that session.

## Links

- Codex adapter/plugin source: https://github.com/kuwosaad/titan-pi-memory/tree/main/integrations/codex_titan_plugin
- Titan repository: https://github.com/kuwosaad/titan-pi-memory
- License: Apache-2.0
