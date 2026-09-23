from unittest.mock import Mock, patch

import pytest

from app.embedding.embedder import embed


def test_openai_embedding_response_is_restored_to_input_order(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]
    }
    config = {
        "current": "openai",
        "openai": {
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "model": "text-embedding-3-small",
        },
    }

    with patch("app.embedding.embedder.load_embedding_config", return_value=config), patch(
        "app.embedding.embedder.requests.post", return_value=response
    ) as post:
        vectors = embed(["first", "second"])

    assert [vector.tolist() for vector in vectors] == [[1.0, 0.0], [0.0, 1.0]]
    assert post.call_args.kwargs["json"] == {
        "model": "text-embedding-3-small",
        "input": ["first", "second"],
    }


def test_openai_embedding_response_rejects_duplicate_indices(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {"index": 0, "embedding": [1.0, 0.0]},
            {"index": 0, "embedding": [0.0, 1.0]},
        ]
    }
    config = {
        "current": "openai",
        "openai": {
            "api_key_env": "OPENAI_API_KEY",
            "model": "text-embedding-3-small",
        },
    }

    with patch("app.embedding.embedder.load_embedding_config", return_value=config), patch(
        "app.embedding.embedder.requests.post", return_value=response
    ), pytest.raises(ValueError, match="indices"):
        embed(["first", "second"])
