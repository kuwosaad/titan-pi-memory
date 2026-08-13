# API Agent Guide

## Purpose

`app/api/` contains the HTTP interface for Titan Memory. It should stay thin: validate requests, call the right pipeline/storage functions, and return responses.

## Structure

- `routes.py`: FastAPI route definitions and HTTP handlers.
- `__init__.py`: package marker.

## Working Notes

- Put product logic in `app/save_pipeline/`, `app/retrieval_pipeline/`, `app/graph/`, or `app/storage/`, not directly in routes.
- When adding routes, check `entrypoints/main.py` to confirm router registration behavior.
- Keep route handlers small enough that a future agent can trace request flow quickly.
