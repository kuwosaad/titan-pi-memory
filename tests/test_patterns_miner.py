import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.patterns.miner import build_evidence_packet
from app.patterns.models import Pattern, PatternEvidence
from app.patterns.processing import PatternProcessingLedger
from app.patterns.store import PatternStore
from app.storage.memories import SqliteMemoryRepository


PROCESSOR_VERSION = "pattern-miner-v1"
CONFIG_HASH = "test-config"


def _records() -> list[dict]:
    base = {
        "stream": "learnings",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
    }
    rows = [
        {
            **base,
            "id": "old1:1:0",
            "text": "Billing webhook changes require entitlement checks and integration tests.",
            "type": "workflow",
            "embedding": [1.0, 0.0, 0.0],
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "old1",
            "scene_id": "scene-old-1",
        },
        {
            **base,
            "id": "old2:1:0",
            "text": "Stripe webhook bugs happened when dashboard subscription state drifted.",
            "type": "issue",
            "embedding": [0.95, 0.05, 0.0],
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "old2",
            "scene_id": "scene-old-2",
        },
        {
            **base,
            "id": "old3:1:0",
            "text": "Package export changes require TypeScript validation.",
            "type": "workflow",
            "embedding": [0.0, 1.0, 0.0],
            "ts": "2026-06-03T00:00:00+00:00",
            "session_id": "old3",
            "scene_id": "scene-old-3",
        },
        {
            **base,
            "id": "new1:1:0",
            "text": "Use webhook entitlement checks before editing billing state.",
            "type": "workflow",
            "embedding": [0.98, 0.02, 0.0],
            "ts": "2026-06-04T00:00:00+00:00",
            "session_id": "new1",
            "scene_id": "scene-new-1",
        },
        {
            **base,
            "id": "new2:1:0",
            "text": "Billing dashboard state should match Stripe webhook subscription outcomes.",
            "type": "issue",
            "embedding": [0.96, 0.04, 0.0],
            "ts": "2026-06-05T00:00:00+00:00",
            "session_id": "new2",
            "scene_id": "scene-new-2",
        },
        {
            **base,
            "id": "new3:1:0",
            "text": "Avoid webhook edits without integration tests for billing entitlement flows.",
            "type": "workflow",
            "embedding": [0.94, 0.06, 0.0],
            "ts": "2026-06-06T00:00:00+00:00",
            "session_id": "new3",
            "scene_id": "scene-new-3",
        },
    ]
    return rows


def _adaptive_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
    }
    return [
        {
            **base,
            "id": "low:1:0",
            "text": "Documented neutral notes about dashboard spacing and labels.",
            "type": "workflow",
            "embedding": [0.0, 1.0, 0.0],
            "ts": "2026-06-01T00:00:00+00:00",
            "turn": 1,
            "session_id": "s-low",
            "scene_id": "scene-low",
        },
        {
            **base,
            "id": "high:1:0",
            "text": "Bug fixed by validating the CLI command before reporting completion.",
            "type": "issue",
            "embedding": [1.0, 0.0, 0.0],
            "ts": "2026-06-02T00:00:00+00:00",
            "turn": 1,
            "session_id": "s-high",
            "scene_id": "scene-high-1",
        },
        {
            **base,
            "id": "high:2:0",
            "text": "Always validate CLI command output after tooling edits.",
            "type": "workflow",
            "embedding": [0.95, 0.05, 0.0],
            "ts": "2026-06-03T00:00:00+00:00",
            "turn": 2,
            "session_id": "s-high",
            "scene_id": "scene-high-2",
        },
    ]


