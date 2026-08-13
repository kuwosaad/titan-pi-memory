from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT_DIR = Path(__file__).resolve().parents[2]


def _preparse_paths(argv: Sequence[str]) -> Tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bench-dir")
    parser.add_argument("--titan-home")
    args, _ = parser.parse_known_args(argv)

    bench_dir = Path(args.bench_dir).expanduser().resolve() if args.bench_dir else (ROOT_DIR / "artifacts" / "generated" / "bench" / "longmemeval-oracle")
    titan_home = Path(args.titan_home).expanduser().resolve() if args.titan_home else (bench_dir / "titan-home")
    return bench_dir, titan_home


BENCH_DIR, TITAN_HOME = _preparse_paths(sys.argv[1:])
os.environ["TITAN_HOME"] = str(TITAN_HOME)
os.environ["TITAN_BASE_DIR"] = str(TITAN_HOME)
os.environ.setdefault("TITAN_AUTO_INGEST_ENABLED", "0")


def _set_default_config_overrides() -> None:
    config_dir = BENCH_DIR / "config"
    os.environ.setdefault("TITAN_SETTINGS_PATH", str(config_dir / "settings.yaml"))
    os.environ.setdefault("TITAN_EXTRACTION_CONFIG_PATH", str(config_dir / "extraction_models.yaml"))
    os.environ.setdefault("TITAN_EMBEDDING_CONFIG_PATH", str(config_dir / "embedding_models.yaml"))


_set_default_config_overrides()

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.save_pipeline.pipeline import process_session_events, retrieve_memory_brief
from app.save_pipeline.extraction.adapters import get_extraction_adapter
from app.storage.traces import append_events_batch


DEFAULT_READER_PROMPT = """You answer LongMemEval benchmark questions for a memory system evaluation.

Rules:
- Use only the supplied retrieved memory.
- If the memory is insufficient, answer exactly: I don't know.
- Keep the answer short and direct.
- Do not mention the benchmark, retrieval, or missing context.
"""


