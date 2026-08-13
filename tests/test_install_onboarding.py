import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pi_package_points_at_the_current_npm_install_contract():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "titan-pi-memory"
    assert package["version"] == "0.2.6"
    assert package["pi"]["extensions"] == ["./tools/pi_extension"]
    assert any(
        "tools/pi_extension/install.sh" == entry
        or "tools/pi_extension/install.sh".startswith(entry.rstrip("/") + "/")
        for entry in package["files"]
    )
    assert (ROOT / "tools/pi_extension/install.sh").exists()


def test_pi_package_includes_the_complete_titan_runtime():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    package_files = package["files"]

    for runtime_root in ("app/**/*.py", "entrypoints/**/*.py", "config/", "tools/pi_extension/skills/"):
        assert runtime_root in package_files

    required_modules = (
        "app/runtime/context.py",
        "app/storage/sqlite.py",
        "app/save_pipeline/trace_intake.py",
        "app/graph/corpus_analysis.py",
        "app/patterns/errors.py",
        "app/patterns/memory.py",
        "app/patterns/storage.py",
        "app/save_pipeline/extraction/policy.py",
    )
    for module in required_modules:
        assert (ROOT / module).exists()

    assert "app/graph/ui/" in package_files
    assert "tools/pi_extension/index.ts" in package_files

    npmignore = (ROOT / ".npmignore").read_text(encoding="utf-8")
    assert "__pycache__/" in npmignore
    assert "tests/" in npmignore


def test_actual_npm_pack_contains_runtime_without_local_artifacts(tmp_path):
    npm = shutil.which("npm")
    if npm is None:
        return

    result = subprocess.run(
        [npm, "pack", "--dry-run", "--json"],
        cwd=ROOT,
        env={**os.environ, "npm_config_cache": str(tmp_path / "npm-cache")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)[0]
    paths = {item["path"] for item in payload["files"]}

    for required in (
        "app/runtime/context.py",
        "app/storage/sqlite.py",
        "app/save_pipeline/trace_intake.py",
        "app/graph/corpus_analysis.py",
        "app/patterns/memory.py",
    ):
        assert required in paths
    assert not any("__pycache__" in path or path.endswith((".pyc", ".pyo")) for path in paths)
    assert not any(path.startswith("tests/") or path.startswith("docs/") for path in paths)


def test_python_package_exposes_the_current_pypi_cli_contract():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["name"] == "titan-memory-cli"
    assert project["readme"] == "docs/pypi_titan_memory_cli.md"
    assert "mcp" in project["dependencies"]
    assert project["scripts"]["titan"] == "tools.cli.titan:main"
    assert "integrations*" in pyproject["tool"]["setuptools"]["packages"]["find"]["include"]


def test_readme_points_new_users_at_current_install_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "pi install npm:titan-pi-memory" in readme
    assert "/titan-setup" in readme
    assert "raw.githubusercontent.com/kuwosaad/titan-karu/main/install.sh" not in readme
    assert "git+https://github.com/kuwosaad/titan-karu.git" not in readme


def test_codex_install_docs_use_npm_or_pypi_without_root_installer():
    npm_readme = (ROOT / "packages/titan-memory-cli/README.md").read_text(encoding="utf-8")
    pypi_readme = (ROOT / "docs/pypi_titan_memory_cli.md").read_text(encoding="utf-8")

    assert "npx -y titan-memory-cli@latest setup codex" in npm_readme
    assert "pip install titan-memory-cli" in pypi_readme
    assert "install.sh" not in npm_readme
    assert "install.sh" not in pypi_readme
