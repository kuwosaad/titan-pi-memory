from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.request import urlretrieve

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_DATASET_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DEFAULT_INPUT = ROOT_DIR / "artifacts" / "generated" / "bench" / "locomo" / "locomo10.json"
DEFAULT_RUN_ROOT = ROOT_DIR / "artifacts" / "generated" / "bench" / "locomo" / "runs"
DEFAULT_SETTINGS = ROOT_DIR / "config" / "settings.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe Titan shadow-memory probe on LoCoMo.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to locomo10.json")
    parser.add_argument("--dataset-url", default=DEFAULT_DATASET_URL, help="Download URL if input is missing")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT), help="Directory for benchmark artifacts")
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS), help="Base Titan settings.yaml path")
    parser.add_argument("--limit-dialogues", type=int, default=1, help="How many LoCoMo dialogues to ingest")
    parser.add_argument("--limit-questions", type=int, default=20, help="How many QA rows to evaluate")
    parser.add_argument("--top-k", type=int, default=8, help="Retrieval depth for each question")
    parser.add_argument(
        "--max-exchanges-per-dialogue",
        type=int,
        default=0,
        help="If set, only ingest this many exchange pairs from each dialogue for a quick smoke test.",
    )
    parser.add_argument(
        "--ingest-mode",
        choices=["auto", "full", "fallback-only"],
        default="auto",
        help="Use full extraction, deterministic fallback, or auto-select based on model availability.",
    )
    return parser.parse_args()


def ensure_dataset(input_path: Path, dataset_url: str) -> Path:
    if input_path.exists():
        return input_path
    input_path.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(dataset_url, input_path)
    return input_path


def normalize_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def content_tokens(value: Any) -> set[str]:
    return {token for token in normalize_text(value).split() if len(token) > 2}


def answer_matches(answer: Any, response: Dict[str, Any]) -> bool:
    answer_norm = normalize_text(answer)
    if not answer_norm:
        return False

    texts: List[str] = [response.get("brief") or "", response.get("scene_brief") or ""]
    for memory in response.get("memories") or []:
        texts.append(str(memory.get("text") or ""))
    for scene in response.get("scenes") or []:
        for message in scene.get("messages") or []:
            if isinstance(message, dict):
                texts.append(str(message.get("content") or ""))

    pool_norm = " ".join(normalize_text(text) for text in texts if text)
    if answer_norm in pool_norm:
        return True

    answer_terms = content_tokens(answer)
    pool_terms = content_tokens(pool_norm)
    return bool(answer_terms) and answer_terms.issubset(pool_terms)


def iter_session_keys(conversation: Dict[str, Any]) -> List[str]:
    session_keys = []
    for key in conversation:
        if re.fullmatch(r"session_\d+", key):
            session_keys.append(key)
    session_keys.sort(key=lambda item: int(item.split("_")[-1]))
    return session_keys


def iter_exchange_pairs(sample: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any], Dict[str, Any]]]:
    conversation = sample.get("conversation") or {}
    for session_key in iter_session_keys(conversation):
        session_turns = conversation.get(session_key) or []
        date_key = f"{session_key}_date_time"
        session_date = str(conversation.get(date_key) or "")
        for index in range(0, len(session_turns) - 1, 2):
            first = session_turns[index]
            second = session_turns[index + 1]
            if not isinstance(first, dict) or not isinstance(second, dict):
                continue
            yield session_key, session_date, first, second


def build_shadow_settings(base_settings_path: Path, shadow_root: Path) -> Dict[str, Any]:
    settings = yaml.safe_load(base_settings_path.read_text(encoding="utf-8")) or {}
    settings["memory_store_backend"] = "sqlite"
    settings["memory_store_sqlite_path"] = "out/memories/memory_store.db"
    settings["plugin_spool_dir"] = "out/traces"
    settings["traces_dir"] = "out/traces"
    settings["memories_dir"] = "out/memories"
    settings["sessions_dir"] = "out/sessions"
    settings["graphs_dir"] = "out/graphs"
    settings["output_dir"] = "out"
    settings["locomo_shadow_root"] = str(shadow_root)
    return settings


def write_shadow_settings(base_settings_path: Path, run_dir: Path) -> Path:
    shadow_root = run_dir / "shadow-home"
    shadow_root.mkdir(parents=True, exist_ok=True)
    settings = build_shadow_settings(base_settings_path, shadow_root)
    settings_path = run_dir / "shadow-settings.yaml"
    settings_path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    return settings_path


