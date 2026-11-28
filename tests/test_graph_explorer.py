import base64
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app.graph.explorer import (
    MAX_DETAIL_TEXT,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_NODES,
    ExplorerError,
    ExplorerFilters,
    WarningCollector,
    correction_edges,
    count_records,
    detail,
    handle_request,
    newest_records,
    resolve_agent,
)


ROOT = Path(__file__).resolve().parents[1]


class ExplorerStoreMixin:
    def make_store(self, home: Path, agent: str, rows: list[dict], *, json_only: bool = False) -> Path:
        memory_dir = home / "agents" / agent / "out" / "memories"
        memory_dir.mkdir(parents=True, exist_ok=True)
        if json_only:
            path = memory_dir / "memories.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            return path

        path = memory_dir / "memory_store.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                scene_id TEXT,
                idx INTEGER NOT NULL,
                text TEXT NOT NULL,
                type TEXT,
                stream TEXT NOT NULL,
                ts TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_reliability REAL NOT NULL,
                verification_status TEXT NOT NULL,
                fallback_generated INTEGER NOT NULL,
                source_event_ids_json TEXT NOT NULL,
                provenance_user TEXT NOT NULL,
                provenance_assistant TEXT NOT NULL,
                speaker_focus TEXT,
                memory_kind TEXT,
                embedding_blob BLOB,
                embedding_dim INTEGER,
                embedding_dtype TEXT,
                h REAL NOT NULL DEFAULT 0.0,
                tau REAL NOT NULL DEFAULT 0.5,
                outgoing_weights BLOB,
                incoming_weights BLOB
            );
            CREATE INDEX idx_memories_ts ON memories(ts DESC);
            CREATE INDEX idx_memories_type_ts ON memories(type, ts DESC);
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                text, content='memories', content_rowid='rowid'
            );
            """
        )
        for index, row in enumerate(rows):
            vector = row.get("embedding")
            blob = struct.pack("<" + ("f" * len(vector)), *vector) if vector else None
            connection.execute(
                """
                INSERT INTO memories (
                    id, session_id, turn, scene_id, idx, text, type, stream, ts,
                    source_type, source_reliability, verification_status,
                    fallback_generated, source_event_ids_json, provenance_user,
                    provenance_assistant, speaker_focus, memory_kind,
                    embedding_blob, embedding_dim, embedding_dtype
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row.get("session_id", "session"),
                    row.get("turn", index),
                    row.get("scene_id"),
                    index,
                    row.get("text", ""),
                    row.get("type", "fact"),
                    row.get("stream", "learnings"),
                    row.get("ts", f"2026-01-01T00:00:{index:02d}+00:00"),
                    row.get("source_type", "test"),
                    row.get("source_reliability", 1.0),
                    row.get("verification_status", "verified"),
                    int(row.get("fallback_generated", False)),
                    "[]",
                    "",
                    "",
                    row.get("speaker_focus"),
                    row.get("memory_kind"),
                    row.get("embedding_blob", blob),
                    row.get("embedding_dim", len(vector) if vector else None),
                    row.get("embedding_dtype", "f32" if vector else None),
                ),
            )
        connection.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
        connection.commit()
        connection.close()
        return path

    def row(self, memory_id: str, index: int = 0, **overrides) -> dict:
        result = {
            "id": memory_id,
            "text": f"Memory {memory_id} needle",
            "type": "decision",
            "stream": "learnings",
            "ts": f"2026-01-01T00:00:{index:02d}+00:00",
            "embedding": [1.0, 0.0],
        }
        result.update(overrides)
        return result


