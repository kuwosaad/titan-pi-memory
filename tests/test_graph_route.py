import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


class GraphRouteTests(unittest.TestCase):
    def test_graph_endpoint_returns_html_response(self):
        expected_html = "<html><body>graph ok</body></html>"

        with patch("app.api.routes.build_graph", return_value=expected_html) as mock_build_graph:
            client = TestClient(app)
            response = client.get("/graph", params={"session_id": "abc"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertEqual(response.text, expected_html)
        mock_build_graph.assert_called_once_with(session_id="abc")

    def test_cluster_analysis_endpoint_delegates_to_cortex_analysis(self):
        expected = {"summary": "ok", "cluster_ids": [1, 2]}

        with patch("app.api.routes.analyze_memory_clusters", return_value=expected) as mock_analyze:
            client = TestClient(app)
            response = client.get(
                "/api/clusters/analyze",
                params={"cluster_ids": "1,2", "session_id": "abc", "question": "why", "detail_limit": 5},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        mock_analyze.assert_called_once_with(
            cluster_ids="1,2",
            session_id="abc",
            limit=0,
            question="why",
            detail_limit=5,
        )

    def test_clusters_endpoint_defaults_to_all_memories(self):
        expected = {"cluster_count": 3, "raw_memory_count": 2840}

        with patch("app.api.routes.inspect_memory_clusters", return_value=expected) as mock_inspect:
            client = TestClient(app)
            response = client.get("/api/clusters", params={"detail_limit": 7})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        mock_inspect.assert_called_once_with(
            session_id=None,
            limit=0,
            cluster_id=None,
            detail_limit=7,
        )


if __name__ == "__main__":
    unittest.main()
