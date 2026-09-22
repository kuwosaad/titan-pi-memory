from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional
from pathlib import Path
import os
import requests
import yaml
import numpy as np


def _freeze_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_identity(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_identity(item) for item in value)
    return value


def _embedding_provider_identity(config: dict) -> tuple[Any, ...]:
    """Describe every configured input that can change a provider response."""

    backend = str(config.get("current", "ollama"))
    provider = dict(config.get(backend, {}) or {})
    model = provider.get("model")
    configured_dim = provider.get("dimensions", provider.get("dimension"))
    if backend == "ollama":
        base_url = str(provider.get("base_url") or "").rstrip("/")
        endpoint = (f"{base_url}/api/embed", f"{base_url}/api/embeddings")
    elif backend == "openai":
        # This is the endpoint used by ``openai_embed`` today. Keep the
        # configured provider block in the identity as well so a future
        # request option or endpoint switch cannot reuse an incompatible row.
        endpoint = "https://api.openai.com/v1/embeddings"
    else:
        endpoint = provider.get("base_url")
    return (
        backend,
        endpoint,
        model,
        _freeze_identity(provider),
        configured_dim,
    )


def _validated_vectors(
    values: Any,
    *,
    expected_count: int,
    configured_dim: Any,
) -> tuple[np.ndarray, ...]:
    if not isinstance(values, (list, tuple)) or len(values) != expected_count:
        raise ValueError("Embedding response count did not match the requested inputs")
    vectors: list[np.ndarray] = []
    realized_dim: Optional[int] = None
    for value in values:
        try:
            vector = np.asarray(value, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ValueError("Embedding response contained a non-numeric vector") from exc
        if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
            raise ValueError("Embedding response contained an invalid vector")
        if realized_dim is None:
            realized_dim = int(vector.size)
        elif vector.size != realized_dim:
            raise ValueError("Embedding response contained inconsistent vector dimensions")
        vectors.append(vector.copy())
    if configured_dim is not None and vectors:
        try:
            expected_dim = int(configured_dim)
        except (TypeError, ValueError) as exc:
            raise ValueError("Embedding configuration has an invalid dimension") from exc
        if realized_dim != expected_dim:
            raise ValueError("Embedding response dimension did not match configuration")
    return tuple(vectors)


@dataclass
class _QueryEmbeddingReuse:
    entries: dict[tuple[Any, ...], tuple[int, tuple[np.ndarray, ...]]] = field(default_factory=dict)

    def embed(
        self,
        texts: List[str],
        embed_fn: Callable[[List[str]], List[np.ndarray]],
    ) -> List[np.ndarray]:
        exact_inputs = tuple(texts)
        config_before = load_embedding_config()
        identity = _embedding_provider_identity(config_before)
        key = (identity, exact_inputs)
        cached = self.entries.get(key)
        if cached is not None:
            _dimension, vectors = cached
            return [vector.copy() for vector in vectors]

        values = embed_fn(list(exact_inputs))
        provider = dict(config_before.get(str(config_before.get("current", "ollama")), {}) or {})
        configured_dim = provider.get("dimensions", provider.get("dimension"))
        vectors = _validated_vectors(
            values,
            expected_count=len(exact_inputs),
            configured_dim=configured_dim,
        )

        # Configuration is process-global today. If it changes during the
        # provider call, return the valid result but do not make it reusable.
        try:
            identity_after = _embedding_provider_identity(load_embedding_config())
        except Exception:
            identity_after = None
        if identity_after == identity:
            realized_dim = int(vectors[0].size) if vectors else 0
            self.entries[key] = (realized_dim, vectors)
        return [vector.copy() for vector in vectors]


_QUERY_EMBEDDING_REUSE: ContextVar[Optional[_QueryEmbeddingReuse]] = ContextVar(
    "titan_query_embedding_reuse",
    default=None,
)


@contextmanager
def query_embedding_reuse_scope() -> Iterator[None]:
    """Reuse exact query/aspect vectors only within one federated request."""

    token = _QUERY_EMBEDDING_REUSE.set(_QueryEmbeddingReuse())
    try:
        yield
    finally:
        _QUERY_EMBEDDING_REUSE.reset(token)


def embed_query_inputs(
    texts: List[str],
    embed_fn: Callable[[List[str]], List[np.ndarray]],
) -> List[np.ndarray]:
    reuse = _QUERY_EMBEDDING_REUSE.get()
    if reuse is None:
        return embed_fn(texts)
    return reuse.embed(texts, embed_fn)


def load_embedding_config() -> dict:
    override = os.getenv("TITAN_EMBEDDING_CONFIG_PATH")
    if override:
        config_path = Path(override).expanduser()
    else:
        base_dir = Path(__file__).resolve().parents[2]
        config_path = base_dir / "config" / "embedding_models.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ollama_embed(texts: List[str], model: str, base_url: str) -> List[np.ndarray]:
    r = requests.post(
        f"{base_url}/api/embed",
        json={"model": model, "input": texts},
        timeout=120,
    )
    if r.status_code == 404:
        r = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "input": texts},
            timeout=120,
        )
    r.raise_for_status()
    data = r.json()

    if "embeddings" in data:
        emb = data["embeddings"]
        if emb and isinstance(emb[0], (int, float)):
            return [np.array(emb, dtype=np.float32)]
        return [np.array(v, dtype=np.float32) for v in emb]
    if "embedding" in data:
        return [np.array(data["embedding"], dtype=np.float32)]
    raise ValueError(f"Unexpected embedding response keys: {list(data.keys())}")


def openai_embed(texts: List[str], model: str, api_key: str) -> List[np.ndarray]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        json={"model": model, "input": texts},
        headers=headers,
        timeout=120,
    )
    r.raise_for_status()

    data = r.json()
    items = data.get("data")
    if not isinstance(items, list) or len(items) != len(texts):
        raise ValueError("OpenAI embedding response count did not match the requested inputs")
    indexed: list[tuple[int, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("OpenAI embedding response contained an invalid item")
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("OpenAI embedding response contained an invalid index")
        indexed.append((index, item.get("embedding")))
    indexed.sort(key=lambda pair: pair[0])
    if [index for index, _embedding in indexed] != list(range(len(texts))):
        raise ValueError("OpenAI embedding response indices did not match the requested inputs")
    return [np.array(embedding, dtype=np.float32) for _index, embedding in indexed]


def _resolve_api_key(config: dict, backend: str) -> str:
    env_name = config.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        value = os.getenv(env_name)
        if value:
            return value
        raise ValueError(f"Missing required env var {env_name} for embedding backend '{backend}'")
    raise ValueError(f"Missing api_key_env for embedding backend '{backend}'")


def embed(texts: List[str]) -> List[np.ndarray]:
    config = load_embedding_config()
    current = config.get("current", "ollama")

    if current == "disabled":
        raise ValueError("Embedding backend disabled by configuration")
    if current == "ollama":
        ollama_cfg = config["ollama"]
        return ollama_embed(
            texts,
            model=ollama_cfg["model"],
            base_url=ollama_cfg["base_url"]
        )
    elif current == "openai":
        openai_cfg = config["openai"]
        return openai_embed(
            texts,
            model=openai_cfg["model"],
            api_key=_resolve_api_key(openai_cfg, "openai"),
        )
    else:
        raise ValueError(f"Unsupported embedding backend: {current}")
