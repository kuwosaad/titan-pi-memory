import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import app.save_pipeline.pipeline as pipeline


class SaveIntegrityTests(unittest.TestCase):
    def test_save_dedup_flag_does_not_buffer_or_rewrite_extracted_memory(self):
        settings_path = Path(__file__).parents[1] / "config" / "settings.yaml"
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        settings["verification"] = {"enabled": False}
        settings["dedup"] = {"enabled": True}

        extracted = {"text": "Use the event id for exact save deduplication.", "type": "decision"}
        with (
            patch.object(pipeline, "load_settings", return_value=settings),
            patch("app.retrieval_pipeline.config.load_settings", return_value=settings),
            patch.object(pipeline, "get_extraction_adapter", return_value=object()),
            patch.object(pipeline, "extract_atomic_memories", return_value=[extracted]),
            patch.object(pipeline, "embed", return_value=[]),
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
        self.assertEqual(outcome["records"][0]["verification_status"], "unverified")
        append_memories.assert_called_once_with(outcome["records"])

    def test_function_name_cannot_verify_a_false_behavior_claim(self):
        claim = "The function cosine_similarity encrypts memories with AES-256."
        extracted = {"text": claim, "type": "fact", "source": "assistant", "reliability": 0.3}
        with (
            patch("app.retrieval_pipeline.config.load_settings", return_value={
                "verification": {"enabled": True},
            }),
            patch.object(pipeline, "get_extraction_adapter", return_value=object()),
            patch.object(pipeline, "extract_atomic_memories", return_value=[extracted]),
            patch.object(pipeline, "embed", return_value=[]),
            patch.object(pipeline, "append_memories"),
            patch.object(pipeline, "append_memory_notes"),
        ):
            result = pipeline.run_memory_pipeline_outcome(
                session_id="claim-evidence", turn=1,
                user_text="How does the cosine_similarity function work?",
                assistant_text=claim,
            )

        record = result["records"][0]
        self.assertEqual(record["text"], claim)
        self.assertEqual(record["verification_status"], "unverified")
        self.assertEqual(record["source_reliability"], 0.3)


if __name__ == "__main__":
    unittest.main()
