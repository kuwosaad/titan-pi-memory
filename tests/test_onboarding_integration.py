"""Exercise browser onboarding over a real loopback socket with isolated data."""
import http.client
import json
import threading
import time
from unittest.mock import patch

import pytest

from tools.cli import onboarding, titan


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(titan, 'TITAN_HOME', tmp_path / 'titan')
    monkeypatch.setattr(titan, '_explicit_titan_home', False)
    instance = onboarding.OnboardingServer('pi')
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    thread.join(timeout=5)
    instance.server_close()


def request(server, method, path, data=None, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    body = json.dumps(data).encode() if data is not None else None
    conn.request(method, path, body, headers or {})
    response = conn.getresponse()
    result = (response.status, response.read())
    conn.close()
    return result


def auth(server):
    return {'X-Titan-Token': server.token, 'Content-Type': 'application/json'}


def selection():
    return {'agent': 'pi', 'extraction': {'backend': 'openai', 'model': 'gpt-4o-mini'},
            'embedding': {'backend': 'ollama', 'model': 'nomic-embed-text:v1.5'},
            'keys': {'OPENAI_API_KEY': 'fixture-secret-value'}}


def test_loopback_host_origin_and_token_are_enforced(server):
    assert server.server_address[0] == '127.0.0.1'
    assert request(server, 'GET', '/api/state')[0] == 403
    assert request(server, 'GET', '/api/state', headers=auth(server))[0] == 200
    assert request(server, 'GET', '/api/state', headers={**auth(server), 'Host': 'evil.test'})[0] == 403
    assert request(server, 'GET', '/api/state', headers={**auth(server), 'Origin': 'https://evil.test'})[0] == 403
    assert request(server, 'POST', '/api/install', selection(), {'Content-Type': 'application/json'})[0] == 403
    assert request(server, 'POST', '/api/install', selection(), {**auth(server), 'Origin': 'https://evil.test'})[0] == 403


def test_install_persists_only_to_agent_namespace_and_redacts_secret(server, tmp_path):
    secret = selection()['keys']['OPENAI_API_KEY']
    with patch.object(onboarding, 'check_configuration', return_value={'ok': True, 'errors': []}):
        assert request(server, 'POST', '/api/validate', selection(), auth(server))[0] == 200
    status, body = request(server, 'POST', '/api/install', selection(), auth(server))
    assert status == 200 and secret.encode() not in body
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, body = request(server, 'GET', '/api/status', headers=auth(server))
        assert secret.encode() not in body
        if json.loads(body)['state'] != 'running':
            break
        time.sleep(.01)
    assert json.loads(body)['state'] == 'complete'
    env = tmp_path / 'titan' / 'agents' / 'pi' / '.env'
    assert secret in env.read_text()
    assert env.stat().st_mode & 0o077 == 0
    assert not (tmp_path / 'titan' / '.env').exists()
    status, body = request(server, 'GET', '/api/state', headers=auth(server))
    assert status == 200 and secret.encode() not in body
    assert 'OPENAI_API_KEY' in json.loads(body)['saved_keys']


def test_existing_database_blocks_embedding_change(server, tmp_path):
    home = tmp_path / 'titan' / 'agents' / 'pi'
    config = home / 'config' / 'embedding_models.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('current: ollama\nollama:\n  model: nomic-embed-text:v1.5\n')
    (home / 'memory.db').write_bytes(b'fixture')
    data = selection()
    data['embedding']['model'] = 'mxbai-embed-large'
    status, body = request(server, 'POST', '/api/validate', data, auth(server))
    assert status == 400
    assert 'migration' in body.decode().lower()


def test_install_failure_is_reported_without_leaking_exception(server):
    with patch.object(onboarding, 'check_configuration', return_value={'ok': True, 'errors': []}):
        assert request(server, 'POST', '/api/validate', selection(), auth(server))[0] == 200
    with patch.object(onboarding, '_install', side_effect=RuntimeError('fixture-secret-value')):
        status, _ = request(server, 'POST', '/api/install', selection(), auth(server))
        assert status == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, body = request(server, 'GET', '/api/status', headers=auth(server))
            if json.loads(body)['state'] != 'running':
                break
            time.sleep(.01)
        assert json.loads(body)['state'] == 'error'
        assert b'fixture-secret-value' not in body


def test_ui_assets_are_served(server):
    for path in ('/', '/style.css', '/app.js', '/source-serif-regular.ttf'):
        assert request(server, 'GET', path)[0] == 200


def test_validation_is_bound_to_exact_payload_and_failure_writes_nothing(server, tmp_path):
    data = selection()
    with patch.object(onboarding, 'check_configuration', return_value={'ok': True, 'errors': []}):
        assert request(server, 'POST', '/api/validate', data, auth(server))[0] == 200
    changed = selection()
    changed['extraction']['model'] = 'gpt-4.1-mini'
    assert request(server, 'POST', '/api/install', changed, auth(server))[0] == 400
    assert not (tmp_path / 'titan').exists()
    with patch.object(onboarding, 'check_configuration', return_value={'ok': False, 'errors': ['Provider unavailable']}):
        assert request(server, 'POST', '/api/validate', data, auth(server))[0] == 400
    assert request(server, 'POST', '/api/install', data, auth(server))[0] == 400
    assert not (tmp_path / 'titan').exists()
