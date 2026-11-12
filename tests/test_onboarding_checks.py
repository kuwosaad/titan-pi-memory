import unittest
from unittest.mock import Mock, patch

from tools.cli.onboarding_checks import check_configuration


class ProviderChecksTest(unittest.TestCase):
    def setUp(self):
        self.data = {'extraction': {'backend': 'openai', 'model': 'gpt-test'},
                     'embedding': {'backend': 'ollama', 'model': 'embed'},
                     'keys': {'OPENAI_API_KEY': 'secret'}}
        self.configs = {'extraction': {'openai': {}}, 'embedding': {'ollama': {}}}

    @patch('tools.cli.onboarding_checks.requests.get')
    @patch('tools.cli.onboarding_checks.requests.post')
    def test_success_checks_models_and_credentials(self, post, get):
        post.return_value = Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': 'OK'}}]})
        get.return_value = Mock(status_code=200, json=lambda: {'models': [{'name': 'embed:latest'}]})
        result = check_configuration(self.data, self.configs, {})
        self.assertTrue(result['ok'])
        self.assertEqual(post.call_args.kwargs['timeout'], 15)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    @patch('tools.cli.onboarding_checks.requests.get')
    @patch('tools.cli.onboarding_checks.requests.post')
    def test_failures_do_not_expose_secrets_or_response(self, post, get):
        post.return_value = Mock(status_code=401, text='secret')
        get.return_value = Mock(status_code=200, json=lambda: {'models': []})
        result = check_configuration(self.data, self.configs, {})
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['errors']), 2)
        self.assertNotIn('secret', str(result))
        self.assertIn('ollama pull', result['errors'][1])

    @patch('tools.cli.onboarding_checks.requests.post')
    def test_rejects_custom_cloud_endpoint(self, post):
        self.configs['extraction']['openai']['base_url'] = 'https://evil.example/v1'
        self.data['embedding'] = {'backend': 'openai', 'model': 'embed'}
        self.configs['embedding']['openai'] = {'base_url': 'https://evil.example/v1'}
        result = check_configuration(self.data, self.configs, {})
        self.assertFalse(result['ok'])
        post.assert_not_called()

    @patch('tools.cli.onboarding_checks.requests.get')
    def test_rejects_remote_ollama(self, get):
        self.data['extraction'] = self.data['embedding']
        self.configs['extraction']['ollama'] = {'base_url': 'http://example.com'}
        self.configs['embedding']['ollama'] = {'base_url': 'http://example.com'}
        self.assertFalse(check_configuration(self.data, self.configs, {})['ok'])
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
