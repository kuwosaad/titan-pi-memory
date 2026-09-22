"""Keep performance comparisons honest about Python's bytecode cache."""

import importlib.util
from pathlib import Path

import pytest


def test_benchmark_requires_matching_clean_project_bytecode(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools/benchmarks/efficiency_comparison.py"
    spec = importlib.util.spec_from_file_location("efficiency_comparison", script)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)

    benchmark._require_clean_project_bytecode(tmp_path)
    cache = tmp_path / "app" / "__pycache__" / "example.cpython-311.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cached bytecode")
    with pytest.raises(ValueError, match="project bytecode cache found"):
        benchmark._require_clean_project_bytecode(tmp_path)
