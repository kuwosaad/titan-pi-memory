import unittest
from unittest.mock import patch

from app.save_pipeline.pipeline import retrieve_memory_brief


class RetrieveOptOutTests(unittest.TestCase):
    @patch("app.retrieval_pipeline.retriever.retrieve_memories")
    @patch("app.save_pipeline.pipeline.route_query")
    def test_route_opt_out_skips_retrieval(self, mock_route_query, mock_retrieve_memories):
        mock_route_query.return_value = {
            "schema_version": "v2",
            "use_memory": False,
            "mode": "none",
            "top_k": 0,
            "reason": "Memory disabled: user requested fresh context only.",
            "summary_mode": None,
        }

        result = retrieve_memory_brief(query="don't use memory for this answer")

        mock_retrieve_memories.assert_not_called()
        self.assertEqual(result["mode"], "none")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["memories"], [])
        self.assertIn("disabled", result["brief"].lower())


if __name__ == "__main__":
    unittest.main()
