<p align="center">
  <img src="assets/titan-pi-banner.png" alt="Titan Memory" width="960" />
</p>

<h1 align="center">Titan Memory</h1>
<p align="center"><strong>Give your coding agents a memory that survives the session.</strong></p>
<p align="center">
  <a href="#get-started">Get started</a> ·
  <a href="#use-titan">Usage</a> ·
  <a href="#explore-your-memory-in-bb">BB explorer</a> ·
  <a href="#configuration-and-data">Configuration</a> ·
  <a href="#development">Development</a>
</p>

Titan saves useful knowledge from your work—decisions, bugs, constraints,
preferences, and outcomes—so your agents can find it again later. Ask what you
changed last week, recover the reasoning behind a decision, or pick up a project
with a different agent.

It connects to **Pi, Codex, Claude Code, OpenCode, and Grok**. Each agent writes to
its own local memory store; recall can search across them. A separate **BB plugin**
lets you explore those memories visually beside your chat.

## What Titan does

- **Remembers useful work.** Supported adapters capture session activity. Titan
  extracts durable memories from it, rather than making every message a memory.
- **Finds knowledge by meaning.** Semantic search returns relevant memories with
  their source and available context.
- **Recovers the conversation behind a memory.** Scene references link memories
  to preserved messages and tool evidence. Older or incomplete scenes may have
  only partial evidence.
- **Connects your agents.** Recall can read other Titan namespaces while keeping
  each result's source identity. Writes stay with the active agent.
- **Makes memory inspectable.** Browse recent records, explore similarity graphs,
  and review evidence-backed pattern candidates.

Titan is local-first: memory stores live on your machine. Extraction and embedding
requests may go to an external provider, depending on your configuration.

## Get started

Choose the integration for the agent you use. The **Pi extension**
(`titan-pi-memory`) and the **standalone CLI** (`titan-memory-cli`) are separate
packages; installing the Pi extension does not install the `titan` command.

| Interface | Setup path | Guide |
| --- | --- | --- |
| Pi | npm extension and in-agent setup | [Pi](tools/pi_extension/README.md) |
| Codex | CLI-managed plugin setup; manual hook trust | [Codex](integrations/codex_titan_plugin/README.md) |
| Claude Code | Plugin from a source checkout | [Claude Code](integrations/claude_titan_plugin/README.md) |
| OpenCode | Source installer for classic OpenCode 1.x | [OpenCode](integrations/opencode_titan_plugin/README.md) |
| Grok | Local plugin installer from source | [Grok](integrations/grok_titan_plugin/README.md) |
| BB | Separate visual explorer plugin | [BB](integrations/bb_titan_memory_plugin/README.md) |

### Pi

