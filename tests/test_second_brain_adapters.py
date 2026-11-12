"""Regression tests for automatic recall output and empty prompt handling."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

from integrations.claude_titan_plugin.runtime.daemon import _compact_recall_payload


HOOK = Path(__file__).resolve().parent.parent / "integrations/claude_titan_plugin/scripts/titan_claude_hook.py"
spec = importlib.util.spec_from_file_location("second_brain_claude_hook", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def test_compact_recall_skips_empty_and_duplicate_entries_without_losing_provenance():
    memories = [
        {"source_agent": "codex", "scene_id": "s1", "text": "  "},
        {"source_agent": "codex", "scene_id": "s1", "text": "A decision"},
        {"source_agent": "codex", "scene_id": "s1", "text": " A   decision "},
        {"source_agent": "pi", "scene_id": "s1", "text": "A decision"},
        {"source_agent": "codex", "scene_id": "s2", "text": "A decision"},
    ]
    result = _compact_recall_payload({"memories": memories}, limit=3)
    assert result["count"] == 3
    assert len(result["brief"].splitlines()) == 3
    assert result["scene_refs"] == [
        {"source_agent": "codex", "scene_id": "s1"},
        {"source_agent": "pi", "scene_id": "s1"},
        {"source_agent": "codex", "scene_id": "s2"},
    ]


def test_empty_or_non_string_user_prompts_do_not_trigger_recent_recall():
    with patch.object(hook, "_import_runtime") as load:
        for prompt in ("", "   ", {"text": "hello"}):
            result = hook.automatic_recall(
                {"hook_event_name": "UserPromptSubmit", "prompt": prompt},
                agent_name="claude-code",
                env={"CLAUDE_PLUGIN_DATA": "/tmp/unused"},
            )
            assert result == ""
        load.assert_not_called()


def test_compact_recall_refs_only_include_rendered_memories_under_character_cap():
    memories = [
        {"source_agent": "pi", "scene_id": f"s{i}", "text": "x" * 690}
        for i in range(8)
    ]
    result = _compact_recall_payload({"memories": memories, "scene_refs": [
        {"source_agent": "pi", "scene_id": "unrelated"}
    ]}, limit=8)
    assert result["count"] == 5
    assert len(result["brief"]) <= 4000
    assert [ref["scene_id"] for ref in result["scene_refs"]] == [f"s{i}" for i in range(5)]
