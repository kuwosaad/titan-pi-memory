"""Offline regression gates for conservative, evidence-preserving recall."""
from copy import deepcopy
from unittest.mock import patch

import numpy as np
import pytest

from app.retrieval_pipeline import retriever
from app.retrieval_pipeline.federated import FederatedRecall
from app.retrieval_pipeline.router import route_intent, route_query


class Repository:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.filters = []

    def query_candidates_with_text(self, query, filters):
        self.filters.append(filters)
        return deepcopy(self.rows)

    def query_candidates(self, filters):
        self.filters.append(filters)
        return deepcopy(self.rows)


def memory(memory_id, text, **kwargs):
    return {"id": memory_id, "text": text, "stream": "learnings", "type": "fact",
            "ts": "2026-01-01T00:00:00+00:00", "scene_id": f"scene-{memory_id}",
            "embedding": [1.0, 0.0], "source_reliability": 0.9, **kwargs}


@pytest.fixture
def settings(monkeypatch):
    settings = {
        "retrieval_top_k": 8, "retrieval_min_similarity": 0,
        "retrieval": {"min_reliability": 0},
        "retrieval_selection": {"enabled": True, "hybrid_candidates_enabled": True,
                                "lexical_coverage_weight": 0.2},
        "step2": {"enabled": False},
    }
    monkeypatch.setattr("app.retrieval_pipeline.config.load_settings", lambda: settings)
    monkeypatch.setattr("app.retrieval_pipeline.router.load_settings", lambda: settings)
    monkeypatch.setattr(retriever, "embed", lambda texts: [np.array([1., 0.]) for _ in texts])
    return settings


@pytest.mark.parametrize("query", [
    "How does the showroom work?", "What happened last time?", "Which rule did we implement?",
])
def test_router_searches_both_streams_by_default(settings, query):
    assert route_query(query)["mode"] == "both"
    assert route_query(query)["use_memory"] is True
    assert route_intent("the showroom") == "balanced"


@pytest.mark.parametrize("query", [
    "Why did the 'no memory' error happen?", "How does ignore memory work?",
    "What happened when I said don't use memory?", "No memory leak was found in the showroom",
    "Don't use memory management as the example", "What did 'Ignore memory for this answer' mean?",
    'Explain this command: "Ignore memory for this answer."',
    'Explain this quote: "What? Ignore memory for this answer."',
])
def test_factual_mentions_do_not_disable_retrieval(settings, query):
    assert route_query(query)["use_memory"] is True


@pytest.mark.parametrize("query", [
    "Don't use memory for this answer", "Please ignore memory", "No memory, fresh only",
    "For this answer, do not use memory", "fresh only", "Don't use memory to answer this.",
    "What is the cache setting? Ignore memory for this answer.",
    "Please answer from this message only; don't use memory.",
])
def test_direct_opt_out_still_disables_retrieval(settings, query):
    assert route_query(query)["use_memory"] is False


def test_exact_group_retains_all_local_evidence_and_does_not_mutate(settings):
    rows = [memory("old", "Use SQLite.", ts="2025-01-01T00:00:00+00:00"),
            memory("new", " Use SQLite. "), memory("other", "Use Postgres.")]
    repo = Repository(rows)
    hits = retriever.retrieve_memories("database", repository=repo)
    assert len(hits) == 2
    grouped = next(hit["memory"] for hit in hits if hit["memory"]["id"] == "new")
    assert {ref["memory_id"] for ref in grouped["evidence_refs"]} == {"old", "new"}
    assert repo.rows == rows


def test_same_scene_numbers_polarity_subject_and_scope_are_not_duplicates(settings):
    texts = ["Database is PostgreSQL.", "Cache is Redis.", "Pool size is 5.", "Pool size is 50.",
             "Use the cache.", "Do not use the cache.", "Alice likes SQLite.", "Bob likes SQLite."]
    rows = [memory(str(i), text, scene_id="same", source_event_ids=["same"]) for i, text in enumerate(texts)]
    rows += [memory("scope-a", "Use SQLite.", scope="alpha"), memory("scope-b", "Use SQLite.", scope="beta")]
    # Even identical embeddings are not evidence that the claims are duplicates.
    hits = retriever.retrieve_memories("database cache pool likes", top_k=10, repository=Repository(rows))
    assert {hit["memory"]["id"] for hit in hits} == {row["id"] for row in rows}


