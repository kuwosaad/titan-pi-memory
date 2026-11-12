"""Local, token-protected browser onboarding for Titan's agent integrations."""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tools.cli import titan
from tools.cli.onboarding_checks import check_configuration

UI_DIR = Path(__file__).parent / 'onboarding_ui'
MAX_BODY = 32768
AGENTS = [{'id': 'opencode', 'label': 'OpenCode'}, {'id': 'codex', 'label': 'Codex'},
          {'id': 'claude-code', 'label': 'Claude Code'}, {'id': 'pi', 'label': 'Pi'}]
MODELS = {
    'ollama': ['llama3.1:8b', 'qwen2.5:7b', 'mistral'],
    'openai': ['gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4.1'],
    'gemini': ['gemini-2.5-flash', 'gemini-2.5-pro'],
    'openrouter': ['anthropic/claude-sonnet-4', 'openai/gpt-4o-mini', 'deepseek/deepseek-chat'],
    'opencode_go': ['deepseek-v4-flash'],
}
EMBEDDINGS = {'ollama': ['nomic-embed-text:v1.5', 'mxbai-embed-large'],
              'openai': ['text-embedding-3-small', 'text-embedding-3-large']}


def _catalog(kind):
    cfg = titan._load_yaml(titan.ROOT_DIR / 'config' / (kind + '_models.yaml'))
    models = MODELS if kind == 'extraction' else EMBEDDINGS
    result = []
    for name, examples in models.items():
        block = cfg.get(name, {})
        choices = list(dict.fromkeys([str(block.get('model') or examples[0]), *examples]))
        result.append({'id': name, 'label': {'opencode_go': 'OpenCode Go', 'openai': 'OpenAI', 'ollama': 'Ollama', 'gemini': 'Gemini', 'openrouter': 'OpenRouter'}[name], 'models': choices, 'key_env': block.get('api_key_env')})
    return result


def _state(agent):
    home = titan.resolve_agent_titan_home(agent)
    env = titan._read_agent_effective_env(home, {})
    catalogs = {kind: _catalog(kind) for kind in ('extraction', 'embedding')}
    selected = {'agent': agent}
    for kind in catalogs:
        path = Path(env.get('TITAN_' + kind.upper() + '_CONFIG_PATH') or home / 'config' / (kind + '_models.yaml')).expanduser()
        cfg = titan._load_yaml(path if path.exists() else titan.ROOT_DIR / 'config' / (kind + '_models.yaml'))
        backend = cfg.get('current')
        if backend not in {item['id'] for item in catalogs[kind]}:
            backend = catalogs[kind][0]['id']
        selected[kind] = {'backend': backend, 'model': str(cfg.get(backend, {}).get('model') or catalogs[kind][0]['models'][0])}
        entry = next(item for item in catalogs[kind] if item['id'] == backend)
        if selected[kind]['model'] not in entry['models']:
            entry['models'].insert(0, selected[kind]['model'])
    keys = {item['key_env'] for catalog in catalogs.values() for item in catalog if item['key_env']}
    return {'agents': AGENTS, **catalogs, 'selected': selected, 'saved_keys': sorted(key for key in keys if env.get(key))}


