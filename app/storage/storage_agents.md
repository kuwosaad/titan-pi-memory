# Storage Agent Guide

## Purpose

`app/storage/` contains Titan's persistence layer. It is shared by save, retrieval, graph, MCP, CLI, and tests.

## Structure

- `models.py`: storage data models and shared record shapes.
- `repository.py`: shared repository helpers and storage coordination.
- `sessions.py`: session persistence.
- `scenes.py`: scene persistence.
- `traces.py`: trace persistence.
- `memories.py`: memory persistence.
- `notes.py`: note persistence.
- `verifier.py`: storage verification helpers.
- `__init__.py`: package marker.

## Working Notes

- Treat this folder as high-impact infrastructure. Small changes can affect most of the product.
- Preserve existing on-disk compatibility unless the user explicitly approves a migration or breaking change.
- When changing schemas or file layout, inspect tests and CLI behavior before assuming only one caller exists.
