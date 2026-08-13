import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


client = TestClient(app)


class PatternGraphRouteTests(unittest.TestCase):
    def test_pattern_graph_endpoint_returns_html_response(self):
        expected_html = "<html><body>pattern graph ok</body></html>"
        with patch("app.api.routes.build_pattern_graph", return_value=expected_html) as mock_build_graph:
            response = client.get("/pattern-graph", params={"limit": 25})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/html")
        self.assertEqual(response.text, expected_html)
        mock_build_graph.assert_called_once_with(limit=25)

    def test_patterns_graph_api_route_is_not_captured_as_pattern_id(self):
        payload = {"nodes": [], "links": [], "count": 0, "edge_count": 0}
        with patch("app.api.routes.build_pattern_graph_data", return_value=payload) as mock_graph_data, patch(
            "app.api.routes.get_pattern_impl",
            return_value={"error": "wrong route"},
        ) as mock_get_pattern:
            response = client.get("/api/patterns/graph", params={"limit": 10})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        mock_graph_data.assert_called_once_with(limit=10)
        mock_get_pattern.assert_not_called()


if __name__ == "__main__":
    unittest.main()
