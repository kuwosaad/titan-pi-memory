"""Tests for evaluator honesty: fixture integrity, scoring, and isolation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.benchmarks import lean_memory_eval as evaluation


class LeanMemoryEvaluationTests(unittest.TestCase):
    def test_frozen_fixture_matches_preimplementation_digest(self):
        self.assertEqual(hashlib.sha256(evaluation.FIXTURE.read_bytes()).hexdigest(), evaluation.FROZEN_SHA256)
        self.assertEqual(evaluation.validate_fixture(json.loads(evaluation.FIXTURE.read_text())), [])

    def test_grouped_fact_gets_credit_only_with_resolvable_original_provenance(self):
        case = {"expected": ["first", "second"]}
        rows = {
            "first:0": {"source_agent": "alpha", "scene_id": "one", "source_event_ids": ["e1"]},
            "second:0": {"source_agent": "alpha", "scene_id": "two", "source_event_ids": ["e2"]},
        }
        response = {"memories": [{"id": "second:0", "source_agent": "alpha", "scene_id": "two", "source_event_ids": ["e2"],
                    "evidence_refs": [{"memory_id": "first:0", "source_agent": "alpha", "scene_id": "one"}]}],
                    "scene_refs": [{"scene_id": "one", "source_agent": "alpha"}, {"scene_id": "two", "source_agent": "alpha"}]}
        missing = evaluation.score_case(case, response, rows)
        self.assertEqual(missing["true_positive"], 2)
        self.assertEqual(missing["evidence_retained"], 1)
        self.assertFalse(missing["pass"])
        resolved = evaluation.score_case(case, response, rows, {("alpha", "one"): {"source_event_ids": ["e1"]}})
        self.assertEqual(resolved["evidence_retained"], 2)
        self.assertTrue(resolved["pass"])

    def test_off_topic_evidence_and_non_abstention_are_failures(self):
        response = {"memories": [{"id": "noise:0", "source_agent": "alpha"}], "scene_refs": []}
        scored = evaluation.score_case({"expected": []}, response, {})
        self.assertFalse(scored["pass"])
        self.assertFalse(scored["abstained"])
        self.assertEqual(scored["false_positive"], 1)

    def test_wrong_source_cannot_count_as_retained_evidence(self):
        rows = {"first:0": {"source_agent": "alpha", "scene_id": "one", "source_event_ids": ["e1"]}}
        response = {"memories": [{"id": "first:0", "source_agent": "beta", "scene_id": "one", "source_event_ids": ["e1"]}],
                    "scene_refs": [{"scene_id": "one", "source_agent": "beta"}]}
        self.assertEqual(evaluation.score_case({"expected": ["first"]}, response, rows)["evidence_retained"], 0)

    def test_event_identity_without_supporting_scene_text_is_not_retention(self):
        rows = {"first:0": {"text": "The database is PostgreSQL.", "source_agent": "alpha",
                            "scene_id": "one", "source_event_ids": ["e1"]}}
        response = {"memories": [{"id": "first:0", "source_agent": "alpha", "scene_id": "one", "source_event_ids": ["e1"]}],
                    "scene_refs": [{"scene_id": "one", "source_agent": "alpha"}]}
        resolved = {("alpha", "one"): {"source_event_ids": ["e1"], "extraction_user_text": "An unrelated source."}}
        scored = evaluation.score_case({"expected": ["first"]}, response, rows, resolved)
        self.assertEqual(scored["evidence_retained"], 0)
        self.assertFalse(scored["pass"])

    def test_schema_gate_rejects_unqualified_rows_and_bad_evidence(self):
        response = {"count": 2, "memories": [{"id": "one", "text": "fact", "evidence_refs": [{}]}],
                    "brief": "fact", "scene_refs": []}
        errors = evaluation.validate_response(response)
        self.assertIn("count differs from memories length", errors)
        self.assertIn("memory requires source-qualified identity", errors)
        self.assertIn("evidence reference requires memory and scene identities", errors)

    def test_subprocess_runs_public_path_offline_in_disposable_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            source = evaluation.ROOT / "app/save_pipeline/pipeline.py"
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            completed = subprocess.run([sys.executable, str(Path(evaluation.__file__)),
                "--baseline", "", "--ablations", "default", "--repeats", "1", "--output", str(output)],
                text=True, capture_output=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            report = json.loads(output.read_text())
            result = report["results"][0]
            self.assertEqual(result["metrics"]["query_count"], 13)
            self.assertEqual(result["metrics"]["capture_count"], 5)
            self.assertEqual(result["isolation"]["blocked_network_attempts"], [])
            self.assertTrue(result["isolation"]["bytecode_disabled"])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
