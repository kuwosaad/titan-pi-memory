# Extraction Agent Guide

## Purpose

`app/save_pipeline/extraction/` converts raw trace or conversation text into structured memory candidates.

## Structure

- `prompts.py`: extraction prompt text and prompt-building helpers.
- `adapters.py`: model adapter code used by extraction.
- `extractor.py`: extraction orchestration and parsing.
- `__init__.py`: package marker.

## Working Notes

- Prompt edits can change a lot of downstream behavior. Run focused extraction tests if they exist, then the broader suite when practical.
- Keep parsing strict enough to detect malformed model output, but not so brittle that one wording change breaks ingestion.
- Do not put persistence logic here. Store extracted results through the save pipeline and storage layer.
