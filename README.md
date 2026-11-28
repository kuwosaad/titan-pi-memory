<p align="center">
  <img src="assets/titan-banner-geometric.png" alt="Titan Memory" width="960" />
</p>

<h1 align="center">Titan Memory</h1>
<p align="center"><strong>Local-first multi-agent memory for coding agents.</strong></p>
<p align="center">
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="#use-titan">Usage</a> ·
  <a href="#explore-your-memory-in-bb">BB explorer</a> ·
  <a href="#configuration-and-data">Configuration</a> ·
  <a href="#development">Development</a>
</p>

Titan Memory is a local-first memory layer for coding agents. It saves useful
knowledge from your work—decisions, bugs, constraints, preferences, and
outcomes—so your agents can find it again later. Ask what you changed last week,
recover the reasoning behind a decision, or pick up a project with a different
agent.

Adapters connect **Pi, Codex, Claude Code, OpenCode, Grok, and Hermes Agent**. Each agent writes
to its own local memory store; recall can search across them. A separate **BB
plugin** lets you explore those memories visually beside your chat.

## Benchmarks

On LongMemEval-S (500 questions), with Titan's R5 memory pipeline feeding eight
reader models, the top four readers score:

| Reader | Rubric accuracy |
| --- | --- |
| Claude Opus 5.5 | 97.2% |
| Gemini 4 Neon | 96.0% |
| Claude Sonnet 5.5 | 93.6% |
| GPT-6.2 Sol | 93.4% |

Scored with the official rubric and one judge. **R5 was tuned on these same 500
cases, so these are development-set results, not a held-out score.** See the
[full LongMemEval-S report](docs/benchmarks/longmemeval-s.md) for all readers,
speed, and method.

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

Install Titan through your agent, then choose your extraction and embedding providers.
The six capture plugins in this checkout include the memory engine. On first use,
Python 3.10+ (with venv and pip) installs the engine’s dependencies in a private
environment. Allow time for that download. Later starts reuse the environment.
The standalone Titan CLI is optional.

Use the source or local-package instructions in each guide to try this version.
Public registries and catalogs deliver the versions listed by those providers.

| Agent | First step |
| --- | --- |
| [Codex](integrations/codex_titan_plugin/README.md) | Add the local marketplace, install Titan Memory, and follow its setup skill. Approve its hooks in Codex for automatic capture. |
| [Claude Code](integrations/claude_titan_plugin/README.md) | Install Titan Memory from the local marketplace and choose a provider. |
| [Pi](tools/pi_extension/README.md) | Install the Pi package from this checkout, then run `/titan-setup`. The registry command `pi install npm:titan-pi-memory` installs the published version. |
| [Grok Build](integrations/grok_titan_plugin/README.md) | Install the local plugin and open its setup workflow before connecting MCP. |
| [Hermes Agent](integrations/hermes_titan_plugin/README.md) | Install and enable this plugin revision, then use the native connect command in your Hermes profile. |
| [OpenCode](integrations/opencode_titan_plugin/README.md) | Follow the instructions for your host version. The plugin guide covers current and classic OpenCode. |
| [bb explorer](integrations/bb_titan_memory_plugin/README.md) | Install the explorer to read the memories your agents have saved. |

To verify your installation, ask the agent to save a specific decision, restart the agent,
and ask it to recall that decision and show its source. The default MCP tool set includes
`query_memories`, `store_trace_packet`, `get_scene_context`, and `doctor`.
Set `TITAN_MCP_TOOL_PROFILE=full` on the MCP server to expose optional tools.

### Optional command-line tools

For diagnostics, scripting and administration from this checkout, install the Python
package into an isolated environment with `pipx install .`. Run `titan --help` to
see the available commands. See the [CLI guide](docs/pypi_titan_memory_cli.md).

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

Treat memory IDs and scene IDs as opaque addresses. New scene-backed memories
use stable scene-qualified IDs so delayed extraction retries cannot collide
with another scene that reused a legacy turn number.

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

Open **Titan Memory** from a BB thread’s panel actions. The explorer bundles its reader and uses Python on the target host.

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

Start with the [documentation index](docs/README.md). Read
[AGENTS.md](AGENTS.md) for repository guidance and the relevant integration's
README for its checks. The BB explorer's focused checks are:

```bash
# From the repository root
python3 -m unittest tests.test_graph_explorer

# From the BB plugin directory
cd integrations/bb_titan_memory_plugin
npm ci --ignore-scripts
npm run check
```

After changing the engine or the native runtime launcher, run
`python3 tools/release/sync_plugin_runtime.py`. This regenerates the six native
plugins’ identical engine archives and launcher copies. CI checks their contents
against the source. The bb explorer ships only its read-only Python bridge.

The Pi npm package, standalone npm CLI, and Python CLI have separate packaging
paths. See [repository ownership](docs/repository-ownership.md) and the
[CLI distribution guide](docs/pypi_titan_memory_cli.md) before preparing a release.

## Links

[Pi package](https://www.npmjs.com/package/titan-pi-memory) ·
[CLI package](https://www.npmjs.com/package/titan-memory-cli) ·
[Documentation](docs/README.md) ·
[Issues](https://github.com/kuwosaad/titan-pi-memory/issues) ·
[Apache 2.0 license](LICENSE)
