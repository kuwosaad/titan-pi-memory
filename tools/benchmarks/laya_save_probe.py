"""Compare local Laya with the frozen Jev save probe; never opens a live Titan store."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time

from jev_duplicate_prototype import public_input, read_json, write_json
from jev_save_review import SCREENING_INSTRUCTIONS, SCREENING_OPTIONS, SIMPLE_INSTRUCTIONS, SIMPLE_OPTIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--simple", action="store_true", help="Use the predeclared short prompt and only a/b texts")
    parser.add_argument("--model-path", type=Path, help="Reuse an already downloaded pinned checkpoint")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    os.environ.update({"HF_HOME": str(run_dir / "hf-cache"), "USE_TF": "0",
                       "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false"})
    import torch
    import laya
    from huggingface_hub import HfApi, snapshot_download
    from laya.common import build_sequence, render_options, serialize_state

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    records = read_json(run_dir / "output.json")["records"]
    jev = read_json(run_dir.parent / "jev-save-prototype/jev_report.json")
    pairs = jev["pairs"]
    repo = "convaiinnovations/laya"
    started = time.perf_counter()
    if args.model_path:
        snapshot = str(args.model_path.resolve())
        revision = args.model_path.name
    else:
        revision = HfApi().model_info(repo).sha
        snapshot = snapshot_download(repo, revision=revision, allow_patterns=[
            "rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*"])
    download_ms = round((time.perf_counter() - started) * 1000, 1)
    print(f"Checkpoint downloaded at revision {revision}", flush=True)
    started = time.perf_counter()
    agent = laya.load(snapshot, device="cpu")
    load_ms = round((time.perf_counter() - started) * 1000, 1)
    original_config = dict(agent.cfg)
    instructions = SIMPLE_INSTRUCTIONS if args.simple else SCREENING_INSTRUCTIONS
    options = SIMPLE_OPTIONS if args.simple else SCREENING_OPTIONS
    project = (lambda m: m["text"]) if args.simple else public_input
    prepared, audit = [], []
    for pair in pairs:
        qid = pair["pair_id"]
        state = {qid: {"a": project(records[pair["a"]]), "b": project(records[pair["b"]])}}
        question = {"type": "choice", "instructions": instructions.format(pair_id=qid),
                    "criteria": options}
        internal = agent._to_internal(question)
        instruction_tokens = len(agent.tok("choice question: " + internal["ins"], add_special_tokens=False)["input_ids"])
        option_lengths = [1 + len(agent.tok(" " + opt, add_special_tokens=False)["input_ids"])
                          for opt in render_options(internal)]
        if max(option_lengths) > 49:
            raise ValueError("An option exceeds the SDK's 48-token description cap")
        state_tokens = len(agent.tok(serialize_state(state), add_special_tokens=False)["input_ids"])
        head_needed = instruction_tokens + sum(option_lengths)
        audit.append({"pair_id": qid, "head_tokens": head_needed, "state_tokens": state_tokens,
                      "full_tokens": head_needed + state_tokens + 4})
        prepared.append((qid, state, question))
    # Choose budgets using lengths only, before observing any model answers.
    agent.cfg["head_max_len"] = max(agent.cfg.get("head_max_len", 192), max(a["head_tokens"] for a in audit))
    agent.cfg["max_len"] = max(agent.cfg.get("max_len", 512), max(a["full_tokens"] for a in audit))
    for (qid, state, question), lengths in zip(prepared, audit):
        seq, markers = build_sequence(agent.tok, state, agent._to_internal(question),
                                      agent.cfg["max_len"], agent.cfg["head_max_len"])
        assert len(seq) == lengths["full_tokens"] and len(markers) == 3, qid
    metadata = {
        "model": repo, "revision": revision, "sdk_version": importlib.metadata.version("laya"),
        "torch_version": torch.__version__, "transformers_version": importlib.metadata.version("transformers"),
        "device": str(agent.device), "machine": platform.machine(), "threads": torch.get_num_threads(),
        "download_ms": download_ms, "load_ms": load_ms, "original_config": original_config,
        "effective_max_len": agent.cfg["max_len"], "effective_head_max_len": agent.cfg["head_max_len"],
        "token_audit": audit, "all_inputs_untruncated": True,
        "prompt": instructions, "criteria": options, "text_only": args.simple,
        "execution": "One pair per SDK call so each question receives exactly its own state; no training or threshold fitting.",
    }
    write_json(run_dir / "metadata.json", metadata)
    print(f"Loaded; all {len(pairs)} inputs fit without truncation. Running on CPU.", flush=True)
    # Unscored warmup isolates steady inference from first-call initialization.
    qid, state, question = prepared[0]
    started = time.perf_counter()
    agent.predict(state, {qid: question})
    metadata["warmup_ms"] = round((time.perf_counter() - started) * 1000, 1)
    answers, calls = {}, []
    for i, (qid, state, question) in enumerate(prepared):
        started = time.perf_counter()
        result = agent.predict(state, {qid: question})
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        answer = result["answers"][qid]
        assert answer["choice"] in options
        answers[qid] = answer
        calls.append({"pair_id": qid, "elapsed_ms": elapsed, "usage": result.get("usage")})
        if (i + 1) % 15 == 0 or i + 1 == len(prepared):
            report = {"metadata": metadata, "pairs": pairs, "answers": answers, "calls": calls,
                      "elapsed_ms": round(sum(c["elapsed_ms"] for c in calls), 1),
                      "flagged_pairs": [p for p in pairs if answers.get(p["pair_id"], {}).get("choice") in {"same", "unclear"}]}
            write_json(run_dir / "laya_report.json", report)
            print(f"{i + 1}/{len(prepared)} pairs; {len(report['flagged_pairs'])} flags", flush=True)


if __name__ == "__main__":
    main()
