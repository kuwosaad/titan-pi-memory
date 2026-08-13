# Retrieval Pipeline Agent Guide

## Purpose

`app/retrieval_pipeline/` contains the query-time path: deciding how to retrieve memories, retrieving candidates, and shaping answers or briefs.

## Structure

- `router.py`: query routing and retrieval-mode selection.
- `retriever.py`: candidate retrieval logic.
- `brief.py`: retrieval brief or response summary generation.
- `config.py`: retrieval pipeline configuration.
- `schema.py`: retrieval-specific data structures.
- `__init__.py`: package marker.

## Working Notes

- Start in `router.py` when behavior depends on query type.
- Start in `retriever.py` when candidate selection, ranking, or filtering is wrong.
- Start in `brief.py` when the retrieved context is right but the final summary shape is wrong.
- Retrieval usually depends on `app/storage/`, `app/embedding/`, and sometimes `app/graph/`.
