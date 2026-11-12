import copy
import json
import unittest

from tools.benchmarks import native_step2_eval as evaluation


class NativeStepTwoEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.memory = {"id": "claim:0", "fixture_id": "claim", "source_agent": "codex", "scene_id": "scene",
                       "source_event_ids": ["event"], "text": "Cedar writes work without Wi-Fi."}
        self.case = {"required_sets": [["codex:claim"]], "acceptable_ids": ["codex:claim"]}
        self.params = {"max_items": 6}
        self.response = {"brief": "MEMORY BRIEF:\n1. Cedar writes work without Wi-Fi.",
                         "scene_refs": [{"source_agent": "codex", "scene_id": "scene"}]}
        self.scenes = {("codex", "scene"): {"extraction_user_text": self.memory["text"], "source_event_ids": ["event"]}}

    def test_independent_fixture_is_frozen(self):
        self.assertEqual(evaluation.sha(evaluation.FIXTURE), evaluation.FROZEN_SHA256)
        self.assertEqual(json.loads(evaluation.FIXTURE.read_text())["params"]["max_chars"], 700)

    def test_on_off_configuration_changes_only_enabled_and_preserves_original(self):
        settings = {"step2": {"enabled": False, "alpha": 1.0, "cluster_compression_enabled": True}, "retrieval_top_k": 8}
        original = copy.deepcopy(settings)
        on, off = evaluation.effective_config(settings, True), evaluation.effective_config(settings, False)
        self.assertEqual(evaluation.config_without_switch_sha(on), evaluation.config_without_switch_sha(off))
        self.assertEqual(settings, original)
        self.assertEqual(on["step2"]["alpha"], 1.0)

    def test_hidden_response_and_tension_quotes_cannot_earn_claim_credit(self):
        self.response["memories"] = [self.memory]
        score = evaluation.score_brief(self.case, self.response, [], self.scenes, self.params, 700)
        self.assertFalse(score["complete_required_evidence"])
        self.assertEqual(score["visible_ids"], [])

    def test_complete_native_renderer_claim_requires_durable_evidence(self):
        good = evaluation.score_brief(self.case, self.response, [self.memory], self.scenes, self.params, 700)
        self.assertTrue(good["complete_required_evidence"])
        self.scenes[("codex", "scene")]["source_event_ids"] = []
        self.assertFalse(evaluation.score_brief(self.case, self.response, [self.memory], self.scenes, self.params, 700)["complete_required_evidence"])

    def test_empty_minimal_sets_require_abstention(self):
        case = {"required_sets": [], "acceptable_ids": [], "expect_abstain": True}
        self.assertTrue(evaluation.score_brief(case, {"brief": "", "scene_refs": []}, [], {}, self.params, 700)["complete_required_evidence"])
        self.assertFalse(evaluation.score_brief(case, self.response, [self.memory], self.scenes, self.params, 700)["complete_required_evidence"])

    def test_full_claim_must_fit_real_brief_budget(self):
        response = {**self.response, "brief": "MEMORY BRIEF:\nCedar writes work..."}
        self.assertFalse(evaluation.score_brief(self.case, response, [self.memory], self.scenes, self.params, 700)["complete_required_evidence"])
        self.assertFalse(evaluation.score_brief(self.case, self.response, [self.memory], self.scenes, self.params, 10)["complete_required_evidence"])


if __name__ == "__main__":
    unittest.main()
