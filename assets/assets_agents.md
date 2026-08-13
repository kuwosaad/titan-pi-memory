# Assets Agent Guide

## Purpose

`assets/` contains brand, design, logo, font, and static web files. It is for source assets, not runtime-generated product data.

## Structure

- `fonts/`: font assets, including the Rostex font folder.
- `logos/`: logo source/output folders from prior logo generation work.
- `titan-logos/`: Titan logo asset collection.
- `web/`: static web prototypes and exported web files.
- `titan_logo.svg`: root Titan logo SVG asset.

## Working Notes

- Keep generated or scratch outputs out of product code paths unless the app explicitly serves them.
- Before deleting assets, check docs, README references, and any web prototypes.
- Prefer descriptive folders over loose root-level design files.
