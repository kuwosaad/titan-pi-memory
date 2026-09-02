import tempfile
import unittest
from pathlib import Path

from tools.opencode.install_plugin import install_opencode_plugin


class OpenCodePluginInstallTests(unittest.TestCase):
    def test_installs_project_plugin_to_project_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            template = root / "integrations" / "opencode_titan_plugin" / "dist" / "titan_v2_spool_plugin.ts"
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_text("export const plugin = 1\n", encoding="utf-8")

            result = install_opencode_plugin(scope="project", root_dir=root)

            self.assertEqual(result["status"], "installed")
            self.assertTrue(result["target_path"].endswith(".opencode/plugins/titan_v2_spool_plugin.ts"))
            self.assertTrue(Path(result["target_path"]).exists())

    def test_global_install_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "root"
            root.mkdir(parents=True, exist_ok=True)
            template = root / "integrations" / "opencode_titan_plugin" / "dist" / "titan_v2_spool_plugin.ts"
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_text("version-one\n", encoding="utf-8")

            global_root = Path(tmp_dir) / "global"
            first = install_opencode_plugin(scope="global", root_dir=root, global_config_root=global_root)
            second = install_opencode_plugin(scope="global", root_dir=root, global_config_root=global_root)

            self.assertEqual(first["status"], "installed")
            self.assertEqual(second["status"], "already_up_to_date")
            self.assertTrue(first["target_path"].startswith(str(global_root)))

            template.write_text("version-two\n", encoding="utf-8")
            third = install_opencode_plugin(scope="global", root_dir=root, global_config_root=global_root)
            self.assertEqual(third["status"], "updated")


if __name__ == "__main__":
    unittest.main()
