import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


class SidecarIsolationTests(unittest.TestCase):
    def test_trace_pipeline_works_even_if_graph_builder_is_broken(self):
        with patch("app.api.routes.build_graph", side_effect=RuntimeError("graph unavailable")):
            client = TestClient(app)
            response = client.post(
                "/api/trace",
                json={
                    "goal": "Capture save-only event",
                    "thoughts": "Testing sidecar isolation",
                    "tool_calls": [],
                    "outcome": "Saved",
                    "session_id": "sidecar-test",
                    "event_id": "evt-sidecar-1",
                    "save_intent": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "sidecar-test")
        self.assertIn(payload["memory_status"], {"skipped", "duplicate"})


if __name__ == "__main__":
    unittest.main()
