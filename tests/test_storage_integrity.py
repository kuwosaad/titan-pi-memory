import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository
from app.storage.scenes import JsonSceneRepository, SqliteSceneRepository


def _complete_scene(answer: str = "Use the stable event ID.") -> dict:
    raw_events = [
        {
            "seq": 1,
            "session_id": "storage-integrity",
            "event_id": "user-1",
            "event_type": "user_message",
            "payload": {"content": "How should replay behave?"},
        },
        {
            "seq": 2,
            "session_id": "storage-integrity",
            "event_id": "assistant-1",
            "event_type": "assistant_message",
            "payload": {"content": answer},
        },
    ]
    return {
        "scene_id": "storage-integrity:scene:assistant-1",
        "session_id": "storage-integrity",
        "turn": 1,
        "kind": "message_exchange",
        "scene_seq": 2,
        "start_event_seq": 1,
        "end_event_seq": 2,
        "anchor_event_id": "assistant-1",
        "source_event_ids": ["user-1", "assistant-1"],
        "raw_events": raw_events,
        "evidence_version": 1,
        "evidence_status": "complete",
        "missing_source_event_ids": [],
        "messages": [
            {"role": "user", "content": "How should replay behave?", "event_id": "user-1"},
            {"role": "assistant", "content": answer, "event_id": "assistant-1"},
        ],
        "tool_calls": [],
        "extraction_user_text": "How should replay behave?",
        "extraction_assistant_text": answer,
        "used_context_fallback": False,
        "ts": "2026-09-05T00:00:02+00:00",
    }


def _partial_scene() -> dict:
    scene = _complete_scene()
    scene.update(
        {
            # Scene identity remains stable while the missing assistant event
            # is recovered; only the evidence bounds/content become fuller.
            "scene_seq": 2,
            "end_event_seq": 1,
            "anchor_event_id": "assistant-1",
            "source_event_ids": ["user-1"],
            "raw_events": [scene["raw_events"][0]],
            "evidence_status": "partial",
            "missing_source_event_ids": ["assistant-1"],
            "messages": [scene["messages"][0]],
            "tool_calls": [],
            "extraction_assistant_text": "",
            "ts": "2026-09-05T00:00:01+00:00",
        }
    )
    return scene


def _memory(text: str = "Use the stable event ID.", scene_id: str = "storage-integrity:scene:assistant-1", embedding=None) -> dict:
    return {
        "id": "storage-integrity:1:0",
        "text": text,
        "type": "decision",
        "stream": "learnings",
        "embedding": [1.0, 0.5] if embedding is None else embedding,
        "ts": "2026-09-05T00:00:02+00:00",
        "session_id": "storage-integrity",
        "turn": 1,
        "scene_id": scene_id,
        "provenance": {"user": "How should replay behave?", "assistant": text},
        "source_event_ids": ["user-1", "assistant-1"],
        "source_type": "assistant",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "h": 0.0,
        "tau": 0.5,
    }


