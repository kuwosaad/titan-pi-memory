from typing import List
from pathlib import Path
import os
import requests
import yaml
import numpy as np


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
    return [np.array(item["embedding"], dtype=np.float32) for item in data["data"]]


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
