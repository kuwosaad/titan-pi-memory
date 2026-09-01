# Repository ownership

`titan-pi-memory` is the canonical Titan repository.

It is the source of truth for:

- the memory engine, storage, retrieval, and runtime contracts;
- all supported agent adapters, including Pi and Codex;
- the Codex plugin and CLI packaging;
- tests, compatibility checks, and release configuration.

Pi is one adapter and one npm distribution package. It is not the boundary of
engine ownership. The engine and shared contracts remain canonical even when a
user installs Titan through Pi, Codex, or the CLI.

The older `titan-karu` checkout and separate Codex/CLI repositories are
compatibility distributions. They may be archived later and must not become a
second source of truth. Production changes graduate into this repository and
must pass the canonical tests before distribution.

## Codex storage and setup

Codex writes to its own local namespace, normally:

```text
~/.titan/agents/codex
```

Pi uses `~/.titan/agents/pi`. Codex does not write Pi data. Its default recall
path is a read-only federation over `codex` and `pi`; an absent namespace is
skipped and other agent namespaces require explicit source selection. Codex
continues writing only to `codex`, while cross-agent imports remain explicit.
This is the live Codex MCP behavior: recall tools query the federation by
default, but write tools and passive hook capture stay in the Codex namespace.

The supported local setup and repair entrypoints are:

```bash
titan setup codex
titan setup codex --verify
titan codex verify
titan codex reinstall-plugin
```

Hook trust remains a manual Codex safety decision. Users must inspect and
trust hooks in `/hooks`; CLI checks cannot claim that the live Codex session
has loaded MCP tools or enabled passive capture. Confirm live state with
`/mcp` and `/hooks`.