@dataclass
class BenchmarkItem:
    question_id: str
    question_type: str
    question: str
    answer: str
    question_date: Optional[str]
    haystack_sessions: List[List[Dict[str, Any]]]
    haystack_dates: List[Optional[str]]
    haystack_session_ids: List[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Titan on LongMemEval oracle in an isolated TITAN_HOME.")
    parser.add_argument("--dataset", required=True, help="Path to longmemeval_oracle.json")
    parser.add_argument("--bench-dir", default=str(BENCH_DIR), help="Directory for benchmark artifacts")
    parser.add_argument("--titan-home", default=str(TITAN_HOME), help="Isolated TITAN_HOME for this benchmark run")
    parser.add_argument("--mode", choices=["pilot", "full"], default="pilot", help="Pilot uses a small stratified subset.")
    parser.add_argument("--pilot-size", type=int, default=20, help="Number of questions in pilot mode.")
    parser.add_argument("--max-questions", type=int, default=None, help="Optional cap after mode-based selection.")
    parser.add_argument("--run-name", default=None, help="Optional run name. Defaults to mode plus timestamp.")
    parser.add_argument("--retrieval-limit", type=int, default=8, help="Top-k memories to retrieve per question.")
    parser.add_argument("--brief-max-items", type=int, default=6, help="Max memories to summarize in Titan brief.")
    parser.add_argument("--brief-max-chars", type=int, default=700, help="Max Titan brief length.")
    parser.add_argument("--reader-prompt", default=DEFAULT_READER_PROMPT, help="System prompt for the reader model.")
    parser.add_argument("--reader-temperature", type=float, default=0.1, help="Temperature for final answer generation.")
    parser.add_argument("--resume", action="store_true", help="Skip questions already present in the predictions file.")
    parser.add_argument("--skip-eval", action="store_true", help="Skip the official LongMemEval evaluator step.")
    parser.add_argument("--longmemeval-repo", default=None, help="Path to a cloned LongMemEval repo for official evaluation.")
    parser.add_argument("--eval-model", default="gpt-4o", help="Model name passed to LongMemEval's evaluate_qa.py.")
    parser.add_argument("--python-bin", default=sys.executable, help="Python executable for running the official evaluator.")
    parser.add_argument("--enable-lnn", action="store_true", help="Enable benchmark-only LNN ODE reranking.")
    parser.add_argument("--max-runtime-minutes", type=float, default=None,
                        help="Hard wall-clock timeout for the entire run. Questions in-flight are abandoned when deadline is reached.")
    parser.add_argument("--notify-webhook", default=None,
                        help="URL to POST run summary JSON after completion (optional).")
    parser.add_argument("--notify-artifact", default=None,
                        help="Path to write a {run_name}.done marker file after completion (optional).")
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_benchmark_config_files(bench_dir: Path, titan_home: Path, *, enable_lnn: bool) -> Dict[str, str]:
    config_dir = bench_dir / "config"
    ensure_dir(config_dir)

    settings = """# Benchmark-only Titan settings
output_dir: out
sessions_dir: out/sessions
memories_dir: out/memories
traces_dir: out/traces
graphs_dir: out/graphs
memories_file: memories.json
memory_store_backend: sqlite
memory_store_sqlite_path: out/memories/memory_store.db
memory_store_read_fallback: json
trace_packets_file: trace_packets.json
trace_events_file: events.jsonl
event_index_file: event_index.json
checkpoints_file: checkpoints.json
graph_file: graph.html
graph_artifacts_file: graph_artifacts.json
host: 127.0.0.1
port: 8000
ingest_mode: event_first
plugin_spool_dir: artifacts/generated/bench/no-spool
ingest_spool_mode: incremental
ingest_spool_max_lines_per_pass: 20000
ingest_debug_metrics_enabled: true
retrieval_enabled: true
retrieval_top_k: 8
retrieval_min_similarity: 0.25
retrieval_recency_days:
retrieval_session_bias: true
notes_max_items: 6
notes_max_chars: 700
router_schema_version: v2
router_mode: rule_based
source_reliability:
  user: 0.9
  assistant: 0.3
  code: 1.0
  mixed: 0.5
  legacy: 0.3
verification:
  enabled: true
  verify_code_facts: true
  verify_api_claims: true
  max_verification_time: 5
  verification_cache_enabled: true
retrieval:
  min_reliability: 0.4
  allow_unverified: true
  show_source_notes: false
extraction:
  assistant_hallucination_warning: true
  require_user_corroboration: true
  skip_unverifiable_technical: true
lnn:
  enabled: {str(enable_lnn).lower()}
  use_ode_rerank: {str(enable_lnn).lower()}
  tick_enabled: false
  debug_activation_trace: true
"""

    extraction = """current: gemini

ollama:
  enabled: false
  base_url: http://localhost:11434
  model: llama3.1:8b

openrouter:
  enabled: false
  api_key_env: OPENROUTER_API_KEY
  base_url: https://openrouter.ai/api/v1
  model: meta-llama/llama-3.1-8b-instruct:free

openai:
  enabled: false
  api_key_env: OPENAI_API_KEY
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini

gemini:
  enabled: true
  api_key_env: GEMINI_API_KEY
  base_url: https://generativelanguage.googleapis.com/v1beta
  model: gemini-2.0-flash
"""

    embedding = """current: ollama

ollama:
  enabled: true
  base_url: http://localhost:11434
  model: nomic-embed-text:v1.5

openai:
  enabled: false
  api_key_env: OPENAI_API_KEY
  base_url: https://api.openai.com/v1
  model: text-embedding-3-small
"""

    settings_path = config_dir / "settings.yaml"
    extraction_path = config_dir / "extraction_models.yaml"
    embedding_path = config_dir / "embedding_models.yaml"
    settings_path.write_text(settings, encoding="utf-8")
    extraction_path.write_text(extraction, encoding="utf-8")
    embedding_path.write_text(embedding, encoding="utf-8")

    os.environ["TITAN_SETTINGS_PATH"] = str(settings_path)
    os.environ["TITAN_EXTRACTION_CONFIG_PATH"] = str(extraction_path)
    os.environ["TITAN_EMBEDDING_CONFIG_PATH"] = str(embedding_path)
    os.environ["TITAN_HOME"] = str(titan_home)
    os.environ["TITAN_BASE_DIR"] = str(titan_home)
    return {
        "settings": str(settings_path),
        "extraction": str(extraction_path),
        "embedding": str(embedding_path),
    }


def load_dataset(path: Path) -> List[BenchmarkItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items: List[BenchmarkItem] = []
    for raw in payload:
        items.append(
            BenchmarkItem(
                question_id=str(raw.get("question_id") or ""),
                question_type=str(raw.get("question_type") or ""),
                question=str(raw.get("question") or ""),
                answer=str(raw.get("answer") or ""),
                question_date=_optional_str(raw.get("question_date")),
                haystack_sessions=list(raw.get("haystack_sessions") or []),
                haystack_dates=[_optional_str(item) for item in list(raw.get("haystack_dates") or [])],
                haystack_session_ids=[str(item) for item in list(raw.get("haystack_session_ids") or [])],
            )
        )
    return items


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def select_items(items: List[BenchmarkItem], mode: str, pilot_size: int, max_questions: Optional[int]) -> List[BenchmarkItem]:
    selected = items
    if mode == "pilot":
        selected = stratified_pilot(items, pilot_size)
    if max_questions is not None:
        selected = selected[: max(0, max_questions)]
    return selected


def stratified_pilot(items: List[BenchmarkItem], pilot_size: int) -> List[BenchmarkItem]:
    grouped: Dict[str, List[BenchmarkItem]] = defaultdict(list)
    for item in sorted(items, key=lambda current: (current.question_type, current.question_id)):
        grouped[item.question_type].append(item)

    ordered_types = sorted(grouped)
    selected: List[BenchmarkItem] = []
    index = 0
    while len(selected) < pilot_size and ordered_types:
        question_type = ordered_types[index % len(ordered_types)]
        bucket = grouped[question_type]
        if bucket:
            selected.append(bucket.pop(0))
        ordered_types = [value for value in ordered_types if grouped[value]]
        index += 1
    return selected


def build_run_name(mode: str, run_name: Optional[str]) -> str:
    if run_name:
        return run_name
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{mode}-{stamp}"


def run_dir(bench_dir: Path, run_name: str) -> Path:
    return bench_dir / "runs" / run_name


def load_done_question_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        question_id = str(payload.get("question_id") or "").strip()
        if question_id:
            done.add(question_id)
    return done


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")


def sanitize_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    return cleaned.strip("-") or "item"


def parse_benchmark_datetime(value: Optional[str], fallback_index: int = 0) -> datetime:
    if value:
        normalized = value.strip()
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            pass
        try:
            return datetime.combine(datetime.strptime(normalized, "%Y-%m-%d").date(), time(12, 0), tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(days=fallback_index)


def sorted_history(item: BenchmarkItem) -> List[Tuple[int, Optional[str], str, List[Dict[str, Any]]]]:
    rows: List[Tuple[int, Optional[str], str, List[Dict[str, Any]]]] = []
    total_sessions = max(len(item.haystack_sessions), len(item.haystack_dates), len(item.haystack_session_ids))
    for index in range(total_sessions):
        session = item.haystack_sessions[index] if index < len(item.haystack_sessions) else []
        date = item.haystack_dates[index] if index < len(item.haystack_dates) else None
        session_id = item.haystack_session_ids[index] if index < len(item.haystack_session_ids) else f"session-{index + 1}"
        rows.append((index, date, session_id, session))
    rows.sort(key=lambda row: (parse_benchmark_datetime(row[1], fallback_index=row[0]), row[0]))
    return rows


def build_benchmark_events(item: BenchmarkItem) -> Tuple[str, List[Dict[str, Any]], Dict[str, int]]:
    titan_session_id = f"longmemeval-oracle-{sanitize_id(item.question_id)}"
    events: List[Dict[str, Any]] = []
    stats = {"history_sessions": 0, "raw_turns": 0, "paired_assistant_turns": 0}

    for history_index, (original_index, session_date, source_session_id, turns) in enumerate(sorted_history(item), start=1):
        stats["history_sessions"] += 1
        base_dt = parse_benchmark_datetime(session_date, fallback_index=original_index)
        pending_user_message_id: Optional[str] = None

        for turn_index, turn in enumerate(turns, start=1):
            role = str(turn.get("role") or "").strip().lower()
            content = str(turn.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue

            stats["raw_turns"] += 1
            turn_dt = base_dt + timedelta(seconds=((turn_index - 1) * 4))
            text = decorate_turn_text(
                content=content,
                session_date=session_date,
                source_session_id=source_session_id,
                history_index=history_index,
                turn_index=turn_index,
                include_header=(turn_index == 1),
            )

            message_id = f"{titan_session_id}-hs{history_index}-m{turn_index}-{role}"
            parent_id = pending_user_message_id if role == "assistant" else None
            updated_event_id = f"{message_id}-updated"
            part_event_id = f"{message_id}-part"

            events.append(
                build_message_updated_event(
                    session_id=titan_session_id,
                    event_id=updated_event_id,
                    message_id=message_id,
                    role=role,
                    text=text,
                    ts=turn_dt.isoformat(),
                    parent_id=parent_id,
                )
            )
            events.append(
                build_message_part_event(
                    session_id=titan_session_id,
                    event_id=part_event_id,
                    message_id=message_id,
                    text=text,
                    ts=(turn_dt + timedelta(seconds=1)).isoformat(),
                )
            )

            if role == "user":
                pending_user_message_id = message_id
            elif pending_user_message_id:
                stats["paired_assistant_turns"] += 1
                pending_user_message_id = None

    return titan_session_id, events, stats


def decorate_turn_text(
    *,
    content: str,
    session_date: Optional[str],
    source_session_id: str,
    history_index: int,
    turn_index: int,
    include_header: bool,
) -> str:
    if not include_header:
        return content
    date_label = session_date or "unknown-date"
    header = f"[LongMemEval session {history_index} | source_session_id={source_session_id} | session_date={date_label} | turn={turn_index}]"
    return f"{header}\n{content}"


def build_message_updated_event(
    *,
    session_id: str,
    event_id: str,
    message_id: str,
    role: str,
    text: str,
    ts: str,
    parent_id: Optional[str],
) -> Dict[str, Any]:
    info: Dict[str, Any] = {"id": message_id, "role": role, "text": text, "summary": text}
    if parent_id:
        info["parentID"] = parent_id
    return {
        "session_id": session_id,
        "event_id": event_id,
        "event_type": "benchmark_message_updated",
        "ts": ts,
        "payload": {"raw_type": "message.updated", "body": {"properties": {"info": info}}},
        "schema_version": "v1",
    }


def build_message_part_event(
    *,
    session_id: str,
    event_id: str,
    message_id: str,
    text: str,
    ts: str,
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "event_id": event_id,
        "event_type": "benchmark_message_part_updated",
        "ts": ts,
        "payload": {
            "raw_type": "message.part.updated",
            "body": {"properties": {"part": {"type": "text", "messageID": message_id, "text": text}}},
        },
        "schema_version": "v1",
    }


def ingest_item(item: BenchmarkItem) -> Dict[str, Any]:
    titan_session_id, events, stats = build_benchmark_events(item)
    append_result = append_events_batch(events)
    process_result = process_session_events(titan_session_id, limit=max(len(events) + 10, 200))
    return {
        "session_id": titan_session_id,
        "events_built": len(events),
        "append": append_result,
        "process": process_result,
        "stats": stats,
    }


def get_reader_adapter():
    return get_extraction_adapter()


def render_memory_context(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return "No retrieved memories."
    lines: List[str] = []
    for index, memory in enumerate(memories, start=1):
        memory_text = str(memory.get("text") or "").strip()
        memory_type = str(memory.get("type") or "unknown")
        stream = str(memory.get("stream") or "rough")
        lines.append(f"{index}. [{stream}/{memory_type}] {memory_text}")
    return "\n".join(lines)


def answer_question(
    *,
    adapter: Any,
    item: BenchmarkItem,
    retrieval: Dict[str, Any],
    prompt: str,
    temperature: float,
) -> str:
    memory_context = render_memory_context(list(retrieval.get("memories") or []))
    brief = str(retrieval.get("brief") or "").strip() or "No brief available."
    user_prompt = (
        f"Question date: {item.question_date or 'unknown'}\n"
        f"Question type: {item.question_type}\n"
        f"Question: {item.question}\n\n"
        f"Titan memory brief:\n{brief}\n\n"
        f"Top retrieved memories:\n{memory_context}\n"
    )
    raw = adapter.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )
    answer = " ".join(str(raw or "").strip().split())
    return answer or "I don't know."


def evaluate_predictions(
    *,
    repo_path: Path,
    python_bin: str,
    eval_model: str,
    predictions_file: Path,
    dataset_path: Path,
    run_directory: Path,
) -> Dict[str, Any]:
    script_path = repo_path / "src" / "evaluation" / "evaluate_qa.py"
    if not script_path.exists():
        raise FileNotFoundError(f"LongMemEval evaluator not found at {script_path}")

    command = [python_bin, str(script_path), eval_model, str(predictions_file), str(dataset_path)]
    completed = subprocess.run(
        command,
        cwd=str(script_path.parent),
        capture_output=True,
        text=True,
        check=False,
    )

    stdout_path = run_directory / "evaluation_stdout.txt"
    stderr_path = run_directory / "evaluation_stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")

    log_path = Path(f"{predictions_file}.log")
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "log_path": str(log_path) if log_path.exists() else None,
    }


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def parse_eval_label(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "correct", "yes"}:
            return True
        if lowered in {"0", "false", "incorrect", "no"}:
            return False
    return None


def infer_failure_bucket(question_type: str, meta: Dict[str, Any], correct: bool) -> Optional[str]:
    if correct:
        return None
    stored_memories = int(((meta.get("ingest") or {}).get("process") or {}).get("stored_memories") or 0)
    retrieval_count = int(((meta.get("retrieval") or {}).get("count")) or 0)
    if stored_memories <= 0:
        return "extraction issue"
    if retrieval_count <= 0:
        return "retrieval miss"
    if question_type == "temporal-reasoning":
        return "temporal reasoning weakness"
    if question_type == "knowledge-update":
        return "knowledge update weakness"
    return "reader answering issue"


def summarize_run(
    *,
    run_name: str,
    mode: str,
    dataset_path: Path,
    predictions_path: Path,
    metadata_path: Path,
    evaluation_info: Optional[Dict[str, Any]],
    run_directory: Path,
) -> Dict[str, Any]:
    dataset_items = {item.question_id: item for item in load_dataset(dataset_path)}
    predictions = {str(row.get("question_id") or ""): row for row in load_jsonl(predictions_path)}
    metadata_rows = {str(row.get("question_id") or ""): row for row in load_jsonl(metadata_path)}

    summary: Dict[str, Any] = {
        "run_name": run_name,
        "mode": mode,
        "questions": len(predictions),
        "evaluation": evaluation_info or {"status": "skipped"},
        "per_question_type": {},
        "failure_buckets": {},
        "sample_failures": [],
    }

    log_path = Path(evaluation_info["log_path"]) if evaluation_info and evaluation_info.get("log_path") else None
    if not log_path or not log_path.exists():
        write_summary_files(run_directory, summary)
        return summary

    per_type: Dict[str, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    failure_buckets: Dict[str, int] = defaultdict(int)
    sample_failures: List[Dict[str, Any]] = []

    for row in load_jsonl(log_path):
        question_id = str(row.get("question_id") or "")
        if not question_id or question_id not in dataset_items:
            continue

        item = dataset_items[question_id]
        correct = parse_eval_label(row.get("autoeval_label"))
        if correct is None:
            continue
        per_type[item.question_type]["total"] += 1
        if correct:
            per_type[item.question_type]["correct"] += 1

        meta = metadata_rows.get(question_id, {})
        bucket = infer_failure_bucket(item.question_type, meta, correct)
        if bucket:
            failure_buckets[bucket] += 1
            if len(sample_failures) < 12:
                sample_failures.append(
                    {
                        "question_id": question_id,
                        "question_type": item.question_type,
                        "failure_bucket": bucket,
                        "question": item.question,
                        "gold_answer": item.answer,
                        "hypothesis": str(predictions.get(question_id, {}).get("hypothesis") or ""),
                        "retrieval_count": int(((meta.get("retrieval") or {}).get("count")) or 0),
                        "brief": str(((meta.get("retrieval") or {}).get("brief")) or ""),
                    }
                )

    total_correct = sum(bucket["correct"] for bucket in per_type.values())
    total_questions = sum(bucket["total"] for bucket in per_type.values())
    summary["overall_accuracy"] = (total_correct / total_questions) if total_questions else None
    summary["scored_questions"] = total_questions
    summary["per_question_type"] = per_type
    summary["failure_buckets"] = dict(sorted(failure_buckets.items()))
    summary["sample_failures"] = sample_failures
    write_summary_files(run_directory, summary)
    return summary


def write_summary_files(run_directory: Path, summary: Dict[str, Any]) -> None:
    json_path = run_directory / "summary.json"
    md_path = run_directory / "summary.md"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")

    accuracy = summary.get("overall_accuracy")
    accuracy_text = f"{accuracy:.2%}" if isinstance(accuracy, float) else "not available"
    lines = [
        f"# LongMemEval oracle summary: {summary.get('run_name')}",
        "",
        f"- mode: {summary.get('mode')}",
        f"- answered questions: {summary.get('questions')}",
        f"- scored questions: {summary.get('scored_questions', 0)}",
        f"- overall accuracy: {accuracy_text}",
        "",
        "## Failure buckets",
    ]

    failure_buckets = summary.get("failure_buckets") or {}
    if failure_buckets:
        for label, count in failure_buckets.items():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("- not available")

    lines.append("")
    lines.append("## Question type breakdown")
    per_type = summary.get("per_question_type") or {}
    if per_type:
        for question_type, counts in sorted(per_type.items()):
            total = int(counts.get("total") or 0)
            correct = int(counts.get("correct") or 0)
            ratio = (correct / total) if total else 0.0
            lines.append(f"- {question_type}: {correct}/{total} ({ratio:.2%})")
    else:
        lines.append("- not available")

    lines.append("")
    lines.append("## Sample failures")
    failures = summary.get("sample_failures") or []
    if failures:
        for failure in failures:
            lines.append(
                f"- {failure['question_id']} [{failure['question_type']}] {failure['failure_bucket']} | hypothesis={failure['hypothesis']}"
            )
    else:
        lines.append("- not available")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Bounded run packets — deadline tracking + run state
# ---------------------------------------------------------------------------

@dataclass
class RunState:
    """Lightweight run-state tracker for overnight / autonomous runs."""
    run_dir: Path
    started_at: str
    status: str = "running"  # running | completed | deadline_exceeded | error
    completed_questions: int = 0
    failed_questions: int = 0
    last_completed_id: Optional[str] = None
    deadlined_at: Optional[str] = None

    def save(self) -> None:
        path = self.run_dir / "run_state.json"
        payload = {
            "started_at": self.started_at,
            "completed_at": datetime.now(timezone.utc).isoformat() if self.status != "running" else None,
            "status": self.status,
            "completed_questions": self.completed_questions,
            "failed_questions": self.failed_questions,
            "last_completed_id": self.last_completed_id,
            "deadlined_at": self.deadlined_at,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")

    def mark_done(self, status: str = "completed") -> None:
        self.status = status
        self.save()

    def record_question(self, question_id: str, had_error: bool) -> None:
        self.completed_questions += 1
        if had_error:
            self.failed_questions += 1
        self.last_completed_id = question_id
        self.save()


DEADLINE_CHECK_INTERVAL = 5  # check every N questions


class DeadlineExceeded(Exception):
    pass


def check_deadline(deadline: Optional[float]) -> None:
    """Raise DeadlineExceeded if the wall-clock deadline has passed."""
    if deadline is None:
        return
    if time.monotonic() >= deadline:
        raise DeadlineExceeded(f"wall-clock deadline exceeded ({deadline})")


def _notify_done(args: argparse.Namespace, run_name: str, run_dir: Path, summary: Dict[str, Any]) -> None:
    """Send webhook POST and/or write a done-file marker if configured."""
    payload = json.dumps({
        "run_name": run_name,
        "run_dir": str(run_dir),
        "summary": summary,
    }).encode("utf-8")

    if args.notify_webhook:
        try:
            import urllib.request
            req = urllib.request.Request(
                args.notify_webhook,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=15)
            print(f"[HARNESS] webhook notification sent to {args.notify_webhook}")
        except Exception as exc:
            print(f"[HARNESS] webhook notification failed: {exc}")

    if args.notify_artifact:
        marker = Path(str(args.notify_artifact).format(run_name=run_name))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"run_name": run_name, "completed_at": datetime.now(timezone.utc).isoformat()}, indent=2))
        print(f"[HARNESS] done artifact written to {marker}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset).expanduser().resolve()
    bench_dir = Path(args.bench_dir).expanduser().resolve()
    titan_home = Path(args.titan_home).expanduser().resolve()
    run_name = build_run_name(args.mode, args.run_name)
    current_run_dir = run_dir(bench_dir, run_name)
    predictions_path = current_run_dir / "predictions.jsonl"
    metadata_path = current_run_dir / "run_log.jsonl"

    ensure_dir(bench_dir)
    ensure_dir(titan_home)
    ensure_dir(current_run_dir)

    # Bounded run — set up deadline and run state
    RUN_DEADLINE: Optional[float] = None
    if args.max_runtime_minutes:
        RUN_DEADLINE = time.monotonic() + (args.max_runtime_minutes * 60.0)
        print(f"[HARNESS] wall-clock deadline set: {args.max_runtime_minutes} minute(s)")

    run_state = RunState(
        run_dir=current_run_dir,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    run_state.save()
    print(f"[HARNESS] run state written to {current_run_dir / 'run_state.json'}")

    config_paths = write_benchmark_config_files(bench_dir, titan_home, enable_lnn=args.enable_lnn)

    items = select_items(load_dataset(dataset_path), args.mode, args.pilot_size, args.max_questions)
    done_question_ids = load_done_question_ids(predictions_path) if args.resume else set()
    adapter = get_reader_adapter()

    config_payload = {
        "dataset": str(dataset_path),
        "bench_dir": str(bench_dir),
        "titan_home": str(titan_home),
        "mode": args.mode,
        "pilot_size": args.pilot_size,
        "max_questions": args.max_questions,
        "retrieval_limit": args.retrieval_limit,
        "brief_max_items": args.brief_max_items,
        "brief_max_chars": args.brief_max_chars,
        "reader_temperature": args.reader_temperature,
        "enable_lnn": args.enable_lnn,
        "selected_questions": [item.question_id for item in items],
        "config_paths": config_paths,
    }
    (current_run_dir / "config.json").write_text(json.dumps(config_payload, indent=2) + "\n", encoding="utf-8")

    # Bounded run — pre-flight deadline check every N questions
    loop_start = time.monotonic()
    try:
        for index, item in enumerate(items, start=1):
            if item.question_id in done_question_ids:
                continue

            # Periodic deadline check — not every iteration to keep overhead low
            if index % DEADLINE_CHECK_INTERVAL == 1 and RUN_DEADLINE is not None:
                check_deadline(RUN_DEADLINE)

            error_message: Optional[str] = None
            ingest: Dict[str, Any] = {}
            retrieval: Dict[str, Any] = {}
            hypothesis = "I don't know."
            try:
                ingest = ingest_item(item)
                retrieval = retrieve_memory_brief(
                    query=item.question,
                    session_id=str(ingest["session_id"]),
                    limit=args.retrieval_limit,
                    max_items=args.brief_max_items,
                    max_chars=args.brief_max_chars,
                )
                hypothesis = answer_question(
                    adapter=adapter,
                    item=item,
                    retrieval=retrieval,
                    prompt=args.reader_prompt,
                    temperature=args.reader_temperature,
                )
            except DeadlineExceeded:
                raise  # re-raise to outer try
            except Exception as exc:  # pragma: no cover - defensive run logging
                error_message = f"{exc.__class__.__name__}: {exc}"

            append_jsonl(predictions_path, {"question_id": item.question_id, "hypothesis": hypothesis})
            append_jsonl(
                metadata_path,
                {
                    "index": index,
                    "question_id": item.question_id,
                    "question_type": item.question_type,
                    "question_date": item.question_date,
                    "session_id": ingest["session_id"],
                    "gold_answer": item.answer,
                    "hypothesis": hypothesis,
                    "ingest": ingest,
                    "retrieval": retrieval,
                    "error": error_message,
                },
            )

            run_state.record_question(item.question_id, had_error=(error_message is not None))

    except DeadlineExceeded:
        run_state.status = "deadline_exceeded"
        run_state.deadlined_at = datetime.now(timezone.utc).isoformat()
        run_state.save()
        print(f"[HARNESS] deadline exceeded after {run_state.completed_questions} question(s) — abandoning remaining questions")
        evaluation_info = {"status": "skipped", "reason": "deadline_exceeded"}
        summary = summarize_run(
            run_name=run_name,
            mode=args.mode,
            dataset_path=dataset_path,
            predictions_path=predictions_path,
            metadata_path=metadata_path,
            evaluation_info=evaluation_info,
            run_directory=current_run_dir,
        )
        run_state.mark_done("deadline_exceeded")
        _notify_done(args, run_name, current_run_dir, summary)
        print(json.dumps({"run_dir": str(current_run_dir), "summary": summary}, indent=2, ensure_ascii=True))
        return

    evaluation_info: Optional[Dict[str, Any]] = None
    if not args.skip_eval:
        if args.longmemeval_repo:
            evaluation_info = evaluate_predictions(
                repo_path=Path(args.longmemeval_repo).expanduser().resolve(),
                python_bin=args.python_bin,
                eval_model=args.eval_model,
                predictions_file=predictions_path,
                dataset_path=dataset_path,
                run_directory=current_run_dir,
            )
        else:
            evaluation_info = {"status": "skipped", "reason": "missing --longmemeval-repo"}

    summary = summarize_run(
        run_name=run_name,
        mode=args.mode,
        dataset_path=dataset_path,
        predictions_path=predictions_path,
        metadata_path=metadata_path,
        evaluation_info=evaluation_info,
        run_directory=current_run_dir,
    )

    run_state.mark_done("completed")
    _notify_done(args, run_name, current_run_dir, summary)
    print(json.dumps({"run_dir": str(current_run_dir), "summary": summary}, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
