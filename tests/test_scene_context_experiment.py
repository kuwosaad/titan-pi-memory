"""Invariant checks for the opt-in, pure scene-context benchmark prototype."""
from copy import deepcopy
import json
import math

import pytest

from app.retrieval_pipeline.scene_context_experiment import build_scene_context_result


def memory(memory_id, scene="scene", source="codex", text=None, **metadata):
    return {"source_agent": source, "id": memory_id, "scene_id": scene,
            "text": text or f"Fact {memory_id}.", **metadata}


def hit(memory_id, score=.6, **kwargs):
    return {"memory": memory(memory_id, **kwargs), "score": score}


def run(seeds, siblings=(), vectors=None, **kwargs):
    if vectors is None:
        vectors = {(record["source_agent"], record["id"]): [1., 0.]
                   for record in [*(seed["memory"] for seed in seeds), *siblings]}
    return build_scene_context_result(seeds, siblings, query_vector=[1., 0.], vectors=vectors,
                                      top_k=kwargs.pop("top_k", 20), max_chars=kwargs.pop("max_chars", 20000), **kwargs)


def test_no_admitted_seed_cannot_be_rescued_by_perfect_context():
    result = run([], [memory("answer")])
    assert result["hits"] == result["packets"] == result["inspected_sibling_ids"] == []
    assert result["rendered"] == ""
    assert result["stats"]["provider_calls"] == result["stats"]["embedding_calls"] == 0


def test_source_qualified_scene_is_a_hard_one_hop_boundary():
    seeds = [hit("seed")]
    siblings = [memory("same-scene"), memory("foreign", source="pi"), memory("other-scene", scene="next")]
    result = run(seeds, siblings)
    assert result["inspected_sibling_ids"] == [("codex", "same-scene")]
    assert result["context_ids"] == [("codex", "same-scene")]
    moved = run(seeds, [{**siblings[0], "scene_id": "changed"}])
    assert moved["context_ids"] == []


def test_hard_scene_and_sibling_caps_with_no_admitted_fact_duplication():
    seeds = [hit(f"seed-{i}", scene=f"scene-{i}") for i in range(5)]
    siblings = [memory(f"context-{i}-{j}", scene=f"scene-{i}") for i in range(5) for j in range(4)]
    siblings.append(seeds[0]["memory"])
    result = run(seeds, siblings)
    assert result["stats"]["seed_scene_count"] == 3
    assert len(result["context_ids"]) == 6
    assert len(result["selected_ids"]) == len(set(result["selected_ids"])) == 11
    assert all(identity not in result["context_ids"] for identity in result["candidate_ids"])


@pytest.mark.parametrize("bad", [None, [], [0., 0.], [float("nan"), 0.], [1., 0., 0.], "bad"])
def test_invalid_missing_or_incompatible_vectors_cannot_create_context(bad):
    vectors = {("codex", "seed"): [1., 0.], ("codex", "context"): bad}
    result = run([hit("seed")], [memory("context")], vectors=vectors)
    assert result["context_ids"] == []
    assert result["selected_ids"] == [("codex", "seed")]


def test_capped_max_connection_does_not_accumulate_duplicate_votes():
    seed = hit("seed", score=.6)
    one = run([seed], [memory("context")], arm="contextual_selection")
    repeated = run([seed], [memory("context")] * 50, arm="contextual_selection")
    crowded = run([seed], [memory(f"context-{i}") for i in range(50)], arm="contextual_selection")
    assert one["packets"][0]["score"] == pytest.approx(.7)
    assert repeated["packets"][0]["score"] == crowded["packets"][0]["score"] == one["packets"][0]["score"]
    assert len(repeated["context_ids"]) == 1
    assert len(crowded["context_ids"]) == 2


def test_arms_inspect_and_construct_same_context_but_only_contextual_score_changes():
    seeds = [hit("seed", score=.6), hit("other", score=.65, scene="other-scene")]
    siblings = [memory("context")]
    expansion = run(seeds, siblings)
    contextual = run(seeds, siblings, arm="contextual_selection")
    assert expansion["inspected_sibling_ids"] == contextual["inspected_sibling_ids"]
    expansion_records = {tuple(packet["seed_id"]): packet["records"] for packet in expansion["packets"]}
    contextual_records = {tuple(packet["seed_id"]): packet["records"] for packet in contextual["packets"]}
    assert expansion_records == contextual_records
    assert expansion["packets"][0]["seed_id"] == ["codex", "other"]
    assert contextual["packets"][0]["seed_id"] == ["codex", "seed"]