def _high_signal_order_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "embedding": [1.0, 0.0, 0.0],
        "turn": 1,
    }
    return [
        {
            **base,
            "id": "older-related:1:0",
            "text": "CLI command output reporting notes for validation work.",
            "type": "workflow",
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "s-related",
            "scene_id": "scene-related",
        },
        {
            **base,
            "id": "later-bug:1:0",
            "text": "Bug fixed by validating the CLI command output before reporting completion.",
            "type": "issue",
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "s-bug",
            "scene_id": "scene-bug",
        },
        {
            **base,
            "id": "later-rule:1:0",
            "text": "Always validate CLI command output after tooling edits.",
            "type": "workflow",
            "ts": "2026-06-03T00:00:00+00:00",
            "session_id": "s-rule",
            "scene_id": "scene-rule",
        },
    ]


def _scene_records() -> list[dict]:
    base = {
        "stream": "rough",
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "embedding": [1.0, 0.0, 0.0],
        "session_id": "scene-session",
        "scene_id": "scene-episode",
        "ts": "2026-06-01T00:00:00+00:00",
        "type": "workflow",
    }
    return [
        {**base, "id": "scene:2:0", "turn": 2, "text": "Assistant proposed a change before checking the actual files."},
        {**base, "id": "scene:1:0", "turn": 1, "text": "User asked for implementation strategy."},
        {**base, "id": "scene:3:0", "turn": 3, "text": "User corrected the plan and asked to inspect repo files first."},
    ]


def _semantic_cluster_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "type": "workflow",
    }
    return [
        {
            **base,
            "id": "cluster-old-1:1:0",
            "text": "Billing webhook memory about entitlement state and dashboard subscription sync.",
            "embedding": [1.0, 0.0, 0.0],
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "cluster-old-1",
            "scene_id": "cluster-scene-1",
        },
        {
            **base,
            "id": "cluster-old-2:1:0",
            "text": "Stripe webhook memory about subscription sync and integration test coverage.",
            "embedding": [0.98, 0.02, 0.0],
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "cluster-old-2",
            "scene_id": "cluster-scene-2",
        },
        {
            **base,
            "id": "cluster-new-1:1:0",
            "text": "Billing entitlement changes need webhook and dashboard subscription checks.",
            "embedding": [0.97, 0.03, 0.0],
            "ts": "2026-06-03T00:00:00+00:00",
            "session_id": "cluster-new-1",
            "scene_id": "cluster-scene-3",
        },
        {
            **base,
            "id": "cluster-new-2:1:0",
            "text": "Subscription sync edits need billing integration test coverage.",
            "embedding": [0.96, 0.04, 0.0],
            "ts": "2026-06-04T00:00:00+00:00",
            "session_id": "cluster-new-2",
            "scene_id": "cluster-scene-4",
        },
        {
            **base,
            "id": "other:1:0",
            "text": "Package export changes require TypeScript validation.",
            "embedding": [0.0, 1.0, 0.0],
            "ts": "2026-06-05T00:00:00+00:00",
            "session_id": "other",
            "scene_id": "other-scene",
        },
    ]


def _entity_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "type": "workflow",
        "embedding": [1.0, 0.0, 0.0],
    }
    return [
        {
            **base,
            "id": "entity-old:1:0",
            "text": "Billing portal webhook evidence needs entitlement validation.",
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "entity-old",
            "scene_id": "entity-scene-old",
        },
        {
            **base,
            "id": "entity-new-1:1:0",
            "text": "Billing webhook updates should verify portal entitlement state.",
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "entity-new-1",
            "scene_id": "entity-scene-1",
        },
        {
            **base,
            "id": "entity-new-2:1:0",
            "text": "Billing dashboard webhook flows need integration coverage.",
            "ts": "2026-06-03T00:00:00+00:00",
            "session_id": "entity-new-2",
            "scene_id": "entity-scene-2",
        },
    ]


