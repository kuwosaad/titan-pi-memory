import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.retrieval_pipeline.retriever import (
    _build_fts_query,
    _content_tokens,
    extract_date_brackets,
)
from app.storage.memories import SqliteMemoryRepository
from app.storage.repository import CandidateFilters


class SlugExpanderTests(unittest.TestCase):
    def test_slug_squished_form_included_in_tokens(self):
        tokens = _content_tokens("We added T3 Code integration")
        self.assertIn("t3code", tokens)

    def test_slug_already_one_token_not_changed(self):
        tokens = _content_tokens("The project uses t3code everywhere")
        self.assertIn("t3code", tokens)
        self.assertIn("project", tokens)

    def test_normal_text_unchanged(self):
        tokens = _content_tokens("what database does this project use")
        self.assertIn("database", tokens)
        self.assertIn("project", tokens)

    def test_short_text_returns_empty(self):
        tokens = _content_tokens("a b c")
        self.assertEqual(tokens, set())


class FtsQueryBuilderTests(unittest.TestCase):
    def test_empty_query_returns_empty_string(self):
        self.assertEqual(_build_fts_query(""), "")
        self.assertEqual(_build_fts_query("  "), "")

    def test_builds_or_query_with_tokens_and_slug(self):
        result = _build_fts_query("T3 Code")
        self.assertIn('"code"', result)
        self.assertIn('"t3code"', result)
        self.assertIn(" OR ", result)

    def test_stopwords_excluded(self):
        result = _build_fts_query("the should what is at")
        self.assertEqual(result, "")

    def test_special_characters_stripped(self):
        result = _build_fts_query("T3-Code!")
        self.assertIn("t3code", result)


class DateExtractorTests(unittest.TestCase):
    def test_iso_date_range(self):
        result = extract_date_brackets("2024-05-29 to 2024-05-30")
        self.assertEqual(result["date_from"], "2024-05-29")
        self.assertEqual(result["date_to"], "2024-05-30")

    def test_single_iso_date(self):
        result = extract_date_brackets("find memories from 2024-05-29")
        self.assertEqual(result["date_from"], "2024-05-29")
        self.assertEqual(result["date_to"], "2024-05-29")

    def test_last_week(self):
        result = extract_date_brackets("what did we do last week")
        self.assertIsNotNone(result["date_from"])
        self.assertIsNotNone(result["date_to"])
        today = datetime.now(timezone.utc).date()
        last_monday = today - timedelta(days=today.weekday() + 7)
        last_sunday = last_monday + timedelta(days=6)
        self.assertEqual(result["date_from"], last_monday.isoformat())
        self.assertEqual(result["date_to"], last_sunday.isoformat())

    def test_yesterday(self):
        result = extract_date_brackets("what did we do yesterday")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        self.assertEqual(result["date_from"], yesterday.isoformat())
        self.assertEqual(result["date_to"], yesterday.isoformat())

    def test_today(self):
        result = extract_date_brackets("what did we do today")
        today = datetime.now(timezone.utc).date()
        self.assertEqual(result["date_from"], today.isoformat())
        self.assertEqual(result["date_to"], today.isoformat())

    def test_month_range(self):
        result = extract_date_brackets("may 29 to 30")
        self.assertIsNotNone(result["date_from"])
        self.assertIsNotNone(result["date_to"])

    def test_in_month(self):
        result = extract_date_brackets("what happened in june")
        self.assertIsNotNone(result["date_from"])
        self.assertIsNotNone(result["date_to"])
        self.assertIn("-06-01", result["date_from"])

    def test_empty_query(self):
        result = extract_date_brackets("")
        self.assertIsNone(result["date_from"])
        self.assertIsNone(result["date_to"])

    def test_no_date_in_query(self):
        result = extract_date_brackets("what database does this project use")
        self.assertIsNone(result["date_from"])
        self.assertIsNone(result["date_to"])

    def test_cross_month_range(self):
        result = extract_date_brackets("may 29 to jun 30")
        self.assertIsNotNone(result["date_from"])
        self.assertIsNotNone(result["date_to"])
        self.assertEqual(result["date_from"][-5:], "05-29")
        self.assertEqual(result["date_to"][-5:], "06-30")

    def test_invalid_day_no_crash(self):
        result = extract_date_brackets("feb 30")
        self.assertIsNone(result["date_from"])

    def test_in_mayhem_no_false_positive(self):
        result = extract_date_brackets("find everything in mayhem")
        self.assertIsNone(result["date_from"])


