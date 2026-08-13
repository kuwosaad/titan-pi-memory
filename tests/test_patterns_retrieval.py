import tempfile
import unittest
from pathlib import Path

from app.patterns.models import Pattern, PatternEvidence
from app.patterns.retrieval import retrieve_accepted_patterns
from app.patterns.store import PatternStore
from app.storage.memories import SqliteMemoryRepository


def _memory_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "turn": 1,
        "embedding": [1.0, 0.0],
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
    }
    return [
        {**base, "id": "s1:1:0", "text": "Billing changes need Stripe webhook checks.", "type": "decision", "session_id": "s1", "scene_id": "scene-1", "ts": "2026-06-01T00:00:00+00:00"},
        {**base, "id": "s2:1:0", "text": "Entitlement checks broke after a billing dashboard update.", "type": "issue", "session_id": "s2", "scene_id": "scene-2", "ts": "2026-06-02T00:00:00+00:00"},
        {**base, "id": "s3:1:0", "text": "Stripe subscription state must match dashboard state.", "type": "workflow", "session_id": "s3", "scene_id": "scene-3", "ts": "2026-06-03T00:00:00+00:00"},
    ]


def _pattern(status: str = "accepted", title: str = "Billing changes require Stripe checks") -> Pattern:
    return Pattern(
        title=title,
        kind="workflow",
        scope="repo",
        status=status,  # type: ignore[arg-type]
        summary="Billing work repeatedly breaks when webhook and entitlement checks are missed.",
        recommended_behavior="When changing billing, inspect Stripe webhooks, entitlement checks, dashboard state, and tests.",
        applies_when="Billing, Stripe, subscription, or entitlement work.",
        trigger_terms=["billing", "stripe", "webhook", "entitlement"],
        confidence=0.82,
        actionability=0.9,
        retrieval_value=0.9,
    )


def _evidence(pattern_id: str) -> list[PatternEvidence]:
    return [
        PatternEvidence(pattern_id=pattern_id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9),
        PatternEvidence(pattern_id=pattern_id, memory_id="s2:1:0", scene_id="scene-2", role="support", score=0.8),
        PatternEvidence(pattern_id=pattern_id, memory_id="s3:1:0", scene_id="scene-3", role="support", score=0.7),
    ]


class PatternRetrievalTests(unittest.TestCase):
    def test_accepted_matching_pattern_is_retrieved(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            store = PatternStore(sqlite_file)
            pattern = _pattern(status="accepted")
            store.create_pattern(pattern, _evidence(pattern.id), min_support_evidence=3)

            hits = retrieve_accepted_patterns("billing change touching stripe webhooks", db_path=sqlite_file)

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["pattern"]["id"], pattern.id)
            self.assertGreater(hits[0]["trigger_overlap"], 0)
            self.assertIn("billing", hits[0]["matched_terms"])

    def test_candidate_rejected_and_unrelated_patterns_do_not_return(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            store = PatternStore(sqlite_file)
            for status in ["candidate", "rejected"]:
                pattern = _pattern(status=status, title=f"{status} billing pattern")
                store.create_pattern(pattern, _evidence(pattern.id), min_support_evidence=3)
            unrelated = Pattern(
                title="Package exports require TypeScript checks",
                kind="workflow",
                scope="repo",
                status="accepted",
                summary="Package exports need tsc validation.",
                recommended_behavior="Run TypeScript checks when changing package exports.",
                trigger_terms=["package", "exports", "typescript"],
                confidence=0.9,
            )
            store.create_pattern(unrelated, _evidence(unrelated.id), min_support_evidence=3)

            hits = retrieve_accepted_patterns("billing change touching stripe webhooks", db_path=sqlite_file)

            self.assertEqual(hits, [])

    def test_trigger_overlap_sorts_before_weaker_keyword_overlap(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            store = PatternStore(sqlite_file)
            weak = Pattern(
                title="Dashboard changes need state checks",
                kind="workflow",
                scope="repo",
                status="accepted",
                summary="Dashboard state should be checked during product changes.",
                recommended_behavior="Inspect dashboard state.",
                trigger_terms=["dashboard"],
                confidence=0.95,
            )
            strong = _pattern(status="accepted")
            store.create_pattern(weak, _evidence(weak.id), min_support_evidence=3)
            store.create_pattern(strong, _evidence(strong.id), min_support_evidence=3)

            hits = retrieve_accepted_patterns("billing change touching stripe dashboard", db_path=sqlite_file, limit=2)

            self.assertEqual([hit["pattern"]["id"] for hit in hits], [strong.id, weak.id])


if __name__ == "__main__":
    unittest.main()
