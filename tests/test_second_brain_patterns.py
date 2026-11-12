from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from app.patterns.brief import build_pattern_brief
from app.patterns.models import Pattern, PatternApplication, PatternEvidence
from app.patterns.retrieval import record_pattern_applications, retrieve_accepted_patterns
from app.patterns.store import PatternStore
from app.runtime.context import RuntimeContext
from app.storage.memories import SqliteMemoryRepository
from tests.test_patterns_retrieval import _memory_records


def _context(tmp_path: Path, settings: str) -> RuntimeContext:
    config = tmp_path / "settings.yaml"
    config.write_text(settings)
    return RuntimeContext.from_environment(
        root_dir=tmp_path, environ={"TITAN_HOME": str(tmp_path), "TITAN_SETTINGS_PATH": str(config)}
    )


def _store(path: Path) -> PatternStore:
    SqliteMemoryRepository(path).append_memories(_memory_records())
    store = PatternStore(path)
    for title in ("Billing first", "Billing second"):
        pattern = Pattern(title=title, status="accepted", summary="Billing work",
                          recommended_behavior="Check billing tests.", trigger_terms=["billing"], confidence=0.9)
        store.create_pattern(pattern, [PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0")])
    return store


def test_runtime_settings_control_pattern_retrieval(tmp_path):
    path = tmp_path / "patterns.db"
    _store(path)
    context = _context(tmp_path, "patterns:\n  enabled: false\n  retrieve_limit: 1\n")
    with patch("app.retrieval_pipeline.config.get_runtime_context", return_value=context):
        assert retrieve_accepted_patterns("billing", db_path=path, record_applications=False) == []
    context = _context(tmp_path, "patterns:\n  enabled: true\n  retrieve_limit: 1\n")
    with patch("app.retrieval_pipeline.config.get_runtime_context", return_value=context):
        assert len(retrieve_accepted_patterns("billing", db_path=path, record_applications=False)) == 1


def test_no_record_lookup_is_read_only_and_default_keeps_logging(tmp_path):
    path = tmp_path / "patterns.db"
    store = _store(path)
    context = _context(tmp_path, "patterns:\n  enabled: true\n  retrieve_limit: 2\n")
    before = path.stat().st_mtime_ns
    with patch("app.retrieval_pipeline.config.get_runtime_context", return_value=context):
        hits = retrieve_accepted_patterns("billing", db_path=path, record_applications=False)
        assert len(hits) == 2
        assert store.list_applications() == []
        assert path.stat().st_mtime_ns == before
        record_pattern_applications("billing", hits[:1], db_path=path)
        assert [a.pattern_id for a in store.list_applications()] == [hits[0]["pattern"]["id"]]
        retrieve_accepted_patterns("billing", db_path=path)
    assert len(store.list_applications()) == 3
    with pytest.raises(sqlite3.OperationalError):
        PatternStore(path, initialize=False).record_application(PatternApplication(pattern_id=hits[0]["pattern"]["id"], query="x"))


def test_no_record_lookup_does_not_create_or_initialize_store(tmp_path):
    context = _context(tmp_path, "patterns:\n  enabled: true\n")
    missing = tmp_path / "missing" / "store.db"
    existing = tmp_path / "legacy.db"
    sqlite3.connect(existing).close()
    before = existing.read_bytes()
    with patch("app.retrieval_pipeline.config.get_runtime_context", return_value=context):
        assert retrieve_accepted_patterns("billing", db_path=missing, record_applications=False) == []
        assert retrieve_accepted_patterns("billing", db_path=existing, record_applications=False) == []
    assert not missing.parent.exists()
    assert existing.read_bytes() == before


def test_brief_skips_oversized_patterns_and_tracks_exactly_rendered_hits():
    oversized = {"pattern": {"title": "x" * 500, "recommended_behavior": "Do something"}}
    too_long = {"pattern": {"title": "long", "recommended_behavior": "x" * 500}}
    compact = {"pattern": {"title": "Billing", "recommended_behavior": "Run tests"}}
    included = []
    text = build_pattern_brief([oversized, too_long, compact], max_items=1, max_chars=100, included_hits=included)
    assert len(text) <= 100
    assert "Do: Run tests" in text
    assert included == [compact]
    assert build_pattern_brief([compact], max_items=0, max_chars=100) == ""
    assert build_pattern_brief([compact], max_items=1, max_chars=0) == ""