You need Pi, Python **3.10+** with pip, and a configured extraction provider.
The default embedding backend also needs [Ollama](https://ollama.com) running
with the model below:

```bash
ollama pull nomic-embed-text:v1.5
pi install npm:titan-pi-memory
```

Inside Pi:

```text
/titan-setup
/titan-key
/titan-status
```

Setup prepares `~/.titan/agents/pi`, installs missing Python dependencies, and
starts Titan's local server. `/titan-key` currently offers **OpenCode Go** and
**Gemini**. Choose your provider and enter its key.

Pi setup does not install Ollama or download its model. You can use OpenAI
embeddings instead; see [configuration](#configuration-and-data).

### Codex

You need Codex, Node.js **18+**, and Python **3.10+**:

```bash
npx -y titan-memory-cli@latest setup codex
```

The installer prepares the managed runtime and registers Titan's plugin and MCP
configuration. In Codex, inspect `/plugins` and `/mcp`, then open `/hooks` and
approve Titan Memory if you want passive capture.

**Hook trust is manual.** MCP tools can work while passive capture remains
unapproved. See the [Codex guide](integrations/codex_titan_plugin/README.md) for
verification, repair, and provider setup.

### Claude Code, OpenCode, and Grok

These source-based paths start from a checkout:

```bash
git clone https://github.com/kuwosaad/titan-pi-memory.git
cd titan-pi-memory
```

<details>
<summary><strong>Claude Code</strong></summary>

Load the plugin from the checkout:

```bash
claude --plugin-dir ./integrations/claude_titan_plugin
```

The plugin uses Node.js 18+, npm, and Python 3.10+ for its managed runtime.
Inspect `/plugin`, `/mcp`, and `/hooks` after loading. Restart Claude or use
`/reload-plugins` after configuration changes.

The adapter supports automatic recall, configurable capture, and project
exclusions. See the [Claude Code guide](integrations/claude_titan_plugin/README.md)
for settings and marketplace installation details.

</details>

<details>
<summary><strong>OpenCode 1.x</strong></summary>

Install the standalone CLI below first. With `titan`, `opencode`, and Bun on
your `PATH`, run:

```bash
cd integrations/opencode_titan_plugin
bun run setup
```

Setup builds and installs the global plugin, workflow skills, and MCP entry.
Restart OpenCode and confirm that the `titan-memory_` tools are available.
This adapter targets the classic OpenCode **1.x** plugin contract.

See the [OpenCode guide](integrations/opencode_titan_plugin/README.md).

</details>

<details>
<summary><strong>Grok</strong></summary>

With Grok and Titan's Python runtime dependencies available, run from the
repository root:

```bash
./integrations/grok_titan_plugin/scripts/install_grok.sh
```

Enable the plugin in `~/.grok/config.toml` if needed:

```toml
[plugins]
enabled = ["titan-memory"]
```

Restart Grok, then check `/plugins` and `/mcps`. The installer creates a
`titan-grok` launcher in `~/.local/bin`; add that directory to your `PATH` if it
isn't already there. This is a local integration, with no separate Grok npm
package.

See the [Grok guide](integrations/grok_titan_plugin/README.md).

</details>

### Standalone CLI

Choose one installation method:

```bash
# npm wrapper: Node.js 18+ and Python 3.10+
npm install -g titan-memory-cli

# Or Python package: use your Python environment
pip install titan-memory-cli
```

Then:

```bash
titan --help
titan doctor --agent codex
```

The npm wrapper manages the Python runtime dependencies. The Python package
installs the `titan` entrypoint directly. See the
[CLI guide](docs/pypi_titan_memory_cli.md) for setup and maintenance.

## Use Titan

Ask your connected agent questions such as:

> What did we decide about authentication, and why?
>
> Find the bug we fixed last week and recover the original discussion.
>
> What constraints should I remember before changing the storage layer?

The agent uses Titan's recall and scene tools. Tool names and prefixes vary by
integration; retrieved memories are historical evidence, so verify them against
current code before acting.

In Pi, you can also use commands directly:

| Command | Purpose |
| --- | --- |
| `/titan-query <question>` | Search memories by meaning |
| `/titan-recent` | Browse recent memories |
| `/titan-save <note>` | Submit useful knowledge for storage |
| `/titan-status` | Check the runtime and memory store |
| `/titan-graph` | Open the browser memory graph |
| `/titan-dashboard` | Inspect memory and pipeline health in the terminal |
| `/titan-clusters` | Explore groups of related memories |
| `/memory-sync` | Start a guided import from local agent history |

**Patterns** are proposed conclusions supported by multiple memories and scenes.
You can inspect candidates and explicitly accept or reject them. Automatic mining
is disabled by default; pattern discovery is a review workflow, not a promise
that Titan learns everything unattended.

**Memory Sync** imports distilled knowledge from local history. It is different
from federated recall, which reads existing Titan namespaces without copying
their memories. The sync workflow previews the import and requests approval
before its first write.

## Explore your memory in BB

The BB explorer puts a 3D memory graph beside your chat. Filter by agent, memory
type, search text, or date; hover for a preview and click for details. The graph
stays free of memory labels until you interact. Hide the memory list when you
want more visual space, or use numbered pages to browse matching records.

From a source checkout, with BB, Node.js/npm, and Python 3.10+ available:

```bash
cd integrations/bb_titan_memory_plugin
npm ci --ignore-scripts
npm run check
python3 scripts/install-local.py --install
```

Set **Titan root on target host** in the plugin settings to your Titan checkout,
then open **Titan Memory** from a BB thread's panel actions.

The explorer reads existing data and stored embeddings. It does not capture
conversations, generate embeddings, or change memories. Its graph shows at most
**150 memories and 450 connections**; the list provides **50 records per page**.
Connections indicate similarity, not proven relationships.

Rendering pauses after interaction. The installer builds in an isolated release
directory before switching BB to it; use the same installer for updates.
See the [BB guide](integrations/bb_titan_memory_plugin/README.md) for host settings
and development details.

## Configuration and data

Titan uses two model roles:

| Role | Job | Default in this source tree |
| --- | --- | --- |
| Extraction | Turn captured evidence into useful memories | OpenCode Go / `deepseek-v4-flash` |
| Embedding | Represent memories and queries for semantic search | Ollama / `nomic-embed-text:v1.5` |

Extraction also has configurations for Gemini, OpenAI, OpenRouter, and Ollama.
Embeddings support Ollama and OpenAI. Installed versions and existing namespaces
may have different settings; inspect your agent's configuration before changing
it.

```text
~/.titan/agents/<agent>/
├── config/
│   ├── extraction_models.yaml
│   ├── embedding_models.yaml
│   └── settings.yaml
└── out/memories/memory_store.db
```

Select the provider in the relevant YAML file and supply its configured API-key
environment variable. Keep credentials out of source control. The
[extraction defaults](config/extraction_models.yaml) and
[embedding defaults](config/embedding_models.yaml) show the available backends.

Each agent owns its writes. Default federated recall searches valid namespaces
under `~/.titan/agents` and preserves `source_agent` so a memory can be traced
back to the right store and scene.

**Local storage does not mean offline processing.** With cloud extraction,
captured content is sent to that provider. With cloud embeddings, memory/query
text is sent to the embedding provider. Capture scope and redaction vary by
adapter; consult its privacy guide. Local Ollama backends are configurable for
both roles.

Capture and extraction are separate steps. A connected plugin does not guarantee
that new memories are being extracted: missing credentials, unavailable models,
quality gates, or failed processing can prevent storage. Start with the agent's
status/doctor command when something is missing.

## Development

This repository owns the shared engine, adapters, CLI distributions, and BB
explorer. The name `titan-pi-memory` reflects its Pi package; Pi is one integration
of the same engine.

| Path | Contents |
| --- | --- |
| `app/` | Storage, capture processing, extraction, retrieval, graphs, and API |
| `entrypoints/` | HTTP and MCP servers |
| `tools/pi_extension/` | Pi extension and workflows |
| `tools/cli/` | Python CLI |
| `integrations/` | Agent plugins and BB explorer |
| `packages/titan-memory-cli/` | npm CLI wrapper and bundled runtime packaging |
| `config/` | Default model and runtime settings |
| `tests/` | Python regression tests |

Read [AGENTS.md](AGENTS.md) for repository guidance and the relevant integration's
README for its checks. The BB explorer's focused checks are:

```bash
# From the repository root
python3 -m unittest tests.test_graph_explorer

# From the BB plugin directory
cd integrations/bb_titan_memory_plugin
npm ci --ignore-scripts
npm run check
```

The Pi npm package, standalone npm CLI, and Python CLI have separate packaging
paths. See [repository ownership](docs/repository-ownership.md) and the
[CLI distribution guide](docs/pypi_titan_memory_cli.md) before preparing a release.

## Links

[Pi package](https://www.npmjs.com/package/titan-pi-memory) ·
[CLI package](https://www.npmjs.com/package/titan-memory-cli) ·
[Issues](https://github.com/kuwosaad/titan-pi-memory/issues) ·
[Apache 2.0 license](LICENSE)
