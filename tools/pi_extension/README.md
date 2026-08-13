# titan-pi-memory

> Persistent evolutionary memory for the Pi coding agent.

**Install:** `pi install npm:titan-pi-memory`

**Setup in Pi:** `/titan-setup` then `/titan-key`

**Memory import:** `/memory-sync` imports useful local Claude Code/Codex session memories into Titan.

Pi is the adapter layer. The npm package bundles the Titan engine that performs trace
processing, memory storage, retrieval, patterns, and graph analysis. Pi keeps its own
agent namespace under `~/.titan/agents/pi` so its memories and traces do not mix with
other agents. The `titan` tool's doctor operation reports the selected storage backend
and whether LNN features are available; JSON storage supports basic memory but not LNN.

**Docs:** [github.com/kuwosaad/titan-karu](https://github.com/kuwosaad/titan-karu)
