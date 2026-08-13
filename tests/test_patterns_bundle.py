import json
import tempfile
import unittest
from pathlib import Path

from app.patterns.bundle import PATTERN_BUNDLE_SCHEMA, export_pattern_bundle, import_pattern_bundle
from app.patterns.models import Pattern, PatternEvidence
from app.patterns.processing import PatternProcessingLedger
from app.patterns.store import PatternStore
from app.storage.memories import SqliteMemoryRepository


BASE_MEMORY = {
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


def _memory(memory_id: str, scene_id: str, text: str) -> dict:
    return {
        **BASE_MEMORY,
        "id": memory_id,
        "text": text,
        "type": "decision",
        "session_id": scene_id.split(":")[0],
        "scene_id": scene_id,
        "ts": "2026-06-01T00:00:00+00:00",
    }


def _pattern(status: str = "accepted") -> Pattern:
    return Pattern(
        title="Billing changes require Stripe checks",
        kind="workflow",
        scope="repo",
        status=status,  # type: ignore[arg-type]
        summary="Billing changes repeatedly need Stripe and entitlement checks.",
        recommended_behavior="Inspect Stripe webhooks, entitlements, dashboard state, and tests before shipping billing changes.",
        trigger_terms=["billing", "stripe", "webhook"],
        confidence=0.87,
        actionability=0.9,
        retrieval_value=0.9,
    )


class PatternBundleTests(unittest.TestCase):
    def test_export_defaults_to_accepted_patterns_and_redacted_memory_summaries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(db).append_memories([
                _memory("s1:1:0", "s1:scene:1", "Use Stripe key sk-testsecret1234567890 in a local fixture."),
                _memory("s2:1:0", "s2:scene:1", "Billing dashboard check."),
            ])
            store = PatternStore(db)
            accepted = _pattern("accepted")
            accepted.recommended_behavior += " Never export GEMINI_API_KEY=super-secret-value."
            candidate = _pattern("candidate")
            store.create_pattern(
                accepted,
                [PatternEvidence(pattern_id=accepted.id, memory_id="s1:1:0", scene_id="s1:scene:1", role="support", score=0.9)],
                min_support_evidence=1,
            )
            store.create_pattern(
                candidate,
                [PatternEvidence(pattern_id=candidate.id, memory_id="s2:1:0", scene_id="s2:scene:1", role="support", score=0.8)],
                min_support_evidence=1,
            )
            ledger = PatternProcessingLedger(db)
            run = ledger.start_run(processor_version="v1", processor_config_hash="hash", mode="test")
            ledger.mark_processed(["s1:1:0"], processor_version="v1", processor_config_hash="hash", run_id=run.id, pattern_ids=[accepted.id])
            ledger.mark_processed(["unrelated:1:0"], processor_version="v1", processor_config_hash="hash", run_id=run.id, pattern_ids=[f"{accepted.id}-suffix"])

            bundle = export_pattern_bundle(db_path=db)

            self.assertEqual(bundle["schema"], PATTERN_BUNDLE_SCHEMA)
            self.assertEqual([p["id"] for p in bundle["patterns"]], [accepted.id])
            self.assertEqual(len(bundle["evidence"]), 1)
            self.assertIn("[API_KEY_REDACTED]", bundle["memory_summaries"][0]["summary"])
            bundle_json = json.dumps(bundle)
            self.assertNotIn("sk-testsecret", bundle_json)
            self.assertNotIn("super-secret-value", bundle_json)
            self.assertEqual(len(bundle["progress"]["memory_processing"]), 1)
            self.assertEqual(bundle["progress"]["memory_processing"][0]["memory_id"], "s1:1:0")
            self.assertTrue(any(item["kind"] == "raw_scene_text_omitted" for item in bundle["redactions"]))

    def test_import_bundle_skips_existing_and_does_not_require_local_memories(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source_db = Path(source_dir) / "memory_store.db"
            target_db = Path(target_dir) / "memory_store.db"
            SqliteMemoryRepository(source_db).append_memories([
                _memory("s1:1:0", "s1:scene:1", "Billing dashboard check."),
            ])
            store = PatternStore(source_db)
            pattern = _pattern("accepted")
            store.create_pattern(
                pattern,
                [PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0", scene_id="s1:scene:1", role="support", score=0.9)],
                min_support_evidence=1,
            )
            bundle = export_pattern_bundle(db_path=source_db)

            result = import_pattern_bundle(bundle, db_path=target_db)
            second_result = import_pattern_bundle(bundle, db_path=target_db)

            imported = PatternStore(target_db).get_pattern(pattern.id)
            self.assertIsNotNone(imported)
            self.assertEqual(result["imported_patterns"], 1)
            self.assertEqual(second_result["skipped_existing_patterns"], 1)
            self.assertEqual(len(PatternStore(target_db).list_evidence(pattern.id)), 1)

    def test_export_imported_only_bundle_without_local_memories(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source_db = Path(source_dir) / "memory_store.db"
            target_db = Path(target_dir) / "memory_store.db"
            SqliteMemoryRepository(source_db).append_memories([
                _memory("s1:1:0", "s1:scene:1", "Billing dashboard check."),
            ])
            store = PatternStore(source_db)
            pattern = _pattern("accepted")
            store.create_pattern(
                pattern,
                [PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0", scene_id="s1:scene:1", role="support", score=0.9)],
                min_support_evidence=1,
            )
            bundle = export_pattern_bundle(db_path=source_db)
            import_pattern_bundle(bundle, db_path=target_db)

            exported = export_pattern_bundle(db_path=target_db)

            self.assertEqual([item["id"] for item in exported["patterns"]], [pattern.id])
            self.assertEqual(exported["memory_summaries"], [])

    def test_import_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(ValueError):
                import_pattern_bundle({"schema": "unknown", "patterns": []}, db_path=Path(tmp_dir) / "memory_store.db")


if __name__ == "__main__":
    unittest.main()
