import unittest
import json
import math
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.builder import (
    DEFAULT_GRAPH_MEMORY_LIMIT,
    MAX_GRAPH_MEMORIES,
    NODE_PREVIEW_CHARS,
    GraphMemories,
    build_graph,
    graph_payload,
    load_memories,
    parse_memory_timestamp,
)
from app.graph.clusters import inspect_memory_clusters
from app.graph.corpus_analysis import snapshot_for_memories
from app.storage.memories import SqliteMemoryRepository


def loaded(*memories, total=None, warnings=()):
    return GraphMemories(list(memories), len(memories) if total is None else total, list(warnings))


def _lab(hex_colour):
    """CIELAB (D65) coordinates of an sRGB hex colour."""
    def linear(channel):
        channel /= 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(int(hex_colour[i:i + 2], 16)) for i in (1, 3, 5))
    x = (0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = (0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883

    def f(value):
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


class BuildGraphTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        # Graph reads resolve stores from the runtime's shared home; keep that in a temp dir.
        self.context = SimpleNamespace(shared_home=self.home, agent_name="codex")
        runtime = patch("app.graph.builder.get_runtime_context", return_value=self.context)
        runtime.start()
        self.addCleanup(runtime.stop)

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

    def _store(self, agent: str, memories: list[dict]) -> Path:
        path = self.home / "agents" / agent / "out" / "memories" / "memory_store.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        SqliteMemoryRepository(path).append_memories(memories)
        return path

    def _payload(self, html):
        start = html.index("    nodes: ", html.index("const graphData = {")) + len("    nodes: ")
        nodes, end = json.JSONDecoder().raw_decode(html[start:])
        links_start = html.index("    links: ", start + end) + len("    links: ")
        links, _ = json.JSONDecoder().raw_decode(html[links_start:])
        return nodes, links

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_preserves_owner_identity_and_handles_invalid_vectors(self, load, _config):
        load.return_value = loaded(
            self._memory("same", "alpha", "", source_agent="codex", embedding=[1, 0]),
            self._memory("same", "beta", "", source_agent="pi", embedding=[1, 0]),
            self._memory("missing", "missing", "", embedding=None),
            self._memory("bad", "bad", "", embedding=["bad", 0]),
            self._memory("nan", "nan", "", embedding=[float("nan"), 0]),
            self._memory("different", "different", "", embedding=[1, 0, 0]),
        )
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
    @patch("app.graph.builder.load_graph_memories")
    def test_text_is_safe_in_script_and_inspector(self, load, _config):
        text = '</script><script>alert(1)</script>'
        load.return_value = loaded(self._memory("x", text, ""))
        html = build_graph()
        nodes, _ = self._payload(html)
        self.assertEqual(nodes[0]["text"], text)
        self.assertNotIn(text, html)

    @patch("app.graph.builder.get_recent_memories", return_value=[])
    def test_load_memories_scopes_only_when_agent_requested(self, recent):
        load_memories(agent="pi")
        recent.assert_called_once_with(limit=DEFAULT_GRAPH_MEMORY_LIMIT, session_id=None, sources=["pi"])

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_build_graph_renders_html_with_graph_payload(self, mock_load, _mock_load_config):
        now = datetime.now(timezone.utc).isoformat()
        mock_load.return_value = loaded(
            self._memory("s1:1:0", "First memory", now),
            self._memory("s1:1:1", "Second memory", now, embedding=[0.9, 0.1, 0.0]),
        )

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
        mock_load.assert_called_once_with("s1", agent=None)

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_nodes_are_coloured_by_agent_with_reserved_colours_kept_apart(self, mock_load, _config):
        mock_load.return_value = loaded(
            self._memory("a", "alpha", "", source_agent="codex"),
            self._memory("b", "beta", "", source_agent="pi", embedding=[0.9, 0.1, 0.0]),
        )
        html = build_graph()
        nodes, _ = self._payload(html)
        # Community colours are gone; the page assigns --agent-N colours by agent.
        self.assertTrue(all("color" not in node for node in nodes))
        tokens = dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", html))
        palette = {tokens[f"--agent-{index}"].upper() for index in range(1, 9)}
        self.assertEqual(len(palette), 8)
        reserved = {tokens[name].upper() for name in ("--graph-selection", "--edge-correction", "--ink")}
        self.assertFalse(palette & reserved)
        # The first six agents (the most a lab scenario has) stay clearly apart:
        # CIE76 colour difference of at least 30 for every pair.
        first_six = [_lab(tokens[f"--agent-{index}"]) for index in range(1, 7)]
        for index, colour in enumerate(first_six):
            for other in first_six[index + 1:]:
                self.assertGreaterEqual(math.dist(colour, other), 30)

    @patch("app.graph.builder.load_graph_memories", return_value=loaded())
    def test_build_graph_empty_memories_keeps_explorer_view_available(self, _mock_load):
        html = build_graph(session_id="missing")
        self.assertIn('id="graphEmpty"', html)
        self.assertIn('aria-label="Search memories"', html)
        self.assertEqual(self._payload(html), ([], []))

    @patch("app.graph.builder.load_graph_memories", side_effect=sqlite3.OperationalError("database is locked"))
    def test_build_graph_reports_locked_database(self, _mock_load):
        html = build_graph(session_id="s1")

        self.assertIn("Memory database is busy", html)
        self.assertIn("another Titan process is holding a SQLite write lock", html)

    @patch("app.graph.builder.get_recent_memories", return_value=[])
    def test_load_memories_uses_recent_500_by_default(self, mock_get_recent_memories):
        memories = load_memories(session_id="s1")

        self.assertEqual(memories, [])
        mock_get_recent_memories.assert_called_once_with(limit=DEFAULT_GRAPH_MEMORY_LIMIT, session_id="s1")

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_build_graph_marks_recent_and_old_memories_for_sidebar_views(self, mock_load, _mock_load_config):
        now = datetime.now(timezone.utc)
        mock_load.return_value = loaded(
            self._memory("s1:recent", "Recent memory", now.isoformat()),
            self._memory("s1:old", "Older memory", (now - timedelta(days=3)).isoformat(), embedding=[0.9, 0.1, 0.0]),
        )

        html = build_graph(session_id="s1")

        nodes, _ = self._payload(html)
        self.assertEqual({node["raw_id"]: node["recent"] for node in nodes}, {"s1:recent": True, "s1:old": False})

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_build_graph_invalid_timestamps_still_render_in_all_view(self, mock_load, _mock_load_config):
        mock_load.return_value = loaded(
            self._memory("s1:bad-ts", "Bad timestamp memory", "not-a-timestamp"),
            self._memory("s1:no-ts", "Missing timestamp memory", "", embedding=[0.9, 0.1, 0.0]),
            self._memory("s1:overflow-ts", "Year one with an offset", "0001-01-01T00:00:00+01:00"),
        )

        html = build_graph(session_id="s1")

        nodes, _ = self._payload(html)
        self.assertEqual(
            {node["raw_id"]: node["recent"] for node in nodes},
            {"s1:bad-ts": False, "s1:no-ts": False, "s1:overflow-ts": False},
        )
        self.assertIn('data-view="all"', html)
        self.assertIn("memoryPreviewBody", html)

    def test_overflowing_timestamp_parses_as_undated(self):
        self.assertIsNone(parse_memory_timestamp("0001-01-01T00:00:00+01:00"))
        self.assertIsNotNone(parse_memory_timestamp("2026-01-01T00:00:00+01:00"))

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_nodes_carry_a_bounded_preview_not_the_full_body(self, mock_load, _config):
        body = "word " * 4000
        mock_load.return_value = loaded(self._memory("long", body, ""), self._memory("short", "short", ""))
        html = build_graph()
        nodes, _ = self._payload(html)
        by_id = {node["raw_id"]: node for node in nodes}
        self.assertLessEqual(len(by_id["long"]["text"]), NODE_PREVIEW_CHARS)
        self.assertTrue(by_id["long"]["text"].endswith("…"))
        self.assertTrue(by_id["long"]["text_truncated"])
        self.assertEqual(by_id["short"]["text"], "short")
        self.assertFalse(by_id["short"]["text_truncated"])
        self.assertNotIn(body, html)

    @patch("app.graph.builder.load_visual_config", return_value={})
    def test_same_text_from_two_agents_stays_visible_for_each_agent(self, _config):
        shared = "The release moved to Tuesday"
        self._store("codex", [self._memory("codex:1:0", shared, "2026-01-01T00:00:00+00:00")])
        self._store("claude", [self._memory("claude:1:0", shared, "2026-01-01T00:00:01+00:00")])

        payload = graph_payload()

        owners = {(node["source_agent"], node["raw_id"]) for node in payload["nodes"] if node["text"] == shared}
        self.assertEqual(owners, {("codex", "codex:1:0"), ("claude", "claude:1:0")})

    @patch("app.graph.builder.load_visual_config", return_value={})
    def test_memory_cap_reports_total_and_omitted_counts(self, _config):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._store("codex", [
            self._memory(f"m:{index}:0", f"memory {index}", (base + timedelta(minutes=index)).isoformat(), embedding=None)
            for index in range(MAX_GRAPH_MEMORIES + 10)
        ])

        payload = graph_payload()

        self.assertEqual(len(payload["nodes"]), MAX_GRAPH_MEMORIES)
        self.assertEqual(payload["nodes"][0]["raw_id"], f"m:{MAX_GRAPH_MEMORIES + 9}:0")
        meta = payload["meta"]
        self.assertEqual(
            (meta["memory_total"], meta["memory_shown"], meta["memory_omitted"]),
            (MAX_GRAPH_MEMORIES + 10, MAX_GRAPH_MEMORIES, 10),
        )
        html = build_graph()
        self.assertIn(f'"memory_total": {MAX_GRAPH_MEMORIES + 10}', html)

    @patch("app.graph.builder.MAX_GRAPH_LINKS", 2)
    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories")
    def test_link_cap_keeps_corrections_then_strongest_similarity(self, mock_load, _config):
        mock_load.return_value = loaded(
            self._memory("a", "a", "", source_agent="codex", embedding=[1.0, 0.0]),
            self._memory("b", "b", "", source_agent="codex", embedding=[0.99, 0.1]),
            self._memory("c", "c", "", source_agent="codex", embedding=[0.6, 0.8]),
        )
        correction = {"source": "codex::b", "target": "codex::a", "kind": "supersedes", "weight": 1}
        with patch("app.graph.builder.correction_edges", return_value=[correction]) as corrections:
            payload = graph_payload()

        self.assertEqual(sorted(link["kind"] for link in payload["links"]), ["similarity", "supersedes"])
        similarity = next(link for link in payload["links"] if link["kind"] == "similarity")
        self.assertEqual(
            {similarity["source"], similarity["target"]},
            {node["id"] for node in payload["nodes"] if node["raw_id"] in {"a", "b"}},
        )
        self.assertGreater(payload["meta"]["link_omitted"], 0)
        self.assertEqual(payload["meta"]["link_omitted"], payload["meta"]["link_total"] - 2)
        visible = dict(corrections.call_args.args[1])
        self.assertEqual(set(visible), {("codex", "a"), ("codex", "b"), ("codex", "c")})

    @patch("app.graph.builder.load_visual_config", return_value={})
    def test_malformed_legacy_rows_render_without_dropping_the_memory(self, _config):
        store = self.home / "agents" / "codex" / "out" / "memories"
        store.mkdir(parents=True)
        (store / "memories.json").write_text(
            '[{"id": "surrogate", "text": "bad \\ud800 text", "ts": "2026-01-01T00:00:00+00:00", "embedding": ["bad", 0]},'
            ' {"id": "nul", "text": "before \\u0000 after", "ts": "0001-01-01T00:00:00+01:00", "embedding": [1, 0]},'
            ' {"id": "nested", "text": "odd vector", "ts": 17, "embedding": [[1], [0]]}]',
            encoding="utf-8",
        )

        payload = graph_payload()
        html = build_graph()

        by_id = {node["raw_id"]: node for node in payload["nodes"]}
        self.assertEqual(set(by_id), {"surrogate", "nul", "nested"})
        self.assertEqual(by_id["surrogate"]["text"], "bad \ufffd text")
        self.assertEqual(by_id["nul"]["text"], "before \x00 after")
        self.assertIn("memoryPreviewBody", html)

    @patch("app.graph.builder.load_visual_config", return_value={})
    @patch("app.graph.builder.load_graph_memories", return_value=loaded())
    def test_scope_names_every_active_filter(self, _load, _config):
        self.assertEqual(graph_payload()["meta"]["scope"], "All agents")
        self.assertEqual(graph_payload(session_id="chain")["meta"]["scope"], "Session chain")
        self.assertEqual(graph_payload(agent="claude")["meta"]["scope"], "claude memories")
        self.assertEqual(
            graph_payload(session_id="chain", agent="claude")["meta"]["scope"],
            "claude memories · Session chain",
        )

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


class SimilarityEdgeTests(unittest.TestCase):
    def test_vectorized_edges_match_pairwise_top_k_semantics(self):
        from app.graph.similarity import build_similarity_edges, cosine_similarity

        vectors = [[1.0, 0.0], [0.99, 0.1], [0.8, 0.6], [0.7, 0.714], [0.0, 0.0], [-1.0, 0.0]]
        expected: dict[tuple[int, int], float] = {}
        for i, left in enumerate(vectors):
            scores = sorted(
                ((j, cosine_similarity(left, right)) for j, right in enumerate(vectors) if j != i),
                key=lambda item: item[1], reverse=True,
            )
            for j, score in scores[:2]:
                if score >= 0.35:
                    key = (min(i, j), max(i, j))
                    expected[key] = max(expected.get(key, 0.0), score)

        edges = build_similarity_edges(vectors, top_k=2, min_sim=0.35)

        self.assertEqual([(a, b) for a, b, _ in edges], list(expected))
        for (a, b, weight), expected_weight in zip(edges, expected.values()):
            self.assertAlmostEqual(weight, expected_weight)
        self.assertEqual(build_similarity_edges([[1.0, 0.0]]), [])


if __name__ == "__main__":
    unittest.main()