def import_titan_api(settings_path: Path, run_dir: Path) -> Dict[str, Any]:
    os.environ["TITAN_SETTINGS_PATH"] = str(settings_path)
    os.environ["TITAN_BASE_DIR"] = str((run_dir / "shadow-home").resolve())

    from app.save_pipeline.pipeline import retrieve_memory_brief, run_memory_pipeline_outcome
    from app.embedding.embedder import embed
    from app.save_pipeline.extraction.adapters import get_extraction_adapter
    from app.save_pipeline.extraction.extractor import build_safe_fallback_memories
    from app.storage.memories import append_memories, create_memory_record, get_memory_count
    from app.storage.models import Scene, SceneMessage
    from app.storage.notes import append_memory_notes
    from app.storage.scenes import append_scene, get_recent_scenes
    from app.storage.sessions import ensure_dirs

    ensure_dirs()
    return {
        "retrieve_memory_brief": retrieve_memory_brief,
        "run_memory_pipeline_outcome": run_memory_pipeline_outcome,
        "embed": embed,
        "get_extraction_adapter": get_extraction_adapter,
        "build_safe_fallback_memories": build_safe_fallback_memories,
        "append_memories": append_memories,
        "create_memory_record": create_memory_record,
        "get_memory_count": get_memory_count,
        "Scene": Scene,
        "SceneMessage": SceneMessage,
        "append_memory_notes": append_memory_notes,
        "append_scene": append_scene,
        "get_recent_scenes": get_recent_scenes,
    }


def resolve_ingest_mode(requested: str, titan_api: Dict[str, Any]) -> Tuple[str, str]:
    if requested == "fallback-only":
        return requested, "forced by flag"
    try:
        titan_api["get_extraction_adapter"]()
        return "full", "extraction adapter is available"
    except Exception as exc:
        if requested == "full":
            raise
        return "fallback-only", f"auto fallback because extractor is unavailable: {exc}"


def store_fallback_scene(
    titan_api: Dict[str, Any],
    *,
    session_id: str,
    turn: int,
    session_label: str,
    session_date: str,
    user_turn: Dict[str, Any],
    assistant_turn: Dict[str, Any],
) -> Dict[str, Any]:
    user_text = format_exchange_side(session_label, session_date, user_turn)
    assistant_text = format_exchange_side(session_label, session_date, assistant_turn, include_session_prefix=False)
    extracted = titan_api["build_safe_fallback_memories"](user_text, assistant_text)
    if not extracted:
        return {"records": [], "fallback_used": True, "skip_reason": "empty_after_filter"}

    texts = [item["text"] for item in extracted]
    vectors = titan_api["embed"](texts) if texts else []
    scene = titan_api["Scene"](
        scene_id=f"{session_id}:scene:locomo-{turn}",
        session_id=session_id,
        turn=turn,
        kind="message_exchange",
        anchor_event_id=str(assistant_turn.get("dia_id") or user_turn.get("dia_id") or f"locomo-{turn}"),
        source_event_ids=[str(user_turn.get("dia_id") or ""), str(assistant_turn.get("dia_id") or "")],
        messages=[
            titan_api["SceneMessage"](
                role="user",
                content=user_text,
                message_id=str(user_turn.get("dia_id") or "") or None,
                event_id=None,
            ),
            titan_api["SceneMessage"](
                role="assistant",
                content=assistant_text,
                message_id=str(assistant_turn.get("dia_id") or "") or None,
                event_id=None,
            ),
        ],
        extraction_user_text=user_text,
        extraction_assistant_text=assistant_text,
        used_context_fallback=False,
        ts=datetime.now(timezone.utc).isoformat(),
    )

    records = []
    for index, item in enumerate(extracted):
        vector = vectors[index] if index < len(vectors) else None
        records.append(
            titan_api["create_memory_record"](
                session_id=session_id,
                turn=turn,
                index=index,
                text=item["text"],
                user_text=user_text,
                assistant_text=assistant_text,
                scene_id=scene.scene_id,
                memory_type=item.get("type"),
                stream=item.get("stream", "rough"),
                embedding=vector.tolist() if vector is not None else None,
                source_event_ids=[str(user_turn.get("dia_id") or ""), str(assistant_turn.get("dia_id") or "")],
                source_type=str(item.get("source") or "mixed"),
                source_reliability=float(item.get("reliability") or 0.5),
                verification_status="unverified",
                fallback_generated=True,
                speaker_focus=item.get("speaker_focus"),
                memory_kind=item.get("memory_kind"),
            )
        )

    titan_api["append_memories"](records)
    titan_api["append_scene"](scene)
    titan_api["append_memory_notes"](records)
    return {"records": records, "fallback_used": True, "skip_reason": None}


def format_exchange_side(session_label: str, session_date: str, turn: Dict[str, Any], *, include_session_prefix: bool = True) -> str:
    speaker = str(turn.get("speaker") or "Unknown")
    text = str(turn.get("text") or "").strip()
    if include_session_prefix and session_date:
        return f"[{session_label} at {session_date}] {speaker}: {text}"
    return f"{speaker}: {text}"