def test_nested_group_evidence_is_qualified_and_all_scenes_resolve(settings):
    repos = {
        "codex": Repository([memory("shared", "Use SQLite."), memory("old", " Use SQLite. ")]),
        "pi": Repository([memory("shared", "Use SQLite.", scene_id="pi-scene")]),
    }
    class Scenes:
        def get_scene_references(self, scene_ids):
            return [{"scene_id": sid, "scene_available": True} for sid in scene_ids]
    recall = FederatedRecall(active_agent="codex", memory_repositories=repos,
                             scene_repositories={source: Scenes() for source in repos})
    hits = recall.query_hits("SQLite", limit=3)
    assert len(hits) == 1
    refs = hits[0]["memory"]["evidence_refs"]
    assert {(ref["source_agent"], ref["memory_id"]) for ref in refs} == {
        ("codex", "shared"), ("codex", "old"), ("pi", "shared")}
    scenes = recall.scene_references([hits[0]["memory"]])
    assert {(ref["source_agent"], ref["scene_id"]) for ref in scenes} == {
        ("codex", "scene-shared"), ("codex", "scene-old"), ("pi", "pi-scene")}
    assert all(ref["scene_available"] for ref in scenes)


def test_federation_uses_selection_rank_including_valid_zero_score(settings):
    recall = FederatedRecall(active_agent="codex", memory_repositories={})
    hits = [{"source_agent": "pi", "score": 1, "selection_score": 0, "memory": memory("raw", "Raw winner")},
            {"source_agent": "pi", "score": .5, "selection_score": .8, "memory": memory("selected", "Selected winner")}]
    assert recall._merge_hits(hits, 1)[0]["memory"]["id"] == "selected"


def test_keyword_failure_retains_grouped_evidence(settings):
    repo = Repository([memory("old", "SQLite database"), memory("new", "SQLite database")])
    with patch.object(retriever, "embed", side_effect=ConnectionError("offline")):
        hits = retriever.retrieve_memories("SQLite", top_k=1, repository=repo)
    assert len(hits) == 1
    assert {ref["memory_id"] for ref in hits[0]["memory"]["evidence_refs"]} == {"old", "new"}
    assert hits[0]["retrieval_method"] == "keyword_fallback"


def test_explicit_stream_and_date_session_filters_are_preserved(settings):
    repo = Repository([memory("rough", "SQLite", stream="rough"), memory("learning", "SQLite")])
    hits = retriever.retrieve_memories("SQLite", mode="rough", session_id="session-1",
                                      date_from="2025-01-01", date_to="2026-02-01", repository=repo)
    assert [hit["memory"]["id"] for hit in hits] == ["rough"]
    assert len(repo.filters) == 2  # lexical and semantic candidates
    assert all(filters.mode == "rough" and filters.session_id == "session-1"
               and filters.date_from == "2025-01-01" and filters.date_to == "2026-02-01"
               for filters in repo.filters)


@pytest.mark.parametrize("detail", ["full", "compact"])
def test_local_public_response_preserves_qualified_group_and_scene_pointers(settings, monkeypatch, detail):
    from types import SimpleNamespace
    from app.save_pipeline import pipeline

    repo = Repository([memory("old", "SQLite database"), memory("new", "SQLite database")])
    hits = retriever.retrieve_memories("SQLite", repository=repo)
    monkeypatch.setattr(retriever, "retrieve_memories", lambda *args, **kwargs: hits)
    monkeypatch.setattr("app.runtime.context.get_runtime_context", lambda: SimpleNamespace(agent_name="codex"))
    monkeypatch.setattr("app.retrieval_pipeline.federated.get_federated_recall", lambda **kwargs:
                        FederatedRecall(active_agent="codex", memory_repositories={"codex": repo}))
    monkeypatch.setattr(pipeline, "load_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "get_scene_references", lambda ids: [
        {"scene_id": sid, "evidence_status": "complete"} for sid in ids])
    monkeypatch.setattr("app.patterns.retrieval.retrieve_accepted_patterns", lambda *args, **kwargs: [])
    response = pipeline.retrieve_memory_brief("SQLite", detail=detail, max_chars=300, max_items=2)
    assert response["count"] == 1
    assert {(ref["source_agent"], ref["memory_id"]) for ref in response["memories"][0]["evidence_refs"]} == {
        ("codex", "old"), ("codex", "new")}
    assert {ref["scene_id"] for ref in response["scene_refs"]} == {"scene-old", "scene-new"}
    assert len(response["brief"]) <= 300


