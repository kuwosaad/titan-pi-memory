import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


class SceneRouteTests(unittest.TestCase):
    def test_scene_endpoint_returns_scene_payload(self):
        with patch(
            "app.api.routes.get_scene_context",
            return_value={"scene": {"scene_id": "s1:scene:e-1", "messages": [{"role": "user", "content": "hello"}]}},
        ) as mock_get_scene_context:
            client = TestClient(app)
            response = client.get("/api/scenes/s1:scene:e-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scene"]["scene_id"], "s1:scene:e-1")
        mock_get_scene_context.assert_called_once_with("s1:scene:e-1")

    def test_scene_endpoint_returns_404_for_missing_scene(self):
        with patch(
            "app.api.routes.get_scene_context",
            return_value={"error": "scene not found", "scene_id": "missing-scene"},
        ) as mock_get_scene_context:
            client = TestClient(app)
            response = client.get("/api/scenes/missing-scene")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "scene not found", "scene_id": "missing-scene"})
        mock_get_scene_context.assert_called_once_with("missing-scene")


if __name__ == "__main__":
    unittest.main()
