import sqlite3

from app.retrieval_pipeline.federated import FederatedRecall
from app.storage.scenes import SqliteSceneRepository


def test_legacy_scene_references_are_read_without_schema_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE scenes (scene_id TEXT PRIMARY KEY, raw_events_json TEXT)")
        connection.executemany("INSERT INTO scenes VALUES (?, ?)", [("a", "[]"), ("b", "[]")])
    before = path.read_bytes()
    repository = SqliteSceneRepository(path, initialize=False)
    recall = FederatedRecall(active_agent="codex", scene_repositories={"pi": repository})

    refs = recall.scene_references([
        {"scene_id": "b", "source_agent": "pi"},
        {"scene_id": "missing", "source_agent": "pi"},
        {"scene_id": "a", "source_agent": "pi"},
    ], sources=["pi"])

    assert refs == [
        {"scene_id": "b", "source_agent": "pi", "evidence_version": 0, "evidence_status": "partial", "scene_available": True},
        {"scene_id": "missing", "source_agent": "pi", "scene_available": False, "evidence_status": "partial"},
        {"scene_id": "a", "source_agent": "pi", "evidence_version": 0, "evidence_status": "partial", "scene_available": True},
    ]
    assert path.read_bytes() == before
