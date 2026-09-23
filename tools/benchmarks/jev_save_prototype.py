"""Run the real Titan extraction/save path against an isolated SQLite store.

Input is read from ``.bench/jev-save-prototype/input.json`` by default:

{
  "session_id": "jev-save-probe",
  "turns": [
    {
      "turn": 1,
      "user_text": "A public source excerpt or question.",
      "assistant_text": "The corresponding public source excerpt.",
      "source_url": "https://example.test/source"
    }
  ]
}

The probe requires the configured live extraction backend and disables Titan's
deterministic fallback.  All Titan runtime paths, including the SQLite DB,
live under a fresh ignored ``runtime-*`` directory.  No credentials are ever
written to the output.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import patch
from uuid import uuid4


ROOT_DIR = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT_DIR / ".bench" / "jev-save-prototype"
DEFAULT_INPUT = RUN_DIR / "input.json"
DEFAULT_OUTPUT = RUN_DIR / "output.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _opencode_key_from_environment() -> str | None:
    for name in ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"):
        if os.environ.get(name):
            if name != "OPENCODE_GO_API_KEY":
                os.environ.setdefault("OPENCODE_GO_API_KEY", os.environ[name])
            return "environment"

    auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    try:
        auth = read_json(auth_path)
    except (OSError, json.JSONDecodeError):
        return None

    for provider in ("opencode-go", "opencode"):
        entry = auth.get(provider) if isinstance(auth, dict) else None
        if isinstance(entry, dict) and entry.get("key"):
            # The key stays in this process only; never include it in output or
            # an exception raised by this probe.
            os.environ.setdefault("OPENCODE_GO_API_KEY", str(entry["key"]))
            return "opencode auth file"
    return None


def _validate_input(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("input.json must contain an object")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("input.json requires a non-empty session_id")
    raw_turns = payload.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError("input.json requires a non-empty turns array")

    turns: list[dict[str, Any]] = []
    seen_turns: set[int] = set()
    for index, raw in enumerate(raw_turns):
        if not isinstance(raw, dict):
            raise ValueError(f"turns[{index}] must be an object")
        try:
            turn = int(raw.get("turn", index + 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"turns[{index}].turn must be an integer") from exc
        if turn < 1 or turn in seen_turns:
            raise ValueError(f"turns[{index}].turn must be a unique positive integer")
        seen_turns.add(turn)

        user_text = raw.get("user_text")
        assistant_text = raw.get("assistant_text")
        source_url = raw.get("source_url")
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError(f"turns[{index}].user_text must be a non-empty string")
        if not isinstance(assistant_text, str) or not assistant_text.strip():
            raise ValueError(f"turns[{index}].assistant_text must be a non-empty string")
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError(f"turns[{index}].source_url must be a non-empty string")
        turns.append(
            {
                "turn": turn,
                "user_text": user_text,
                "assistant_text": assistant_text,
                "source_url": source_url.strip(),
            }
        )

    turns.sort(key=lambda item: item["turn"])
    return session_id, turns


def _public_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    current = str(config.get("current") or "")
    selected = config.get(current) if isinstance(config.get(current), dict) else {}
    if not isinstance(selected, dict):
        selected = {}
    # Model and endpoint are useful provenance; credentials are intentionally
    # omitted even if a local config contains an inline key.
    return {
        "current": current,
        "backend": current,
        "model": selected.get("model"),
        "base_url": selected.get("base_url"),
    }


def _record_without_private_embedding(record: dict[str, Any]) -> dict[str, Any]:
    # The vector is a normal persisted field and is retained for Jev review.
    # Strip only internal repository fields if a future repository exposes them.
    return {key: value for key, value in record.items() if not key.startswith("_")}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Reuse an existing ignored runtime-* directory for a targeted retry.",
    )
    parser.add_argument("--turn", type=int, help="Process only this input turn when resuming a failed run.")
    return parser.parse_args()


def run(
    input_path: Path,
    output_path: Path,
    runtime_path: Path | None = None,
    only_turn: int | None = None,
) -> dict[str, Any]:
    payload = read_json(input_path)
    session_id, turns = _validate_input(payload)

    run_dir = output_path.parent.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    if runtime_path is None:
        runtime_dir = run_dir / f"runtime-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{os.getpid()}"
        runtime_dir.mkdir(parents=True, exist_ok=False)
    else:
        runtime_dir = runtime_path.resolve()
        if runtime_dir.parent != run_dir or not runtime_dir.name.startswith("runtime-"):
            raise ValueError("--runtime-dir must be an existing or new runtime-* directory beside output.json")
        runtime_dir.mkdir(parents=True, exist_ok=True)
    scratch_home = runtime_dir / "home"
    scratch_base = runtime_dir / "base"
    scratch_db = runtime_dir / "memory_store.db"
    scratch_spool = runtime_dir / "traces"

    # Set every storage selector explicitly so inherited Titan variables cannot
    # redirect a write to a personal or shared store.
    os.environ.update(
        {
            "TITAN_HOME": str(scratch_home),
            "TITAN_BASE_DIR": str(scratch_base),
            "TITAN_SHARED_HOME": str(scratch_home),
            "TITAN_AGENT_NAME": "jev-save-prototype",
            "TITAN_SPOOL_DIR": str(scratch_spool),
            "TITAN_MEMORY_BACKEND": "sqlite",
            "TITAN_MEMORY_DB_PATH": str(scratch_db),
            "TITAN_MEMORY_READ_FALLBACK": "sqlite",
            "TITAN_AUTO_INGEST_ENABLED": "0",
            "TITAN_SETTINGS_PATH": str(ROOT_DIR / "config" / "settings.yaml"),
            "TITAN_EXTRACTION_CONFIG_PATH": str(ROOT_DIR / "config" / "extraction_models.yaml"),
            "TITAN_EMBEDDING_CONFIG_PATH": str(ROOT_DIR / "config" / "embedding_models.yaml"),
        }
    )
    auth_source = _opencode_key_from_environment()
    if not auth_source:
        raise RuntimeError("No OpenCode extraction credential found in environment or ~/.local/share/opencode/auth.json")

    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

    # Imports intentionally happen after the isolated environment is installed;
    # Titan's legacy storage modules resolve their context at import time.
    from app.runtime.context import reset_runtime_context_cache

    reset_runtime_context_cache()
    from app.save_pipeline.pipeline import run_memory_pipeline_outcome
    import app.save_pipeline.extraction.extractor as extractor_module
    from app.storage.memories import get_memory_repository
    from app.storage.sessions import ensure_dirs

    ensure_dirs()
    repository = get_memory_repository()
    before_count = repository.get_memory_count(session_id=session_id)
    config_meta = _public_config(ROOT_DIR / "config" / "extraction_models.yaml")
    work_turns = turns
    if only_turn is not None:
        work_turns = [item for item in turns if int(item["turn"]) == only_turn]
        if not work_turns:
            raise ValueError(f"input.json has no turn {only_turn}")

    output_turns: list[dict[str, Any]] = []
    all_extracted: list[dict[str, Any]] = []
    failed_turn: dict[str, Any] | None = None
    started_all = time.perf_counter()
    import requests

    # OpenCode's Go gateway requires a routing session header.  The production
    # adapter remains responsible for the request body, auth, and response
    # parsing; this probe supplies only the per-run routing identity locally.
    original_post = requests.post
    opencode_session = f"jev-save-probe-{uuid4()}"

    def routed_post(*args: Any, **kwargs: Any) -> Any:
        url = str(args[0]) if args else str(kwargs.get("url") or "")
        if "opencode.ai/zen/go/" in url:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("x-opencode-session", opencode_session)
            kwargs["headers"] = headers
        return original_post(*args, **kwargs)

    def reject_invalid_model_output(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("model extraction returned invalid JSON; deterministic fallback blocked")

    with patch("requests.post", side_effect=routed_post), patch.object(
        extractor_module, "fallback_memories", side_effect=reject_invalid_model_output
    ):
        for item in work_turns:
            turn = int(item["turn"])
            source_event_id = f"jev-save-probe:{session_id}:{turn}"
            started = time.perf_counter()
            try:
                outcome = run_memory_pipeline_outcome(
                    session_id=session_id,
                    turn=turn,
                    user_text=item["user_text"],
                    assistant_text=item["assistant_text"],
                    source_event_ids=[source_event_id],
                    fallback_enabled=False,
                )
            except Exception as exc:
                failed_turn = {
                    "turn": turn,
                    "source_url": item["source_url"],
                    "source_event_ids": [source_event_id],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error_type": type(exc).__name__,
                    "fallback_or_mocking_possible": False,
                }
                print(f"turn {turn}: failed with {type(exc).__name__}", file=sys.stderr, flush=True)
                break
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            extracted = [_record_without_private_embedding(record) for record in outcome.get("records", [])]
            all_extracted.extend(extracted)
            output_turns.append(
                {
                    "turn": turn,
                    "source_url": item["source_url"],
                    "source_event_ids": [source_event_id],
                    "elapsed_ms": elapsed_ms,
                    "extracted_count": len(extracted),
                    "saved_count": len(extracted),
                    "fallback_used": bool(outcome.get("fallback_used", False)),
                    "skipped_low_signal": bool(outcome.get("skipped_low_signal", False)),
                    "skip_reason": outcome.get("skip_reason"),
                    "record_ids": [record.get("id") for record in extracted],
                    "status": "ok",
                }
            )
            print(f"turn {turn}: extracted and saved {len(extracted)} records in {elapsed_ms} ms", flush=True)

    # Read the actual SQLite rows back after the pipeline returns.  This makes
    # the output evidence of persistence, rather than only evidence of model
    # extraction.
    persisted = [
        _record_without_private_embedding(record)
        for record in repository.load_all_memories()
        if record.get("session_id") == session_id
    ]
    persisted_by_id = {str(record.get("id")): record for record in persisted}
    for record in persisted:
        record.setdefault("source_url", next(
            (item["source_url"] for item in turns if str(record.get("id", "")).startswith(f"{session_id}:{item['turn']}:")),
            None,
        ))

    result = {
        "status": "error" if failed_turn else "ok",
        "metadata": {
            "schema_version": 1,
            "probe": "jev-save-prototype",
            "session_id": session_id,
            "input_path": str(input_path.resolve()),
            "runtime_dir": str(runtime_dir),
            "memory_db_path": str(scratch_db),
            "extraction": config_meta,
            "credential_source": auth_source,
            "fallback_enabled": False,
            "real_extraction_required": True,
            "invalid_json_fallback_blocked": True,
            "transport_header_shim": {
                "required": True,
                "header": "x-opencode-session",
                "reason": "OpenCode Go gateway returned MissingSessionID without routing header",
                "injected_locally": True,
            },
            "selected_turns": [int(item["turn"]) for item in work_turns],
            "before_count_for_session": before_count,
            "after_count_for_session": len(persisted),
            "elapsed_ms": round((time.perf_counter() - started_all) * 1000, 1),
        },
        "turns": output_turns,
        "records": persisted,
        "extracted_records": all_extracted,
        "persistence_check": {
            "extracted_ids": sorted(str(record.get("id")) for record in all_extracted),
            "persisted_ids": sorted(persisted_by_id),
            "all_extracted_ids_persisted": all(str(record.get("id")) in persisted_by_id for record in all_extracted),
        },
    }
    if failed_turn is not None:
        result["error"] = failed_turn
    write_json(output_path, result)
    return result


def main() -> int:
    args = parse_args()
    try:
        result = run(
            args.input.resolve(),
            args.output.resolve(),
            runtime_path=args.runtime_dir,
            only_turn=args.turn,
        )
    except Exception as exc:
        # Keep failures actionable without exposing credential-bearing exception
        # payloads.  A failed run never claims that extraction was real/successful.
        print(f"jev save probe failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if result["status"] != "ok":
        print(f"jev save probe failed: {result['error']['error_type']}; partial output written", file=sys.stderr)
        return 1

    metadata = result["metadata"]
    print(
        f"saved {len(result['records'])} records from {len(result['turns'])} turns "
        f"using {metadata['extraction'].get('backend')}/{metadata['extraction'].get('model')} "
        f"in {metadata['elapsed_ms']} ms",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
