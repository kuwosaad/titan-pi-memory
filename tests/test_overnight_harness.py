"""
Tests for the Titan-Karu overnight retrieval harness.
"""

import os
import tempfile
import unittest
from pathlib import Path

from entrypoints.overnight.manifest import (
    HarnessManifest,
    load_default_manifest,
    load_manifest,
    isolation_env,
    _resolve_defaults,
)


class TestManifestLoading(unittest.TestCase):
    def test_resolve_defaults_produces_valid_manifest(self):
        m = load_default_manifest()
        assert isinstance(m, dict)
        assert m["version"] == "1"
        assert "isolation" in m
        assert "runtime" in m
        assert "health" in m
        assert "artifacts" in m
        assert "benchmarks" in m
        # Verify no_production_writes is always enforced
        assert m["isolation"].get("no_production_writes") is True

    def test_resolve_defaults_merges_lists(self):
        raw = {
            "benchmarks": [
                {"id": "test-bench", "queries": [{"q": "test query", "mode": "both", "top_k": 8}]}
            ]
        }
        m = _resolve_defaults(raw)
        assert len(m["benchmarks"]) == 1
        assert m["benchmarks"][0]["id"] == "test-bench"

    def test_resolve_defaults_preserves_nested_defaults(self):
        raw = {"version": "2"}
        m = _resolve_defaults(raw)
        assert m["version"] == "2"
        assert m["isolation"]["base_dir"] == str(Path.home() / ".titan-overnight")

    def test_load_manifest_from_yaml_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                """
version: "1"
benchmarks:
  - id: "inline_bench"
    queries:
      - q: "what was decided"
        mode: "learnings"
        top_k: 10
"""
            )
            f.flush()
            path = Path(f.name)

        try:
            m = load_manifest(path)
            assert len(m["benchmarks"]) == 1
            assert m["benchmarks"][0]["id"] == "inline_bench"
            assert len(m["benchmarks"][0]["queries"]) == 1
        finally:
            path.unlink()

    def test_load_manifest_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_manifest(Path("/nonexistent/manifest.yaml"))


class TestIsolationEnv(unittest.TestCase):
    def test_isolation_env_sets_titan_base_dir(self):
        m = load_default_manifest()
        env = isolation_env(m)
        assert "TITAN_BASE_DIR" in env
        assert "TITAN_HOME" in env
        assert "TITAN_OVERNIGHT_RUN" in env
        assert env["TITAN_OVERNIGHT_RUN"] == "1"
        assert "TITAN_OVERNIGHT_LABEL" in env

    def test_isolation_env_custom_label(self):
        raw = {"isolation": {"label": "custom-label-42", "base_dir": "/tmp/test-overnight"}}
        m = _resolve_defaults(raw)
        env = isolation_env(m)
        assert env["TITAN_BASE_DIR"] == "/tmp/test-overnight"
        assert env["TITAN_OVERNIGHT_LABEL"] == "custom-label-42"


class TestNoProductionWrites(unittest.TestCase):
    def test_no_production_writes_always_true(self):
        # Even if explicitly set to false in raw, it should be forced to True
        raw = {"isolation": {"no_production_writes": False}}
        m = _resolve_defaults(raw)
        assert m["isolation"].get("no_production_writes") is True


if __name__ == "__main__":
    unittest.main()