def test_one_pointer_can_cover_two_facets_without_consuming_another_slot(settings):
    hits = [
        {"memory": memory("both", "Database and cache configuration"), "direct_similarity": .9,
         "base_score": .9, "aspect_scores": [.9, 1., 1.]},
        {"memory": memory("overall", "Useful overall context"), "direct_similarity": .95,
         "base_score": .95, "aspect_scores": [.95, .8, .8]},
        {"memory": memory("weaker", "Weaker repeated context"), "direct_similarity": .8,
         "base_score": .8, "aspect_scores": [.8, .9, .9]},
    ]
    selected = retriever._select_diverse_hits(hits, {}, 2, 3, settings["retrieval_selection"])
    assert [hit["memory"]["id"] for hit in selected] == ["both", "overall"]


def test_global_federation_preserves_facets_after_combining_namespaces(settings, monkeypatch):
    aspects = ["database and cache", "database", "cache"]
    database = {"memory": memory("database", "Database is PostgreSQL"), "query_aspects": aspects,
                "score": .98, "direct_similarity": .98, "base_score": .98,
                "aspect_scores": [.8, .98, .1]}
    database_other = {"memory": memory("database-other", "Database has a pool"), "query_aspects": aspects,
                      "score": .97, "direct_similarity": .97, "base_score": .97,
                      "aspect_scores": [.8, .97, .1]}
    cache = {"memory": memory("cache", "Cache is Redis"), "query_aspects": aspects,
             "score": .8, "direct_similarity": .8, "base_score": .8,
             "aspect_scores": [.7, .1, .8]}
    codex, pi = Repository([]), Repository([])
    monkeypatch.setattr(retriever, "retrieve_memories", lambda *args, **kwargs:
                        [database, database_other] if kwargs["repository"] is codex else [cache])
    recall = FederatedRecall(active_agent="codex", memory_repositories={"codex": codex, "pi": pi})
    assert {hit["memory"]["id"] for hit in recall.query_hits("database and cache", limit=2)} == {"database", "cache"}


def test_keyword_pointer_can_earn_remaining_multifacet_slot(settings):
    hits = [
        {"memory": memory("both", "Database and cache configuration"), "direct_similarity": .9,
         "base_score": .9, "aspect_scores": [.9, 1., 1.]},
        {"memory": memory("keyword", "Exact database cache location"), "retrieval_method": "keyword_fallback",
         "lexical_coverage": 1., "score": 1., "aspect_scores": []},
        {"memory": memory("weaker", "Repeated database context"), "direct_similarity": .8,
         "base_score": .8, "aspect_scores": [.8, .9, .9]},
    ]
    selected = retriever._select_diverse_hits(hits, {}, 2, 3, settings["retrieval_selection"])
    assert [hit["memory"]["id"] for hit in selected] == ["both", "keyword"]


def test_case_and_internal_whitespace_are_part_of_code_claim_identity(settings):
    texts = ["Set HTTP_PROXY=proxy.example.", "Set http_proxy=proxy.example.",
             "Use /Users/Alice/config.", "Use /users/alice/config.",
             "Set token='a b'.", "Set token='a  b'."]
    rows = [memory(str(index), text) for index, text in enumerate(texts)]
    hits = retriever.retrieve_memories("proxy config token", repository=Repository(rows))
    assert {hit["memory"]["text"] for hit in hits} == set(texts)
    assert all("evidence_refs" not in hit["memory"] for hit in hits)
    recall = FederatedRecall(active_agent="codex", memory_repositories={})
    grouped = recall._merge_hits(hits, len(hits))
    assert len(grouped) == len(texts)
