import unittest

from tests.test_titan_memory_workflow_skill import WORKFLOW_PATHS


class RetrievalSkillProductContractTests(unittest.TestCase):
    def test_each_adapter_keeps_retrieval_and_product_workflows(self):
        required = (
            "Open each source event you will cite",
            "Before declaring a fact unknown, search for its original conversation text.",
            "identify every arithmetic operand and open evidence supporting",
            "significant work, store one concise trace packet containing the durable decision,",
            "`titan-memory_store_trace_packet`: preserve a significant distilled outcome.",
            "Use the pattern workflow for pattern lifecycle work, the cluster workflow for graph",
            "synthesis, the doctor workflow for runtime or setup failures, and memory-sync for",
        )
        canonical = WORKFLOW_PATHS[0].read_text(encoding="utf-8")
        for path in WORKFLOW_PATHS:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text, canonical)
                for instruction in required:
                    self.assertIn(instruction, text)


if __name__ == "__main__":
    unittest.main()