class GraphExplorerTests(ExplorerStoreMixin, unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / ".titan"

    def tearDown(self):
        self.temp_dir.cleanup()

    def call(self, method: str, **request):
        return handle_request({"method": method, **request}, self.home)

    def test_snapshot_preserves_namespace_identity_and_is_read_only(self):
        pi_db = self.make_store(self.home, "pi", [self.row("same-id", 1, text="Pi memory", embedding=[1.0, 0.0])])
        codex_db = self.make_store(
            self.home,
            "codex",
            [self.row("same-id", 2, text="Codex memory", embedding=[0.99, 0.1])],
        )
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (pi_db, codex_db)}

        response = self.call("snapshot")

        self.assertEqual({node["nodeId"] for node in response["nodes"]}, {"pi::same-id", "codex::same-id"})
        self.assertEqual(response["edges"][0]["kind"], "similarity")
        self.assertTrue(all(set(edge) == {"source", "target", "kind", "weight"} for edge in response["edges"]))
        after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (pi_db, codex_db)}
        self.assertEqual(before, after)

    def test_similarity_edges_match_deterministic_top_k_semantics(self):
        self.make_store(
            self.home,
            "pi",
            [
                self.row("a", 1, embedding=[1.0, 0.0]),
                self.row("b", 2, embedding=[0.99, 0.1]),
                self.row("c", 3, embedding=[0.8, 0.6]),
                self.row("d", 4, embedding=[0.7, 0.714]),
            ],
        )
        response = self.call("snapshot", filters={"agent": "pi"})
        self.assertEqual(
            response["edges"],
            [
                {"source": "pi::a", "target": "pi::b", "kind": "similarity", "weight": 0.994937},
                {"source": "pi::a", "target": "pi::c", "kind": "similarity", "weight": 0.8},
                {"source": "pi::b", "target": "pi::c", "kind": "similarity", "weight": 0.856249},
                {"source": "pi::b", "target": "pi::d", "kind": "similarity", "weight": 0.76829},
                {"source": "pi::c", "target": "pi::d", "kind": "similarity", "weight": 0.988501},
            ],
        )

    def test_missing_and_invalid_embeddings_still_produce_nodes_without_edges(self):
        self.make_store(
            self.home,
            "pi",
            [
                self.row("valid-a", 1, embedding=[1.0, 0.0]),
                self.row("valid-b", 2, embedding=[0.99, 0.1]),
                self.row("invalid", 3, embedding_blob=b"bad", embedding_dim=2, embedding_dtype="f32"),
                self.row("missing", 4, embedding=None),
            ],
        )

        response = self.call("snapshot")

        by_id = {node["memoryId"]: node for node in response["nodes"]}
        self.assertTrue(by_id["valid-a"]["hasEmbedding"])
        self.assertTrue(by_id["valid-b"]["hasEmbedding"])
        self.assertFalse(by_id["invalid"]["hasEmbedding"])
        self.assertFalse(by_id["missing"]["hasEmbedding"])
        self.assertEqual(len(response["edges"]), 1)
        self.assertLessEqual(len(response["edges"]), 450)

    def test_search_filters_exact_no_match_and_cursor_preserves_duplicates(self):
        self.make_store(
            self.home,
            "pi",
            [self.row(f"pi-{index:03d}", index, type="decision") for index in range(55)],
        )
        self.make_store(
            self.home,
            "codex",
            [self.row("shared", 100, type="decision", text="Memory shared needle", ts="2026-01-01T00:01:00+00:00")],
        )

        first = self.call("search", filters={"query": "needle", "type": "decision"})
        second = self.call(
            "search",
            filters={"query": "needle", "type": "decision"},
            cursor=first["nextCursor"],
        )
        ids = [item["nodeId"] for item in first["items"] + second["items"]]
        self.assertEqual(len(ids), 56)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("codex::shared", ids)
        self.assertEqual(len(first["items"]), 50)
        self.assertIsNone(second["nextCursor"])

        no_match = self.call("search", filters={"query": "not-present-anywhere"})
        self.assertEqual(no_match["items"], [])
        self.assertIsNone(no_match["nextCursor"])

        filtered = self.call(
            "search",
            filters={
                "agent": "codex",
                "dateFrom": "2026-01-01T00:00:00+00:00",
                "dateTo": "2026-01-01T00:01:59+00:00",
            },
        )
        self.assertEqual([item["nodeId"] for item in filtered["items"]], ["codex::shared"])

    def test_submillisecond_sql_order_matches_chronological_cursor_order(self):
        rows = [
            self.row("z", 1, text="z", ts="2026-01-01T00:00:00.000000+00:00"),
            self.row("a", 2, text="a", ts="2026-01-01T00:00:00.000100+00:00"),
        ]
        rows.extend(
            self.row(f"id{index}", index + 2, text=f"id{index}", ts=f"2026-01-01T00:00:00.{index:06d}+00:00")
            for index in range(1, 50)
        )
        self.make_store(self.home, "pi", rows)

        first_page = self.call("search", filters={"agent": "pi"}, page=1)
        self.assertEqual(len(first_page["items"]), 50)
        self.assertIn("pi::a", {item["nodeId"] for item in first_page["items"]})
        self.assertNotIn("pi::z", {item["nodeId"] for item in first_page["items"]})

        first_cursor_page = self.call("search", filters={"agent": "pi"})
        second_cursor_page = self.call(
            "search",
            filters={"agent": "pi"},
            cursor=first_cursor_page["nextCursor"],
        )
        self.assertNotIn("pi::z", {item["nodeId"] for item in first_cursor_page["items"]})
        self.assertEqual([item["nodeId"] for item in second_cursor_page["items"]], ["pi::z"])

    def test_date_only_date_to_covers_full_day_for_snapshot_page_and_cursor(self):
        for agent, json_only in (("sqlite-day", False), ("json-day", True)):
            rows = [
                self.row(
                    f"day-{index:02d}",
                    index,
                    text=f"day {index}",
                    ts=f"2026-01-01T12:{index // 60:02d}:{index % 60:02d}+00:00",
                )
                for index in range(51)
            ]
            rows.append(self.row("next-day", 99, text="next day", ts="2026-01-02T00:00:00+00:00"))
            self.make_store(self.home, agent, rows, json_only=json_only)
            filters = {"agent": agent, "dateTo": "2026-01-01"}
            snapshot_response = self.call("snapshot", filters=filters)
            page_response = self.call("search", filters=filters, page=1)
            cursor_first = self.call("search", filters=filters)
            cursor_second = self.call("search", filters=filters, cursor=cursor_first["nextCursor"])
            self.assertEqual(len(snapshot_response["nodes"]), 51)
            self.assertEqual(page_response["totalItems"], 51)
            self.assertEqual(len(page_response["items"]), 50)
            self.assertEqual(len(cursor_first["items"]), 50)
            self.assertEqual(len(cursor_second["items"]), 1)
            self.assertNotIn("next-day", {item["memoryId"] for item in snapshot_response["nodes"]})

        with self.assertRaises(ExplorerError) as context:
            self.call("search", filters={"dateTo": "9999-12-31"}, page=1)
        self.assertEqual(context.exception.code, "invalid_input")

    def test_offset_date_filters_are_chronological_for_sqlite_and_json(self):
        timestamp = "2026-01-01T00:00:00+00:00"
        lower_offset = "2025-12-31T23:30:00-01:00"  # 00:30Z; row is too early.
        upper_offset = "2025-12-31T23:30:00-01:00"  # 00:30Z; row is before the bound.
        for agent, json_only in (("sqlite-agent", False), ("json-agent", True)):
            self.make_store(
                self.home,
                agent,
                [self.row("offset-row", 1, ts=timestamp)],
                json_only=json_only,
            )
            excluded = self.call("search", filters={"agent": agent, "dateFrom": lower_offset})
            included = self.call("search", filters={"agent": agent, "dateTo": upper_offset})
            self.assertEqual(excluded["items"], [])
            self.assertEqual([item["memoryId"] for item in included["items"]], ["offset-row"])

    def test_invalid_cursor_shape_is_a_bounded_input_error(self):
        malformed = base64.urlsafe_b64encode(json.dumps([]).encode()).decode()
        with self.assertRaises(ExplorerError) as context:
            self.call("search", filters={}, cursor=malformed)
        self.assertEqual(context.exception.code, "invalid_input")

    def test_cursor_paginates_equivalent_offset_timestamps_across_namespaces(self):
        self.make_store(
            self.home,
            "zzz",
            [
                self.row(
                    f"zzz-{index:02d}",
                    index,
                    ts="2026-01-01T01:00:00+01:00",
                    text=f"zzz {index}",
                )
                for index in range(50)
            ],
        )
        self.make_store(
            self.home,
            "aaa",
            [
                self.row(
                    f"aaa-{index:02d}",
                    index,
                    ts="2026-01-01T00:00:00+00:00",
                    text=f"aaa {index}",
                )
                for index in range(5)
            ],
        )
        first = self.call("search", filters={})
        second = self.call("search", filters={}, cursor=first["nextCursor"])
        ids = [item["nodeId"] for item in first["items"] + second["items"]]
        self.assertEqual(len(ids), 55)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[-5:], [f"aaa::aaa-{index:02d}" for index in range(4, -1, -1)])

    def test_summary_metadata_and_identity_bounds(self):
        long_id = "i" * 257
        self.make_store(
            self.home,
            "pi",
            [
                self.row(
                    "bounded",
                    1,
                    session_id="s" * 600_000,
                    type="t" * 200,
                    stream="r" * 100,
                ),
                self.row(long_id, 2),
            ],
        )
        response = self.call("snapshot", filters={"agent": "pi"})
        self.assertEqual([node["memoryId"] for node in response["nodes"]], ["bounded"])
        node = response["nodes"][0]
        self.assertLessEqual(len(node["sessionId"]), 256)
        self.assertLessEqual(len(node["type"]), 128)
        self.assertLessEqual(len(node["stream"]), 64)
        self.assertLessEqual(len(node["timestamp"]), 128)
        self.assertLessEqual(len(node["memoryId"]), 256)
        self.assertLessEqual(len(node["nodeId"]), 512)
        self.assertLessEqual(len(node["sourceAgent"]), 128)
        self.assertTrue(any("oversized id" in warning for warning in response["warnings"]))

    def test_snapshot_and_detail_payloads_are_bounded(self):
        rows = [
            self.row(
                f"memory-{index:03d}",
                index % 60,
                text="x" * 2000,
                embedding=[1.0, 0.0],
            )
            for index in range(MAX_SNAPSHOT_NODES + 20)
        ]
        self.make_store(self.home, "pi", rows)

        response = self.call("snapshot")
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(response["nodes"]), MAX_SNAPSHOT_NODES)
        self.assertTrue(response["partial"])
        self.assertLess(len(encoded), MAX_SNAPSHOT_BYTES)
        self.assertTrue(all(len(node["text"]) <= 240 for node in response["nodes"]))

        long_text = "z" * (MAX_DETAIL_TEXT + 123)
        self.make_store(self.home, "detail-agent", [self.row("long", 1, text=long_text, embedding=[1.0, 0.0])])
        detail_response = detail(self.home, "detail-agent", "long")
        self.assertEqual(len(detail_response["text"]), MAX_DETAIL_TEXT)
        self.assertTrue(detail_response["truncated"])
        self.assertNotIn("embedding", detail_response["metadata"])
        self.assertNotIn("provenance_user", detail_response["metadata"])

    def test_absent_home_is_not_materialized(self):
        self.assertFalse(self.home.exists())
        response = self.call("snapshot")
        self.assertEqual(response["nodes"], [])
        self.assertEqual(response["edges"], [])
        self.assertFalse(self.home.exists())

    def test_json_fallback_and_malicious_namespace_are_bounded(self):
        self.make_store(
            self.home,
            "legacy",
            [self.row("legacy-id", 1, text="legacy needle", embedding=[1.0, 0.0])],
            json_only=True,
        )
        response = self.call("search", filters={"agent": "legacy", "query": "legacy needle"})
        self.assertEqual([item["nodeId"] for item in response["items"]], ["legacy::legacy-id"])

        with self.assertRaisesRegex(Exception, "valid Titan namespace"):
            detail(self.home, "../escape", "anything")

    def test_catalog_caps_types_at_contract_limit(self):
        rows = [
            self.row(
                f"type-{index:03d}",
                index % 60,
                type=f"type-{index:03d}",
                ts=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
            )
            for index in range(257)
        ]
        self.make_store(self.home, "pi", rows)
        response = self.call("catalog")
        expected = sorted(f"type-{index:03d}" for index in range(257))[:256]
        self.assertEqual(response["types"], expected)
        self.assertLessEqual(len(response["types"]), 256)
        self.assertTrue(any("limited to 256" in warning for warning in response["warnings"]))

    def test_catalog_and_direct_page_search_cover_all_namespaces(self):
        codex_rows = [
            self.row(
                f"codex-{index:03d}",
                index % 60,
                text=f"codex page needle {index}",
                type="decision",
                ts=f"2026-01-{(index // 24) + 1:02d}T{index % 24:02d}:00:00+00:00",
            )
            for index in range(160)
        ]
        grok_rows = [
            self.row(
                "shared-id" if index == 0 else f"grok-{index:03d}",
                index % 60,
                text=f"grok older needle {index}",
                type="decision" if index else "fact",
                ts=f"2025-12-{(index // 24) + 1:02d}T{index % 24:02d}:00:00+00:00",
            )
            for index in range(101)
        ]
        codex_db = self.make_store(self.home, "codex", codex_rows)
        grok_db = self.make_store(self.home, "grok", grok_rows)
        claude_db = self.make_store(self.home, "claude-code", [])
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (codex_db, grok_db, claude_db)}

        catalog_response = self.call("catalog")
        agents = {agent["id"]: agent["count"] for agent in catalog_response["agents"]}
        self.assertEqual(agents, {"claude-code": 0, "codex": 160, "grok": 101})
        self.assertEqual(catalog_response["types"], ["decision", "fact"])

        page_five = self.call("search", filters={"type": "decision", "query": "needle"}, page=5)
        self.assertEqual(page_five["page"], 5)
        self.assertEqual(page_five["totalItems"], 260)
        self.assertEqual(page_five["totalPages"], 6)
        self.assertEqual(len(page_five["items"]), 50)
        self.assertIsNone(page_five["nextCursor"])
        self.assertTrue(all(item["type"] == "decision" and "needle" in item["text"] for item in page_five["items"]))

        page_six = self.call("search", filters={"type": "decision", "query": "needle"}, page=6)
        self.assertEqual(len(page_six["items"]), 10)
        self.assertEqual(page_six["totalItems"], 260)
        self.assertEqual(
            len({item["nodeId"] for item in page_five["items"] + page_six["items"]}),
            60,
        )
        out_of_range = self.call("search", filters={"type": "decision", "query": "needle"}, page=11)
        self.assertEqual(out_of_range["items"], [])
        self.assertEqual(out_of_range["page"], 11)
        self.assertEqual(out_of_range["totalPages"], 6)

        after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (codex_db, grok_db, claude_db)}
        self.assertEqual(before, after)

    def test_ten_thousand_row_search_is_bounded(self):
        rows = [self.row(f"large-{index:05d}", index % 60, text=f"large corpus needle {index}") for index in range(10000)]
        self.make_store(self.home, "large", rows)

        started = time.perf_counter()
        response = self.call("search", filters={"agent": "large", "query": "needle"})
        elapsed = time.perf_counter() - started

        self.assertEqual(len(response["items"]), 50)
        self.assertIsNotNone(response["nextCursor"])
        page_started = time.perf_counter()
        late_page = self.call("search", filters={"agent": "large", "query": "needle"}, page=10)
        page_elapsed = time.perf_counter() - page_started
        self.assertEqual(late_page["page"], 10)
        self.assertEqual(late_page["totalItems"], 10000)
        self.assertEqual(late_page["totalPages"], 200)
        self.assertEqual(len(late_page["items"]), 50)
        # These are deliberately loose regression ceilings, not performance
        # claims. CI and backend profiling establish the real targets later.
        self.assertLess(elapsed, 5.0, f"bounded search took {elapsed:.3f}s")
        self.assertLess(page_elapsed, 5.0, f"direct page search took {page_elapsed:.3f}s")

    def test_corrections_scene_patterns_and_namespace_isolation(self):
        rows = [self.row("old", 1), self.row("new", 2, scene_id="scene-1"), self.row("third", 3)]
        db = self.make_store(self.home, "pi", rows)
        self.make_store(self.home, "other", [self.row("old", 1), self.row("new", 2)])
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.executescript("""
                ALTER TABLE memories ADD COLUMN event_date TEXT;
                ALTER TABLE memories ADD COLUMN event_start TEXT;
                ALTER TABLE memories ADD COLUMN event_end TEXT;
                CREATE TABLE memory_supersessions (source TEXT, old_id TEXT, new_id TEXT, evidence TEXT, created_at TEXT, PRIMARY KEY(source,old_id));
                CREATE TABLE scenes (scene_id TEXT PRIMARY KEY, evidence_version INTEGER, evidence_status TEXT, extraction_user_text TEXT, extraction_assistant_text TEXT, source_event_ids_json TEXT);
                CREATE TABLE patterns (id TEXT PRIMARY KEY, title TEXT, status TEXT);
                CREATE TABLE pattern_evidence (pattern_id TEXT, memory_id TEXT, role TEXT);
                CREATE INDEX evidence_memory ON pattern_evidence(memory_id);
            """)
            conn.execute("UPDATE memories SET event_date=?, event_start=?, event_end=? WHERE id='new'", ("2026-01", "2026-01-01", "2026-02-01"))
            conn.executemany("INSERT INTO memory_supersessions VALUES (?,?,?,?,?)", [
                ("pi", "old", "new", "x" * 1500, "2026-01-03"),
                ("pi", "gone", "new", "removed old", "2026-01-04"),
                ("other", "third", "new", "wrong source", "2026-01-05"),
            ])
            conn.execute("INSERT INTO scenes VALUES (?,?,?,?,?,?)", ("scene-1", 1, "complete", "u" * 3000, "assistant", json.dumps(["e" * 220] * 20)))
            conn.execute("INSERT INTO patterns VALUES ('p1','Pattern','accepted')")
            conn.execute("INSERT INTO pattern_evidence VALUES ('p1','new','support')")
        snapshot = self.call("snapshot")
        corrections = [edge for edge in snapshot["edges"] if edge["kind"] == "supersedes"]
        self.assertEqual(corrections, [{"source": "pi::new", "target": "pi::old", "kind": "supersedes", "weight": 1}])
        filtered = self.call("snapshot", filters={"agent": "other"})
        self.assertFalse(any(edge["kind"] == "supersedes" for edge in filtered["edges"]))
        response = self.call("detail", sourceAgent="pi", memoryId="new")
        self.assertEqual(response["metadata"]["event_date"], "2026-01")
        self.assertEqual(response["metadata"]["event_end"], "2026-02-01")
        self.assertEqual({entry["memoryId"] for entry in response["corrections"]}, {"old", "gone"})
        missing = next(entry for entry in response["corrections"] if entry["memoryId"] == "gone")
        self.assertIsNone(missing["memory"])
        self.assertEqual(response["scene"]["status"], "complete")
        self.assertLessEqual(len(response["scene"]["userText"]), 2048)
        self.assertEqual(len(response["scene"]["eventIds"]), 12)
        self.assertLessEqual(len(response["scene"]["eventIds"][0]), 256)
        self.assertEqual(response["patterns"], [{"id": "p1", "title": "Pattern", "status": "accepted", "role": "support"}])
        old = self.call("detail", sourceAgent="pi", memoryId="old")
        self.assertEqual(old["corrections"][0]["direction"], "replacedBy")
        self.assertLessEqual(len(old["corrections"][0]["evidence"]), 1024)
        self.assertEqual(self.call("detail", sourceAgent="other", memoryId="old")["corrections"], [])
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("UPDATE scenes SET source_event_ids_json=?", (json.dumps(["e" * 220] * 40),))
        response = self.call("detail", sourceAgent="pi", memoryId="new")
        self.assertEqual(response["scene"]["status"], "complete")
        self.assertTrue(any("preview limit" in warning for warning in response["warnings"]))
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("UPDATE scenes SET evidence_version=0")
        response = self.call("detail", sourceAgent="pi", memoryId="new")
        self.assertEqual(response["scene"]["status"], "partial")

    def test_legacy_corrections_scene_and_missing_old_schema(self):
        path = self.make_store(self.home, "json", [
            self.row("old", 1, source_event_ids=["old-event"], provenance={"user": "Original date"}),
            self.row("new", 2, scene_id="s"),
        ], json_only=True)
        path.with_name("memories.json.relations.json").write_text(json.dumps([
            {"source": "json", "old_id": "old", "new_id": "new", "evidence": "change", "created_at": "now"},
            {"source": "other", "old_id": "old", "new_id": "new", "evidence": "bad", "created_at": "now"},
        ]))
        path.with_name("scenes.json").write_text(json.dumps([{"scene_id": "s", "evidence_status": "partial", "extraction_user_text": "source", "source_event_ids": ["event"]}]))
        result = self.call("detail", sourceAgent="json", memoryId="new")
        self.assertEqual(result["scene"]["userText"], "source")
        self.assertEqual(result["scene"]["status"], "partial")
        self.assertEqual(result["corrections"][0]["memory"]["memoryId"], "old")
        self.assertEqual(len(result["corrections"]), 1)
        self.assertEqual(len([e for e in self.call("snapshot")["edges"] if e["kind"] == "supersedes"]), 1)
        original = self.call("detail", sourceAgent="json", memoryId="old")
        self.assertIsNone(original["scene"])
        self.assertEqual(original["memoryEvidence"], {"eventIds": ["old-event"], "userText": "Original date", "assistantText": ""})
        db = self.make_store(self.home, "oldschema", [self.row("one", 1, scene_id="absent")])
        old = self.call("detail", sourceAgent="oldschema", memoryId="one")
        self.assertEqual(old["scene"]["status"], "missing")
        self.assertEqual(old["corrections"], [])
        self.assertEqual(old["patterns"], [])
        self.assertIsNone(old["metadata"]["event_date"])

    def test_snapshot_marks_exactly_one_over_limit_partial(self):
        self.make_store(self.home, "pi", [self.row(f"m-{i}", i % 60) for i in range(151)])
        result = self.call("snapshot")
        self.assertEqual(len(result["nodes"]), 150)
        self.assertTrue(result["partial"])

    def test_module_cli_emits_one_json_response_and_nonzero_errors(self):
        self.make_store(self.home, "pi", [self.row("one", 1)])
        command = [sys.executable, "-m", "app.graph.explorer", "--home", str(self.home)]
        success = subprocess.run(
            command,
            cwd=ROOT,
            input=json.dumps({"method": "search", "filters": {"query": "needle"}}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(len(success.stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(success.stdout)["items"][0]["nodeId"], "pi::one")

        failure = subprocess.run(
            command,
            cwd=ROOT,
            input=json.dumps({"method": "unknown"}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(failure.returncode, 0)
        self.assertEqual(json.loads(failure.stdout)["error"]["code"], "invalid_input")


class ExplorerRobustnessTests(ExplorerStoreMixin, unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / ".titan"

    def tearDown(self):
        self.temp_dir.cleanup()

    def call(self, method: str, **request):
        return handle_request({"method": method, **request}, self.home)

    def test_text_embedding_blob_degrades_to_no_vector(self):
        self.make_store(self.home, "pi", [
            self.row("good", 1),
            self.row("text-blob", 2, embedding_blob="not a blob", embedding_dim=2, embedding_dtype="f32"),
        ])
        snapshot = self.call("snapshot")
        by_id = {node["memoryId"]: node for node in snapshot["nodes"]}
        self.assertEqual(set(by_id), {"good", "text-blob"})
        self.assertFalse(by_id["text-blob"]["hasEmbedding"])

    def test_lone_surrogates_do_not_break_snapshot_detail_or_cli(self):
        path = self.make_store(self.home, "legacy", [], json_only=True)
        # Written as raw JSON so the escaped lone surrogate reaches json.loads.
        path.write_text(
            '[{"id": "bad", "text": "bad \\ud800 surrogate", "type": "fact\\udfff", "ts": "2026-01-01T00:00:00+00:00"},'
            ' {"id": "id\\ud800", "text": "unreadable id", "ts": "2026-01-01T00:00:01+00:00"}]',
            encoding="utf-8",
        )
        snapshot = self.call("snapshot")
        self.assertEqual([node["memoryId"] for node in snapshot["nodes"]], ["bad"])
        self.assertEqual(snapshot["nodes"][0]["text"], "bad \ufffd surrogate")
        self.assertEqual(self.call("detail", sourceAgent="legacy", memoryId="bad")["text"], "bad \ufffd surrogate")
        self.assertEqual(self.call("catalog")["types"], ["fact\ufffd"])
        result = subprocess.run(
            [sys.executable, "-m", "app.graph.explorer", "--home", str(self.home)],
            cwd=ROOT, input=json.dumps({"method": "snapshot"}), text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["nodes"][0]["memoryId"], "bad")

    def test_embedded_nul_does_not_shorten_text(self):
        text = "Null-ish \x00 byte and control \x07 chars"
        self.make_store(self.home, "pi", [self.row("nul", 1, text=text)])
        response = self.call("detail", sourceAgent="pi", memoryId="nul")
        self.assertEqual(response["text"], text)
        self.assertFalse(response["truncated"])
        self.assertEqual(self.call("snapshot")["nodes"][0]["text"], text)

    def test_detail_truncation_is_byte_bounded_for_multibyte_text(self):
        self.make_store(self.home, "pi", [self.row("long", 1, text="é" * (MAX_DETAIL_TEXT + 10))])
        response = self.call("detail", sourceAgent="pi", memoryId="long")
        self.assertTrue(response["truncated"])
        self.assertEqual(response["text"], "é" * (MAX_DETAIL_TEXT // 2))

    def test_search_matches_non_ascii_letters_with_and_without_fts(self):
        self.make_store(self.home, "fts", [self.row("cafe", 1, text="Team lunch at the café on Friday")])
        like_db = self.make_store(self.home, "like", [self.row("naive", 1, text="A naïve CAFÉ plan")])
        with closing(sqlite3.connect(like_db)) as conn, conn:
            conn.execute("DROP TABLE memories_fts")
        for query, expected in (
            ("café", {"fts::cafe", "like::naive"}),
            ("CAFÉ", {"fts::cafe", "like::naive"}),
            ("naïve", {"like::naive"}),
        ):
            with self.subTest(query=query):
                items = self.call("search", filters={"query": query})["items"]
                self.assertEqual({item["nodeId"] for item in items}, expected)

    def test_catalog_offers_only_complete_type_names(self):
        long_type, exact_type = "t" * 300, "x" * 128
        self.make_store(self.home, "pi", [self.row("long", 1, type=long_type), self.row("exact", 2, type=exact_type)])
        catalog_response = self.call("catalog")
        self.assertEqual(catalog_response["types"], [exact_type])
        self.assertTrue(any("longer than 128" in warning for warning in catalog_response["warnings"]))
        items = self.call("search", filters={"type": exact_type})["items"]
        self.assertEqual([item["memoryId"] for item in items], ["exact"])

    def test_namespace_cap_keeps_populated_namespaces_after_empty_directories(self):
        for index in range(70):
            (self.home / "agents" / f"a{index:02d}").mkdir(parents=True)
        self.make_store(self.home, "zz-real", [self.row("kept", 1)])
        snapshot = self.call("snapshot")
        self.assertEqual([node["nodeId"] for node in snapshot["nodes"]], ["zz-real::kept"])
        self.assertIn("zz-real", {agent["id"] for agent in self.call("catalog")["agents"]})

    def test_snapshot_correction_limit_only_counts_visible_endpoints(self):
        db = self.make_store(self.home, "pi", [self.row("old", 1), self.row("new", 2)])
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(
                "CREATE TABLE memory_supersessions (source TEXT, old_id TEXT, new_id TEXT, evidence TEXT, "
                "created_at TEXT, PRIMARY KEY(source, old_id, new_id))"
            )
            conn.executemany("INSERT INTO memory_supersessions VALUES (?,?,?,?,?)", [
                *(("pi", "old", f"hidden-{index}", "", "2026-02-01") for index in range(3)),
                ("pi", "old", "new", "", "2026-01-01"),
            ])
        warnings = WarningCollector()
        with patch("app.graph.explorer.MAX_SNAPSHOT_EDGES", 2):
            edges = correction_edges(self.home, [("pi", "old"), ("pi", "new")], warnings)
        self.assertEqual(edges, [{"source": "pi::new", "target": "pi::old", "kind": "supersedes", "weight": 1}])
        self.assertEqual(warnings.as_list(), [])

    def test_agent_names_resolve_to_the_canonical_namespace(self):
        self.make_store(self.home, "codex", [self.row("one", 1)])
        self.assertEqual(resolve_agent(self.home, "codex"), "codex")
        self.assertEqual(resolve_agent(self.home, "CODEX"), "codex")
        self.assertIsNone(resolve_agent(self.home, "nobody"))
        with self.assertRaises(ExplorerError):
            resolve_agent(self.home, "../x")
        snapshot = self.call("snapshot", filters={"agent": "Codex"})
        self.assertEqual([node["nodeId"] for node in snapshot["nodes"]], ["codex::one"])
        self.assertEqual(self.call("detail", sourceAgent="CODEX", memoryId="one")["memory"]["sourceAgent"], "codex")

    def test_configured_store_path_is_used_for_records_and_detail(self):
        self.make_store(self.home, "codex", [self.row("default-store", 1)])
        configured_db = self.make_store(Path(self.temp_dir.name) / "custom", "codex", [self.row("configured-store", 1)])
        stores = {"codex": configured_db}
        records, more = newest_records(self.home, ExplorerFilters(), 10, WarningCollector(), stores=stores)
        self.assertEqual([record.memory_id for record in records], ["configured-store"])
        self.assertFalse(more)
        self.assertEqual(detail(self.home, "codex", "configured-store", stores=stores)["memory"]["memoryId"], "configured-store")
        with self.assertRaises(ExplorerError):
            detail(self.home, "codex", "default-store", stores=stores)

    def test_session_filter_and_total_count(self):
        self.make_store(self.home, "pi", [
            self.row("a", 1, session_id="one"), self.row("b", 2, session_id="two"), self.row("c", 3, session_id="one"),
        ])
        filters = ExplorerFilters(session_id="one")
        records, more = newest_records(self.home, filters, 1, WarningCollector())
        self.assertEqual([record.memory_id for record in records], ["c"])
        self.assertTrue(more)
        self.assertEqual(count_records(self.home, filters, WarningCollector()), 2)

    def test_invalid_ids_do_not_take_candidate_slots_or_counts(self):
        # The two newest rows have ids that fail validation: a newline, and an
        # embedded NUL that SQLite's length()/substr() would cut to "nul".
        self.make_store(self.home, "pi", [
            self.row("good-old", 0), self.row("good-new", 1),
            self.row("bad\nid", 2), self.row("nul\x00tail", 3),
        ])
        warnings = WarningCollector()
        records, more = newest_records(self.home, ExplorerFilters(), 2, warnings)
        self.assertEqual([record.memory_id for record in records], ["good-new", "good-old"])
        self.assertFalse(more)
        self.assertIn("Skipped invalid id values in namespace pi", warnings.as_list())
        self.assertEqual(count_records(self.home, ExplorerFilters(), WarningCollector()), 2)
        snapshot = self.call("snapshot")
        self.assertEqual([node["memoryId"] for node in snapshot["nodes"]], ["good-new", "good-old"])
        for node in snapshot["nodes"]:
            self.assertEqual(self.call("detail", sourceAgent="pi", memoryId=node["memoryId"])["memory"]["memoryId"], node["memoryId"])
        with self.assertRaises(ExplorerError):
            detail(self.home, "pi", "nul")

    def test_infinite_and_oversized_numbers_drop_only_that_field(self):
        db = self.make_store(self.home, "pi", [self.row("inf-turn", 1), self.row("fine", 0)])
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("UPDATE memories SET turn = 1e999, embedding_dim = 1e999 WHERE id = 'inf-turn'")
        records, _ = newest_records(self.home, ExplorerFilters(), 10, WarningCollector())
        by_id = {record.memory_id: record for record in records}
        self.assertEqual(set(by_id), {"inf-turn", "fine"})
        self.assertIsNone(by_id["inf-turn"].turn)
        self.assertIsNone(by_id["inf-turn"].embedding)
        self.assertIsNotNone(by_id["fine"].embedding)

        self.make_store(self.home, "codex", [
            self.row("json-inf", 1, turn=float("inf"), embedding=[10**400, 1.0]),
            self.row(0, 0, turn=2, source_reliability=10**400),
        ], json_only=True)
        records, _ = newest_records(self.home, ExplorerFilters(agent="codex"), 10, WarningCollector())
        by_id = {record.memory_id: record for record in records}
        self.assertEqual(set(by_id), {"json-inf", "0"})
        self.assertIsNone(by_id["json-inf"].turn)
        self.assertIsNone(by_id["json-inf"].embedding)
        self.assertIsNone(by_id["0"].source_reliability)
        # A falsy legacy id keeps the same identity in the graph and in detail.
        self.assertEqual(detail(self.home, "codex", "0")["memory"]["memoryId"], "0")

    def test_utf16_store_text_and_ids_decode_correctly(self):
        memory_dir = self.home / "agents" / "pi" / "out" / "memories"
        memory_dir.mkdir(parents=True)
        text = "héllo ✓ 😀 Arabic عربى\x00after-nul"
        with closing(sqlite3.connect(memory_dir / "memory_store.db")) as connection, connection:
            connection.execute("PRAGMA encoding = 'UTF-16le'")
            connection.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, text TEXT, ts TEXT)")
            connection.executemany("INSERT INTO memories VALUES (?, ?, ?)", [
                ("ünï-id", text, "2026-01-01T00:00:01+00:00"),
                ("nul\x00id", "hidden", "2026-01-01T00:00:02+00:00"),
            ])
        snapshot = self.call("snapshot")
        self.assertEqual([node["memoryId"] for node in snapshot["nodes"]], ["ünï-id"])
        self.assertEqual(snapshot["nodes"][0]["text"], text)
        payload = self.call("detail", sourceAgent="pi", memoryId="ünï-id")
        self.assertEqual(payload["text"], text)
        self.assertFalse(payload["truncated"])

    def test_json_store_path_selects_json_over_a_stale_sqlite_file(self):
        stale = self.make_store(self.home, "codex", [self.row("stale-sqlite", 1)])
        json_path = self.make_store(self.home, "codex", [self.row("json-row", 1)], json_only=True)
        self.assertEqual(stale.parent, json_path.parent)
        stores = {"codex": json_path}
        records, _ = newest_records(self.home, ExplorerFilters(), 10, WarningCollector(), stores=stores)
        self.assertEqual([record.memory_id for record in records], ["json-row"])
        self.assertEqual(detail(self.home, "codex", "json-row", stores=stores)["memory"]["memoryId"], "json-row")
        with self.assertRaises(ExplorerError):
            detail(self.home, "codex", "stale-sqlite", stores=stores)


if __name__ == "__main__":
    unittest.main()
