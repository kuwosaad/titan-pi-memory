"""Independent evaluator tests: evidence, audit, scope, and fixed fixture."""
import json
import unittest

from tools.benchmarks import scene_context_eval as evaluation


def memory(ident="fact:0", text="The chosen database is SQLite.", **overrides):
    return {"id": ident, "text": text, "source_agent": "eval", "scene_id": "scene",
            "source_event_ids": ["event"], "source_reliability": .95,
            "ts": "2026-04-10T10:00:00+00:00", "stream": "learnings", **overrides}


def result(*memories):
    packets = [{"records": [{"role": "seed", "memory": row}]} for row in memories]
    return {"rendered": "\n".join(json.dumps(packet) for packet in packets)}


class SceneContextEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.params = {"top_k": 3, "max_chars": 2400}
        self.case = {"id": "example", "required_sets": [["fact:0", "reason:0"]],
                     "acceptable_ids": ["fact:0", "reason:0"], "filters": {"sources": ["eval"], "mode": "both"}}

    def test_frozen_fixture_and_mechanical_budget_revision_are_explicit(self):
        self.assertEqual(evaluation.digest(evaluation.FIXTURE), evaluation.FROZEN_SHA256)
        fixture = json.loads(evaluation.FIXTURE.read_text())
        self.assertEqual(evaluation.validate_fixture(fixture), [])
        self.assertGreater(fixture["pre_outcome_budget_adjustment"]["mechanical_exemplar_chars"]["two_records"], 900)
        self.assertEqual(fixture["parameters"]["max_chars"], 2400)

    def test_scene_and_duplicate_pointers_do_not_supply_absent_claim_text(self):
        seed = memory(evidence_refs=[{"source_agent": "eval", "memory_id": "reason:0", "scene_id": "scene"}])
        reason = memory("reason:0", "This works without a network connection.")
        rows = {evaluation.identity(seed): seed, evaluation.identity(reason): reason}
        score = evaluation.score_result(self.case, result(seed), rows, self.params)
        self.assertFalse(score["complete_required_evidence"])
        self.assertEqual(score["missing_required_ids"], ["reason:0"])
        self.assertEqual(score["alternate_evidence_ref_count"], 1)

    def test_durable_scene_must_support_text_and_event_lineage(self):
        seed = memory()
        case = {**self.case, "required_sets": [["fact:0"]]}
        rows = {evaluation.identity(seed): seed}
        wrong_text = {("eval", "scene"): {"source_event_ids": ["event"], "extraction_user_text": "Unrelated."}}
        bad = evaluation.score_result(case, result(seed), rows, self.params, wrong_text)
        self.assertFalse(bad["complete_required_evidence"])
        valid = {("eval", "scene"): {"source_event_ids": ["event"], "extraction_user_text": seed["text"]}}
        self.assertTrue(evaluation.score_result(case, result(seed), rows, self.params, valid)["complete_required_evidence"])
        valid[("eval", "scene")]["source_event_ids"] = ["wrong-event"]
        self.assertFalse(evaluation.score_result(case, result(seed), rows, self.params, valid)["complete_required_evidence"])

    def test_minimal_alternative_evidence_sets_are_accepted(self):
        seed = memory()
        case = {**self.case, "required_sets": [["fact:0", "reason:0"], ["fact:0"]]}
        rows = {evaluation.identity(seed): seed}
        self.assertTrue(evaluation.score_result(case, result(seed), rows, self.params)["complete_required_evidence"])

    def test_unknown_requires_abstention_and_scope_is_separate(self):
        seed = memory(source_agent="peer")
        case = {**self.case, "required_sets": [[]], "acceptable_ids": []}
        rows = {evaluation.identity(seed): seed}
        score = evaluation.score_result(case, result(seed), rows, self.params)
        self.assertFalse(score["complete_required_evidence"])
        self.assertEqual(score["scope_violations"], ["fact:0"])
        self.assertTrue(evaluation.score_result(case, result(), rows, self.params)["complete_required_evidence"])

    def test_budget_and_invalid_text_cannot_pass_as_evidence(self):
        seed = memory()
        rows = {evaluation.identity(seed): seed}
        case = {**self.case, "required_sets": [["fact:0"]]}
        score = evaluation.score_result(case, result(seed), rows, {"top_k": 0, "max_chars": 10})
        self.assertFalse(score["complete_required_evidence"])
        self.assertFalse(score["budget_ok"])
        changed = memory(text="A different claim.")
        self.assertFalse(evaluation.score_result(case, result(changed), rows, self.params)["complete_required_evidence"])


if __name__ == "__main__":
    unittest.main()
