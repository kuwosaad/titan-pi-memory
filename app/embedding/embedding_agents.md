# Embedding Agent Guide

## Purpose

`app/embedding/` contains shared embedding code used by save, retrieval, and graph flows.

## Structure

- `adapters.py`: embedding provider adapters and external model integration points.
- `embedder.py`: shared embedding helper functions/classes.
- `__init__.py`: package marker.

## Working Notes

- This package should not own retrieval or storage policy. It should turn text into vectors and expose that capability cleanly.
- Be careful with model/config changes because they can affect stored vector compatibility and benchmark behavior.
- Prefer small adapter changes over new abstraction layers unless multiple providers truly need the same interface.
