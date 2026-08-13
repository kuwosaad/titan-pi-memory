# Save Pipeline Agent Guide

## Purpose

`app/save_pipeline/` contains the ingest-time path: accepting trace data, extracting memories, and storing structured memory state.

## Structure

- `pipeline.py`: main save/retrieve orchestration used by MCP and runtime entrypoints.
- `auto_ingest.py`: background trace ingestion worker.
- `extraction/`: LLM extraction prompts, adapters, and extractor implementation.
- `__init__.py`: package marker.

## Working Notes

- Start in `pipeline.py` for end-to-end save behavior.
- Start in `auto_ingest.py` for filesystem trace pickup, background processing, or polling behavior.
- Start in `extraction/` when trace text is being interpreted incorrectly.
- Save behavior usually affects tests around CLI, MCP, storage, and extraction.
