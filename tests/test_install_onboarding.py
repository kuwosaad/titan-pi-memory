import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PI_REQUIRED_PAYLOADS = (
    "README.md", "LICENSE", "requirements.txt", "package.json",
    "app/api/routes.py", "app/graph/cortex_analysis.py", "app/graph/corpus_analysis.py",
    "app/graph/ui/client.js", "app/graph/ui/styles.css", "app/graph/ui/template.html",
    "app/patterns/memory.py", "app/runtime/context.py", "app/save_pipeline/trace_intake.py",
    "app/storage/sqlite.py", "config/__init__.py", "config/.env.example",
    "config/embedding_models.yaml", "config/extraction_models.yaml", "config/settings.yaml",
    "config/visual_config.yaml", "entrypoints/__init__.py", "entrypoints/main.py",
    "entrypoints/mcp_server.py", "tools/pi_extension/index.ts", "tools/pi_extension/install.sh",
    "tools/pi_extension/README.md", "tools/pi_extension/server.py",
    "tools/pi_extension/titan_dashboard.py", "tools/pi_extension/prompts/memory-sync.md",
    "tools/pi_extension/skills/memory-sync/SKILL.md",
    "tools/pi_extension/skills/titan-memory-workflow/SKILL.md",
    "assets/titan-pi-card.png", "assets/titan-pi-banner.png",
)


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

    for runtime_root in ("app/**/*.py", "tools/pi_extension/skills/"):
        assert runtime_root in package_files
    for excluded_root in ("entrypoints/**/*.py", "entrypoints/overnight/scripts/", "config/"):
        assert excluded_root not in package_files

    for required_file in (
        "entrypoints/__init__.py",
        "entrypoints/main.py",
        "entrypoints/mcp_server.py",
        "config/__init__.py",
        "config/.env.example",
        "config/embedding_models.yaml",
        "config/extraction_models.yaml",
        "config/settings.yaml",
        "config/visual_config.yaml",
    ):
        assert required_file in package_files

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
    assert package["scripts"]["prepack"] == "node tools/release/audit_pi_package.js"

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
    for required in PI_REQUIRED_PAYLOADS:
        assert required in paths
    assert not any("__pycache__" in path or path.endswith((".pyc", ".pyo")) for path in paths)
    assert not any(path.startswith("tests/") or path.startswith("docs/") for path in paths)
    assert not any(path.startswith(("entrypoints/overnight/", "config/overnight")) for path in paths)