class Fts5IntegrationTests(unittest.TestCase):
    def test_fts5_matches_slug_against_spaced_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory_store.db"
            repo = SqliteMemoryRepository(db_path)

            repo.append_memories([
                {
                    "id": "s1:1:0",
                    "text": "We completed the t3code pi provider integration.",
                    "type": "outcome",
                    "stream": "rough",
                    "ts": "2026-05-29T00:00:00+00:00",
                    "session_id": "s1",
                    "turn": 1,
                    "scene_id": None,
                    "provenance": {"user": "u1", "assistant": "a1"},
                    "source_event_ids": [],
                    "source_type": "assistant",
                    "source_reliability": 0.5,
                    "verification_status": "unverified",
                    "fallback_generated": False,
                },
            ])

            filters = CandidateFilters(
                recency_days=None,
                session_id=None,
                session_bias=False,
                memory_types=None,
                mode="both",
                min_reliability=0.0,
            )

            ftq = _build_fts_query("T3 Code")
            self.assertIn("t3code", ftq)

            candidates = repo.query_candidates_with_text(ftq, filters)
            self.assertEqual(len(candidates), 1)
            self.assertIn("t3code", candidates[0]["text"])

    def test_fts5_no_match_falls_through_to_all_filtered(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory_store.db"
            repo = SqliteMemoryRepository(db_path)

            repo.append_memories([
                {
                    "id": "s1:1:0",
                    "text": "Remember to always use snake_case.",
                    "type": "decision",
                    "stream": "learnings",
                    "ts": "2026-05-29T00:00:00+00:00",
                    "session_id": "s1",
                    "turn": 1,
                    "scene_id": None,
                    "provenance": {"user": "u1", "assistant": "a1"},
                    "source_event_ids": [],
                    "source_type": "user",
                    "source_reliability": 0.9,
                    "verification_status": "unverified",
                    "fallback_generated": False,
                },
            ])

            filters = CandidateFilters(
                recency_days=None,
                session_id=None,
                session_bias=False,
                memory_types=None,
                mode="both",
                min_reliability=0.0,
            )

            ftq = _build_fts_query("T3 Code")
            candidates = repo.query_candidates_with_text(ftq, filters)
            self.assertEqual(len(candidates), 1)
            self.assertIn("snake_case", candidates[0]["text"])

    def test_query_candidates_with_text_empty_query_delegates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory_store.db"
            repo = SqliteMemoryRepository(db_path)

            repo.append_memories([
                {
                    "id": "s1:1:0",
                    "text": "Use event_id for dedupe.",
                    "type": "decision",
                    "stream": "learnings",
                    "ts": "2026-05-29T00:00:00+00:00",
                    "session_id": "s1",
                    "turn": 1,
                    "scene_id": None,
                    "provenance": {"user": "u1", "assistant": "a1"},
                    "source_event_ids": [],
                    "source_type": "user",
                    "source_reliability": 0.9,
                    "verification_status": "unverified",
                    "fallback_generated": False,
                },
            ])

            filters = CandidateFilters(
                recency_days=None,
                session_id=None,
                session_bias=False,
                memory_types=None,
                mode="both",
                min_reliability=0.0,
            )

            candidates = repo.query_candidates_with_text("", filters)
            self.assertEqual(len(candidates), 1)

            candidates = repo.query_candidates_with_text("   ", filters)
            self.assertEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
