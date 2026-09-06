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

The older `titan-karu` checkout and the standalone
[`titan-memory-cli`](https://github.com/kuwosaad/titan-memory-cli) repository are
compatibility/recovery distributions. They must not become a second source of
truth or a separately maintained hand-written runtime. Production changes
originate here, graduate through the canonical tests, and are distributed from
this repository.

The public `titan-memory-cli` name is preserved for existing users, but its
distributions have separate authorities: the repository-root `pyproject.toml`
owns the Python/PyPI distribution, while
`packages/titan-memory-cli/package.json` owns the npm distribution. The
standalone checkout must not independently publish a stale engine. A generated
compatibility sync is appropriate only when a real consumer requires it; it is
not permission to maintain two runtimes.

## Release authority

For the current package checks and release sequences, see
[`docs/pypi_titan_memory_cli.md`](pypi_titan_memory_cli.md). The npm path
regenerates the bundled runtime, runs final checks after an explicit
`--no-git-tag-version` bump, commits exact reviewed metadata, creates an
annotated scoped tag, pushes the source, and only then publishes from
`packages/titan-memory-cli/`. The PyPI path builds and publishes the
repository-root `pyproject.toml` distribution separately. Publishing the
standalone checkout is not part of either flow.

## Codex storage and setup

Codex writes to its own local namespace, normally:

```text
~/.titan/agents/codex
```

Pi uses `~/.titan/agents/pi`. Codex does not write Pi data. Its default recall
path is a read-only federation over every valid agent namespace discovered
under the shared Titan root; absent, invalid, or unreadable namespaces are
skipped. Codex continues writing only to `codex`, while cross-agent imports
remain explicit. This is the live Codex MCP behavior: recall tools query the
federation by default, but write tools, passive hook capture, settings, neural
state, patterns, and graphs stay in the Codex namespace.

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
