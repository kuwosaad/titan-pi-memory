from __future__ import annotations


DEDUP_SYSTEM = """<role>
you are the titan memory librarian. your job is to merge duplicate or overlapping memories into clean, single entries.
you are efficient, precise, and never discard useful information.
</role>"""


DEDUP_TASK = """<task>
review the list of atomic memories below. find groups of memories that talk about the same thing (same topic, same decision, same fact, same preference, overlapping subject).

for each group:
- merge them into a single memory sentence that captures all the important information.
- keep the highest-confidence version when entries contradict.

for memories that are truly unique (no other memory talks about the same thing):
- keep them as-is.

return a JSON object with two keys:
- "merged": list of merged memory objects. each object has:
  - "text": the merged sentence (one sentence, high-signal)
  - "stream": "rough" or "learnings" (copy from the dominant source)
  - "type": the type label (copy from the dominant source)
  - "speaker_focus": copy from the dominant source or set to "shared"
  - "memory_kind": copy from the dominant source or set to null
  - "merged_from_ids": list of original memory ids that were merged
- "discarded_ids": list of original memory ids that are fully covered by a merged entry (do not need to be stored separately)

rules:
- only merge when two or more memories are truly about the same topic.
- do not merge memories about different topics just because they share a keyword.
- do not discard a unique memory — put it in "merged" as-is with merged_from_ids containing only itself.
- if in doubt, keep separate.
</task>"""


def build_dedup_messages(memories_json: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": DEDUP_SYSTEM},
        {"role": "user", "content": f"{DEDUP_TASK}\n\n<input>\n{memories_json}\n</input>"},
    ]


def build_dedup_input(memories: list[dict]) -> list[dict]:
    slim: list[dict] = []
    for mem in memories:
        slim.append(
            {
                "id": mem.get("id"),
                "text": mem.get("text"),
                "stream": mem.get("stream", "rough"),
                "type": mem.get("type"),
                "speaker_focus": mem.get("speaker_focus"),
                "memory_kind": mem.get("memory_kind"),
            }
        )
    return slim
