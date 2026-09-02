from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal, Optional, TypedDict


InstallScope = Literal["global", "project"]
InstallState = Literal["installed", "updated", "already_up_to_date"]


class InstallStatus(TypedDict):
    status: InstallState
    scope: InstallScope
    target_path: str


def _default_template_path(root_dir: Path) -> Path:
    return (
        root_dir
        / "integrations"
        / "opencode_titan_plugin"
        / "dist"
        / "titan_v2_spool_plugin.ts"
    )


def _project_target_path(root_dir: Path) -> Path:
    return root_dir / ".opencode" / "plugins" / "titan_v2_spool_plugin.ts"


def _global_target_path(config_root: Optional[Path] = None) -> Path:
    base = config_root or (Path.home() / ".config" / "opencode")
    return base / "plugins" / "titan_v2_spool_plugin.ts"


def install_opencode_plugin(
    scope: InstallScope = "project",
    *,
    root_dir: Optional[Path] = None,
    plugin_template: Optional[Path] = None,
    global_config_root: Optional[Path] = None,
) -> InstallStatus:
    root = root_dir or Path(__file__).resolve().parents[2]
    template = plugin_template or _default_template_path(root)
    if not template.exists():
        raise FileNotFoundError(f"Plugin template not found: {template}")

    if scope == "global":
        target = _global_target_path(global_config_root)
    elif scope == "project":
        target = _project_target_path(root)
    else:
        raise ValueError(f"Unsupported scope: {scope}")

    target.parent.mkdir(parents=True, exist_ok=True)
    source_text = template.read_text(encoding="utf-8")

    if not target.exists():
        shutil.copyfile(template, target)
        status: InstallState = "installed"
    else:
        target_text = target.read_text(encoding="utf-8")
        if target_text == source_text:
            status = "already_up_to_date"
        else:
            shutil.copyfile(template, target)
            status = "updated"

    return {
        "status": status,
        "scope": scope,
        "target_path": str(target),
    }
