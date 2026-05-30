# titan-pi-memory

> **Persistent evolutionary memory for the Pi coding agent.**  
> Titan remembers across sessions — decisions, bugs, architecture, preferences. Conversations
> become knowledge. Nothing is lost.

## One-command install

```bash
pi install npm:titan-pi-memory
```

Then start Pi, run `/titan-setup`, add your API key with `/titan-key`, and you're done.

## Prerequisites

- **Pi coding agent** (v0.21+) — `npm install -g @earendil-works/pi-coding-agent`
- **Python 3.10+** with `pip` available
- A **Gemini API key** for memory extraction (free tier works fine)

## Quick start

### 1. Install the package

```bash
pi install npm:titan-pi-memory
```

### 2. Set up

Start Pi and run:

```
/titan-setup
```

This creates the workspace, installs Python dependencies, and starts the Titan server.

### 3. Add your API key

```
/titan-key
```

Select **Gemini**, paste your key when prompted. Done.

### 4. Verify

```
/titan-status
```

You should see:

```
Server:     ✅ running
Workspace:  ~/.titan/agents/pi
Memories:   {"memory_count": 0}
```

### 5. That's it

Every conversation from now on gets remembered. Try:

```
/titan-save I just configured Titan for the first time
```

Come back tomorrow, Pi will know what you were working on.

---

## What it does

### Passive capture (always on)

The extension listens to Pi's lifecycle events and writes trace files. Titan's background
workers process these into **scenes** (conversation chunks) and **memories** (extracted facts).

```
session starts    →  session_created
user sends msg    →  user_message
assistant replies →  assistant_message
tools run         →  tool_execution
turn ends         →  turn_complete
session ends      →  session_closed
```

### Active tools (LLM-callable)

| Tool | Purpose |
|------|---------|
| `titan_query_memories` | Semantic search across all memories |
| `titan_get_scene_context` | Full conversation for a memory |
| `titan_store_trace_packet` | Manually save a decision |
| `titan_get_recent_memories` | Browse recent memories |
| `titan_doctor` | Health check |

### Slash commands (user-callable)

| Command | Purpose |
|---------|---------|
| `/titan-setup` | Prep workspace and start server |
| `/titan-key` | Save API key |
| `/titan-graph` | Open knowledge graph in browser |
| `/titan-query <q>` | Search memories |
| `/titan-recent` | Browse recent memories |
| `/titan-status` | Check server health |
| `/titan-save <goal>` | Save a manual trace packet |
| `/titan-start` | Start Titan server |
| `/memory-sync` | Import useful local Claude Code/Codex session memories into Titan |
| `/skill:memory-sync` | Load the Memory Sync skill directly |

---

## How it works

```
┌─────────────┐   events    ┌──────────────┐   spool       ┌─────────────────┐
│  Pi agent   │ ─────────→  │  Extension   │ ───────────→  │  Titan server    │
│             │             │  index.ts    │   files       │  (port 8002)     │
│  tools:     │ ←────────── │  HTTP API    │               │  auto-ingest     │
│  query,     │   response  │  fetch()     │               │  scene builder   │
│  store, etc │             │              │               │  memory extract  │
└─────────────┘             └──────────────┘               └─────────────────┘
```

Events flow from Pi ➞ the TypeScript extension ➞ spool files on disk ➞ Titan's Python
server ➞ a local SQLite memory store. When the LLM queries memories, the chain reverses.

### Workspace layout

```
~/.titan/agents/pi/
├── .env                         # API keys
├── config/
│   ├── extraction_models.yaml   # Extraction model config
│   └── embedding_models.yaml    # Embedding model config
├── traces/                      # Session traces from Pi
│   └── <session-id>.jsonl
└── out/
    ├── memories/
    │   └── memory_store.db      # SQLite memory database
    └── traces/
        ├── events.jsonl         # Processed events
        ├── checkpoints.json
        └── cursors.json
```

---

## Configuration

### Switch extraction model

By default Titan uses **Gemini 2.5 Pro**. Edit:

```bash
~/.titan/agents/pi/config/extraction_models.yaml
```

Set `current:` to `openai`, `openrouter`, or `ollama`, then add the corresponding
API key to `~/.titan/agents/pi/.env`.

### Switch embedding model

Default is **Ollama + nomic-embed-text** (runs locally, needs Ollama running).

Edit `~/.titan/agents/pi/config/embedding_models.yaml` to switch.

### Custom Titan URL

Set the `TITAN_PI_API_URL` environment variable to point the extension at a remote
Titan instance instead of the local one.

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
- **Pi coding agent:** [github.com/earendil-works/pi-mono](https://github.com/earendil-works/pi-mono)
