"""Regression checks for the Pi onboarding launch and npm payload."""
import json
import os
import tempfile
import tarfile
import sys
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PiWebOnboardingTests(unittest.TestCase):
    def test_pi_waits_for_success_before_restarting_server(self):
        source = (ROOT / 'tools/pi_extension/index.ts').read_text()
        command = source[source.index('pi.registerCommand("titan-setup"'):source.index('pi.registerCommand("titan-key"')]
        self.assertIn('await ensurePythonDependencies()', command)
        self.assertIn('"-m", "tools.cli.onboarding", "--agent", "pi"', command)
        self.assertIn('TITAN_HOME', command)
        self.assertIn('onboardingInProgress', command)
        self.assertLess(command.index('if (result.code !== 0)'), command.index('await restartOwnedServer()'))
        self.assertIn('return;', command[command.index('if (result.code !== 0)'):command.index('await restartOwnedServer()')])
        self.assertIn('If your browser did not open, visit', command)

    def test_npm_package_contains_the_onboarding_runtime(self):
        with tempfile.TemporaryDirectory() as cache:
            output = subprocess.check_output(
                ['npm', 'pack', '--dry-run', '--json', '--ignore-scripts'], cwd=ROOT, text=True,
                env={**os.environ, 'npm_config_cache': cache},
            )
        files = {entry['path'] for entry in json.loads(output)[0]['files']}
        self.assertTrue({
            'tools/cli/onboarding.py', 'tools/cli/titan.py', 'tools/cli/titan_voice.py',
            'tools/cli/onboarding_ui/index.html', 'tools/cli/onboarding_ui/style.css',
            'tools/cli/onboarding_ui/app.js', 'tools/cli/onboarding_ui/source-serif-regular.ttf',
            'tools/opencode/install_plugin.py',
        } <= files)

    def test_packed_module_launches_from_isolated_package(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'npm_config_cache': directory}
            output = subprocess.check_output(
                ['npm', 'pack', '--json', '--ignore-scripts', '--pack-destination', directory],
                cwd=ROOT, env=env, text=True,
            )
            archive = Path(directory) / json.loads(output)[0]['filename']
            with tarfile.open(archive) as packed:
                for member in packed.getmembers():
                    if member.isfile():
                        target = Path(directory) / member.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(packed.extractfile(member).read())
            package = Path(directory) / 'package'
            env.pop('PYTHONPATH', None)
            result = subprocess.run(
                [sys.executable, '-m', 'tools.cli.onboarding', '--help'],
                cwd=package, env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--agent', result.stdout)


if __name__ == '__main__':
    unittest.main()
