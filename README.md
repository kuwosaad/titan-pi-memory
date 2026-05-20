# titan-pi-memory

> **Persistent evolutionary memory for the Pi coding agent.**
> Titan remembers across sessions — decisions, bugs, architecture,
> preferences. Conversations become knowledge. Nothing is lost.

```bash
pi install npm:titan-pi-memory
```

---

## Install (60 seconds)

### What you need

- **Pi coding agent** installed (`npm install -g @earendil-works/pi-coding-agent`)
- **Python 3.10+** (check with `python3 --version`)
- An API key for your chosen extraction provider:
  - **Gemini** — free tier at [aistudio.google.com](https://aistudio.google.com) (recommended)
  - **OpenAI** — [platform.openai.com](https://platform.openai.com)
  - **DeepSeek** — [platform.deepseek.com](https://platform.deepseek.com)
  - **OpenRouter** — [openrouter.ai](https://openrouter.ai)

### Step 1: Install the package

```bash
pi install npm:titan-pi-memory
```

### Step 2: Set up

Open Pi and type:

```
/titan-setup
```

This creates your Titan workspace and installs everything needed.

### Step 3: Add your API key

```
/titan-key
```

Titan will walk you through picking a **provider**, selecting a **model**, and entering your **API key**.
Choose from Gemini, OpenAI, OpenRouter, or DeepSeek. Done.

### Step 4: Verify it's working

```
/titan-status
```

You should see:

```
Server:     running
Workspace:  ~/.titan/agents/pi
Memories:   {"memory_count": 0}
```

### Step 5: Done

Every conversation from now on gets remembered.

```
/titan-save I just configured Titan for the first time
```

Come back tomorrow — Pi will know what you were working on.

---

## Features

### Agent memory (always on)

Titan listens to your Pi conversations and saves everything automatically. Every decision, bug fix, preference, and architectural choice is captured. Your agent remembers across sessions — no more repeating yourself.

### Semantic memory search

Query everything Titan has stored:

```
/titan-query what database does this project use?
```

Titan finds the most relevant memories, even when you don't remember the exact words.

### Full scene context

Every memory links back to the conversation it came from. When your agent finds a relevant memory, it can pull up the full discussion with a single tool call — so it understands the *why* behind the fact, not just the fact itself.

### Work pattern discovery

As your memory graph grows, something interesting happens. You start seeing patterns in your own work — what you consistently care about, how your thinking evolves, what kinds of problems you keep running into.

Open your graph after a few sessions:

```
/titan-graph
```

Users describe it like this:

> *"Very addictive. Watching it grow and develop. You get to know yourself better using it."* — Elina, daily Titan user

The graph shows your memories as clusters of related topics. Look at the connections. You might recognize something.

---

## Tools (what Pi can do)

Your Pi agent has these tools available automatically:

| Tool | What it does |
|------|-------------|
| `titan_query_memories` | Search across all memories (semantic) |
| `titan_get_scene_context` | Get the full conversation for a memory |
| `titan_store_trace_packet` | Manually save an important decision |
| `titan_get_recent_memories` | Browse recent memories |
| `titan_doctor` | Check if everything is healthy |

---

## Commands (what you can type)

| Command | What it does |
|---------|-------------|
| `/titan-graph` | Open your knowledge graph in the browser |
| `/titan-setup` | Set up your workspace and start the server |
| `/titan-key` | Add or change your API key |
| `/titan-query <q>` | Search your memories |
| `/titan-recent` | Browse recent memories |
| `/titan-status` | Check if Titan is running |
| `/titan-save <goal>` | Save a manual memory |
| `/titan-start` | Start the Titan server |
| `/titan-dashboard` | Open a rich terminal memory dashboard |
| `/titan-clusters` | List your memory topic clusters |

---

## Configuration

### Switch extraction model

Default is **Gemini 2.5 Pro**. Edit:

```bash
~/.titan/agents/pi/config/extraction_models.yaml
```

Set `current:` to `openai`, `openrouter`, `deepseek`, or `ollama`, then add the corresponding API key via `/titan-key`.

Or use `/titan-key` directly — it walks you through picking provider, model, and key all at once.

### Switch embedding model

Default is **Ollama + nomic-embed-text** (runs locally, needs Ollama running).

Edit `~/.titan/agents/pi/config/embedding_models.yaml` to switch.

---

## Development

```bash
# Edit the extension
vim tools/pi_extension/index.ts

# Reload in Pi
/reload

# Check for TypeScript errors
npx tsc --noEmit tools/pi_extension/index.ts
```

---

## Links

- **npm:** [npmjs.com/package/titan-pi-memory](https://www.npmjs.com/package/titan-pi-memory)
- **Repository:** [github.com/kuwosaad/titan-karu](https://github.com/kuwosaad/titan-karu)
- **Pi packages:** [pi.dev/packages](https://pi.dev/packages)

---

## Repository Layout

```
app/             → Memory engine (save pipeline, retrieval, graph, storage, API)
tools/           → Pi extension (pi_extension/), CLI, benchmarks
entrypoints/     → HTTP server and MCP server
config/          → Model and runtime configuration
assets/          → Brand, logos, design files
docs/            → Contributor docs, plans, benchmarks
experiments/     → Research code (not product runtime)
```

---

## For Developers

### How It Works

```
Agent session
       │
       ▼
┌──────────────┐
│  Spool File   │  The Pi extension writes every chat
│  (.jsonl)     │  event into a spool file
└──────┬───────┘
       │  Auto-ingest picks up new events every 3 seconds
       ▼
┌──────────────┐
│ Event Ledger  │  Permanent, deduplicated log of all events
│ (events.jsonl)│
└──────┬───────┘
       │  Process new events (after last checkpoint)
       ▼
┌──────────────┐
│  Extraction   │  LLM reads the conversation and pulls out atomic facts
│  + Embedding  │  Each fact gets a vector for semantic search
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Memory Store  │  SQLite database with memories + embeddings
│ (SQLite)      │
└──────┬───────┘
       │
       ▼
  Agent asks "what do you remember about X?"
     → Semantic search → Compact brief → Returned to agent
```

### API Reference

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/api/ingest/event` | POST | Ingest a single event |
| `/api/ingest/spool` | POST | Ingest all events from a session |
| `/api/retrieve` | GET | Semantic search with compact brief |
| `/api/memories` | GET | Browse stored memories |
| `/graph` | GET | Interactive knowledge graph |
| `/api/clusters` | GET | List memory topic clusters |
| `/api/clusters/analyze` | GET | Cross-cluster analysis |
| `/api/debug/pipeline` | GET | Check ingest health |

Example search:
```bash
curl "http://127.0.0.1:8000/api/retrieve?query=what+database+does+the+project+use"
```

### CLI Commands

If installed via pip, use `titan`. Otherwise: `python3 tools/cli/titan.py`

| Command | What it does |
|---------|-------------|
| `titan doctor --agent <name>` | Verify memory readiness |
| `titan init --agent <name>` | Prepare runtime home for an agent |
| `titan mcp --agent <name>` | Start MCP server for that agent |
| `titan config show` | Show current model config |
| `titan config set-model` | Change extraction/embedding models |
| `titan key set <KEY_NAME>` | Set an API key |
| `titan graph --agent <name>` | Build and open the knowledge graph |

### Project Structure

```
titan-karu/
├── app/
│   ├── save_pipeline/      # Save flow: ingest → extract → embed → store
│   │   └── extraction/     # LLM-based memory extraction
│   ├── embedding/          # Vector embedding (Ollama / OpenAI)
│   ├── graph/              # Memory graph builder, clusters, UI
│   ├── retrieval_pipeline/ # Retrieval: router, retriever, brief
│   ├── api/                # FastAPI HTTP endpoints
│   └── storage/            # SQLite-backed memory store
├── config/                 # YAML configuration
├── entrypoints/            # HTTP server, MCP server
├── tools/
│   ├── cli/titan.py        # CLI tool
│   ├── pi_extension/       # Pi extension (TypeScript + Python)
│   └── benchmarks/         # Benchmark harnesses
└── docs/                   # Contributor docs
```

### Running Tests

```bash
pytest
```

### Security

- All API keys stored in `.env` (gitignored)
- Server runs on `127.0.0.1` (localhost only) by default
- All data stays on your machine

---

<a href="https://www.producthunt.com/products/titan-memory?embed=true&amp;utm_source=badge-featured&amp;utm_medium=badge&amp;utm_campaign=badge-titan-memory" target="_blank" rel="noopener noreferrer"><img alt="Titan Memory - A Persistent evolutionary memory layer for AI agents | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1149745&amp;theme=light&amp;t=1779175404906"></a>
