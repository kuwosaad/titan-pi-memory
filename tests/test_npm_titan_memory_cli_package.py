import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT_DIR / "packages" / "titan-memory-cli"


class NpmTitanMemoryCliPackageTests(unittest.TestCase):
    def test_package_exposes_titan_bin_and_prepares_runtime(self):
        payload = json.loads((PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["name"], "titan-memory-cli")
        self.assertEqual(
            payload["bin"],
            {
                "titan": "bin/titan.js",
                "titan-memory-cli": "bin/titan.js",
            },
        )
        self.assertEqual(payload["scripts"]["prepack"], "node scripts/prepare-runtime.js")
        self.assertIn("runtime/", payload["files"])

        bin_script = (PACKAGE_DIR / "bin" / "titan.js").read_text(encoding="utf-8")
        self.assertTrue(bin_script.startswith("#!/usr/bin/env node"))
        self.assertIn(".titan", bin_script)
        self.assertIn("python", bin_script.lower())
        self.assertIn("tools", bin_script)
        self.assertIn("cli", bin_script)
        self.assertIn("titan.py", bin_script)

    def test_prepare_runtime_copies_required_titan_surfaces(self):
        script = (PACKAGE_DIR / "scripts" / "prepare-runtime.js").read_text(encoding="utf-8")

        for required in ("app", "config", "entrypoints", "integrations", "tools", "requirements.txt"):
            with self.subTest(required=required):
                self.assertIn(required, script)

        self.assertIn("__pycache__", script)
        self.assertIn(".pyc", script)


if __name__ == "__main__":
    unittest.main()