def _bridge_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "type": "workflow",
    }
    return [
        {
            **base,
            "id": "bridge-old-1:1:0",
            "text": "Package export validation history for CLI tooling.",
            "embedding": [1.0, 0.0],
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "bridge-old-1",
            "scene_id": "bridge-scene-old-1",
        },
        {
            **base,
            "id": "bridge-old-2:1:0",
            "text": "Graph analysis history for retrieval tooling.",
            "embedding": [0.0, 1.0],
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "bridge-old-2",
            "scene_id": "bridge-scene-old-2",
        },
        {
            **base,
            "id": "bridge-new-1:1:0",
            "text": "CLI tooling export validation should connect package and graph work.",
            "embedding": [0.9, 0.1],
            "ts": "2026-06-03T00:00:00+00:00",
            "session_id": "bridge-new-1",
            "scene_id": "bridge-scene-new-1",
        },
        {
            **base,
            "id": "bridge-new-2:1:0",
            "text": "Graph tooling validation should connect retrieval and package work.",
            "embedding": [0.1, 0.9],
            "ts": "2026-06-04T00:00:00+00:00",
            "session_id": "bridge-new-2",
            "scene_id": "bridge-scene-new-2",
        },
    ]


def _contradiction_records() -> list[dict]:
    base = {
        "stream": "learnings",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "source_event_ids": [],
        "source_type": "mixed",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "type": "decision",
    }
    return [
        {
            **base,
            "id": "tension-old:1:0",
            "text": "Add package tooling validation before release.",
            "embedding": [1.0, 0.0],
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "tension-old",
            "scene_id": "tension-scene-old",
        },
        {
            **base,
            "id": "tension-new:1:0",
            "text": "Remove package tooling validation from release flow.",
            "embedding": [0.98, 0.02],
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "tension-new",
            "scene_id": "tension-scene-new",
        },
    ]


