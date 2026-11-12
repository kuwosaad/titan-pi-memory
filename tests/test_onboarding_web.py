import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cli import onboarding, titan


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch_home = patch.object(titan, 'TITAN_HOME', Path(self.temp.name))
        self.patch_explicit = patch.object(titan, '_explicit_titan_home', None)
        self.patch_home.start()
        self.patch_explicit.start()
        self.server = onboarding.OnboardingServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.patch_explicit.stop()
        self.patch_home.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_state_requires_token_and_rejects_host_and_origin(self):
        self.assertEqual(self.request('GET', '/api/state')[0], 403)
        token = {'X-Titan-Token': self.server.token}
        status, body = self.request('GET', '/api/state', headers=token)
        self.assertEqual(status, 200)
        self.assertIn('pi', [a['id'] for a in json.loads(body)['agents']])
        self.assertEqual(self.request('GET', '/api/state', headers={**token, 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={**token, 'Host': 'evil.example'})[0], 403)

    def test_install_saves_selected_models_and_keys_in_agent_namespace(self):
        data = {'agent': 'pi', 'extraction': {'backend': 'openai', 'model': 'gpt-4o-mini'},
                'embedding': {'backend': 'ollama', 'model': 'nomic-embed-text:v1.5'}, 'keys': {'OPENAI_API_KEY': 'secret-example'}}
        self.assertEqual(onboarding._validate(data), [])
        self.assertEqual(onboarding._install(data, 'global', None), 0)
        home = Path(self.temp.name) / 'agents' / 'pi'
        self.assertIn('OPENAI_API_KEY=secret-example', (home / '.env').read_text())
        self.assertFalse((Path(self.temp.name) / '.env').exists())
        self.assertEqual(titan._load_yaml(home / 'config' / 'extraction_models.yaml')['current'], 'openai')
        self.assertNotIn('secret-example', json.dumps(onboarding._state('pi')))

    def test_validation_and_body_limit(self):
        headers = {'X-Titan-Token': self.server.token, 'Content-Type': 'application/json'}
        status, body = self.request('POST', '/api/validate', '{}', headers)
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)['ok'])
        self.assertEqual(self.request('POST', '/api/validate', 'x' * (onboarding.MAX_BODY + 1), headers)[0], 400)

    def test_pull_requires_token_and_invokes_ollama_without_shell(self):
        headers = {'X-Titan-Token': self.server.token, 'Content-Type': 'application/json'}
        with patch.object(onboarding.subprocess, 'run') as runner:
            runner.return_value.returncode = 0
            status, _ = self.request('POST', '/api/pull', json.dumps({'model': 'nomic-embed-text:v1.5'}), headers)
            self.assertEqual(status, 200)
            for _ in range(100):
                if self.server.status['state'] != 'running':
                    break
                threading.Event().wait(0.01)
            runner.assert_called_once()
            self.assertEqual(runner.call_args.args[0], ['ollama', 'pull', 'nomic-embed-text:v1.5'])
            self.assertFalse(self.server.installed)

    def test_explicit_home_locks_agent(self):
        self.server.locked_agent = 'pi'
        headers = {'X-Titan-Token': self.server.token}
        self.assertEqual(self.request('GET', '/api/state?agent=codex', headers=headers)[0], 403)

    def test_malformed_json_fields_return_400(self):
        headers = {'X-Titan-Token': self.server.token, 'Content-Type': 'application/json'}
        base = {'agent': 'pi', 'extraction': {'backend': 'ollama', 'model': 'llama'},
                'embedding': {'backend': 'ollama', 'model': 'nomic'}, 'keys': {}}
        cases = [[], {'agent': []}, {**base, 'keys': {'OPENAI_API_KEY': 123}},
                 {**base, 'extraction': {'backend': {}, 'model': 'x'}}]
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual(self.request('POST', '/api/validate', json.dumps(data), headers)[0], 400)

    def test_malformed_existing_embedding_config_returns_400(self):
        config = Path(self.temp.name) / 'agents' / 'pi' / 'config' / 'embedding_models.yaml'
        config.parent.mkdir(parents=True)
        config.write_text('current: [bad, value]\n')
        headers = {'X-Titan-Token': self.server.token, 'Content-Type': 'application/json'}
        data = {'agent': 'pi', 'extraction': {'backend': 'ollama', 'model': 'llama'},
                'embedding': {'backend': 'ollama', 'model': 'nomic'}, 'keys': {}}
        self.assertEqual(self.request('POST', '/api/validate', json.dumps(data), headers)[0], 400)

    def test_existing_store_blocks_embedding_change(self):
        home = Path(self.temp.name) / 'agents' / 'pi'
        (home / 'config').mkdir(parents=True)
        (home / 'config' / 'embedding_models.yaml').write_text('current: ollama\nollama:\n  model: old-model\n')
        (home / 'memory.db').write_bytes(b'existing')
        data = {'agent': 'pi', 'extraction': {'backend': 'ollama', 'model': 'llama'},
                'embedding': {'backend': 'ollama', 'model': 'new-model'}, 'keys': {}}
        self.assertTrue(any('migration' in e for e in onboarding._validate(data)))


if __name__ == '__main__':
    unittest.main()
