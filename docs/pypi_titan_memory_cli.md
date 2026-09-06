# titan-memory-cli (canonical distribution)

Titan Memory CLI and local-first memory runtime for coding agents.

The canonical source for this CLI, the engine it launches, the Codex adapter,
tests, and release configuration is [`titan-pi-memory`](https://github.com/kuwosaad/titan-pi-memory).
Pi is one adapter/package in that source tree, not a separate ownership
boundary. Older Titan checkouts and the standalone
[`titan-memory-cli`](https://github.com/kuwosaad/titan-memory-cli) repository
are compatibility distributions.

This package installs the `titan` command used by Titan integrations, including the Codex plugin. The standalone `titan-memory-cli` checkout is a compatibility/recovery distribution, not a release authority.

## Source and release authority

All future engine and shared-adapter changes originate in the canonical
[`titan-pi-memory`](https://github.com/kuwosaad/titan-pi-memory) repository.
The public `titan-memory-cli` name is retained, but npm and PyPI are separate
distributions with separate build and publish paths:

- **PyPI:** the repository-root `pyproject.toml` owns the Python distribution.
  Build it from the repository root; do not use the nested npm package to
  create or publish Python artifacts.
- **npm:** `packages/titan-memory-cli/package.json` owns the npm distribution.
  Its `prepack` step generates the bundled runtime before package checks.

The standalone checkout is compatibility/recovery material. Do not publish it
or maintain a second hand-written runtime there. Generate a compatibility sync
only when a real consumer requires one.

### Canonical npm release sequence (documentation only)

Run only after release approval. The sequence deliberately generates the
bundled runtime before testing it, then repeats the generation and final
artifact checks after the version bump:

```bash
cd /path/to/titan-pi-memory
git fetch origin
git switch main
git pull --ff-only origin main
git status --short --branch
test -z "$(git status --porcelain)"
git diff --check origin/main...HEAD

cd packages/titan-memory-cli
npm run prepack
npm test
npm version X.Y.Z --no-git-tag-version
npm run prepack
npm test
npm pack --dry-run

cd ../..
git diff -- packages/titan-memory-cli/package.json
git add -- packages/titan-memory-cli/package.json
git diff --cached --check
git diff --cached -- packages/titan-memory-cli/package.json
git commit -m "Release titan-memory-cli X.Y.Z"
git tag -a titan-memory-cli-vX.Y.Z -m "Release titan-memory-cli X.Y.Z"
git show --stat --oneline HEAD
git show --stat --oneline titan-memory-cli-vX.Y.Z
git push origin main --follow-tags
```

After GitHub shows the reviewed source and annotated scoped tag, publish the
npm artifact as a separate authorized action:

```bash
cd /path/to/titan-pi-memory/packages/titan-memory-cli
npm whoami
npm publish --access public
npm view titan-memory-cli version
```

### Canonical PyPI path

For a Python release, update the version in the repository-root
`pyproject.toml`, build from the repository root, and review the resulting
artifacts independently of the npm package:

```bash
cd /path/to/titan-pi-memory
python -m build
python -m twine check dist/*
# After the reviewed source/tag and separate PyPI approval:
python -m twine upload dist/*
```

Do not use `npm version` or `npm publish` for the PyPI distribution. The
standalone checkout is not authorized to publish either distribution.

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
read-only federation over every valid namespace discovered under the shared
Titan root; missing, invalid, and unreadable namespaces are skipped. Codex
retains write isolation even when it reads another agent's memories. In a live
Codex session, the MCP recall tools use this federation by default; writes,
settings, neural state, and passive hook traces remain Codex-only.

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
