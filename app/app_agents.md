# App Agent Guide

## Purpose

`app/` contains the product runtime code for Titan Memory. Start here when changing save, retrieval, graph, storage, API, or embedding behavior.

## Structure

- `api/`: FastAPI routes and HTTP-facing request handling.
- `embedding/`: embedding provider adapters and shared embedding helpers.
- `graph/`: memory graph construction and graph similarity utilities.
- `retrieval_pipeline/`: query routing, retrieval, brief generation, and retrieval schemas.
- `save_pipeline/`: trace ingestion, extraction, and save/retrieve orchestration.
- `storage/`: shared persistence models and repositories for sessions, scenes, traces, memories, and notes.

## Working Notes

- Prefer changing pipeline behavior in `save_pipeline/` or `retrieval_pipeline/` before touching API routes.
- Treat `storage/` as shared infrastructure. Changes there can affect save, retrieval, graph, CLI, and tests.
- Keep imports pointed at the reorganized packages. Do not reintroduce old paths like `app.core`, `app.memory`, `app.retrieval`, or `app.server`.
