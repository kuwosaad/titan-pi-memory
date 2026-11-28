# Supermemory local server: correctness contracts

Static inspection of the shipped macOS arm64 local server v0.0.9, 11 November
2026. This supplements the [server investigation](supermemory-server-investigation.md).
The target was not executed. These findings do not establish cloud behavior,
model quality, or the outcome of a concurrent runtime test.

Binary SHA-256:
`b908080270df6d2eb5712637cb833ebd4fc99a6ee15771d38a555084961332e8`.
The inspected `supermemory-artifacts/derived-analysis/server-analysis.js` has
SHA-256 `89e5209960118a376bd1d4fe3914b966c9d41097010f3612deff02651c4aaf1f`.
`derivation.json` records the original binary range and ten omitted large
base64-shaped strings. The parent's REA binary evidence is
`ev_47099a9376cce3c1309704422d8c672650e44e5026433d5a7d7cf654ef47af6c`.
The Babel symbol index reports zero parse errors. Parsing is not execution.

All character offsets below are zero-based offsets into that **derived JS**, not
binary offsets; lines are one-based. Function names are minifier-generated.

## Main finding

The useful mechanism is **look up existing facts, then explicitly record how a
new fact relates to them**. Storage preserves the old rows. The model decides
whether a fact is a correction, extension, or inference; the code does not prove
that decision from source text. Several different write paths enforce different
rules, so a relation label alone is not a complete state-management algorithm.

## What the inspected paths enforce

| Contract | Observed behavior | Source anchor |
| --- | --- | --- |
| Document duplicate check | `l65` hashes sanitized content with SHA-1, restricts to the organization, compares filtered metadata and container tags, and returns the existing document. It does not compare atomic memory meaning. | L1779, char 3830630; hash `zB0` L1724, char 3664165 |
| Caller-identified document | `i65` finds matching custom ID, organization, and ordered tag array. Identical content/metadata skips; changed metadata takes a metadata-only path; failed documents are retried. | L1779, char 3831035 |
| Atomic fact creation | `e$5` embeds the text, validates vector dimensions/finite values, generates a fresh ID, and inserts a fact. No exact-text lookup or comparison against `savedMemories` appears in this function. | L2999, char 4348932 |
| Parent identity | Parent IDs must exist in the current organization and space. Unknown or out-of-scope parents are silently removed. The lookup does not require the parent to be latest, unforgotten, or unexpired. | `e$5`, L2999, char 4348932 |
| History/version | The first valid parent supplies the root and `version + 1`, regardless of relation type. Additional parents get edges but do not determine the root/version. Only `updates` parents are made nonlatest. | `e$5`, L2999, char 4348932 |
| Graph-only relation | `U$5` inserts an edge and does not change either memory's latest flag. Its SQL has no organization/space, self-link, or cycle check. Database constraints enforce endpoint existence and unique `(from,to,relation)`. | L2999, char 4348512; edge schema L32, char 215892 |
| Ordinary local search | `sJ8.searchMemories` resolves organization/tag to a space and selects latest, unforgotten, unexpired memories with embeddings. SQL orders by vector distance; threshold filtering follows the limited query. | L328, char 2009105 |
| History/related attachments | `It8` includes up to two update hops in either direction and one hop for other relations. It checks organization, space, metadata, and normally forgotten/expiry status, but deliberately does not require latest. | L1561, char 3520191 |
| Fact source | Creation records a document join with `chunkId: null`, `relevanceScore: 100`, and `sourceCount: 1`. The tool schema has no citation, quote, or source-span field. | `e$5`; schema `P68`, L2999, char 4352728 |

Document duplicate checking is therefore **not proof of idempotent fact
creation**. `onConflictDoNothing` does not change that: a new random ID does not
conflict with an earlier identical sentence. The inspected memory schema has no
text uniqueness or unique `(root,version)` constraint. Metadata comparison uses
JSON serialization after stripping internal keys; it is not semantic equality.
The anonymous-document duplicate helper also does not inspect processing status,
unlike the custom-ID helper.

## Latest is not simply the highest version

An `extends` or `derives` child receives a higher version while its parent stays
latest. This can be appropriate: both statements may remain valid. A plain
`linkMemories(..., relation="updates")` only adds an edge, so both endpoints can
also remain latest. Normal search filters flags; it does not infer state by
traversing those edges. Related attachments can return older nonlatest facts as
history.

The direct update path has another important boundary. `j88` excludes forgotten
and expired records, but accepts a nonlatest ID. `F25` creates a new child from
that row and retires only that row. Calling it twice with the same old ID can
therefore create two children with the same version and latest=true according
to these function bodies. That is a static counterexample to a guaranteed
single-current-version invariant, not a runtime reproduction. No compare-and-swap
on expected latest/version is present in these functions. Scheduling elsewhere
may reduce concurrency, but does not itself establish that invariant.

Anchors: `j88` L2890, char 4061188; `F25` L2890, char 4062066;
memory indexes L32 around char 214606.

