import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock, patch

from app.save_pipeline.pipeline import retrieve_memory_brief


class CompactRecallTests(unittest.TestCase):
    def setUp(self):
        self.hits = [
            {"memory": {"id": "m1", "text": "A durable preference for direct answers.",
                        "source_agent": "codex", "scene_id": "s1", "source_event_ids": ["e1"],
                        "embedding": [0.1], "provenance": {"user": "private"}}, "score": 1},
            {"memory": {"id": "m2", "text": "Another relevant memory.",
                        "source_agent": "codex", "scene_id": "s2", "source_event_ids": ["e2"]}, "score": .9},
        ]
        self.patterns = [{"pattern": {"id": "p1", "title": "Answer directly", "kind": "workflow", "scope": "global",
                                       "recommended_behavior": "Be direct."}}]
        patches = [
            patch("app.save_pipeline.pipeline.route_query", return_value={"use_memory": True, "mode": "both"}),
            patch("app.retrieval_pipeline.retriever.retrieve_memories", return_value=self.hits),
            patch("app.patterns.retrieval.retrieve_accepted_patterns", return_value=self.patterns),
            patch("app.save_pipeline.pipeline.get_scene_references", return_value=[]),
            patch("app.save_pipeline.pipeline.load_settings", return_value={"notes_max_chars": 700, "notes_max_items": 6}),
            patch("app.patterns.retrieval.record_pattern_applications"),
            patch("app.runtime.context.get_runtime_context", return_value=SimpleNamespace(agent_name="codex")),
            patch("app.retrieval_pipeline.federated.get_federated_recall",
                  return_value=Mock(sources=Mock(return_value=["codex"]))),
        ]
        for item in patches:
            started = item.start()
            if item is patches[2]:
                self.pattern_retrieval = started
            if item is patches[5]:
                self.pattern_record = started
            self.addCleanup(item.stop)

    def test_active_source_is_qualified_without_mutating_retrieved_hits(self):
        self.hits[0]["memory"].pop("source_agent")
        full = retrieve_memory_brief("direct answers", detail="full", max_items=1)
        compact = retrieve_memory_brief("direct answers", detail="compact", max_items=1)
        self.assertEqual(full["memories"][0]["source_agent"], "codex")
        self.assertEqual(compact["memories"][0]["source_agent"], "codex")
        self.assertEqual(compact["scene_refs"][0]["source_agent"], "codex")
        self.assertNotIn("source_agent", self.hits[0]["memory"])
        self.assertNotIn("source_agent", self.hits[0])

    def test_mapping_settings_apply_default_limits_to_both_sections(self):
        settings = MappingProxyType({"notes_max_items": 1, "notes_max_chars": 90,
                                     "step2": MappingProxyType({})})
        with patch("app.save_pipeline.pipeline.load_settings", return_value=settings):
            result = retrieve_memory_brief("direct answers", detail="compact")
        self.assertLessEqual(len(result["brief"]), 90)
        self.assertEqual(len(result["memories"]) + len(result["pattern_refs"]), 1)
        self.pattern_retrieval.assert_not_called()

    def test_combined_brief_respects_requested_budget(self):
        full = retrieve_memory_brief("direct answers", max_chars=150)
        self.assertLessEqual(len(full["brief"]), 150)
        self.assertIn("A durable preference", full["brief"])
        unrestricted = retrieve_memory_brief("direct answers", max_chars=700)
        self.assertIn("PATTERN BRIEF:", unrestricted["brief"])
        self.assertIn("MEMORY BRIEF:", unrestricted["brief"])
        self.assertLessEqual(len(unrestricted["brief"]), 700)

    def test_compact_references_are_bounded_and_do_not_duplicate_text(self):
        result = retrieve_memory_brief("direct answers", detail="compact", max_items=1, max_chars=10)
        self.assertEqual(result["brief"], "")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["memories"], [{"id": "m1", "source_agent": "codex", "scene_id": "s1"}])
        self.assertEqual([ref["scene_id"] for ref in result["scene_refs"]], ["s1"])
        self.assertNotIn("patterns", result)
        self.assertNotIn("scenes", result)

    def test_compact_reference_retains_age_reliability_and_verification(self):
        self.hits[0]["memory"].update(
            ts="2020-01-02T00:00:00Z", source_reliability=0.2,
            verification_status="unverified", fallback_generated=True,
            arbitrary_blob="private" * 1000,
        )
        result = retrieve_memory_brief("direct answers", detail="compact", max_items=1)
        pointer = result["memories"][0]
        self.assertEqual(pointer["ts"], "2020-01-02T00:00:00Z")
        self.assertEqual(pointer["source_reliability"], 0.2)
        self.assertEqual(pointer["verification_status"], "unverified")
        self.assertTrue(pointer["fallback_generated"])
        for field in ("text", "embedding", "provenance", "arbitrary_blob"):
            self.assertNotIn(field, pointer)

    def test_compact_references_follow_rendered_hits_when_first_is_too_long(self):
        self.hits[0]["memory"]["text"] = "Long " * 100
        result = retrieve_memory_brief("direct answers", detail="compact", max_items=1, max_chars=100)
        self.assertIn("Another relevant memory.", result["brief"])
        self.assertEqual(result["memories"][0]["id"], "m2")
        self.assertEqual(result["scene_refs"][0]["scene_id"], "s2")

    def test_compact_and_full_keep_federated_duplicate_evidence(self):
        refs = [{"source_agent": "codex", "memory_id": "m1", "scene_id": "s1"},
                {"source_agent": "other", "memory_id": "m9", "scene_id": "s9"}]
        self.hits[0]["memory"].update(evidence_refs=refs, evidence_refs_truncated=True)
        recall = Mock()
        recall.sources.return_value = ["codex", "other"]
        recall.query_hits.return_value = self.hits
        recall.scene_references.return_value = []
        with patch("app.retrieval_pipeline.federated.get_federated_recall", return_value=recall):
            compact = retrieve_memory_brief("direct answers", detail="compact", max_items=1)
            full = retrieve_memory_brief("direct answers", detail="full")
        for result in (compact, full):
            self.assertEqual(result["memories"][0]["evidence_refs"], refs)
            self.assertTrue(result["memories"][0]["evidence_refs_truncated"])

    def test_zero_budget_and_zero_reference_limit(self):
        result = retrieve_memory_brief("direct answers", detail="compact", max_items=0, max_chars=0)
        self.assertEqual(result["brief"], "")
        self.assertEqual(result["memories"], [])
        self.assertEqual(result["scene_refs"], [])

    def test_shared_item_budget_skips_pattern_retrieval_when_full(self):
        result = retrieve_memory_brief("direct answers", detail="compact", max_items=1)
        self.pattern_retrieval.assert_not_called()
        self.assertEqual(result["pattern_refs"], [])
        self.assertNotIn("PATTERN BRIEF:", result["brief"])

    def test_default_item_budget_also_skips_patterns_when_full(self):
        with patch("app.save_pipeline.pipeline.load_settings", return_value={"notes_max_items": 1}):
            retrieve_memory_brief("direct answers", detail="compact")
        self.pattern_retrieval.assert_not_called()

    def test_rendered_pattern_has_compact_pointer_with_shared_item_budget(self):
        del self.hits[1:]
        result = retrieve_memory_brief("direct answers", detail="compact", max_items=2, max_chars=700)
        self.assertIn("PATTERN BRIEF:", result["brief"])
        self.assertEqual(result["pattern_refs"], [{"id": "p1", "source_agent": "codex"}])
        self.assertEqual(len(result["memories"]) + len(result["pattern_refs"]), 2)
        self.pattern_retrieval.assert_called_once_with("direct answers", record_applications=False)
        self.pattern_record.assert_called_once_with("direct answers", self.patterns)

    def test_unrendered_patterns_are_not_recorded_as_shown(self):
        self.patterns[0]["pattern"]["title"] = "very long " * 100
        retrieve_memory_brief("direct answers", detail="compact", max_chars=150)
        self.pattern_retrieval.assert_called_once_with("direct answers", record_applications=False)
        self.pattern_record.assert_not_called()

    def test_other_agent_only_does_not_read_or_inject_active_patterns(self):
        recall = Mock()
        recall.sources.return_value = ["other"]
        recall.query_hits.return_value = []
        recall.scene_references.return_value = []
        with patch("app.retrieval_pipeline.federated.get_federated_recall", return_value=recall):
            result = retrieve_memory_brief("direct answers", detail="compact", sources=["other"])
        self.pattern_retrieval.assert_not_called()
        self.assertEqual(result["pattern_refs"], [])
        self.assertNotIn("PATTERN BRIEF:", result["brief"])

    def test_zero_character_budget_skips_pattern_retrieval(self):
        retrieve_memory_brief("direct answers", detail="full", max_chars=0)
        self.pattern_retrieval.assert_not_called()

    def test_compact_opt_out_is_bounded_and_never_accesses_memory(self):
        with (patch("app.save_pipeline.pipeline.route_query", return_value={"use_memory": False}),
              patch("app.retrieval_pipeline.retriever.retrieve_memories") as retrieve,
              patch("app.retrieval_pipeline.federated.get_federated_recall") as federation):
            result = retrieve_memory_brief("fresh context", detail="compact", max_chars=0)
        retrieve.assert_not_called()
        federation.assert_not_called()
        self.pattern_retrieval.assert_not_called()
        self.assertEqual(result["brief"], "")
        self.assertEqual(result["pattern_refs"], [])
        self.assertNotIn("scenes", result)

    def test_invalid_detail_is_rejected(self):
        with self.assertRaises(ValueError):
            retrieve_memory_brief("direct answers", detail="verbose")


if __name__ == "__main__":
    unittest.main()
