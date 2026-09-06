import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

import app.save_pipeline.pipeline as pipeline


class SaveIntegrityTests(unittest.TestCase):
    def test_save_dedup_flag_does_not_buffer_or_rewrite_extracted_memory(self):
        settings_path = Path(__file__).parents[1] / "config" / "settings.yaml"
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        settings["verification"] = {"enabled": False}
        settings["dedup"] = {"enabled": True}

        verifier = Mock()
        verifier.verify_memory.return_value = Mock(verified=False, confidence=0.0)
        extracted = {"text": "Use the event id for exact save deduplication.", "type": "decision"}
        with (
            patch.object(pipeline, "load_settings", return_value=settings),
            patch("app.retrieval_pipeline.config.load_settings", return_value=settings),
            patch.object(pipeline, "get_extraction_adapter", return_value=object()),
            patch.object(pipeline, "extract_atomic_memories", return_value=[extracted]),
            patch.object(pipeline, "embed", return_value=[]),
            patch.object(pipeline, "get_verifier", return_value=verifier),
            patch.object(pipeline, "append_memories") as append_memories,
            patch.object(pipeline, "append_memory_notes"),
        ):
            outcome = pipeline.run_memory_pipeline_outcome(
                session_id="save-integrity",
                turn=1,
                user_text="How should exact save deduplication work?",
                assistant_text="Use the event id for exact save deduplication.",
            )

        self.assertEqual([record["text"] for record in outcome["records"]], [extracted["text"]])
        append_memories.assert_called_once_with(outcome["records"])


if __name__ == "__main__":
    unittest.main()
