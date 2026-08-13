# Privacy Policy

Titan Memory for Codex is local-first.

## What is stored

When you enable active MCP tools, Titan stores memories you explicitly ask it to store or import. When you trust the bundled Codex hooks in `/hooks`, Titan can also write passive lifecycle traces such as prompts, selected tool events, compaction events, and stop events.

## Where data is stored

Codex data is stored locally under:

```text
~/.titan/agents/codex
```

Hook traces are stored locally under:

```text
~/.titan/agents/codex/traces
```

The plugin should not write durable user data into the installed plugin cache.

## Hook trust

Passive capture does not start until you manually trust the Titan hook in Codex `/hooks`. If you do not trust the hook, passive capture is disabled.

## Redaction and limits

The bundled hook redacts common token and API-key shapes before writing traces and truncates large outputs. You should still avoid sending secrets through prompts or tool outputs.

## Network behavior

The Codex plugin itself launches the local Titan MCP runtime. Any model or embedding provider network calls are controlled by your Titan runtime configuration, not by the plugin manifest.

## Removing data

To remove Codex-local Titan data, delete the local Codex namespace directory:

```bash
rm -rf ~/.titan/agents/codex
```

Review the directory before deleting if you want to keep exported memories or traces.