def test_pi_privacy_audit_rejects_a_seeded_local_path(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "privacy-fixture", "version": "1.0.0", "files": ["README.md"]}),
        encoding="utf-8",
    )
    (package / "README.md").write_text("/Users/example/private-memory.db\n", encoding="utf-8")

    result = subprocess.run(
        ["node", str(ROOT / "tools/release/audit_pi_package.js"), "--root", str(package)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "macOS home path" in result.stderr


def test_pi_privacy_audit_rejects_a_missing_required_payload(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "missing-fixture", "version": "1.0.0", "files": ["README.md", "entrypoints/main.py"]}),
        encoding="utf-8",
    )
    (package / "README.md").write_text("clean\n", encoding="utf-8")

    result = subprocess.run(
        ["node", str(ROOT / "tools/release/audit_pi_package.js"), "--root", str(package)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "entrypoints/main.py: required Pi payload is missing" in result.stderr


def test_pi_privacy_audit_rejects_internal_note_filenames(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "notes-fixture", "version": "1.0.0", "files": ["README.md", "AGENTS.md", "CONTEXT.md"]}),
        encoding="utf-8",
    )
    (package / "README.md").write_text("clean\n", encoding="utf-8")
    (package / "AGENTS.md").write_text("private\n", encoding="utf-8")
    (package / "CONTEXT.md").write_text("private\n", encoding="utf-8")

    result = subprocess.run(
        ["node", str(ROOT / "tools/release/audit_pi_package.js"), "--root", str(package)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "internal agent notes" in result.stderr


def test_pi_privacy_audit_rejects_a_credential_assignment(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "credential-fixture", "version": "1.0.0", "files": ["README.md"]}),
        encoding="utf-8",
    )
    (package / "README.md").write_text('api_key = "abcdefghijklmnop"\n', encoding="utf-8")

    result = subprocess.run(
        ["node", str(ROOT / "tools/release/audit_pi_package.js"), "--root", str(package)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "credential assignment" in result.stderr


def test_pi_privacy_audit_uses_npm_execpath_with_the_current_node(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    for relative in PI_REQUIRED_PAYLOADS:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative != "package.json":
            target.write_text("clean\n", encoding="utf-8")
    (package / "package.json").write_text(
        json.dumps({"name": "execpath-fixture", "version": "1.0.0", "files": list(PI_REQUIRED_PAYLOADS)}),
        encoding="utf-8",
    )
    marker = tmp_path / "npm-execpath-used"
    fake_npm = tmp_path / "npm-cli.js"
    metadata = [{"files": [{"path": relative} for relative in PI_REQUIRED_PAYLOADS]}]
    fake_npm.write_text(
        "const fs = require('node:fs');\n"
        "fs.writeFileSync(process.env.PI_NPM_MARKER, 'used');\n"
        f"process.stdout.write({json.dumps(json.dumps(metadata))});\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(ROOT / "tools/release/audit_pi_package.js"), "--root", str(package)],
        env={**os.environ, "npm_execpath": str(fake_npm), "PI_NPM_MARKER": str(marker)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.exists()


def test_pi_privacy_audit_resolves_npm_cli_from_path_on_windows_fallback(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    for relative in PI_REQUIRED_PAYLOADS:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative != "package.json":
            target.write_text("clean\n", encoding="utf-8")
    (package / "package.json").write_text(
        json.dumps({"name": "windows-fallback-fixture", "version": "1.0.0", "files": list(PI_REQUIRED_PAYLOADS)}),
        encoding="utf-8",
    )

    npm_bin = tmp_path / "npm-bin"
    fake_npm = npm_bin / "node_modules" / "npm" / "bin" / "npm-cli.js"
    fake_npm.parent.mkdir(parents=True)
    marker = tmp_path / "windows-fallback-used"
    metadata = [{"files": [{"path": relative} for relative in PI_REQUIRED_PAYLOADS]}]
    fake_npm.write_text("placeholder\n", encoding="utf-8")
    fake_node = tmp_path / "node.exe"
    driver = tmp_path / "windows-fallback-driver.js"
    driver.write_text(
        "const fs = require('node:fs');\n"
        "const childProcess = require('node:child_process');\n"
        "Object.defineProperty(process, 'platform', { value: 'win32' });\n"
        f"Object.defineProperty(process, 'execPath', {{ value: {json.dumps(str(fake_node))} }});\n"
        "delete process.env.npm_execpath;\n"
        f"process.env.PATH = {json.dumps(str(npm_bin))};\n"
        f"const metadata = {json.dumps(json.dumps(metadata))};\n"
        "childProcess.spawnSync = (command, args) => {\n"
        f"  fs.writeFileSync({json.dumps(str(marker))}, JSON.stringify({{ command, args }}));\n"
        "  return { status: 0, stdout: metadata, stderr: '' };\n"
        "};\n"
        f"process.argv = ['node', {json.dumps(str(ROOT / 'tools/release/audit_pi_package.js'))}, '--root', {json.dumps(str(package))}];\n"
        f"require({json.dumps(str(ROOT / 'tools/release/audit_pi_package.js'))});\n",
        encoding="utf-8",
    )

    node = shutil.which("node")
    assert node is not None
    result = subprocess.run([node, str(driver)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    invocation = json.loads(marker.read_text(encoding="utf-8"))
    assert invocation["command"] == str(fake_node)
    assert invocation["args"][0] == str(fake_npm)


def test_python_privacy_audit_rejects_a_seeded_local_path(tmp_path):
    artifact = tmp_path / "privacy-fixture.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("app/example.py", "/Users/example/private-memory.db\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/release/audit_python_artifact.py"), str(artifact)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "macOS home path" in result.stderr


def test_python_privacy_audit_rejects_unsafe_archive_member_path(tmp_path):
    artifact = tmp_path / "unsafe.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        payload = b"private fixture\n"
        member = tarfile.TarInfo("../tests/leak.py")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/release/audit_python_artifact.py"), str(artifact)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "unsafe archive member path" in result.stderr


def test_python_privacy_audit_rejects_absolute_archive_member_path(tmp_path):
    artifact = tmp_path / "absolute.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        payload = b"private fixture\n"
        member = tarfile.TarInfo("/tests/leak.py")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/release/audit_python_artifact.py"), str(artifact)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "unsafe archive member path" in result.stderr


def test_python_privacy_audit_rejects_internal_note_filename(tmp_path):
    artifact = tmp_path / "agent-note.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("AGENTS.md", "private note\n")
        archive.writestr("CONTEXT.md", "private context\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/release/audit_python_artifact.py"), str(artifact)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "private-data file type" in result.stderr


def test_python_privacy_audit_rejects_a_credential_assignment(tmp_path):
    artifact = tmp_path / "credential-fixture.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("app/example.py", 'api_key = "abcdefghijklmnop"\n')

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/release/audit_python_artifact.py"), str(artifact)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "credential assignment" in result.stderr


def test_python_package_exposes_the_current_pypi_cli_contract():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["name"] == "titan-memory-cli"
    assert project["license"] == {"file": "LICENSE"}
    assert project["readme"] == "docs/pypi_titan_memory_cli.md"
    assert "mcp>=1.5.0,<2" in project["dependencies"]
    assert project["scripts"]["titan"] == "tools.cli.titan:main"
    assert "integrations*" in pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    excluded = pyproject["tool"]["setuptools"]["packages"]["find"]["exclude"]
    assert "entrypoints.overnight*" in excluded
    assert "tools.benchmarks*" in excluded
    assert "tools.presentations*" in excluded

    config_files = pyproject["tool"]["setuptools"]["package-data"]["config"]
    assert "overnight_manifest.yaml" not in config_files


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
