import unittest
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.graph.builder import DEFAULT_GRAPH_MEMORY_LIMIT, build_graph, load_memories
from app.graph.clusters import inspect_memory_clusters
from app.graph.corpus_analysis import snapshot_for_memories


class BuildGraphTests(unittest.TestCase):
    def _memory(self, memory_id: str, text: str, ts: str, **overrides):
        memory = {
            "id": memory_id,
            "text": text,
            "type": "fact",
            "stream": "rough",
            "session_id": "s1",
            "turn": 1,
            "ts": ts,
            "embedding": [1.0, 0.0, 0.0],
        }
        memory.update(overrides)
        return memory

    def _payload(self, html):
        start = html.index("    nodes: ", html.index("const graphData = {")) + len("    nodes: ")
        nodes, end = json.JSONDecoder().raw_decode(html[start:])
        links_start = html.index("    links: ", start + end) + len("    links: ")
        links, _ = json.JSONDecoder().raw_decode(html[links_start:])
        return nodes, links

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_preserves_owner_identity_and_handles_invalid_vectors(self, load, _config):
        load.return_value = [
            self._memory("same", "alpha", "", source_agent="codex", embedding=[1, 0]),
            self._memory("same", "beta", "", source_agent="pi", embedding=[1, 0]),
            self._memory("missing", "missing", "", embedding=None),
            self._memory("bad", "bad", "", embedding=["bad", 0]),
            self._memory("nan", "nan", "", embedding=[float("nan"), 0]),
            self._memory("different", "different", "", embedding=[1, 0, 0]),
        ]
        html = build_graph()
        nodes, links = self._payload(html)
        memories = [node for node in nodes if node["kind"] == "memory"]
        self.assertEqual(len(memories), 6)
        same = [node for node in memories if node["raw_id"] == "same"]
        self.assertEqual({node["source_agent"] for node in same}, {"pi", "codex"})
        self.assertEqual(len({node["id"] for node in same}), 2)
        similarities = [link for link in links if link["kind"] == "similarity"]
        self.assertEqual(len(similarities), 1)
        self.assertEqual({similarities[0]["source"], similarities[0]["target"]}, {node["id"] for node in same})

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_text_is_safe_in_script_and_inspector(self, load, _config):
        text = '</script><script>alert(1)</script>'
        load.return_value = [self._memory("x", text, "")]
        html = build_graph()
        nodes, _ = self._payload(html)
        self.assertEqual(nodes[0]["text"], text)
        self.assertNotIn(text, html)

    @patch("app.graph.builder.get_recent_memories", return_value=[])
    def test_load_memories_scopes_only_when_agent_requested(self, recent):
        load_memories(agent="pi")
        recent.assert_called_once_with(limit=DEFAULT_GRAPH_MEMORY_LIMIT, session_id=None, sources=["pi"])

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_build_graph_renders_html_with_graph_payload(self, mock_load_memories, _mock_load_config):
        now = datetime.now(timezone.utc).isoformat()
        mock_load_memories.return_value = [
            self._memory("s1:1:0", "First memory", now),
            self._memory("s1:1:1", "Second memory", now, embedding=[0.9, 0.1, 0.0]),
        ]

        html = build_graph(session_id="s1")

        self.assertIn('<canvas id="graph"', html)
        self.assertIn('<dialog class="memory-preview"', html)
        self.assertIn('aria-label="Search memories"', html)
        self.assertIn('aria-label="Show links"', html)
        self.assertNotIn("cdn.jsdelivr.net", html)
        nodes, links = self._payload(html)
        self.assertEqual([node["raw_id"] for node in nodes], ["s1:1:0", "s1:1:1"])
        self.assertEqual(nodes[0]["type"], "fact")
        self.assertEqual(nodes[0]["stream"], "rough")
        self.assertEqual(nodes[0]["session_id"], "s1")
        self.assertEqual(nodes[0]["turn"], 1)
        self.assertEqual([link["kind"] for link in links], ["similarity"])

    @patch("app.graph.builder.load_memories", return_value=[])
    def test_build_graph_empty_memories_keeps_explorer_view_available(self, _mock_load_memories):
        html = build_graph(session_id="missing")
        self.assertIn("No memories found", html)
        self.assertIn('aria-label="Search memories"', html)
        self.assertEqual(self._payload(html), ([], []))

    @patch("app.graph.builder.load_memories", side_effect=sqlite3.OperationalError("database is locked"))
    def test_build_graph_reports_locked_database(self, _mock_load_memories):
        html = build_graph(session_id="s1")

        self.assertIn("Memory database is busy", html)
        self.assertIn("another Titan process is holding a SQLite write lock", html)

    @patch("app.graph.builder.get_recent_memories", return_value=[])
    def test_load_memories_uses_recent_500_by_default(self, mock_get_recent_memories):
        memories = load_memories(session_id="s1")

        self.assertEqual(memories, [])
        mock_get_recent_memories.assert_called_once_with(limit=DEFAULT_GRAPH_MEMORY_LIMIT, session_id="s1")

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_build_graph_marks_recent_and_old_memories_for_sidebar_views(self, mock_load_memories, _mock_load_config):
        now = datetime.now(timezone.utc)
        mock_load_memories.return_value = [
            self._memory("s1:recent", "Recent memory", now.isoformat()),
            self._memory("s1:old", "Older memory", (now - timedelta(days=3)).isoformat(), embedding=[0.9, 0.1, 0.0]),
        ]

        html = build_graph(session_id="s1")

        nodes, _ = self._payload(html)
        self.assertEqual({node["raw_id"]: node["recent"] for node in nodes}, {"s1:recent": True, "s1:old": False})

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_build_graph_invalid_timestamps_still_render_in_all_view(self, mock_load_memories, _mock_load_config):
        mock_load_memories.return_value = [
            self._memory("s1:bad-ts", "Bad timestamp memory", "not-a-timestamp"),
            self._memory("s1:no-ts", "Missing timestamp memory", "", embedding=[0.9, 0.1, 0.0]),
        ]

        html = build_graph(session_id="s1")

        nodes, _ = self._payload(html)
        self.assertEqual({node["raw_id"]: node["recent"] for node in nodes}, {"s1:bad-ts": False, "s1:no-ts": False})
        self.assertIn('data-view="all"', html)
        self.assertIn("memoryPreviewBody", html)

    @patch("app.graph.clusters.load_visual_config", return_value={})
    def test_explicit_corpus_limit_preserves_source_count_and_missing_embeddings(self, _mock_load_config):
        memories = [
            self._memory("limit-old", "Old memory", "2026-06-01T00:00:00+00:00"),
            self._memory("limit-mid", "Middle memory", "2026-06-02T00:00:00+00:00", embedding=[0.9, 0.1, 0.0]),
            self._memory("limit-recent", "Recent memory", "2026-06-04T00:00:00+00:00", embedding=[0.8, 0.2, 0.0]),
            self._memory("limit-missing", "Latest missing embedding", "2026-06-05T00:00:00+00:00", embedding=None),
        ]
        corpus = snapshot_for_memories(memories)

        payload = inspect_memory_clusters(corpus=corpus, limit=2)

        self.assertEqual(payload["total_memory_count"], 4)
        self.assertEqual(payload["raw_memory_count"], 2)
        self.assertEqual(payload["memory_count"], 1)
        self.assertEqual(payload["skipped_missing_embeddings"], 1)
        self.assertEqual(payload["clusters"][0]["memory_ids"], ["limit-recent"])

    @patch("app.graph.clusters.load_visual_config", return_value={})
    def test_cluster_representative_equal_degree_uses_instant_order(self, _mock_load_config):
        template = self._memory("representative-earlier", "Same topic", "2026-06-01T01:00:00+05:30")
        memories = [
            template,
            self._memory(
                "representative-later",
                "Same topic",
                "2026-06-01T00:00:00+00:00",
                embedding=[0.9, 0.1, 0.0],
            ),
        ]

        payload = inspect_memory_clusters(corpus=snapshot_for_memories(memories), limit=0, detail_limit=1)

        self.assertEqual(payload["clusters"][0]["examples"][0]["id"], "representative-earlier")


if __name__ == "__main__":
    unittest.main()
