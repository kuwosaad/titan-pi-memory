import copy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import yaml

from app.retrieval_pipeline import retriever


class _FrozenRepository:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.calls = []

    def query_candidates_with_text(self, query, filters):
        self.calls.append(("lexical", query))
        return copy.deepcopy(self.rows)

    def query_candidates(self, filters):
        self.calls.append(("semantic", None))
        return copy.deepcopy(self.rows)

    def update_lnn_state(self, *args, **kwargs):
        raise AssertionError("retrieval attempted to mutate LNN state")

    def batch_update_weights(self, *args, **kwargs):
        raise AssertionError("retrieval attempted to mutate LNN weights")


def _memory(memory_id, text, stream, embedding):
    return {
        "id": memory_id,
        "text": text,
        "stream": stream,
        "type": "fact",
        "session_id": "cleanup-fixture",
        "scene_id": memory_id + ":scene",
        "ts": "2026-01-01T00:00:00+00:00",
        "source_reliability": 0.9,
        "verification_status": "unverified",
        "fallback_generated": False,
        "embedding": list(embedding),
    }


def _fixture_rows():
    return [
        _memory("exact", "T3 Code uses SQLite for the memory store.", "learnings", (1.0, 0.0)),
        _memory("paraphrase", "The memory database is SQLite and local.", "learnings", (0.98, 0.20)),
        _memory("support", "We completed the T3 Code integration.", "rough", (0.92, 0.39)),
        _memory("opposition", "T3 Code uses Postgres, not SQLite.", "learnings", (0.97, 0.24)),
        _memory("aspect-a", "Saad prefers concise explanations.", "learnings", (0.8, 0.6)),
        _memory("aspect-b", "Saad is frustrated by slow delegation.", "learnings", (0.6, 0.8)),
        _memory("unrelated", "Purple giraffe quantum bakery underwater violin.", "rough", (0.0, 1.0)),
    ]


def test_cleanup_default_yaml_preserves_baseline_hits_without_mutation():
    settings = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text(encoding="utf-8")
    )
    rows = _fixture_rows()
    queries = {
        "exact": ("Which database backs the local memory store for T3 Code?", np.array([1.0, 0.0], dtype=np.float32), {"exact"}),
        "paraphrase": ("What database stores memory locally?", np.array([0.98, 0.20], dtype=np.float32), {"paraphrase"}),
        "multi_aspect": (
            "How does Saad prefer explanations and what frustrates him in collaboration?",
            np.array([0.7, 0.7], dtype=np.float32),
            # Both baseline and cleanup miss aspect-b with these synthetic vectors.
            # Preserve the observed baseline; this is not a full facet-recall claim.
            {"aspect-a"},
        ),
        "opposition": (
            "Does implementation use SQLite or Postgres for persistence?",
            np.array([1.0, 0.0], dtype=np.float32),
            {"exact", "opposition"},
        ),
    }

    for query, vector, expected in queries.values():
        repository = _FrozenRepository(rows)
        repository_before = copy.deepcopy(repository.rows)

        def fake_embed(texts, vector=vector):
            return [vector.copy() for _ in texts]

        with patch("app.retrieval_pipeline.config.load_settings", return_value=settings), patch.object(
            retriever, "embed", side_effect=fake_embed
        ):
            result = retriever.retrieve_memories(
                query=query,
                top_k=3,
                min_similarity=0.0,
                mode="both",
                repository=repository,
                persist_lnn_state=True,
            )
            repeated = retriever.retrieve_memories(
                query=query,
                top_k=3,
                min_similarity=0.0,
                mode="both",
                repository=repository,
                persist_lnn_state=True,
            )

        ids = {item["memory"]["id"] for item in result}
        repeated_ids = {item["memory"]["id"] for item in repeated}
        assert expected <= ids
        assert repeated_ids == ids
        assert repository.rows == repository_before


def test_cleanup_default_yaml_abstains_on_unrelated_candidates():
    settings = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text(encoding="utf-8")
    )
    rows = [
        _memory("noise-a", "A project has a local configuration file.", "rough", (1.0, 0.0)),
        _memory("noise-b", "A project uses a durable storage layer.", "learnings", (1.0, 0.0)),
    ]
    repository = _FrozenRepository(rows)
    repository_before = copy.deepcopy(repository.rows)
    vector = np.array([0.0, 1.0], dtype=np.float32)

    with patch("app.retrieval_pipeline.config.load_settings", return_value=settings), patch.object(
        retriever, "embed", side_effect=lambda texts: [vector.copy() for _ in texts]
    ):
        result = retriever.retrieve_memories(
            query="purple giraffe quantum bakery underwater violin",
            top_k=3,
            min_similarity=0.0,
            mode="both",
            repository=repository,
            persist_lnn_state=True,
        )

    assert result == []
    assert repository.rows == repository_before
