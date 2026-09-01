---
name: titan-dashboard
description: Open Titan's visual memory overview for Grok. Use when the user runs /titan-dashboard or wants a rich view of memory.
---

# Titan Dashboard

Pi's dashboard talks to the HTTP server on port 8002. Grok's equivalent overview is the graph UI:

```bash
titan-grok graph --open
```

That serves `http://127.0.0.1:8010/graph` (or the next free port) for agent `grok`.