class StorageIntegrityTests(unittest.TestCase):
    def _scene_repositories(self, root: Path):
        return (
            JsonSceneRepository(root / "scenes.json"),
            SqliteSceneRepository(root / "scenes.db"),
        )

    def _memory_repositories(self, root: Path):
        return (
            JsonMemoryRepository(root / "memories.json"),
            SqliteMemoryRepository(root / "memories.db"),
        )

    def test_complete_scene_replay_is_a_noop_and_collision_is_rejected_in_both_backends(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for repo in self._scene_repositories(root):
                scene = _complete_scene()
                repo.append_scenes([scene])
                replay = {**scene, "ts": "2026-09-05T00:01:00+00:00"}
                repo.append_scenes([replay])
                self.assertEqual(repo.get_scene(scene["scene_id"])["messages"][1]["content"], scene["messages"][1]["content"])

                collision = _complete_scene("A different complete observation.")
                with self.assertRaisesRegex(ValueError, "scene collision"):
                    repo.append_scenes([collision])
                self.assertEqual(repo.get_scene(scene["scene_id"])["messages"][1]["content"], scene["messages"][1]["content"])

    def test_partial_scene_only_upgrades_when_incoming_evidence_is_fuller(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for repo in self._scene_repositories(root):
                partial = _partial_scene()
                complete = _complete_scene()
                repo.append_scenes([partial])
                repo.append_scenes([complete])
                loaded = repo.get_scene(partial["scene_id"])
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["evidence_status"], "complete")
                self.assertEqual(loaded["source_event_ids"], complete["source_event_ids"])
                self.assertEqual(loaded["raw_events"], complete["raw_events"])

    def test_partial_scene_upgrade_rejects_unbacked_lineage_and_identity_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for repo in self._scene_repositories(root):
                partial = _partial_scene()
                repo.append_scenes([partial])

                unbacked = json.loads(json.dumps(partial))
                unbacked["source_event_ids"] = ["user-1", "missing-from-raw"]
                unbacked["missing_source_event_ids"] = []
                with self.assertRaisesRegex(ValueError, "scene collision"):
                    repo.append_scenes([unbacked])

                explicitly_missing = json.loads(json.dumps(partial))
                explicitly_missing["source_event_ids"] = ["user-1", "assistant-1", "tool-1"]
                explicitly_missing["missing_source_event_ids"] = ["assistant-1", "tool-1"]
                repo.append_scenes([explicitly_missing])
                loaded = repo.get_scene(partial["scene_id"])
                self.assertEqual(loaded["source_event_ids"], explicitly_missing["source_event_ids"])
                self.assertEqual(loaded["missing_source_event_ids"], explicitly_missing["missing_source_event_ids"])

                for field, value in ((
                    ("turn", 99),
                    ("scene_seq", 99),
                    ("anchor_event_id", "user-1"),
                )):
                    changed_identity = _complete_scene()
                    changed_identity[field] = value
                    with self.assertRaisesRegex(ValueError, "scene collision"):
                        repo.append_scenes([changed_identity])

                loaded = repo.get_scene(partial["scene_id"])
                self.assertEqual(loaded["evidence_status"], "partial")
                self.assertEqual(loaded["source_event_ids"], explicitly_missing["source_event_ids"])

    def test_json_historical_scene_duplicates_remain_verbatim_on_unrelated_append(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "scenes.json"
            first = _complete_scene()
            historical_conflict = _complete_scene("A conflicting historic observation.")
            other = _complete_scene()
            other["scene_id"] = "storage-integrity:scene:other"
            path.write_text(json.dumps([first, historical_conflict]), encoding="utf-8")

            repo = JsonSceneRepository(path)
            repo.append_scenes([other])

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(persisted), 3)
            self.assertEqual(persisted[0], first)
            self.assertEqual(persisted[1], historical_conflict)
            self.assertEqual(persisted[2]["scene_id"], other["scene_id"])

    def test_memory_replay_is_one_identity_and_preserves_mutable_retrieval_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for repo in self._memory_repositories(root):
                record = _memory()
                repo.append_memories([record, dict(record)])
                self.assertEqual(repo.get_memory_count(), 1)

                repo.update_lnn_state(
                    record["id"],
                    h=0.7,
                    tau=0.8,
                    outgoing_weights={"other": 0.6},
                )
                replay = {**record, "ts": "2026-09-05T00:03:00+00:00", "h": 0.0, "tau": 0.5}
                repo.append_memories([replay])
                loaded = repo.get_recent_memories(limit=1)[0]
                self.assertEqual(loaded["h"], 0.7)
                self.assertEqual(loaded["tau"], 0.8)
                self.assertEqual(loaded["outgoing_weights"], {"other": 0.6})

    def test_json_historical_duplicate_rows_are_preserved_while_new_ids_still_append(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "memories.json"
            first = _memory()
            second = {**first, "h": 0.9, "tau": 0.6, "outgoing_weights": {"other": 0.4}}
            path.write_text(json.dumps([first, second]), encoding="utf-8")
            repo = JsonMemoryRepository(path)

            new_record = {**_memory(), "id": "storage-integrity:2:0", "text": "A separate memory."}
            repo.append_memories([new_record])
            self.assertEqual(repo.get_memory_count(), 3)
            loaded = repo.load_all_memories()
            self.assertEqual(loaded[0]["h"], 0.0)
            self.assertEqual(loaded[1]["h"], 0.9)
            self.assertEqual(loaded[1]["outgoing_weights"], {"other": 0.4})

            repo.append_memories([first])
            self.assertEqual(repo.get_memory_count(), 3)

    def test_different_memory_text_scene_or_embedding_is_rejected_without_overwrite(self):
        for field, value in (
            ("text", "A different derivation."),
            ("scene_id", "another-scene"),
            ("embedding", [0.0, 1.0]),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                for repo in self._memory_repositories(root):
                    record = _memory()
                    repo.append_memories([record])
                    collision = {**record, field: value}
                    with self.assertRaisesRegex(ValueError, "memory collision"):
                        repo.append_memories([collision])
                    self.assertEqual(repo.get_memory_count(), 1)
                    self.assertEqual(repo.get_recent_memories(limit=1)[0]["text"], record["text"])

    def test_json_and_sqlite_memory_reads_have_matching_identity_and_count(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            record = _memory()
            json_repo, sqlite_repo = self._memory_repositories(root)
            json_repo.append_memories([record])
            sqlite_repo.append_memories([record])

            json_loaded = json_repo.get_recent_memories(limit=1)[0]
            sqlite_loaded = sqlite_repo.get_recent_memories(limit=1)[0]
            self.assertEqual(json_repo.get_memory_count(), sqlite_repo.get_memory_count())
            for field in ("id", "text", "type", "stream", "session_id", "turn", "scene_id", "source_event_ids"):
                self.assertEqual(json_loaded[field], sqlite_loaded[field], field)
            self.assertEqual(json_loaded["embedding"], sqlite_loaded["embedding"])

    def test_sqlite_scene_replays_are_serialized_across_repository_instances(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "scenes.db"
            first_repo = SqliteSceneRepository(db_path)
            second_repo = SqliteSceneRepository(db_path)
            scene = _complete_scene()
            start = threading.Barrier(2)
            errors = []

            def append(repo):
                try:
                    start.wait(timeout=5)
                    repo.append_scenes([scene])
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [threading.Thread(target=append, args=(repo,)) for repo in (first_repo, second_repo)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(errors, [])
            loaded = first_repo.get_scene(scene["scene_id"])
            self.assertEqual([item["content"] for item in loaded["messages"]], [item["content"] for item in scene["messages"]])

            conflict_db = Path(tmp_dir) / "scene-conflict.db"
            conflict_first = SqliteSceneRepository(conflict_db)
            conflict_second = SqliteSceneRepository(conflict_db)
            conflicting = _complete_scene("A conflicting concurrent observation.")
            start = threading.Barrier(2)
            errors = []

            def append_conflict(repo, payload):
                try:
                    start.wait(timeout=5)
                    repo.append_scenes([payload])
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [
                threading.Thread(target=append_conflict, args=(conflict_first, scene)),
                threading.Thread(target=append_conflict, args=(conflict_second, conflicting)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ValueError)
            self.assertEqual(conflict_first.get_scene(scene["scene_id"])["scene_id"], scene["scene_id"])

    def test_sqlite_equivalent_replays_are_serialized_across_repository_instances(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memories.db"
            first_repo = SqliteMemoryRepository(db_path)
            second_repo = SqliteMemoryRepository(db_path)
            record = _memory()
            start = threading.Barrier(2)
            errors = []

            def append(repo):
                try:
                    start.wait(timeout=5)
                    repo.append_memories([record])
                except Exception as exc:  # pragma: no cover - failure is asserted below
                    errors.append(exc)

            threads = [
                threading.Thread(target=append, args=(repo,))
                for repo in (first_repo, second_repo)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(first_repo.get_memory_count(), 1)

            conflict_db = Path(tmp_dir) / "memory-conflict.db"
            conflict_first = SqliteMemoryRepository(conflict_db)
            conflict_second = SqliteMemoryRepository(conflict_db)
            conflicting = {**record, "text": "A conflicting concurrent derivation."}
            start = threading.Barrier(2)
            errors = []

            def append_conflict(repo, payload):
                try:
                    start.wait(timeout=5)
                    repo.append_memories([payload])
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [
                threading.Thread(target=append_conflict, args=(conflict_first, record)),
                threading.Thread(target=append_conflict, args=(conflict_second, conflicting)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ValueError)
            self.assertEqual(conflict_first.get_memory_count(), 1)


if __name__ == "__main__":
    unittest.main()
