# Titan Memory Explorer BB plugin

Browse Titan memories in a native panel beside your BB chat. The panel includes
an interactive 3D similarity graph, memory search, agent/type/date filters, and memory
details. Agent and type selections update the graph and list immediately. Every
available agent appears in the source menu, including agents outside the recent
graph subset. Hide the memory list for more graph space; use numbered pages or
Go to page to jump through matching records. It reads existing stores; opening it never generates embeddings or
changes memories. The existing agent integrations continue handling capture.

## Local setup

Run these commands from this directory in a Titan source checkout:

```bash
npm ci --ignore-scripts
npm run check
python3 scripts/install-local.py --install
```

Set the plugin's **Titan root on target host** setting to the absolute path of
the Titan checkout containing `app/graph/explorer.py`. Python 3.10+ is required;
the explorer helper uses only the standard library. Set **Shared Titan home**
only if your memories are outside the default `~/.titan` location. These paths
refer to the machine running the selected BB thread.

The installer builds a separate release under `~/.bb/local-plugin-builds/`
before switching BB to it. Keep the installed copy separate from the source
checkout: BB's live Tailwind scanner can block its server while reading parent
Git ignore files on a stalled filesystem. Do not run `bb plugin dev` or reload
directly from this working checkout. Run the installer again for local updates;
previous release directories remain available for rollback. Without `--install`,
the script only prepares and validates the build.

Open **Titan Memory** from the thread's panel actions to browse beside chat.
The graph shows at most 150 memories and 450 similarity links. It is a subset
of storage, not a complete inventory. Missing embeddings leave memories visible
without inferred links. Similarity indicates vector similarity, not a proven
factual relationship. Hover to highlight neighbors; orbit, pan, zoom, and drag
nodes to inspect the memory field, then use Fit to restore the overview. The
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
BB's primary host. The host starts exactly one bounded process per request:

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
  process working directory. Omit it when Titan is installed as a Python
  package.
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
