import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


client = TestClient(app)


class PatternBundleRouteTests(unittest.TestCase):
    def test_bundle_export_route_is_not_captured_as_pattern_id(self):
        payload = {"schema": "titan.pattern_bundle.v1", "patterns": [], "evidence": []}
        with patch("app.api.routes.export_pattern_bundle", return_value=payload) as mock_export, patch(
            "app.api.routes.get_pattern_impl",
            return_value={"error": "wrong route"},
        ) as mock_get_pattern:
            response = client.post("/api/patterns/bundle/export", json={"statuses": ["accepted"], "limit": 25})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        mock_export.assert_called_once()
        self.assertEqual(mock_export.call_args.kwargs["statuses"], ["accepted"])
        self.assertEqual(mock_export.call_args.kwargs["limit"], 25)
        mock_get_pattern.assert_not_called()

    def test_bundle_export_returns_400_for_bad_status(self):
        response = client.post("/api/patterns/bundle/export", json={"statuses": ["bad-status"]})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid pattern status", response.json()["error"])

    def test_bundle_import_returns_400_for_bad_schema(self):
        response = client.post("/api/patterns/bundle/import", json={"bundle": {"schema": "bad"}})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported pattern bundle schema", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
