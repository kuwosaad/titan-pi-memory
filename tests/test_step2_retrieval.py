import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import yaml

from app.retrieval_pipeline import retriever
from app.retrieval_pipeline.brief import build_memory_notes


class FrozenRepository:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)

    def query_candidates_with_text(self, *_args):
        return copy.deepcopy(self.rows)

    def query_candidates(self, *_args):
        return copy.deepcopy(self.rows)

    def update_lnn_state(self, *_args, **_kwargs):
        raise AssertionError("retrieval wrote LNN state")

    def batch_update_weights(self, *_args, **_kwargs):
        raise AssertionError("retrieval wrote LNN weights")


def memory(memory_id, text, stream, timestamp, vector):
    return {
        "id": memory_id,
        "text": text,
        "stream": stream,
        "type": "fact",
        "scene_id": f"scene-{memory_id}",
        "ts": timestamp,
        "embedding": list(vector),
        "source_reliability": 0.9,
    }


class StepTwoRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.settings = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text(encoding="utf-8")
        )

    def test_default_retrieval_marks_change_and_keeps_lnn_stateless(self):
        rows = [
            memory("old", "We use SQLite storage.", "learnings", "2026-01-01T00:00:00+00:00", (1.0, 0.0)),
            memory("new", "We avoid SQLite storage.", "learnings", "2026-02-01T00:00:00+00:00", (1.0, 0.0)),
        ]
        repository = FrozenRepository(rows)
        vector = np.array([1.0, 0.0], dtype=np.float32)
        with patch("app.retrieval_pipeline.config.load_settings", return_value=self.settings), patch.object(
            retriever, "embed", side_effect=lambda texts: [vector.copy() for _ in texts]
        ):
            hits = retriever.retrieve_memories(
                "Should we use SQLite storage?",
                top_k=2,
                repository=repository,
                persist_lnn_state=True,
            )
            repeated = retriever.retrieve_memories(
                "Should we use SQLite storage?",
                top_k=2,
                repository=repository,
                persist_lnn_state=True,
            )

        self.assertEqual([hit["memory"]["id"] for hit in hits], ["new", "old"])
        self.assertEqual([hit["memory"]["id"] for hit in repeated], ["new", "old"])
        self.assertGreater(hits[0]["step2_contradiction_delta"], 0)
        self.assertLess(hits[1]["step2_contradiction_delta"], 0)
        self.assertEqual(repository.rows, rows)
        self.assertIn("TENSION", build_memory_notes(hits, max_items=2, max_chars=700, cluster_mode=True))

    def test_related_evidence_changes_final_selection_score(self):
        hits = [
            {"memory": memory("supported", "SQLite storage migration plan.", "learnings", "2026-01-01", (0.7, 0.7)), "base_score": 0.8, "direct_similarity": 0.8},
            {"memory": memory("other", "Postgres storage migration plan.", "learnings", "2026-01-01", (0.7, -0.7)), "base_score": 0.8, "direct_similarity": 0.8},
            {"memory": memory("evidence", "SQLite approach approved during migration.", "rough", "2026-01-01", (0.7, 0.7)), "base_score": 0.75, "direct_similarity": 0.75},
        ]
        vectors = {hit["memory"]["id"]: np.asarray(hit["memory"]["embedding"], dtype=np.float32) for hit in hits}
        ranked = retriever._step2_1_rerank(hits, "Why choose SQLite storage migration?", vectors, 1.0, self.settings["step2"])
        by_id = {hit["memory"]["id"]: hit for hit in ranked}
        selection = self.settings["retrieval_selection"]

        self.assertGreater(by_id["supported"]["support_score"], by_id["other"]["support_score"])
        self.assertGreater(
            retriever._selection_rank_score(by_id["supported"], selection),
            retriever._selection_rank_score(by_id["other"], selection),
        )
        selected = retriever._select_diverse_hits(
            [by_id["other"], by_id["supported"]], vectors, 1, 1, selection
        )
        self.assertEqual(selected[0]["memory"]["id"], "supported")

    def test_temporal_head_favors_nearby_events(self):
        rows = [
            memory("target", "SQLite migration decision.", "learnings", "2026-01-01T00:00:00+00:00", (1.0, 0.0)),
            memory("near", "SQLite migration discussed.", "rough", "2026-01-01T00:05:00+00:00", (1.0, 0.0)),
            memory("far", "SQLite migration discussed earlier.", "rough", "2025-01-01T00:00:00+00:00", (1.0, 0.0)),
        ]
        hits = [{"memory": row, "base_score": 0.8} for row in rows]
        vectors = {row["id"]: np.asarray(row["embedding"], dtype=np.float32) for row in rows}
        matrix = retriever._build_attention_matrix(hits, {"sqlite", "migration"}, vectors, "balanced", self.settings["step2"])

        self.assertGreater(matrix[0, 1], matrix[0, 2])

    def test_cluster_brief_keeps_agent_groups_separate(self):
        hits = [
            {"source_agent": "alpha", "cluster_id": 0, "cluster_size": 2, "cluster_representative_text": "Alpha decision", "score": 0.8, "memory": memory("a", "Alpha decision", "learnings", "2026-01-01", (1.0, 0.0))},
            {"source_agent": "beta", "cluster_id": 0, "cluster_size": 2, "cluster_representative_text": "Beta decision", "score": 0.8, "memory": memory("b", "Beta decision", "learnings", "2026-01-01", (1.0, 0.0))},
        ]
        brief = build_memory_notes(hits, max_items=2, max_chars=700, cluster_mode=True)

        self.assertIn("alpha · INSIGHT 1", brief)
        self.assertIn("beta · INSIGHT 1", brief)


if __name__ == "__main__":
    unittest.main()
