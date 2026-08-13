import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.patterns.models import Pattern, PatternEvidence
from app.patterns.store import PatternStore
from app.save_pipeline.pipeline import retrieve_memory_brief
from app.storage.memories import SqliteMemoryRepository


MEMORY = {
    "id": "s1:1:0",
    "text": "Billing changes need Stripe webhook checks.",
    "stream": "learnings",
    "type": "decision",
    "session_id": "s1",
    "scene_id": "scene-1",
    "ts": "2026-06-01T00:00:00+00:00",
    "source_reliability": 0.9,
    "embedding": [1.0, 0.0],
    "provenance": {"user": "u", "assistant": "a"},
    "source_event_ids": [],
    "source_type": "mixed",
    "verification_status": "unverified",
    "fallback_generated": False,
}


def _seed_pattern(sqlite_file: Path, *, status: str = "accepted") -> Pattern:
    SqliteMemoryRepository(sqlite_file).append_memories([MEMORY])
    store = PatternStore(sqlite_file)
    pattern = Pattern(
        title="Billing changes require Stripe checks",
        kind="workflow",
        scope="repo",
        status=status,  # type: ignore[arg-type]
        summary="Billing work repeatedly needs webhook and entitlement checks.",
        recommended_behavior="When changing billing, inspect Stripe webhooks, entitlement checks, dashboard state, and tests.",
        applies_when="Billing, Stripe, subscription, or entitlement work.",
        trigger_terms=["billing", "stripe", "webhook"],
        confidence=0.82,
    )
    evidence = [
        PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9),
    ]
    store.create_pattern(pattern, evidence, min_support_evidence=1)
    return pattern


class RetrieveWithPatternsTests(unittest.TestCase):
    def test_pattern_brief_is_prepended_before_memory_brief(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            pattern = _seed_pattern(sqlite_file, status="accepted")

            with (
                patch("app.patterns.retrieval.resolve_pattern_db_path", return_value=sqlite_file),
                patch(
                    "app.retrieval_pipeline.retriever.retrieve_memories",
                    return_value=[{"memory": MEMORY, "score": 0.9}],
                ),
                patch("app.save_pipeline.pipeline.get_scenes", return_value=[]),
            ):
                payload = retrieve_memory_brief("billing stripe webhook change")

            self.assertEqual(payload["patterns"][0]["id"], pattern.id)
            self.assertTrue(payload["pattern_brief"].startswith("PATTERN BRIEF:"))
            self.assertTrue(payload["brief"].startswith("PATTERN BRIEF:"))
            self.assertIn("MEMORY BRIEF:", payload["brief"])
            self.assertLess(payload["brief"].index("PATTERN BRIEF:"), payload["brief"].index("MEMORY BRIEF:"))

    def test_no_pattern_brief_for_candidate_pattern(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            _seed_pattern(sqlite_file, status="candidate")

            with (
                patch("app.patterns.retrieval.resolve_pattern_db_path", return_value=sqlite_file),
                patch(
                    "app.retrieval_pipeline.retriever.retrieve_memories",
                    return_value=[{"memory": MEMORY, "score": 0.9}],
                ),
                patch("app.save_pipeline.pipeline.get_scenes", return_value=[]),
            ):
                payload = retrieve_memory_brief("billing stripe webhook change")

            self.assertEqual(payload["patterns"], [])
            self.assertEqual(payload["pattern_brief"], "")
            self.assertTrue(payload["brief"].startswith("MEMORY BRIEF:"))


if __name__ == "__main__":
    unittest.main()
