# Terms for Titan Memory in OpenCode

Titan Memory for OpenCode is provided as a local developer tool for durable memory,
scene recovery, graph and pattern workflows, and passive conversation capture.

You choose whether to install it, enable the MCP server, and retain local traces.
Review the privacy behavior before enabling capture. Do not store secrets, regulated
data, or information you do not have permission to process.

The integration depends on the installed OpenCode 1.x contract and the existing
Titan CLI/runtime. It is provided without a guarantee that OpenCode, provider APIs,
or local runtime dependencies will remain compatible. A memory is evidence and a
pointer to a scene, not a guarantee that the remembered repository state is current;
verify consequential technical claims against the current files and tests.

Federated recall is read-only and preserves `source_agent` provenance. Imported
history requires an explicit scope and approval; source agent directories remain
unchanged. Writes, traces, patterns, and learning state stay in the active OpenCode
namespace.

You are responsible for local access controls, provider agreements, backups, and
deletion of data you no longer want. Titan stores OpenCode data locally under
`~/.titan/agents/opencode` by default. You may remove local data by deleting that
namespace and any separately created exports or backups.

This integration does not modify Titan's engine or publish a separate package. Use
of the integration means acceptance of these conditions and the licenses of Titan,
OpenCode, and any configured model or embedding provider.