## Scope and grounding have different strengths

Normal search and CreateMemory parent lookup carry explicit organization/space
restrictions. `getMemoryDetails` first checks both, then returns the root's
history, incident edges, and document-source IDs (`c$5`, L2999, char 4347736).
The extraction agent uses the first document-space row returned by `N$5`;
that query has no ordering and the agent does not loop over all returned spaces.
This is not proof of symmetric extraction across all spaces of a multi-tag
document (`N$5`, char 4346647; `_$5`, char 4334925).

There is also a bounded namespace round-trip concern. The accepted tag schema
allows 100 characters from `[a-zA-Z0-9_:-]` (`n7`, L1509, char 3388267).
`u9`/`sV` sanitize and shorten generated namespace strings to 128 characters;
they append the original tag only when it contains a colon. `xz0` then decodes
the shortened string and local search uses that tag in an exact space lookup.
With a 22-character organization ID, plain tags of length 97–100 for memory
search, or 99–100 for chunk search, cross that shortening threshold without
retaining their original spelling. The inspected expressions therefore predict
a failed lookup for those otherwise valid tags. This is a static concern, not
a runtime-confirmed bug. Slash/dot collision examples would be invalid input
and are not evidence. Anchors: L322, `vC` char 2005634, `es0` char 2005821,
`sV` char 2005976, `u9` char 2006066, `xz0` char 2006749.

Do not generalize these restrictions to every tool. `linkMemories` supplies only
the environment and endpoint IDs to its insert helper. `forgetMemory` first
updates by memory ID plus organization, without space, then invokes
`invalidateMemories`. In the local adapter that latter function updates IDs
without checking organization or space. This is an observed helper-level
enforcement gap if an out-of-scope ID reaches the tool; exploitability and model
behavior were not tested. Anchors: `n$5` L2999, char 4348669;
`sJ8.invalidateMemories` L328, char 2010863.

Grounding is weaker than source retention. The agent receives the first eight
active chunks and can read more, search other documents, inspect memories, and
write facts. The prompt requests full reading and several searches, but the
CreateMemory tool does not check that either happened. A fact may be influenced
by wider context, yet the inserted source is the current document with no
specific chunk. Retaining a source link is not an entailment check.

Anchors: `a$5` L2999, char 4346943; `h$5` char 4347352;
agent `_$5` L2956, char 4334925; prompt `j$5` char 4342918;
CreateMemory schema `P68` L2999, char 4352728.

## Recovery is useful, but not an exactly-once promise

For the local adapter, vector writes are **another operation on the same
PostgreSQL table**, not a second vector database. `sJ8` updates the embedding
columns by row ID (L322, char 2007002).

`e$5` creates the row, document-source join, edges, and retirement of update
parents in one transaction. It then writes the embedding. If that fails, it
tries to delete the new row/edges/source and restore each update parent's saved
flags, provided no remaining update child exists. Rollback failures are logged.
Parent flags were read before the transaction; the path does not demonstrate
protection against every concurrent change or transient visibility gap.

Each successful tool call is durable before the complete model run succeeds.
The agent accumulates created records in a local array. If the model fails later,
it returns a failed result without undoing all earlier successful creations.
`D$5` fails the generation stage if any document fails; `g$5` and `W1` permit
generation retries. A repeated generation pass can create fresh IDs for the same
fact. Existing-memory search may let the model avoid that, but no deterministic
per-fact retry identity was found in these inspected paths.

A completed result artifact permits rebuilding embeddings from saved rows
without rerunning generation. That is meaningful recovery, but only after the
artifact exists. Anchors: `D$5` L2999, char 4355430; `g$5` char 4359984;
`W1` char 4362734; `Y$5` L2956, char 4333837.

The separate direct-update path `F25` commits state before embedding the new
text and has no matching compensating rollback in its function body. Direct
batch fact insertion `s25` likewise commits before its vector operation. These
paths should not inherit stronger claims from the agent-specific creation path.

## Smallest Titan experiment worth taking from this

Keep Titan's existing event/scene evidence and retry identity. Test a bounded
correction resolver after extraction, initially as a scored proposal rather
than automatic deletion or broad version migration:

1. For an explicit replacement claim, retrieve at most three plausible old
   facts, retaining source-qualified IDs and source excerpts.
2. Produce either `retain_both` or a proposed `supersedes(old_id)` with evidence
   references from the **current** scene. Old facts are comparison context,
   never newly attributed source evidence.
3. Reject unresolved IDs and unsupported links. Different people, projects,
   environments, lifecycle states, or uncertain scope default to retaining both.
4. Compare current-state and historical answers on independent raw-scene cases,
   including quoted old statements, reversals, independent corroboration,
   lab/production differences, replay after partial failure, and two competing
   corrections. Charge added calls and latency.

Only after that earns its cost should Titan persist additive typed links, with
atomic validation of the target state and stable operation identity. Do not copy
the 40-step model loop, memory-count incentives, unverified model relations, or
document-level dedup as a substitute for preserving independent evidence.
