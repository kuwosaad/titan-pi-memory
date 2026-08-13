import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.graph.cortex_analysis import analyze_memory_clusters


class CortexAnalysisTests(unittest.TestCase):
    def _memory(self, memory_id, text, embedding, ts=None):
        return {
            "id": memory_id,
            "text": text,
            "type": "decision",
            "stream": "rough",
            "session_id": "s1",
            "turn": 1,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "embedding": embedding,
        }

    def _clusters(self):
        return {
            "clusters": [
                {"cluster_id": 1, "memory_ids": ["a", "b"]},
                {"cluster_id": 2, "memory_ids": ["c", "d"]},
            ]
        }

    def _settings(self):
        return {
            "step2": {
                "sim_floor": 0.45,
                "contradiction_sim_threshold": 0.7,
                "contradiction_antonyms": [["add", "drop"]],
            }
        }

    @patch("app.graph.cortex_analysis.load_settings")
    @patch("app.graph.cortex_analysis.load_memories")
    @patch("app.graph.cortex_analysis.inspect_memory_clusters")
    def test_analyze_memory_clusters_surfaces_bridges_and_central_memories(
        self, mock_clusters, mock_memories, mock_settings
    ):
        mock_clusters.return_value = self._clusters()
        mock_settings.return_value = self._settings()
        now = datetime.now(timezone.utc)
        mock_memories.return_value = [
            self._memory("a", "add package tooling decision", [1.0, 0.0], now.isoformat()),
            self._memory("b", "package tooling readme", [0.95, 0.05], now.isoformat()),
            self._memory("c", "drop package tooling decision", [0.9, 0.1], (now + timedelta(days=1)).isoformat()),
            self._memory("d", "unrelated visual graph", [0.0, 1.0], now.isoformat()),
        ]

        result = analyze_memory_clusters([1, 2], session_id="s1")

        self.assertNotIn("error", result)
        self.assertEqual(result["memory_count"], 4)
        self.assertGreaterEqual(len(result["bridges"]), 1)
        self.assertGreaterEqual(len(result["central_memories"]), 1)
        self.assertGreaterEqual(len(result["subclusters"]), 1)
        self.assertIn("Analyzed 4 memories", result["summary"])

    @patch("app.graph.cortex_analysis.load_settings")
    @patch("app.graph.cortex_analysis.load_memories")
    @patch("app.graph.cortex_analysis.inspect_memory_clusters")
    def test_analyze_memory_clusters_flags_lexical_tension(
        self, mock_clusters, mock_memories, mock_settings
    ):
        mock_clusters.return_value = self._clusters()
        mock_settings.return_value = self._settings()
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = datetime(2026, 1, 2, tzinfo=timezone.utc)
        mock_memories.return_value = [
            self._memory("a", "add package tooling", [1.0, 0.0], old.isoformat()),
            self._memory("b", "support package tooling", [0.95, 0.05], old.isoformat()),
            self._memory("c", "drop package tooling", [0.99, 0.01], new.isoformat()),
            self._memory("d", "separate graph memory", [0.0, 1.0], new.isoformat()),
        ]

        result = analyze_memory_clusters("1,2")

        self.assertGreaterEqual(len(result["tensions"]), 1)
        self.assertIn("add", result["tensions"][0]["signal"])
        self.assertEqual(result["tensions"][0]["older_memory"]["id"], "a")
        self.assertEqual(result["tensions"][0]["newer_memory"]["id"], "c")

    @patch("app.graph.cortex_analysis.inspect_memory_clusters")
    def test_analyze_memory_clusters_reports_missing_cluster(self, mock_clusters):
        mock_clusters.return_value = {"clusters": [{"cluster_id": 1, "memory_ids": []}]}

        result = analyze_memory_clusters([99])

        self.assertIn("error", result)
        self.assertIn("99", result["error"])


if __name__ == "__main__":
    unittest.main()