def _validate(data):
    errors = []
    if not isinstance(data, dict):
        return ['Expected an object.']
    agent = data.get('agent')
    if not isinstance(agent, str) or agent not in {a['id'] for a in AGENTS}:
        errors.append('Choose a supported agent.')
        return errors
    keys = data.get('keys', {})
    if not isinstance(keys, dict):
        return ['Keys must be an object.']
    home = titan.resolve_agent_titan_home(agent)
    env = titan._read_agent_effective_env(home, {})
    allowed_keys = {x['key_env'] for kind in ('extraction', 'embedding') for x in _catalog(kind) if x['key_env']}
    if any(k not in allowed_keys or not isinstance(v, str) or len(v) > 4096 or '\n' in v or '\r' in v for k, v in keys.items()):
        return ['Invalid credential entry.']
    for kind in ('extraction', 'embedding'):
        value = data.get(kind)
        if not isinstance(value, dict):
            errors.append(f'Choose a {kind} provider and model.')
            continue
        backend = value.get('backend')
        entry = next((x for x in _catalog(kind) if isinstance(backend, str) and x['id'] == backend), None)
        model = value.get('model')
        if not entry or not isinstance(model, str) or not model.strip() or len(model) > 300 or any(ord(c) < 32 for c in model):
            errors.append(f'Choose a valid {kind} provider and model.')
            continue
        key = entry['key_env']
        if key and not (keys.get(key, '').strip() or env.get(key)):
            errors.append(f'{key} is required.')
    if errors:
        return errors
    # Do not mix vectors from different embedding models in an existing store.
    cfg_path = Path(env.get('TITAN_EMBEDDING_CONFIG_PATH') or home / 'config' / 'embedding_models.yaml').expanduser()
    if cfg_path.exists() and isinstance(data.get('embedding'), dict):
        try:
            cfg = titan._load_yaml(cfg_path)
            if not isinstance(cfg, dict) or not isinstance(cfg.get('current'), str):
                raise ValueError()
            old = cfg['current']
            block = cfg.get(old)
            if not isinstance(block, dict):
                raise ValueError()
        except (OSError, ValueError, titan.yaml.YAMLError):
            return ['Existing embedding configuration is invalid. Fix it before continuing.']
        new = data['embedding']
        if (old, str(block.get('model', ''))) != (new.get('backend'), new.get('model')):
            if any(p.is_file() and p.stat().st_size for pattern in ('*.db', '*.sqlite', '*.sqlite3') for p in home.rglob(pattern)):
                errors.append('This agent has an existing database. Changing its embedding model requires a migration.')
    return errors


def _install(data, scope, config_path):
    agent = data['agent']
    home = titan.resolve_agent_titan_home(agent)
    home.mkdir(parents=True, exist_ok=True)
    env_path = home / '.env'
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    os.chmod(env_path, 0o600)
    titan.bootstrap_agent_home(agent)
    configs = {}
    env = titan._read_agent_effective_env(home, {})
    for kind in ('extraction', 'embedding'):
        path = Path(env.get('TITAN_' + kind.upper() + '_CONFIG_PATH') or home / 'config' / (kind + '_models.yaml')).expanduser()
        cfg = titan._load_yaml(path if path.exists() else titan.ROOT_DIR / 'config' / (kind + '_models.yaml'))
        selection = data[kind]
        configs[kind] = titan._set_config_backend_model(cfg, selection['backend'], selection['model'].strip())
    extraction_path, embedding_path = titan._write_agent_model_configs(home, configs['extraction'], configs['embedding'])
    updates = {'TITAN_EXTRACTION_CONFIG_PATH': str(extraction_path), 'TITAN_EMBEDDING_CONFIG_PATH': str(embedding_path)}
    updates.update({k: v.strip() for k, v in data.get('keys', {}).items() if v.strip()})
    titan.upsert_env_keys(home / '.env', updates)
    if agent == 'pi':
        return 0
    if agent == 'opencode':
        candidate = config_path or titan._find_agent_config(agent)[0]
        if candidate is None:
            raise RuntimeError('OpenCode configuration was not found. Install OpenCode first.')
        titan.install_opencode_plugin(scope=scope, root_dir=titan.ROOT_DIR, global_config_root=config_path.parent if config_path else None)
        result = titan.patch_opencode_config(agent=agent, config_path=candidate)
        if not result['ok']:
            raise RuntimeError('Could not safely update OpenCode configuration.')
    elif agent == 'codex':
        ok, _ = titan.ensure_codex_marketplace_snapshot()
        if not ok or not titan._codex_plugin_files_ok()[0]:
            raise RuntimeError('Codex marketplace or plugin files are unavailable.')
        return titan.run_codex_reinstall_plugin(config_path=config_path)
    elif agent == 'claude-code':
        ok, _ = titan.ensure_claude_marketplace_snapshot()
        if not ok or not titan._claude_plugin_files_ok()[0]:
            raise RuntimeError('Claude marketplace or plugin files are unavailable.')
        return titan.run_claude_reinstall_plugin()
    return 0


class OnboardingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, agent='opencode', scope='global', config_path=None):
        super().__init__(('127.0.0.1', 0), Handler)
        self.token = secrets.token_urlsafe(32)
        self.agent, self.scope, self.config_path = agent, scope, config_path
        self.locked_agent = agent if titan._explicit_titan_home else None
        self.status = {'state': 'idle', 'message': '', 'exit_code': None}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.installed = False
        self.validated = None

    def install(self, data):
        with self.lock:
            if self.status['state'] == 'running':
                return False
            self.status = {'state': 'running', 'message': 'Installing Titan…', 'exit_code': None}
        def work():
            try:
                code = _install(data, self.scope, self.config_path if data['agent'] == self.agent else None)
                message = (('Pi configuration saved and provider checks passed. Install or reload the Titan Pi extension separately.' if data['agent'] == 'pi' else ('Installation complete and provider checks passed. In Codex, run /hooks and trust the Titan hook, then restart.' if data['agent'] == 'codex' else 'Installation complete and provider checks passed. Restart your agent.')) if code == 0 else 'Installation failed. Check the terminal for details.')
            except RuntimeError as exc:
                safe_errors = {'OpenCode configuration was not found. Install OpenCode first.', 'Could not safely update OpenCode configuration.', 'Codex marketplace or plugin files are unavailable.', 'Claude marketplace or plugin files are unavailable.'}
                code, message = 1, str(exc) if str(exc) in safe_errors else 'Installation failed. Check your agent configuration and try again.'
            except Exception:
                code, message = 1, 'Installation failed. Check your agent configuration and try again.'
            with self.lock:
                self.status = {'state': 'complete' if code == 0 else 'error', 'message': message, 'exit_code': code}
                self.installed = code == 0
        threading.Thread(target=work, daemon=True).start()
        return True


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass

    def _headers_ok(self, api=False):
        host = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != host:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            return False
        if api and not hmac.compare_digest(self.headers.get('X-Titan-Token', '').encode('utf-8'), self.server.token.encode('ascii')):
            return False
        return True

    def _send(self, code, body, content_type='application/json'):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._headers_ok(path.startswith('/api/')):
            return self._send(403, {'error': 'Forbidden'})
        if path == '/api/state':
            agent = parse_qs(urlsplit(self.path).query).get('agent', [self.server.agent])[0]
            if agent not in {a['id'] for a in AGENTS}:
                return self._send(400, {'error': 'Unsupported agent'})
            if self.server.locked_agent and agent != self.server.locked_agent:
                return self._send(403, {'error': 'Agent is locked for this session'})
            state = _state(agent)
            state['locked_agent'] = self.server.locked_agent
            return self._send(200, state)
        if path == '/api/status':
            with self.server.lock:
                return self._send(200, dict(self.server.status))
        assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/app.js': ('app.js', 'application/javascript; charset=utf-8'), '/titan-logo.svg': ('titan-logo.svg', 'image/svg+xml'), '/source-serif-regular.ttf': ('source-serif-regular.ttf', 'font/ttf')}
        if path in assets:
            filename, mime = assets[path]
            try:
                return self._send(200, (UI_DIR / filename).read_bytes(), mime)
            except OSError:
                pass
        self._send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self._headers_ok(True):
            return self._send(403, {'error': 'Forbidden'})
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self._send(415, {'error': 'Expected JSON'})
        try:
            length = int(self.headers.get('Content-Length', ''))
            if length < 0 or length > MAX_BODY:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {'error': 'Invalid request'})
        path = urlsplit(self.path).path
        if path == '/api/close':
            with self.server.lock:
                if self.server.status['state'] == 'running':
                    return self._send(409, {'error': 'Installation is running'})
            self._send(200, {'ok': True})
            self.server.closed.set()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == '/api/pull':
            model = data.get('model') if isinstance(data, dict) else None
            if not isinstance(model, str) or not model or len(model) > 300 or model.startswith('-') or any(ord(c) < 33 for c in model):
                return self._send(400, {'ok': False, 'errors': ['Invalid model name.']})
            with self.server.lock:
                if self.server.status['state'] == 'running':
                    return self._send(409, {'ok': False, 'errors': ['Another operation is running.']})
                self.server.status = {'state': 'running', 'message': 'Downloading model…', 'exit_code': None}
            def pull():
                try:
                    result = subprocess.run(['ollama', 'pull', model], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800, check=False)
                    code = result.returncode
                except (OSError, subprocess.TimeoutExpired):
                    code = 1
                with self.server.lock:
                    self.server.status = {'state': 'complete' if code == 0 else 'error', 'message': 'Model downloaded.' if code == 0 else 'Model download failed. Check that Ollama is installed and running.', 'exit_code': code}
            threading.Thread(target=pull, daemon=True).start()
            return self._send(200, {'ok': True})
        if path in ('/api/validate', '/api/install'):
            errors = _validate(data)
            if self.server.locked_agent and (not isinstance(data, dict) or data.get('agent') != self.server.locked_agent):
                errors.append('Agent is locked for this session.')
            if errors:
                return self._send(400, {'ok': False, 'errors': errors})
            if path == '/api/validate':
                home = titan.resolve_agent_titan_home(data['agent'])
                configs = {}
                for kind in ('extraction', 'embedding'):
                    effective_env = titan._read_agent_effective_env(home, {})
                    config_path = Path(effective_env.get('TITAN_' + kind.upper() + '_CONFIG_PATH') or home / 'config' / (kind + '_models.yaml')).expanduser()
                    configs[kind] = titan._load_yaml(config_path if config_path.exists() else titan.ROOT_DIR / 'config' / (kind + '_models.yaml'))
                result = check_configuration(data, configs=configs, env=titan._read_agent_effective_env(home, {}))
                self.server.validated = hmac.new(self.server.token.encode(), json.dumps(data, sort_keys=True).encode(), hashlib.sha256).digest() if result['ok'] else None
                return self._send(200 if result['ok'] else 400, result)
            signature = hmac.new(self.server.token.encode(), json.dumps(data, sort_keys=True).encode(), hashlib.sha256).digest()
            if self.server.validated is None or not hmac.compare_digest(signature, self.server.validated):
                return self._send(400, {'ok': False, 'errors': ['Validate these settings before installing.']})
            if path == '/api/install' and not self.server.install(data):
                return self._send(409, {'ok': False, 'errors': ['Installation is already running.']})
            return self._send(200, {'ok': True})
        self._send(404, {'error': 'Not found'})


def run_onboarding(agent='opencode', *, scope='global', config_path=None, open_browser=True):
    if agent not in {a['id'] for a in AGENTS}:
        raise ValueError('Unsupported onboarding agent')
    server = OnboardingServer(agent, scope, config_path)
    url = f'http://127.0.0.1:{server.server_port}/#{server.token}'
    print(f'[titan] Open onboarding: {url}', flush=True)
    if open_browser:
        webbrowser.open(url)
    # Bound idle lifetime; a running installation is allowed to finish.
    def expire():
        time.sleep(1800)
        while server.status['state'] == 'running':
            time.sleep(1)
        server.shutdown()
    threading.Thread(target=expire, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0 if server.installed else 1


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Open Titan browser onboarding')
    parser.add_argument('--agent', default='opencode', choices=[a['id'] for a in AGENTS])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run_onboarding(args.agent, open_browser=not args.no_browser))