class PatternMinerTests(unittest.TestCase):
    def test_packet_reports_unprocessed_remaining_for_processor_and_window(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records())

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=0,
                from_ts="2026-06-02T00:00:00+00:00",
                to_ts="2026-06-06T23:59:59+00:00",
                snapshot_cutoff="2026-06-06T23:59:59+00:00",
                mode="chronological",
            )

            self.assertEqual(packet["unprocessed_memory_ids"], ["old2:1:0", "old3:1:0"])
            self.assertEqual(packet["unprocessed_remaining"], 3)
    def test_date_bounds_filter_unprocessed_seeds_before_limit_but_keep_older_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records())

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=1,
                context_limit=3,
                from_ts="2026-06-04T00:00:00+00:00",
                to_ts="2026-06-05T23:59:59+00:00",
                snapshot_cutoff="2026-06-05T23:59:59+00:00",
                mode="chronological",
            )

            self.assertEqual(packet["unprocessed_memory_ids"], ["new1:1:0"])
            self.assertIn("old1:1:0", packet["related_old_memory_ids"])

    def test_serialized_evidence_preserves_memory_provenance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records()[:1])

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=1,
                context_limit=0,
                mode="chronological",
            )

            evidence = packet["memories"]["unprocessed"][0]
            self.assertEqual(evidence["provenance"], {"user": "u", "assistant": "a"})
            self.assertEqual(evidence["source_event_ids"], [])
            self.assertEqual(evidence["source_type"], "mixed")
            self.assertEqual(evidence["source_reliability"], 0.9)
            self.assertEqual(evidence["verification_status"], "unverified")

    def test_build_evidence_packet_from_unprocessed_memories_with_related_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["old1:1:0", "old2:1:0", "old3:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=3,
                context_limit=3,
            )

            self.assertEqual(packet["unprocessed_memory_ids"], ["new2:1:0", "new3:1:0", "new1:1:0"])
            self.assertEqual(packet["packet_type"], "high_signal")
            self.assertEqual(packet["seed_memory_ids"], packet["unprocessed_memory_ids"])
            self.assertIn("packet_id", packet)
            self.assertIn("selection_reasons", packet)
            self.assertIn("questions_for_agent", packet)
            self.assertIn("old1:1:0", packet["related_old_memory_ids"])
            self.assertGreaterEqual(len(packet["cluster_summaries"]), 1)
            self.assertGreaterEqual(len(packet["central_memories"]), 1)
            self.assertGreaterEqual(len(packet["bridge_memories"]), 1)
            self.assertGreaterEqual(len(packet["tensions"]), 1)
            self.assertIn("webhook", packet["suggested_trigger_terms"])
            self.assertEqual(packet["suggested_kind"], "failure")
            self.assertGreaterEqual(packet["confidence_hints"]["scene_count"], 3)

    def test_adaptive_packet_prioritizes_high_signal_memories(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_adaptive_records())

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=0,
            )

            self.assertEqual(packet["packet_type"], "high_signal")
            self.assertEqual(packet["unprocessed_memory_ids"], ["high:1:0", "high:2:0"])
            self.assertNotIn("low:1:0", packet["unprocessed_memory_ids"])

    def test_high_signal_packet_orders_by_signal_score_not_chronology(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_high_signal_order_records())

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=0,
                packet_type="high_signal",
            )

            self.assertEqual(packet["packet_type"], "high_signal")
            self.assertEqual(packet["unprocessed_memory_ids"], ["later-bug:1:0", "later-rule:1:0"])
            self.assertNotIn("older-related:1:0", packet["unprocessed_memory_ids"])

    def test_scene_packet_preserves_causal_order(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_scene_records())

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=3,
                context_limit=0,
                packet_type="scene_episode",
            )

            self.assertEqual(packet["packet_type"], "scene_episode")
            self.assertEqual(packet["unprocessed_memory_ids"], ["scene:1:0", "scene:2:0", "scene:3:0"])
            self.assertEqual([item["id"] for item in packet["temporal_context"]], ["scene:1:0", "scene:2:0", "scene:3:0"])

    def test_cluster_packet_uses_related_cluster_memories(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            records = _semantic_cluster_records()
            SqliteMemoryRepository(sqlite_file).append_memories(records)
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["cluster-old-1:1:0", "cluster-old-2:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )
            cluster_payload = {
                "clusters": [
                    {
                        "cluster_id": 7,
                        "topic": "billing / webhook / subscription",
                        "keywords": ["billing", "webhook", "subscription"],
                        "memory_count": 4,
                        "connection_count": 3,
                        "avg_similarity": 0.91,
                        "memory_ids": [
                            "cluster-old-1:1:0",
                            "cluster-old-2:1:0",
                            "cluster-new-1:1:0",
                            "cluster-new-2:1:0",
                        ],
                    }
                ]
            }

            with patch("app.patterns.miner.inspect_memory_clusters", return_value=cluster_payload):
                packet = build_evidence_packet(
                    processor_version=PROCESSOR_VERSION,
                    processor_config_hash=CONFIG_HASH,
                    db_path=sqlite_file,
                    batch_size=2,
                    context_limit=2,
                    packet_type="semantic_cluster",
                )

            self.assertEqual(packet["packet_type"], "semantic_cluster")
            self.assertEqual(packet["unprocessed_memory_ids"], ["cluster-new-1:1:0", "cluster-new-2:1:0"])
            self.assertEqual(packet["context_memory_ids"], ["cluster-old-1:1:0", "cluster-old-2:1:0"])
            self.assertEqual(packet["related_old_memory_ids"], ["cluster-old-1:1:0", "cluster-old-2:1:0"])
            self.assertEqual(packet["graph_context"]["source_cluster"]["cluster_id"], 7)
            self.assertEqual([item["id"] for item in packet["temporal_context"]], ["cluster-old-1:1:0", "cluster-old-2:1:0", "cluster-new-1:1:0", "cluster-new-2:1:0"])

    def test_entity_packet_groups_repeated_entity_with_processed_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_entity_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["entity-old:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=1,
                packet_type="entity",
            )

            self.assertEqual(packet["packet_type"], "entity")
            self.assertEqual(packet["unprocessed_memory_ids"], ["entity-new-1:1:0", "entity-new-2:1:0"])
            self.assertEqual(packet["context_memory_ids"], ["entity-old:1:0"])
            self.assertEqual(packet["graph_context"]["source_entity"]["term"], "billing")

    def test_bridge_packet_uses_cortex_bridge_analysis(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_bridge_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["bridge-old-1:1:0", "bridge-old-2:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )
            cluster_payload = {
                "clusters": [
                    {"cluster_id": 1, "connection_count": 4, "avg_similarity": 0.8, "memory_ids": ["bridge-old-1:1:0", "bridge-new-1:1:0"]},
                    {"cluster_id": 2, "connection_count": 3, "avg_similarity": 0.75, "memory_ids": ["bridge-old-2:1:0", "bridge-new-2:1:0"]},
                ]
            }
            analysis_payload = {
                "cluster_ids": [1, 2],
                "memory_count": 4,
                "edge_count": 2,
                "summary": "Analyzed 4 memories from cluster(s) 1, 2.",
                "bridges": [
                    {
                        "source_cluster_id": 1,
                        "target_cluster_id": 2,
                        "similarity": 0.88,
                        "bridge_score": 0.72,
                        "source_memory": {"id": "bridge-new-1:1:0"},
                        "target_memory": {"id": "bridge-new-2:1:0"},
                    },
                    {
                        "source_cluster_id": 1,
                        "target_cluster_id": 2,
                        "similarity": 0.81,
                        "bridge_score": 0.4,
                        "source_memory": {"id": "bridge-old-1:1:0"},
                        "target_memory": {"id": "bridge-new-2:1:0"},
                    },
                ],
                "bridge_memories": [{"id": "bridge-old-1:1:0", "score": 0.5}],
                "central_memories": [{"id": "bridge-old-2:1:0", "score": 0.4}],
            }

            with patch("app.patterns.miner.inspect_memory_clusters", return_value=cluster_payload), patch(
                "app.patterns.miner.analyze_memory_clusters",
                return_value=analysis_payload,
            ) as mock_analyze:
                packet = build_evidence_packet(
                    processor_version=PROCESSOR_VERSION,
                    processor_config_hash=CONFIG_HASH,
                    db_path=sqlite_file,
                    batch_size=2,
                    context_limit=2,
                    packet_type="bridge",
                )

            mock_analyze.assert_called_once_with([1, 2], session_id=None, limit=0, detail_limit=25)
            self.assertEqual(packet["packet_type"], "bridge")
            self.assertEqual(packet["unprocessed_memory_ids"], ["bridge-new-1:1:0", "bridge-new-2:1:0"])
            self.assertEqual(packet["context_memory_ids"], ["bridge-old-1:1:0", "bridge-old-2:1:0"])
            self.assertEqual(packet["graph_context"]["source_analysis"]["cluster_ids"], [1, 2])

    def test_contradiction_packet_uses_cortex_tensions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_contradiction_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["tension-old:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )
            cluster_payload = {
                "clusters": [
                    {"cluster_id": 1, "connection_count": 2, "avg_similarity": 0.84, "memory_ids": ["tension-old:1:0", "tension-new:1:0"]},
                ]
            }
            analysis_payload = {
                "cluster_ids": [1],
                "memory_count": 2,
                "edge_count": 1,
                "summary": "Found one possible tension.",
                "bridges": [],
                "tensions": [
                    {
                        "similarity": 0.91,
                        "signal": "possible shift around 'add' vs 'remove'",
                        "shared_terms": ["package", "tooling", "validation"],
                        "older_memory": {"id": "tension-old:1:0"},
                        "newer_memory": {"id": "tension-new:1:0"},
                    }
                ],
            }

            with patch("app.patterns.miner.inspect_memory_clusters", return_value=cluster_payload), patch(
                "app.patterns.miner.analyze_memory_clusters",
                return_value=analysis_payload,
            ) as mock_analyze:
                packet = build_evidence_packet(
                    processor_version=PROCESSOR_VERSION,
                    processor_config_hash=CONFIG_HASH,
                    db_path=sqlite_file,
                    batch_size=2,
                    context_limit=2,
                    packet_type="contradiction",
                )

            mock_analyze.assert_called_once_with([1], session_id=None, limit=0, detail_limit=25)
            self.assertEqual(packet["packet_type"], "contradiction")
            self.assertEqual(packet["unprocessed_memory_ids"], ["tension-new:1:0"])
            self.assertEqual(packet["context_memory_ids"], ["tension-old:1:0"])
            self.assertEqual(packet["graph_context"]["source_tensions"][0]["newer_memory_id"], "tension-new:1:0")

    def test_adaptive_packet_can_auto_select_contradiction(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_contradiction_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["tension-old:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )
            cluster_payload = {
                "clusters": [
                    {"cluster_id": 1, "connection_count": 2, "avg_similarity": 0.82, "memory_ids": ["tension-old:1:0"]},
                    {"cluster_id": 2, "connection_count": 2, "avg_similarity": 0.84, "memory_ids": ["tension-new:1:0"]},
                ]
            }
            analysis_payload = {
                "cluster_ids": [2, 1],
                "memory_count": 2,
                "edge_count": 1,
                "summary": "Found one possible tension.",
                "bridges": [],
                "tensions": [
                    {
                        "similarity": 0.91,
                        "signal": "possible shift around 'add' vs 'remove'",
                        "shared_terms": ["package", "tooling", "validation"],
                        "older_memory": {"id": "tension-old:1:0"},
                        "newer_memory": {"id": "tension-new:1:0"},
                    }
                ],
            }

            with patch("app.patterns.miner.inspect_memory_clusters", return_value=cluster_payload), patch(
                "app.patterns.miner.analyze_memory_clusters",
                return_value=analysis_payload,
            ) as mock_analyze:
                packet = build_evidence_packet(
                    processor_version=PROCESSOR_VERSION,
                    processor_config_hash=CONFIG_HASH,
                    db_path=sqlite_file,
                    batch_size=2,
                    context_limit=2,
                )

            mock_analyze.assert_called_once_with([2, 1], session_id=None, limit=0, detail_limit=25)
            self.assertEqual(packet["packet_type"], "contradiction")
            self.assertEqual(packet["unprocessed_memory_ids"], ["tension-new:1:0"])

    def test_contradiction_packet_keeps_unselected_endpoint_as_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_contradiction_records())
            cluster_payload = {
                "clusters": [
                    {"cluster_id": 1, "connection_count": 2, "avg_similarity": 0.84, "memory_ids": ["tension-old:1:0", "tension-new:1:0"]},
                ]
            }
            analysis_payload = {
                "cluster_ids": [1],
                "memory_count": 2,
                "edge_count": 1,
                "summary": "Found one possible tension.",
                "bridges": [],
                "tensions": [
                    {
                        "similarity": 0.91,
                        "signal": "possible shift around 'add' vs 'remove'",
                        "shared_terms": ["package", "tooling", "validation"],
                        "older_memory": {"id": "tension-old:1:0"},
                        "newer_memory": {"id": "tension-new:1:0"},
                    }
                ],
            }

            with patch("app.patterns.miner.inspect_memory_clusters", return_value=cluster_payload), patch(
                "app.patterns.miner.analyze_memory_clusters",
                return_value=analysis_payload,
            ):
                packet = build_evidence_packet(
                    processor_version=PROCESSOR_VERSION,
                    processor_config_hash=CONFIG_HASH,
                    db_path=sqlite_file,
                    batch_size=1,
                    context_limit=1,
                    packet_type="contradiction",
                )

            self.assertEqual(packet["packet_type"], "contradiction")
            self.assertEqual(packet["unprocessed_memory_ids"], ["tension-old:1:0"])
            self.assertEqual(packet["context_memory_ids"], ["tension-new:1:0"])

    def test_contradiction_packet_includes_existing_pattern_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_contradiction_records())
            PatternStore(sqlite_file).create_pattern(
                Pattern(
                    id="pattern:validation-add",
                    title="Add package validation before release",
                    kind="workflow",
                    scope="repo",
                    status="accepted",
                    summary="Package tooling releases should add validation before shipping.",
                    recommended_behavior="Add package tooling validation before release.",
                    trigger_terms=["package", "tooling", "validation", "release"],
                    confidence=0.8,
                    actionability=0.9,
                    retrieval_value=0.7,
                ),
                [
                    PatternEvidence(
                        pattern_id="pattern:validation-add",
                        memory_id="tension-old:1:0",
                        scene_id="tension-scene-old",
                        role="support",
                        score=0.9,
                    )
                ],
            )
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                ["tension-old:1:0"],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )
            cluster_payload = {
                "clusters": [
                    {"cluster_id": 1, "connection_count": 2, "avg_similarity": 0.84, "memory_ids": ["tension-old:1:0", "tension-new:1:0"]},
                ]
            }
            analysis_payload = {
                "cluster_ids": [1],
                "memory_count": 2,
                "edge_count": 1,
                "summary": "Found one possible tension.",
                "bridges": [],
                "tensions": [
                    {
                        "similarity": 0.91,
                        "signal": "possible shift around 'add' vs 'remove'",
                        "shared_terms": ["package", "tooling", "validation"],
                        "older_memory": {"id": "tension-old:1:0"},
                        "newer_memory": {"id": "tension-new:1:0"},
                    }
                ],
            }

            with patch("app.patterns.miner.inspect_memory_clusters", return_value=cluster_payload), patch(
                "app.patterns.miner.analyze_memory_clusters",
                return_value=analysis_payload,
            ):
                packet = build_evidence_packet(
                    processor_version=PROCESSOR_VERSION,
                    processor_config_hash=CONFIG_HASH,
                    db_path=sqlite_file,
                    batch_size=2,
                    context_limit=2,
                    packet_type="contradiction",
                )

            self.assertEqual(packet["packet_type"], "contradiction")
            self.assertEqual(packet["pattern_context"][0]["pattern_id"], "pattern:validation-add")
            self.assertEqual(packet["pattern_context"][0]["status"], "accepted")
            self.assertEqual(packet["graph_context"]["source_patterns"][0]["pattern_id"], "pattern:validation-add")

    def test_old_chronological_packet_still_works_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records())

            packet = build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=0,
                mode="chronological",
            )

            self.assertEqual(packet["packet_type"], "chronological_fallback")
            self.assertEqual(packet["unprocessed_memory_ids"], ["old1:1:0", "old2:1:0"])

    def test_evidence_packet_does_not_mark_memories_processed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records())
            ledger = PatternProcessingLedger(sqlite_file)
            before = ledger.status(processor_version=PROCESSOR_VERSION, processor_config_hash=CONFIG_HASH)

            build_evidence_packet(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=2,
            )

            after = ledger.status(processor_version=PROCESSOR_VERSION, processor_config_hash=CONFIG_HASH)
            self.assertEqual(before.processed_current, after.processed_current)
            self.assertEqual(before.unprocessed, after.unprocessed)

    def test_new_processor_version_can_reprocess_old_memories(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                mode="backfill",
            )
            ledger.mark_processed(
                [record["id"] for record in _records()],
                processor_version=PROCESSOR_VERSION,
                processor_config_hash=CONFIG_HASH,
                run_id=run.id,
            )

            packet = build_evidence_packet(
                processor_version="pattern-miner-v2",
                processor_config_hash=CONFIG_HASH,
                db_path=sqlite_file,
                batch_size=2,
                context_limit=0,
                mode="chronological",
            )

            self.assertEqual(packet["unprocessed_memory_ids"], ["old1:1:0", "old2:1:0"])


if __name__ == "__main__":
    unittest.main()
