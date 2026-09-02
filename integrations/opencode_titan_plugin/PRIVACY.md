# Privacy for Titan Memory in OpenCode

Titan Memory is local-first. The OpenCode integration writes to the local Titan
namespace and does not send captured conversation data to a plugin service.

## What capture stores

When enabled, the plugin may store:

- text from user messages and completed assistant responses;
- tool names, call IDs, redacted inputs, compact output excerpts, and errors;
- session and message identifiers needed for provenance and deduplication;
- timestamps and stable idle boundaries.

Before writing, the plugin recursively redacts known credential-shaped values and
stores only the fields needed by Titan's trace format. Titan's own MCP tool results
are omitted so recalled memory is not captured again as new evidence.

## Where data lives

OpenCode data is stored in:

```text
~/.titan/agents/opencode/
```

Trace batches are under `traces/`; extracted memories and scenes are under the
namespace's normal Titan storage. Directories are created as `0700` and trace files
as `0600`. Batches are written privately and renamed atomically, so a reader does
not observe a partial JSONL file.

Federated recall can inspect other valid Titan namespaces in read-only mode. Results
retain `source_agent` provenance, and scene lookups route to that owning namespace.
Capture and all writes remain local to `opencode`.

## Provider data

If Titan is configured with a remote model or embedding provider, that provider's
handling is governed by its own terms and the Titan runtime configuration. The
integration does not add a provider or silently change Titan settings. Keep provider
credentials in the normal protected Titan configuration and never place them in a
trace, skill, issue, or commit.

## Control and deletion

Setup is explicit and can be rerun after reviewing the files it will install. To
disable the integration, remove the installed plugin file and the `titan-memory`
entry from the OpenCode configuration, then restart OpenCode. To remove local
OpenCode Titan data, stop active Titan processes and delete:

```text
~/.titan/agents/opencode
```

Also remove any backups or exported bundles if you created them. Deletion is local
and may not be recoverable.
