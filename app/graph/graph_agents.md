# Graph Agent Guide

## Purpose

`app/graph/` builds the living memory graph — the user-facing feature where users see their work patterns and experience pattern discovery. It is not just infrastructure; it is the visual layer of the product.

The module constructs and compares memory graph structures from stored Titan data.

## Structure

- `builder.py`: graph construction from sessions, scenes, traces, memories, or related storage data.
- `similarity.py`: graph similarity and comparison helpers.
- `__init__.py`: package marker.

## Working Notes

- Verify storage assumptions in `app/storage/` before changing graph construction.
- Keep graph logic separate from HTTP route behavior and CLI display concerns.
- If graph output changes, check graph-related docs and any tests that assert node or edge shape.
