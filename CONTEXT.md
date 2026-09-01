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
may choose different defaults, but they must resolve the same layout contract
and must never accidentally read another agent's pending state or traces.

Codex's default write namespace is `~/.titan/agents/codex`; Pi's is
`~/.titan/agents/pi`. Codex writes only to `codex`. Its default recall path is
the read-only federation over `codex` and `pi`, while other agent namespaces
require explicit source selection. Missing namespaces are skipped; writes
remain isolated. The live Codex MCP recall tools use this federation by
default; write tools and passive capture remain Codex-only. Cross-agent
imports are separate explicit operations.

## Compatibility rule

The existing Python functions, HTTP/MCP tool names, CLI flags, configuration
keys, readable files, database tables, Memory IDs, and Scene IDs are public
contracts. Architecture work is additive and reversible: old import paths stay
available as forwarding interfaces while callers move to the deeper seams.
