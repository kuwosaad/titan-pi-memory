# Repository guidance

## Ownership and scope

This repository is the canonical Titan engine, CLI, agent adapters, and packaging
source. Older Titan CLI or adapter checkouts are compatibility distributions.
Inspect current code before changing behavior; preserve unrelated local work.

- `app/`: Python engine, storage, retrieval, and graph data.
- `tools/pi_extension/`: Pi integration.
- `tools/cli/`: CLI entrypoints.
- `integrations/`: other agent plugins and the BB explorer.
- `tests/`: Python regression tests.

Keep each agent's writes inside its own `~/.titan/agents/<agent>` namespace.
Cross-agent reads must retain source identity. Do not run mutation tests against
real user memories, commit local data or credentials, or include generated
artifacts in source changes.

## BB explorer

The BB explorer is a visual layer over existing Titan stores. Its implementation
and detailed setup instructions live in
[`integrations/bb_titan_memory_plugin/README.md`](integrations/bb_titan_memory_plugin/README.md).
The read-only Python bridge is `app/graph/explorer.py`.

Preserve these boundaries when changing it:

- Never generate embeddings, run inference, invoke retrieval that mutates LNN
  state, or start the browser graph server while viewing memories.
- Preserve source-qualified memory identity, including across pagination and
  details. Agent/type filters must affect both graph and list.
- Keep graph data and layout bounded. Cancel obsolete reads and dispose renderer,
  timers, listeners, and GPU resources when the panel closes; no idle render loop.
- Keep the graph uncluttered: memory text appears on hover or selection only.
  Preserve the accessible memory list and panel-local error handling.

For local installation, use `python3 scripts/install-local.py --install` from
that plugin directory after validation. It stages and builds outside Git before
switching the installed release. Do not run live `bb plugin dev`, build, or reload
from the canonical checkout or edit installed releases in place: BB's Tailwind
scanner has blocked on cloud-offloaded parent Git files. Keep canonical source
and installed release directories separate.

## Verification and handoff

Run checks for the surface changed. For the BB explorer:

```bash
# Repository root
python3 -m unittest tests.test_graph_explorer

# Plugin directory
cd integrations/bb_titan_memory_plugin
npm ci --ignore-scripts
npm run check
```

Use isolated data fixtures. A passing mock test does not establish visual quality
or real WebGL behavior; inspect the native BB panel for rendering/interaction
changes and verify that chat remains responsive. Broaden checks when changing
shared storage, retrieval, or adapter contracts.

Report what changed, what was verified, and any remaining limitation. Commit,
push, or publish only when the user authorizes that action. If Git metadata is
cloud-offloaded and reads hang, stop repeating the operation; preserve the source
and use a clean checkout to integrate explicitly scoped changes without replacing
unrelated work.
