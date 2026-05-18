import os
from pathlib import Path
import yaml


def load_settings() -> dict:
    override = os.getenv("TITAN_SETTINGS_PATH")
    if override:
        config_path = Path(override).expanduser()
    else:
        config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
