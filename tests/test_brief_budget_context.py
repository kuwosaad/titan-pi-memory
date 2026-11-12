import unittest

from app.retrieval_pipeline.brief import build_memory_notes, build_timeline


def hit(text, score, **kwargs):
    return {"score": score, "memory": {"text": text, "stream": "rough", "type": "fact"}, **kwargs}


class BriefBudgetTests(unittest.TestCase):
    def test_relevance_and_oversized_hits_do_not_starve_later_hits(self):
        hits = [hit("x" * 300, 1.0), hit("high relevance", 0.9), hit("low relevance", 0.1)]
        hits[2]["memory"].update(stream="learnings", type="decision")
        included = []
        brief = build_memory_notes(hits, max_items=1, max_chars=100, included_hits=included)
        self.assertEqual(included, [hits[1]])
        self.assertIn("1. [rough/fact] high relevance", brief)
        self.assertNotIn("low relevance", brief)
        self.assertLessEqual(len(brief), 100)

    def test_preserves_selected_order_when_scores_do_not_explain_ranking(self):
        hits = [hit("selected facet", .2), hit("higher raw score", .9)]
        brief = build_memory_notes(hits, max_items=1, max_chars=100)
        self.assertIn("selected facet", brief)
        self.assertNotIn("higher raw score", brief)

    def test_deduplicate_within_source_but_keep_distinct_sources(self):
        hits = [hit("Same fact", 1, source_agent="pi"), hit(" Same fact ", .9, source_agent="pi"),
                hit("Same fact", .8, source_agent="codex")]
        brief = build_memory_notes(hits, max_items=3, max_chars=300)
        self.assertEqual(brief.casefold().count("same fact"), 2)
        self.assertIn("pi ·", brief)
        self.assertIn("codex ·", brief)

    def test_cluster_budget_counts_evidence_and_never_emits_empty_header(self):
        common = {"cluster_id": 0, "cluster_size": 2, "source_agent": "pi"}
        hits = [hit("x" * 300, 1, **common), hit("short evidence", .9, **common), hit("later", .8)]
        brief = build_memory_notes(hits, max_items=1, max_chars=110, cluster_mode=True)
        self.assertIn("INSIGHT 1", brief)
        self.assertIn("short evidence", brief)
        self.assertNotIn("later", brief)
        self.assertLessEqual(len(brief), 110)
        self.assertEqual(build_memory_notes(hits[:1], max_chars=110, cluster_mode=True), "")

    def test_singleton_relevance_beats_larger_lower_scoring_cluster(self):
        hits = [hit("relevant", .9), hit("cluster evidence", .2, cluster_id=0, cluster_size=3)]
        brief = build_memory_notes(hits, max_items=1, max_chars=200, cluster_mode=True)
        self.assertIn("relevant", brief)
        self.assertNotIn("cluster evidence", brief)

    def test_explicit_zero_limits_are_respected(self):
        self.assertEqual(build_memory_notes([hit("text", 1)], max_items=0), "")
        self.assertEqual(build_memory_notes([hit("text", 1)], max_chars=0), "")
        self.assertEqual(build_timeline([{"text": "text"}], max_chars=0), {"timeline": [], "timeline_summary": ""})

    def test_timeline_contains_only_fitting_entries_with_source(self):
        memories = [{"text": "x" * 300, "ts": "1", "id": "long"},
                    {"text": "short", "ts": "2", "id": "same-id", "source_agent": "pi"}]
        result = build_timeline(memories, max_items=1, max_chars=100)
        self.assertEqual(len(result["timeline"]), 1)
        self.assertEqual(result["timeline"][0]["evidence_ids"], ["same-id"])
        self.assertEqual(result["timeline"][0]["source_agent"], "pi")
        self.assertIn("pi ·", result["timeline_summary"])
        self.assertLessEqual(len(result["timeline_summary"]), 100)


if __name__ == "__main__":
    unittest.main()
