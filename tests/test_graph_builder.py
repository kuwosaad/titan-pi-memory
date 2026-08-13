import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.graph.builder import DEFAULT_GRAPH_MEMORY_LIMIT, build_graph, load_memories


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

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_build_graph_renders_html_with_graph_payload(self, mock_load_memories, _mock_load_config):
        now = datetime.now(timezone.utc).isoformat()
        mock_load_memories.return_value = [
            self._memory("s1:1:0", "First memory", now),
            self._memory("s1:1:1", "Second memory", now, embedding=[0.9, 0.1, 0.0]),
        ]

        html = build_graph(session_id="s1")

        self.assertIn("3d-force-graph", html)
        self.assertIn('id="graph"', html)
        self.assertIn("const graphData", html)
        self.assertIn("background-image:", html)
        self.assertIn("linear-gradient(90deg", html)
        self.assertIn('class="hud"', html)
        self.assertIn("click a node to inspect memory details", html)
        self.assertIn("s1:1:0", html)
        self.assertIn("s1:1:1", html)
        # New dashboard elements
        self.assertIn("fonts.googleapis.com", html)
        self.assertIn("dashboard-header", html)
        self.assertIn("dashboard-brand", html)
        self.assertIn("stat-chip", html)
        self.assertIn("sidebarSearch", html)
        self.assertIn('"type": "fact"', html)
        self.assertIn('"stream": "rough"', html)
        self.assertIn('"session_id": "s1"', html)
        self.assertIn('"turn": 1', html)
        self.assertIn("memoryPreview", html)
        self.assertIn("recent 24h", html)
        self.assertIn("show all memories", html)
        self.assertIn("activeView = 'recent'", html)
        self.assertIn(".enableNodeDrag(true)", html)
        self.assertNotIn("graph2ScreenCoords", html)
        self.assertIn("Graph.onNodeDragEnd", html)
        self.assertIn("graphContainer.addEventListener('pointerup'", html)

    @patch("app.graph.builder.load_memories", return_value=[])
    def test_build_graph_empty_memories_returns_fallback_html(self, _mock_load_memories):
        html = build_graph(session_id="missing")
        self.assertEqual(html, "<html><body><h1>No memories found</h1></body></html>")

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

        self.assertIn('data-id="s1:recent"', html)
        self.assertIn('data-id="s1:old"', html)
        self.assertIn('data-recent="true"', html)
        self.assertIn('data-recent="false"', html)
        self.assertIn("2 total", html)
        self.assertIn("1 recent", html)

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_memories")
    def test_build_graph_invalid_timestamps_still_render_in_all_view(self, mock_load_memories, _mock_load_config):
        mock_load_memories.return_value = [
            self._memory("s1:bad-ts", "Bad timestamp memory", "not-a-timestamp"),
            self._memory("s1:no-ts", "Missing timestamp memory", "", embedding=[0.9, 0.1, 0.0]),
        ]

        html = build_graph(session_id="s1")

        self.assertIn('data-id="s1:bad-ts"', html)
        self.assertIn('data-id="s1:no-ts"', html)
        self.assertIn('data-recent="false"', html)
        self.assertIn("show all memories", html)
        self.assertIn("memoryPreviewBody", html)


if __name__ == "__main__":
    unittest.main()
