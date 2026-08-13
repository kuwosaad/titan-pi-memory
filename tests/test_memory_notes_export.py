import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage import notes


class MemoryNotesExportTests(unittest.TestCase):
    def test_writes_stream_specific_markdown_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            notes_dir = tmp_path / "memory_notes"
            rough_dir = notes_dir / "rough"
            learnings_dir = notes_dir / "learnings"

            with patch.object(notes, "NOTES_DIR", notes_dir), patch.object(notes, "ROUGH_NOTES_DIR", rough_dir), patch.object(notes, "LEARNINGS_NOTES_DIR", learnings_dir):
                notes.append_memory_notes(
                    [
                        {
                            "session_id": "sess-1",
                            "stream": "rough",
                            "ts": "2026-02-11T00:00:00Z",
                            "turn": 1,
                            "text": "Discussed migration plan.",
                        },
                        {
                            "session_id": "sess-1",
                            "stream": "learnings",
                            "ts": "2026-02-11T00:01:00Z",
                            "turn": 1,
                            "text": "Use idempotent event keys for dedupe.",
                        },
                    ]
                )

            rough_file = rough_dir / "sess-1.md"
            learnings_file = learnings_dir / "sess-1.md"

            self.assertTrue(rough_file.exists())
            self.assertTrue(learnings_file.exists())
            self.assertIn("Discussed migration plan.", rough_file.read_text(encoding="utf-8"))
            self.assertIn("Use idempotent event keys for dedupe.", learnings_file.read_text(encoding="utf-8"))

    def test_skips_low_signal_transport_notes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            notes_dir = tmp_path / "memory_notes"
            rough_dir = notes_dir / "rough"
            learnings_dir = notes_dir / "learnings"

            with patch.object(notes, "NOTES_DIR", notes_dir), patch.object(notes, "ROUGH_NOTES_DIR", rough_dir), patch.object(notes, "LEARNINGS_NOTES_DIR", learnings_dir):
                notes.append_memory_notes(
                    [
                        {
                            "session_id": "sess-2",
                            "stream": "rough",
                            "ts": "2026-02-11T00:00:00Z",
                            "turn": 1,
                            "text": "A message.updated event was captured and stored for memory processing.",
                        },
                        {
                            "session_id": "sess-2",
                            "stream": "rough",
                            "ts": "2026-02-11T00:01:00Z",
                            "turn": 2,
                            "text": "User wants to persist meaningful travel preferences.",
                        },
                    ]
                )

            rough_file = rough_dir / "sess-2.md"
            contents = rough_file.read_text(encoding="utf-8")
            self.assertNotIn("message.updated event", contents)
            self.assertIn("User wants to persist meaningful travel preferences.", contents)

    def test_writes_fallback_generated_plain_language_notes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            notes_dir = tmp_path / "memory_notes"
            rough_dir = notes_dir / "rough"
            learnings_dir = notes_dir / "learnings"

            with patch.object(notes, "NOTES_DIR", notes_dir), patch.object(notes, "ROUGH_NOTES_DIR", rough_dir), patch.object(notes, "LEARNINGS_NOTES_DIR", learnings_dir):
                notes.append_memory_notes(
                    [
                        {
                            "session_id": "sess-3",
                            "stream": "rough",
                            "ts": "2026-02-12T00:00:00Z",
                            "turn": 1,
                            "text": "User asked: can you explain this in simple words?",
                            "fallback_generated": True,
                        },
                        {
                            "session_id": "sess-3",
                            "stream": "rough",
                            "ts": "2026-02-12T00:00:01Z",
                            "turn": 1,
                            "text": "Assistant replied: yes, here is the simple version.",
                            "fallback_generated": True,
                        },
                    ]
                )

            rough_file = rough_dir / "sess-3.md"
            contents = rough_file.read_text(encoding="utf-8")
            self.assertIn("can you explain this in simple words", contents.lower())
            self.assertIn("simple version", contents.lower())


if __name__ == "__main__":
    unittest.main()
