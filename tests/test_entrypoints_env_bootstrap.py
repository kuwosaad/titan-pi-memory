import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _reset_import_state() -> None:
    for module_name in ("entrypoints.main", "entrypoints.mcp_server", "app.storage.sessions"):
        sys.modules.pop(module_name, None)


class EntrypointEnvBootstrapTests(unittest.TestCase):
    def tearDown(self) -> None:
        _reset_import_state()

    def test_entrypoints_default_base_dir_to_titan_home(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            titan_home = Path(tmp_dir) / "titan-home"
            titan_home.mkdir(parents=True, exist_ok=True)
            for module_name in ("entrypoints.main", "entrypoints.mcp_server"):
                with self.subTest(module_name=module_name):
                    _reset_import_state()
                    with patch.dict(os.environ, {"TITAN_HOME": str(titan_home)}, clear=False):
                        os.environ.pop("TITAN_BASE_DIR", None)
                        module = importlib.import_module(module_name)
                        self.assertEqual(os.environ.get("TITAN_BASE_DIR"), str(module.TITAN_HOME))

    def test_entrypoints_preserve_existing_base_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            titan_home = Path(tmp_dir) / "titan-home"
            titan_home.mkdir(parents=True, exist_ok=True)
            explicit_base_dir = Path(tmp_dir) / "explicit-base"
            explicit_base_dir.mkdir(parents=True, exist_ok=True)
            for module_name in ("entrypoints.main", "entrypoints.mcp_server"):
                with self.subTest(module_name=module_name):
                    _reset_import_state()
                    with patch.dict(
                        os.environ,
                        {"TITAN_HOME": str(titan_home), "TITAN_BASE_DIR": str(explicit_base_dir)},
                        clear=False,
                    ):
                        importlib.import_module(module_name)
                        self.assertEqual(os.environ.get("TITAN_BASE_DIR"), str(explicit_base_dir))


if __name__ == "__main__":
    unittest.main()
