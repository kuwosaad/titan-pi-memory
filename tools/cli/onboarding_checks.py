"""Bounded provider checks for onboarding; never read or write memory data."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CLOUD = {
    'openai': ('https://api.openai.com/v1', 'OPENAI_API_KEY'),
    'openrouter': ('https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY'),
    'opencode_go': ('https://opencode.ai/zen/go/v1', 'OPENCODE_GO_API_KEY'),
    'gemini': ('https://generativelanguage.googleapis.com/v1beta', 'GEMINI_API_KEY'),
}
_TIMEOUT = 15


def _configs():
    result = {}
    for kind in ('extraction', 'embedding'):
        with (_ROOT / 'config' / (kind + '_models.yaml')).open(encoding='utf-8') as stream:
            result[kind] = yaml.safe_load(stream) or {}
    return result


def _error(kind, status=None):
    label = kind.capitalize()
    if status in (401, 403):
        return f'{label}: authentication failed or access is denied. Check the API key and provider permissions.'
    if status == 404:
        return f'{label}: the provider could not find this model or endpoint. Check the model name and access.'
    if status == 429:
        return f'{label}: the provider rate limit or quota was reached. Check your quota and try again.'
    if status:
        return f'{label}: provider returned HTTP {status}. Check model access and try again.'
    return f'{label}: could not reach the provider. Check your connection and try again.'


def _local_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            return None
        # Reject malformed ports before requests sees them.
        parsed.port
        return value.rstrip('/')
    except (TypeError, ValueError):
        return None


def check_configuration(data, configs=None, env=None):
    """Check selected provider access. Return only safe, actionable diagnostics.

    Cloud probes make a small billable request. Ollama checks its installed model list.
    Caller should validate the shape of the selection before calling this function.
    """
    configs = _configs() if configs is None else configs
    env = os.environ if env is None else env
    errors, warnings = [], []
    keys = data.get('keys', {})
    for kind in ('extraction', 'embedding'):
        selection = data.get(kind, {})
        backend, model = selection.get('backend'), selection.get('model', '')
        block = configs.get(kind, {}).get(backend, {}) or {}
        if not isinstance(model, str) or not model.strip():
            errors.append(f'{kind.capitalize()}: choose a model.')
            continue
        model = model.strip()
        try:
            if backend == 'ollama':
                base = _local_url(block.get('base_url', 'http://localhost:11434'))
                if not base:
                    errors.append(f'{kind.capitalize()}: Ollama must use a local HTTP address for this check.')
                    continue
                response = requests.get(base + '/api/tags', timeout=_TIMEOUT, allow_redirects=False)
                if response.status_code != 200:
                    errors.append(_error(kind, response.status_code))
                    continue
                models = response.json().get('models', [])
                names = {item.get('name', '') for item in models if isinstance(item, dict)}
                # Ollama displays untagged models with the implicit :latest tag.
                if model not in names and (':' in model or model + ':latest' not in names):
                    errors.append(f'{kind.capitalize()}: Ollama model is not installed. Run ollama pull for the selected model, then retry.')
                continue
            if backend not in _CLOUD or (kind == 'embedding' and backend != 'openai'):
                errors.append(f'{kind.capitalize()}: unsupported provider.')
                continue
            base, default_key = _CLOUD[backend]
            configured_base = block.get('base_url', base).rstrip('/')
            if configured_base != base:
                errors.append(f'{kind.capitalize()}: custom provider endpoints cannot be checked safely in onboarding.')
                continue
            key_name = block.get('api_key_env', default_key)
            key = (keys.get(key_name) or env.get(key_name) or '').strip()
            if not key:
                errors.append(f'{kind.capitalize()}: {default_key} is required.')
                continue
            if backend == 'gemini':
                url = base + '/models/' + quote(model, safe='') + ':generateContent'
                headers = {'x-goog-api-key': key}
                payload = {'contents': [{'role': 'user', 'parts': [{'text': 'Reply OK.'}]}],
                           'generationConfig': {'maxOutputTokens': 32}}
            else:
                headers = {'Authorization': 'Bearer ' + key}
                if kind == 'embedding':
                    url = base + '/embeddings'
                    payload = {'model': model, 'input': ['Titan setup check']}
                else:
                    url = base + '/chat/completions'
                    payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 32}
            response = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT, allow_redirects=False)
            if not 200 <= response.status_code < 300:
                errors.append(_error(kind, response.status_code))
                continue
            result = response.json()
            if kind == 'embedding':
                valid = bool(result.get('data') and result['data'][0].get('embedding'))
            elif backend == 'gemini':
                valid = bool(result.get('candidates'))
            else:
                valid = bool(result.get('choices'))
            if not valid:
                errors.append(f'{kind.capitalize()}: provider returned an unexpected response. Check model access and try again.')
        except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError, IndexError):
            errors.append(_error(kind))
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}
