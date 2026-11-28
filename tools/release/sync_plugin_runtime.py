"""Generate (or verify) the self-contained runtime assets used by native hosts."""
import argparse
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHERS = (
    "integrations/claude_titan_plugin/scripts/titan_runtime.py",
    "integrations/codex_titan_plugin/scripts/titan_plugin_runtime.py",
    "integrations/grok_titan_plugin/scripts/titan_plugin_runtime.py",
    "integrations/hermes_titan_plugin/titan_runtime.py",
    "integrations/opencode_titan_plugin/scripts/titan_runtime.py",
    "tools/pi_extension/titan_runtime.py",
)
FILES = (
    "LICENSE", "requirements.txt", "app/**/*.py", "app/graph/ui/*.js", "app/graph/ui/*.html",
    "app/graph/ui/*.css", "app/graph/ui/vendor/*.js", "app/graph/ui/vendor/*.LICENSE",
    "entrypoints/__init__.py", "entrypoints/main.py", "entrypoints/mcp_server.py",
    "config/__init__.py", "config/.env.example", "config/settings.yaml", "config/visual_config.yaml",
    "config/embedding_models.yaml", "config/extraction_models.yaml", "tools/__init__.py",
    "tools/cli/*.py", "tools/cli/onboarding_ui/index.html", "tools/cli/onboarding_ui/app.js",
    "tools/cli/onboarding_ui/style.css", "tools/cli/onboarding_ui/titan-logo.svg",
    "tools/cli/onboarding_ui/FONT-LICENSE.md", "tools/cli/onboarding_ui/FONT-SOURCE.txt",
    "tools/cli/onboarding_ui/source-serif-regular.ttf", "tools/opencode/__init__.py",
    "tools/opencode/install_plugin.py", "integrations/__init__.py",
    "integrations/codex_titan_plugin/__init__.py", "integrations/codex_titan_plugin/pending_recovery.py",
)


def engine_files():
    files = {}
    for pattern in FILES:
        paths = sorted(ROOT.glob(pattern))
        if not paths:
            raise RuntimeError(f"Missing required engine input: {pattern}")
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Unsupported runtime source: {path}")
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return files


def engine_archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = engine_files()
    reference = ROOT / LAUNCHERS[0]
    published = reference.with_name("titan_engine.zip")
    if args.check and published.exists():
        archive = published.read_bytes()
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            if sorted(bundle.namelist()) != sorted(files) or any(bundle.read(name) != data for name, data in files.items()):
                raise SystemExit("Native engine is stale. Run tools/release/sync_plugin_runtime.py")
    else:
        archive = engine_archive(files)
    launcher = (ROOT / "tools/plugin_runtime/launcher.py").read_bytes()
    stale = []
    for name in LAUNCHERS:
        script = ROOT / name
        for path, data in [(script, launcher), (script.with_name("titan_engine.zip"), archive)]:
            if path.exists() and path.read_bytes() == data:
                continue
            if args.check:
                stale.append(path.relative_to(ROOT).as_posix())
            else:
                path.write_bytes(data)
    if stale:
        raise SystemExit("Regenerate native plugin assets with tools/release/sync_plugin_runtime.py:\n" + "\n".join(stale))


if __name__ == "__main__":
    main()
