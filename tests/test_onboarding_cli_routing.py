import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.cli.titan import main


class OnboardingRoutingTests(unittest.TestCase):
    def test_supported_agents_open_browser(self):
        for agent in ("opencode", "codex", "claude-code", "pi"):
            with self.subTest(agent=agent):
                run = Mock(return_value=7)
                with patch.dict(sys.modules, {"tools.cli.onboarding": types.SimpleNamespace(run_onboarding=run)}):
                    self.assertEqual(main(["setup", agent]), 7)
                run.assert_called_once_with(agent, scope="global", config_path=None, open_browser=True)

    def test_url_mode_and_config_path(self):
        run = Mock(return_value=0)
        with patch.dict(sys.modules, {"tools.cli.onboarding": types.SimpleNamespace(run_onboarding=run)}):
            self.assertEqual(main(["setup", "codex", "--no-browser", "--codex-config", "custom.toml"]), 0)
        run.assert_called_once_with("codex", scope="global", config_path=Path("custom.toml"), open_browser=False)

    def test_terminal_and_automation_keep_existing_path(self):
        for flag in ("--terminal", "--non-interactive", "--dry-run", "--verify", "--skip-plugin-install"):
            with self.subTest(flag=flag), patch("tools.cli.titan.run_setup_codex", return_value=3) as run:
                self.assertEqual(main(["setup", "codex", flag]), 3)
                run.assert_called_once()

    def test_no_browser_rejects_terminal_options(self):
        with self.assertRaises(SystemExit):
            main(["setup", "codex", "--no-browser", "--non-interactive"])


if __name__ == "__main__":
    unittest.main()
