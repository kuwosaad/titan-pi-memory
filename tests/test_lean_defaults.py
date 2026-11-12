from pathlib import Path

import yaml


def test_shipped_defaults_keep_optional_work_opt_in():
    settings = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/settings.yaml").read_text())
    assert settings["step2"]["enabled"] is False
    assert settings["memory_store_read_fallback"] == "none"
    assert settings["patterns"]["record_applications_on_retrieval"] is False
    assert "synthesize_implementation_outcomes" not in settings
    assert "extraction" not in settings
    for key in ("max_per_scene", "max_per_source_event", "semantic_redundancy_threshold",
                "profile_semantic_redundancy_threshold"):
        assert key not in settings["retrieval_selection"]
