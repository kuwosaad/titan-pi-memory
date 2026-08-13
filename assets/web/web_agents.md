# Web Assets Agent Guide

## Purpose

`assets/web/` contains static web prototypes, exported HTML, and web design assets that are not part of the main Python runtime.

## Structure

- `index.html`: root web prototype moved out of the repo root.
- `titanwebsite/`: Titan website prototype/export folder.

## Working Notes

- Do not assume this folder is served by the FastAPI app unless a route explicitly references it.
- Keep static prototypes here instead of mixing them with product entrypoints.
