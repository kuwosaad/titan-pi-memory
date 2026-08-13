import tempfile
import unittest
from pathlib import Path

from app.patterns.graph import build_pattern_graph, build_pattern_graph_data
from app.patterns.models import Pattern, PatternEvidence
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


def _memories() -> list[dict]:
    return [
        {**BASE_MEMORY, "id": "s1:1:0", "text": "Billing and Stripe work needs webhook checks.", "type": "decision", "session_id": "s1", "scene_id": "scene-1", "ts": "2026-06-01T00:00:00+00:00"},
        {**BASE_MEMORY, "id": "s2:1:0", "text": "Dashboard state can contradict entitlement state.", "type": "issue", "session_id": "s2", "scene_id": "scene-2", "ts": "2026-06-02T00:00:00+00:00"},
    ]


def _pattern(title: str, *, status: str, triggers: list[str], confidence: float = 0.8, canonical_key: str | None = None) -> Pattern:
    return Pattern(
        title=title,
        kind="workflow",
        scope="repo",
        status=status,  # type: ignore[arg-type]
        summary=f"Summary for {title}",
        recommended_behavior=f"Recommended behavior for {title}",
        trigger_terms=triggers,
        confidence=confidence,
        canonical_key=canonical_key,
    )


class PatternGraphTests(unittest.TestCase):
    def test_build_pattern_graph_data_uses_status_colors_sizes_and_relationship_edges(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memories())
            store = PatternStore(sqlite_file)

            billing = _pattern("Billing requires Stripe checks", status="accepted", triggers=["billing", "stripe"], confidence=0.9)
            dashboard = _pattern("Dashboard requires billing checks", status="candidate", triggers=["stripe", "dashboard"], confidence=0.5)
            contradiction = _pattern("Avoid dashboard-only billing state", status="accepted", triggers=["billing"], confidence=0.7)
            store.create_pattern(
                billing,
                [
                    PatternEvidence(pattern_id=billing.id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9),
                    PatternEvidence(pattern_id=billing.id, memory_id="s2:1:0", scene_id="scene-2", role="support", score=0.8),
                ],
                min_support_evidence=1,
            )
            store.create_pattern(
                dashboard,
                [PatternEvidence(pattern_id=dashboard.id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9)],
                min_support_evidence=1,
            )
            store.create_pattern(
                contradiction,
                [PatternEvidence(pattern_id=contradiction.id, memory_id="s2:1:0", scene_id="scene-2", role="contradict", score=0.9)],
                min_support_evidence=0,
            )

            data = build_pattern_graph_data(db_path=sqlite_file)

            self.assertEqual(data["count"], 3)
            self.assertEqual(data["status_counts"]["accepted"], 2)
            self.assertEqual(data["status_counts"]["candidate"], 1)
            nodes = {node["id"]: node for node in data["nodes"]}
            self.assertEqual(nodes[billing.id]["color"], "#4ade80")
            self.assertEqual(nodes[dashboard.id]["color"], "#f2c94c")
            self.assertGreater(nodes[billing.id]["val"], nodes[dashboard.id]["val"])
            edge_kinds = {link["kind"] for link in data["links"]}
            self.assertIn("shared_trigger", edge_kinds)
            self.assertIn("supports", edge_kinds)
            self.assertIn("contradicts", edge_kinds)

    def test_superseded_patterns_link_to_same_canonical_key(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memories())
            store = PatternStore(sqlite_file)
            old = _pattern("Old billing rule", status="superseded", triggers=["billing"], canonical_key="billing-rule")
            new = _pattern("New billing rule", status="accepted", triggers=["billing"], canonical_key="billing-rule")
            for pattern in [old, new]:
                store.create_pattern(
                    pattern,
                    [PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9)],
                    min_support_evidence=1,
                )

            data = build_pattern_graph_data(db_path=sqlite_file)

            supersedes = [link for link in data["links"] if link["kind"] == "supersedes"]
            self.assertEqual(len(supersedes), 1)
            self.assertEqual(supersedes[0]["source"], new.id)
            self.assertEqual(supersedes[0]["target"], old.id)

    def test_build_pattern_graph_html_embeds_graph_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memories())
            store = PatternStore(sqlite_file)
            pattern = _pattern("Billing requires Stripe checks", status="accepted", triggers=["billing", "stripe"])
            store.create_pattern(
                pattern,
                [PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9)],
                min_support_evidence=1,
            )

            with unittest.mock.patch("app.patterns.graph.resolve_pattern_graph_db_path", return_value=sqlite_file):
                html = build_pattern_graph()

            self.assertIn("Titan Pattern Graph", html)
            self.assertIn("const graphData", html)
            self.assertIn(pattern.id, html)
            self.assertIn("Node color = status", html)


if __name__ == "__main__":
    unittest.main()
