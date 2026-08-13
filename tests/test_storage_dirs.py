import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StorageDirTests(unittest.TestCase):
    def test_ensure_dirs_creates_missing_parents(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "missing" / "titan-home"
            with patch.dict(os.environ, {"TITAN_BASE_DIR": str(base_dir)}, clear=False):
                import app.storage.sessions as sessions_module

                importlib.reload(sessions_module)
                sessions_module.ensure_dirs()

                self.assertTrue((base_dir / "out").exists())
                self.assertTrue((base_dir / "out" / "sessions").exists())
                self.assertTrue((base_dir / "out" / "memories").exists())
                self.assertTrue((base_dir / "out" / "traces").exists())
                self.assertTrue((base_dir / "out" / "graphs").exists())


if __name__ == "__main__":
    unittest.main()
