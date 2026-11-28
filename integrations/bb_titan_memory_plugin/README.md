# Titan Memory Explorer BB plugin

Browse Titan memories in a native panel beside your BB chat. The panel includes
an interactive 3D graph, memory search, agent/type/date filters, and an evidence
inspector. Agent and type selections update the graph and list immediately. Every
available agent appears in the source menu, including agents outside the recent
graph subset. Hide the memory list for more graph space; use numbered pages or
Go to page to jump through matching records. It reads existing stores; opening it never generates embeddings or
changes memories. The existing agent integrations continue handling capture.

## Connect

Install the **Titan Memory Explorer** plugin package in bb 0.50.1 or later.
The thread host needs Python 3.10 or later. Opening **Titan Memory** reads
existing memories from that host's `~/.titan`. The packaged host bundle
contains the Python reader, including for remote thread hosts.

If you received a staged release directory from the installer, run
`bb plugin install /absolute/path/to/the/release`. When building from a Titan source checkout, run this
command from `integrations/bb_titan_memory_plugin` on the machine running bb:

```bash
python3 scripts/install-local.py --install
```

The installer stages and builds a separate release under
`~/.bb/local-plugin-builds/` before switching bb to it. Omit `--install` to
build the release for inspection. Repeat the command to update. For development,
the canonical reader lives in `app/graph/explorer.py`. Copy it into
`assets/explorer.py` when updating it. The tests check that they match and the
SDK build embeds the asset in `dist/host.js`.

Open **Titan Memory** from the thread's panel actions to browse beside chat.
The graph shows up to 150 matching memories and 450 links. Use the relationship
filter to view stored corrections or vector similarity. Correction arrows point
from the replacement to the earlier memory. Memories without stored embeddings
stay visible and can still have correction links. Select a memory to inspect its
source scene, pattern evidence, or correction history. The inspector can open
linked revisions outside the graph without changing the active filters. Hover to
highlight neighbors, then use Fit to restore the overview. The
force layout is bounded to 36 ticks, the renderer pauses after interaction, and
closing the panel disposes its WebGL resources and cancels its reads. Refresh
is manual. Search is debounced by 200 ms. The agent/type catalog loads only on
open or refresh, and each memory page holds at most 50 records.

## Runtime boundary

The server exposes the typed `catalog`, `snapshot`, `search`, `detail`, and `cancel` RPC
methods from `src/contract.ts`. Each data request carries `threadId` and a
UI-generated `requestId`; those fields are used only for request routing and
exact cancellation and are not sent to Titan. Data requests for the same thread
remain independent.

The server resolves the host from the BB thread's environment, falling back to
BB's primary host. For an explicit checkout override, the host uses this command:

```text
python3 -m app.graph.explorer < request.json
```

The host adds the operation as `method` in the JSON request, passes `--home`
from the host's resolved Titan environment, and never accepts a filesystem path
from the panel. The plugin's server settings (`titanRoot`, `titanPython`, and
`sharedHome`) are trusted host configuration and are forwarded live on each
request, so changing them does not require restarting BB. If a setting is empty,
the host falls back to these target-host environment variables:

- `TITAN_ROOT`: a Titan source checkout, added to `PYTHONPATH` and used as the
  process working directory. Leave it empty to use the bundled reader.
- `TITAN_SHARED_HOME` or `TITAN_HOME`: the shared Titan home containing
  `agents/`. An agent-specific `.../agents/<name>` value is normalized to its
  shared parent. The helper defaults to `~/.titan` when omitted.
- `TITAN_PYTHON`: optional Python executable override; defaults to `python3`.

The bridge does not start FastAPI/Uvicorn, open a browser, use a CDN, run
inference, generate embeddings, perform retrieval/LNN updates, poll in the
background, or create Titan directories. Python errors must be emitted as the
structured `{ "error": { "code", "message" } }` response described by the
contract.

Requests are capped at 64 KiB input, four concurrent host processes, 25 seconds
per helper, and a response below 480 KiB. The `cancel` method targets one exact
`threadId` + `requestId` pair and propagates through BB host cancellation to the
Python child. Closing the panel is handled by the frontend; the server disposes
all registered state on reload/disable/shutdown.

## Development

```bash
npm install
npm run typecheck
npm test
```

The Python regression suite lives at repository root:

```bash
python3 -m unittest tests.test_graph_explorer
```

Use isolated memory fixtures for tests. SDK harness tests do not replace a
live BB panel check for layout, host routing, and cleanup.