def ingest_dialogue(
    titan_api: Dict[str, Any],
    sample: Dict[str, Any],
    dialogue_index: int,
    ingest_mode: str,
    max_exchanges_per_dialogue: int = 0,
) -> Dict[str, Any]:
    session_id = f"locomo-dialogue-{dialogue_index + 1:02d}"
    turn = 1
    exchanges = 0
    stored_memories = 0
    fallback_memories = 0

    for session_label, session_date, user_turn, assistant_turn in iter_exchange_pairs(sample):
        if max_exchanges_per_dialogue > 0 and exchanges >= max_exchanges_per_dialogue:
            break
        user_text = format_exchange_side(session_label, session_date, user_turn)
        assistant_text = format_exchange_side(session_label, session_date, assistant_turn, include_session_prefix=False)
        if ingest_mode == "full":
            outcome = titan_api["run_memory_pipeline_outcome"](
                session_id=session_id,
                turn=turn,
                user_text=user_text,
                assistant_text=assistant_text,
                source_event_ids=[str(user_turn.get("dia_id") or ""), str(assistant_turn.get("dia_id") or "")],
                fallback_enabled=True,
            )
        else:
            outcome = store_fallback_scene(
                titan_api,
                session_id=session_id,
                turn=turn,
                session_label=session_label,
                session_date=session_date,
                user_turn=user_turn,
                assistant_turn=assistant_turn,
            )

        exchanges += 1
        records = outcome.get("records") or []
        if records:
            stored_memories += len(records)
            if outcome.get("fallback_used"):
                fallback_memories += len(records)
            turn += 1

    return {
        "session_id": session_id,
        "dialogue_index": dialogue_index,
        "exchanges": exchanges,
        "stored_memories": stored_memories,
        "fallback_memories": fallback_memories,
        "max_exchanges_per_dialogue": max_exchanges_per_dialogue,
        "scene_count": len(titan_api["get_recent_scenes"](limit=1000, session_id=session_id)),
        "memory_count": titan_api["get_memory_count"](session_id=session_id),
    }


def evaluate_dialogue(
    titan_api: Dict[str, Any],
    sample: Dict[str, Any],
    *,
    session_id: str,
    top_k: int,
    remaining_questions: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for qa in sample.get("qa") or []:
        if len(results) >= remaining_questions:
            break
        question = str(qa.get("question") or "").strip()
        if not question:
            continue
        response = titan_api["retrieve_memory_brief"](
            query=question,
            session_id=session_id,
            mode="both",
            limit=top_k,
        )
        results.append(
            {
                "question": question,
                "answer": qa.get("answer"),
                "category": qa.get("category"),
                "matched": answer_matches(qa.get("answer"), response),
                "memory_ids": [str(memory.get("id") or "") for memory in response.get("memories") or []],
                "memory_texts": [str(memory.get("text") or "") for memory in response.get("memories") or []],
                "scene_ids": [str(scene.get("scene_id") or "") for scene in response.get("scenes") or []],
                "brief": response.get("brief") or "",
                "scene_brief": response.get("scene_brief") or "",
            }
        )
    return results


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, default=str) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")


def main() -> int:
    args = parse_args()
    started = datetime.now(timezone.utc)

    input_path = ensure_dataset(Path(args.input), args.dataset_url)
    run_dir = Path(args.run_root) / started.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    settings_path = write_shadow_settings(Path(args.settings), run_dir)

    titan_api = import_titan_api(settings_path, run_dir)
    ingest_mode, ingest_reason = resolve_ingest_mode(args.ingest_mode, titan_api)
    samples = json.loads(input_path.read_text(encoding="utf-8"))
    selected = samples[: max(0, args.limit_dialogues)]

    ingest_rows = []
    qa_rows: List[Dict[str, Any]] = []
    remaining_questions = max(0, args.limit_questions)

    for dialogue_index, sample in enumerate(selected):
        ingest_row = ingest_dialogue(
            titan_api,
            sample,
            dialogue_index,
            ingest_mode,
            max_exchanges_per_dialogue=max(0, int(args.max_exchanges_per_dialogue or 0)),
        )
        ingest_rows.append(ingest_row)
        if remaining_questions > 0:
            evaluated = evaluate_dialogue(
                titan_api,
                sample,
                session_id=ingest_row["session_id"],
                top_k=args.top_k,
                remaining_questions=remaining_questions,
            )
            qa_rows.extend(evaluated)
            remaining_questions -= len(evaluated)

    matched = sum(1 for row in qa_rows if row.get("matched"))
    summary = {
        "dataset": str(input_path),
        "run_started_at": started.isoformat(),
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
        "ingest_mode": ingest_mode,
        "ingest_mode_reason": ingest_reason,
        "dialogues_ingested": len(ingest_rows),
        "questions_evaluated": len(qa_rows),
        "matched_questions": matched,
        "answer_hit_rate": (matched / len(qa_rows)) if qa_rows else 0.0,
        "top_k": args.top_k,
        "ingest": ingest_rows,
    }

    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "ingest.json", ingest_rows)
    write_jsonl(run_dir / "qa_results.jsonl", qa_rows)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
