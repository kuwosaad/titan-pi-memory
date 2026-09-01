# Titan architecture context

This file is the small, stable vocabulary used when changing Titan. It describes
the contracts that cross module and process boundaries; implementation details
belong in the code and in the design records under `docs/adr/`.

## Repository ownership

`titan-pi-memory` is Titan's canonical source of truth. It owns the engine,
storage and retrieval contracts, agent adapters, Codex plugin, CLI packaging,
tests, and release configuration. Pi is one adapter and npm distribution
package; it is not the boundary of engine ownership. The older `titan-karu`
checkout and separate Codex/CLI repositories are compatibility distributions
that may be archived later, so production changes must be made here first and
verified with the canonical test suite.

Compatibility distributions must preserve the same public contracts, but they
must not become a second source of truth. When a compatibility fix is needed,
graduate the change into this repository with tests before distributing it.

## Memory

A **Memory** is a durable, addressable record of information extracted from a
conversation or trace. Its `id` is stable across storage backends, migrations,
readable projections, and retrieval. A Memory may carry provenance, an optional
embedding, and mutable neural activation state. Activation is operational state;
changing it must not change the Memory's content identity.

## Scene

A **Scene** is the durable interaction boundary produced from one trace segment.
It preserves the ordered messages, tool calls, source event lineage, and the
exact text offered to extraction. Scenes are first-class records: a valid Scene
must remain durable even when extraction yields no Memory.

## Trace Event

A **Trace Event** is one idempotent, append-only observation from an agent
adapter, such as a user message, assistant message, tool call, file edit, or
trace packet. `session_id` groups events, `event_id` deduplicates retries, and
the ledger sequence records their observed order. Event payloads remain
lossless enough to support later Scene reconstruction.

## Pattern

A **Pattern** is a proposed, evidence-backed generalization over Memories. The
Pattern backend may discover and persist evidence and processing progress, but
it does not silently author or auto-accept a final Pattern. Pattern lifecycle
and validation are framework-neutral so HTTP and MCP can expose the same
behavior.

## Agent Namespace

An **Agent Namespace** is the isolated filesystem and configuration scope for
one agent identity. It includes the resolved agent name, Titan home, base
directory, trace/spool directory, settings and model configuration paths, and
the Memory database path. Adapters (Pi, Codex, Claude, CLI, HTTP, and MCP)
may choose different defaults, but they must resolve the same layout contract.

Every agent writes only to its own namespace, for example
`~/.titan/agents/codex` or `~/.titan/agents/pi`. Default memory recall is a
read-only federation over all discovered agent namespaces under
`~/.titan/agents`; missing or invalid namespaces are skipped. Recall results
preserve their `source_agent` provenance, and callers must pass that
`source_agent` when retrieving a scene from a foreign namespace. Operational
state remains local: traces, pending/spool events, settings, and pattern
workspaces are never federated or written across namespaces. Federation adds
no new adapters or coordination protocol; it is a read path over existing
agent stores.

`TITAN_SHARED_HOME` may select a non-default federation root without changing
the established meaning of `TITAN_HOME` or `TITAN_BASE_DIR`. When absent, the
runtime derives the shared root from a conventional `<root>/agents/<agent>`
workspace and otherwise preserves legacy isolated homes. Bundled settings are
immutable defaults; an optional `<agent>/config/settings.yaml` deeply overrides
them for that agent only. An explicit `TITAN_SETTINGS_PATH` remains a full
replacement. Local recall may evolve the active namespace's LNN state, but
foreign recall never persists activation, tau, or weight changes.

## Compatibility rule

The existing Python functions, HTTP/MCP tool names, CLI flags, configuration
keys, readable files, database tables, Memory IDs, and Scene IDs are public
contracts. Architecture work is additive and reversible: old import paths stay
available as forwarding interfaces while callers move to the deeper seams.