def test_seed_only_fallback_preserves_relevant_seed_and_drops_hidden_context_bonus():
    result = run([hit("seed")], [memory("context")], arm="contextual_selection", top_k=1)
    assert result["selected_ids"] == [("codex", "seed")]
    assert result["context_ids"] == []
    assert result["packets"][0]["score"] == .6
    assert "Fact context." not in result["rendered"]
    assert "Fact seed." in result["rendered"]


def test_complete_packet_and_every_public_metadata_character_are_charged():
    seed = hit("seed", source_event_ids=["event-1"], evidence_refs=[
        {"source_agent": "pi", "memory_id": "alt", "scene_id": "alternate-scene"}])
    sibling = memory("context", ts="2026-01-01", source_event_ids=["event-2"])
    generous = run([seed], [sibling])
    assert len(generous["rendered"]) == generous["stats"]["rendered_chars"]
    assert generous["stats"]["selected_record_count"] == 2
    exact = run([seed], [sibling], max_chars=len(generous["rendered"]))
    assert exact["rendered"] == generous["rendered"]
    too_small = run([seed], [sibling], max_chars=len(generous["rendered"]) - 1)
    assert too_small["context_ids"] == []
    assert len(too_small["rendered"]) <= len(generous["rendered"]) - 1
    packet = json.loads(generous["rendered"])
    assert packet["records"][0]["memory"]["evidence_refs"][0]["scene_id"] == "alternate-scene"
    assert packet["records"][1]["memory"]["source_event_ids"] == ["event-2"]
    assert packet["linkage"] == "scene_context_not_causation"


def test_optional_context_vectors_share_bonus_cap_without_changing_candidate_set():
    vectors = {("codex", "seed"): [.6, .8], ("codex", "context"): [1., 0.]}
    plain = run([hit("seed")], [memory("context")], vectors=vectors, arm="contextual_selection")
    supplied = run([hit("seed")], [memory("context")], vectors=vectors, arm="contextual_selection",
                   contextual_vectors={("codex", "seed"): [1., 0.]})
    assert plain["context_ids"] == supplied["context_ids"]
    assert plain["packets"][0]["score"] == pytest.approx(.66)
    assert supplied["packets"][0]["score"] == pytest.approx(.7)


def test_removed_reason_does_not_admit_unrelated_plausible_substitute():
    seed = hit("decision", text="We selected the database.")
    sibling = memory("reason", text="The choice was required by the offline constraint.")
    unrelated = memory("plausible", text="Another project selected it to reduce cost.")
    vectors = {("codex", "decision"): [1., 0.], ("codex", "reason"): [1., 0.],
               ("codex", "plausible"): [0., 1.]}
    with_reason = run([seed], [sibling, unrelated], vectors=vectors)
    without_reason = run([seed], [unrelated], vectors=vectors)
    assert with_reason["context_ids"] == [("codex", "reason")]
    assert without_reason["context_ids"] == []
    assert "reduce cost" not in without_reason["rendered"]


def test_no_input_mutation_or_automatic_newer_wins():
    seeds = [hit("old", ts="2025-01-01"), hit("new", ts="2026-01-01")]
    siblings = [memory("context", evidence_refs=[{"memory_id": "support"}])]
    before = deepcopy((seeds, siblings))
    result = run(seeds, siblings)
    assert result["packets"][0]["seed_id"] == ["codex", "old"]
    assert (seeds, siblings) == before
    assert result["hits"][1]["memory"]["evidence_refs"][0]["source_agent"] == "codex"


def test_source_qualification_and_conflicting_identity_fail_explicitly():
    with pytest.raises(ValueError, match="source_agent"):
        run([{"memory": {"id": "missing-source", "text": "Fact"}}], vectors={})
    with pytest.raises(ValueError, match="Conflicting"):
        run([hit("seed")], [memory("same"), memory("same", text="Different claim")])
    with pytest.raises(ValueError, match="unique"):
        run([hit("seed"), hit("seed")])


def test_zero_budgets_and_cap_validation():
    assert run([hit("seed")], top_k=0)["rendered"] == ""
    assert run([hit("seed")], max_chars=0)["rendered"] == ""
    with pytest.raises(ValueError, match="between"):
        run([hit("seed")], relationship_bonus_cap=.11)
    assert math.isfinite(run([hit("seed")])["packets"][0]["score"])
