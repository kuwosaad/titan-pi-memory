import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

import requests
import yaml


class ExtractionAdapter:
    def chat(self, messages: List[Dict[str, str]], format_hint: Optional[str] = None, temperature: Optional[float] = None) -> str:
        raise NotImplementedError


class OllamaExtractionAdapter(ExtractionAdapter):
    def __init__(self, model: str, base_url: str, temperature: float = 0.1) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    def chat(self, messages: List[Dict[str, str]], format_hint: Optional[str] = None, temperature: Optional[float] = None) -> str:
        temp = temperature if temperature is not None else self.temperature
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temp,
        }
        if format_hint:
            payload["format"] = format_hint

        try:
            r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
            r.raise_for_status()
        except requests.HTTPError:
            if format_hint:
                payload.pop("format", None)
                r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
                r.raise_for_status()
            else:
                raise

        data = r.json()
        return data["message"]["content"]


class OpenRouterExtractionAdapter(ExtractionAdapter):
    def __init__(self, model: str, api_key: str, base_url: str = "https://openrouter.ai/api/v1", temperature: float = 0.1) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    def chat(self, messages: List[Dict[str, str]], format_hint: Optional[str] = None, temperature: Optional[float] = None) -> str:
        temp = temperature if temperature is not None else self.temperature
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        r = requests.post(f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=120)
        r.raise_for_status()

        data = r.json()
        return data["choices"][0]["message"]["content"]


class OpenAIExtractionAdapter(ExtractionAdapter):
    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1", temperature: float = 0.1) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    def chat(self, messages: List[Dict[str, str]], format_hint: Optional[str] = None, temperature: Optional[float] = None) -> str:
        temp = temperature if temperature is not None else self.temperature
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        r = requests.post(f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=120)
        r.raise_for_status()

        data = r.json()
        return data["choices"][0]["message"]["content"]


class GeminiExtractionAdapter(ExtractionAdapter):
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        temperature: float = 0.1,
        request_timeout: float = 300.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.request_timeout = request_timeout
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    def chat(self, messages: List[Dict[str, str]], format_hint: Optional[str] = None, temperature: Optional[float] = None) -> str:
        temp = temperature if temperature is not None else self.temperature
        system_lines = [msg["content"] for msg in messages if msg.get("role") == "system"]
        system_instruction = "\n".join(system_lines).strip()

        contents = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue
            content_role = "user" if role == "user" else "model"
            contents.append({"role": content_role, "parts": [{"text": msg.get("content", "")}]})

        payload: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if format_hint == "json":
            payload["generationConfig"] = {"responseMimeType": "application/json", "temperature": temp}
        else:
            payload["generationConfig"] = {"temperature": temp}

        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        r: Optional[requests.Response] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=self.request_timeout)
                r.raise_for_status()
                break
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                retriable = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status_code in {429, 500, 502, 503, 504}
                last_error = exc
                if attempt >= self.max_retries or not retriable:
                    raise
                time.sleep(self.retry_backoff_seconds * attempt)

        if r is None:
            raise RuntimeError(f"Gemini extraction request failed without response: {last_error}")

        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)


def load_extraction_config(config_path: str = "config/extraction_models.yaml") -> dict:
    override = os.getenv("TITAN_EXTRACTION_CONFIG_PATH")
    if override:
        config_file = Path(override).expanduser()
    else:
        base_dir = Path(__file__).resolve().parents[3]
        config_file = base_dir / config_path
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read_temperature(config: dict, fallback: float = 0.1) -> float:
    temperature = config.get("temperature")
    return temperature if isinstance(temperature, (int, float)) else fallback


def _resolve_api_key(config: dict, backend: str) -> str:
    env_name = config.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        value = os.getenv(env_name)
        if value:
            return value
        raise ValueError(f"Missing required env var {env_name} for extraction backend '{backend}'")

    api_key = config.get("api_key")
    if api_key:
        return str(api_key)

    raise ValueError(f"Missing API key config for extraction backend '{backend}'")


def get_extraction_adapter(config_path: str = "config/extraction_models.yaml") -> ExtractionAdapter:
    config = load_extraction_config(config_path=config_path)
    current = config.get("current", "ollama")

    if current == "ollama":
        ollama_cfg = config["ollama"]
        return OllamaExtractionAdapter(
            model=ollama_cfg["model"],
            base_url=ollama_cfg["base_url"],
            temperature=_read_temperature(ollama_cfg),
        )
    if current == "openrouter":
        openrouter_cfg = config["openrouter"]
        return OpenRouterExtractionAdapter(
            model=openrouter_cfg["model"],
            api_key=_resolve_api_key(openrouter_cfg, "openrouter"),
            base_url=openrouter_cfg.get("base_url", "https://openrouter.ai/api/v1"),
            temperature=_read_temperature(openrouter_cfg),
        )
    if current == "openai":
        openai_cfg = config["openai"]
        return OpenAIExtractionAdapter(
            model=openai_cfg["model"],
            api_key=_resolve_api_key(openai_cfg, "openai"),
            base_url=openai_cfg.get("base_url", "https://api.openai.com/v1"),
            temperature=_read_temperature(openai_cfg),
        )
    if current == "opencode_go":
        opencode_cfg = config["opencode_go"]
        return OpenAIExtractionAdapter(
            model=opencode_cfg.get("model", "deepseek-v4-flash"),
            api_key=_resolve_api_key(opencode_cfg, "opencode_go"),
            base_url=opencode_cfg.get("base_url", "https://opencode.ai/zen/go/v1"),
            temperature=_read_temperature(opencode_cfg),
        )
    if current == "gemini":
        gemini_cfg = config["gemini"]
        return GeminiExtractionAdapter(
            model=gemini_cfg["model"],
            api_key=_resolve_api_key(gemini_cfg, "gemini"),
            base_url=gemini_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta"),
            temperature=_read_temperature(gemini_cfg),
            request_timeout=float(gemini_cfg.get("request_timeout", 300.0) or 300.0),
            max_retries=int(gemini_cfg.get("max_retries", 3) or 3),
            retry_backoff_seconds=float(gemini_cfg.get("retry_backoff_seconds", 2.0) or 2.0),
        )
    raise ValueError(f"Unsupported extraction backend: {current}")


def get_extraction_adapter_with_config(config_path: str) -> ExtractionAdapter:
    return get_extraction_adapter(config_path=config_path)


__all__ = [
    "ExtractionAdapter",
    "OllamaExtractionAdapter",
    "OpenRouterExtractionAdapter",
    "OpenAIExtractionAdapter",
    "GeminiExtractionAdapter",
    "get_extraction_adapter",
    "get_extraction_adapter_with_config",
]
