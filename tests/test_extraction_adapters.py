import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.save_pipeline.extraction.adapters import GeminiExtractionAdapter, get_dedup_adapter, get_extraction_adapter


class GeminiExtractionAdapterTests(unittest.TestCase):
    def test_opencode_go_uses_openai_compatible_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "extraction_models.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    current: opencode_go
                    opencode_go:
                      api_key_env: OPENCODE_GO_API_KEY
                      base_url: https://opencode.ai/zen/go/v1
                      model: deepseek-v4-flash
                      temperature: 0.1
                    """
                ).strip(),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "sk-test"}, clear=False):
                adapter = get_extraction_adapter(str(config_path))

        self.assertEqual(adapter.model, "deepseek-v4-flash")
        self.assertEqual(adapter.base_url, "https://opencode.ai/zen/go/v1")
        self.assertEqual(adapter.api_key, "sk-test")

    def test_dedup_can_use_opencode_go_backend(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "extraction_models.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    current: opencode_go
                    opencode_go:
                      api_key_env: OPENCODE_GO_API_KEY
                      base_url: https://opencode.ai/zen/go/v1
                      model: deepseek-v4-flash
                    dedup:
                      enabled: true
                      backend: opencode_go
                      api_key_env: OPENCODE_GO_API_KEY
                      base_url: https://opencode.ai/zen/go/v1
                      model: deepseek-v4-flash
                    """
                ).strip(),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "sk-test"}, clear=False):
                adapter = get_dedup_adapter(str(config_path))

        self.assertEqual(adapter.model, "deepseek-v4-flash")
        self.assertEqual(adapter.base_url, "https://opencode.ai/zen/go/v1")

    def test_gemini_payload_uses_rest_api_camel_case_fields(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}

        adapter = GeminiExtractionAdapter(model="gemini-test", api_key="key", max_retries=1)
        messages = [
            {"role": "system", "content": "Extract JSON."},
            {"role": "user", "content": "Remember durable facts."},
        ]

        with patch("app.save_pipeline.extraction.adapters.requests.post", return_value=response) as mock_post:
            adapter.chat(messages, format_hint="json", temperature=0.2)

        payload = mock_post.call_args.kwargs["json"]
        self.assertIn("systemInstruction", payload)
        self.assertIn("generationConfig", payload)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertNotIn("system_instruction", payload)
        self.assertNotIn("generation_config", payload)


if __name__ == "__main__":
    unittest.main()
