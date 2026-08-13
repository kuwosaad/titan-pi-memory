---
name: Titan Memory (Website)
description: >-
  Marketing website for Titan Memory — a local-first persistent memory layer for
  AI coding agents. Deployed on Vercel at titanmemz.in. A warm, editorial,
  atmospheric brand rooted in depth, memory, and quiet precision.
colors:
  bg: "#05070b"
  bgDeep: "#02050a"
  panel: "#10080c"
  panelStrong: "#180c11"
  ink: "#f8eef1"
  muted: "#cbaab2"
  dim: "#8f727a"
  faint: "#604952"
  rose: "#d4687a"
  roseSoft: "#e8a0b0"
  rosePale: "#f0c8d0"
  violet: "#8a6dff"
  green: "#45ff8d"
  amber: "#f0c8a0"
  line: "#3a2028"
  lineStrong: "#7a4754"
  grid: "#182337"
typography:
  body:
    fontFamily: "Source Serif 4"
    fallback: "Georgia, Times New Roman, serif"
    description: >
      The only typeface used on the site. A literary serif that gives the brand
      its warm, editorial voice. Used for everything — nav, headings, body,
      labels, buttons, terminal output. The choice is deliberate: memory is a
      human concept, and a serif feels authored, not generated.
    weights:
      - 300 (Light) — sparingly, for large hero text
      - 400 (Regular) — primary body weight
      - 500 (Medium) — emphasis, subheadings
      - 600 (Semi-Bold) — strong emphasis, nav brand
      - 700 (Bold) — terminal headers, strong labels
    sizes:
      hero-headline: "clamp(62px, 10vw, 142px)"
      section-headline: "clamp(42px, 5.5vw, 78px)"
      final-headline: "clamp(54px, 8vw, 108px)"
      hero-copy: "clamp(19px, 2.2vw, 27px)"
      section-copy: "clamp(18px, 2vw, 22px)"
      body: "15px"
      small: "12px"
      label: "11px"
      nav-link: "12px"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace"
    description: >
      Used only inside the terminal demo component. Monospace provides the
      expected developer-terminal feel within the otherwise serif world.
    size: "13px"
rounded:
  pill: 999px
  card: 18px
  graph-shell: 28px
  terminal-body: 24px
  memory-card: 14px
  terminal-demo: 18px
  memory-chip: 14px
  tweaks-panel: 18px
tokens:
  shadow: "0 26px 82px rgba(0, 0, 0, 0.42)"
  ease: "cubic-bezier(0.16, 1, 0.3, 1)"
  nav-height: "88px"
  nav-height-mobile: "72px"
  max-width: "1180px"
  hero-bg-image: "oland-first-slide-bg.png"
  section-bg-images:
    how: "bg-how.png"
    graph: "bg-graph.png"
    start: "bg-start.png"
    final: "bg-graph.png"
  section-brightness: 1.75
  section-contrast: 1.08
  section-saturation: 1.12
  section-opacity: 0.34–0.44
  graph-preview: "knowledge-graph-o2.png"
---

## Overview

Titan Memory's website is a **digital chapbook** — warm, atmospheric, and authored-feeling. Unlike the cold, brutalist aesthetic common in developer tools, the site leans into a literary, editorial voice that treats memory as something human rather than mechanical.

The design pairs a **deep cosmic-blue background** with **warm rose-coral accents** and a **single serif typeface** (Source Serif 4) used for every element — from headlines to terminal output. Landscape photography bleeds through sections as atmospheric layers, grounding the product in a tactile, physical world.

The brand communicates: **Your agent can remember. That's a human thing. Let's treat it like one.**

### Brand voice

- **Tonality:** Warm, direct, confident. Crafted, not generated. Like a letter from a friend who builds tools.
- **Vocabulary:** "Remember", "context", "local-first", "persistent", "evolutionary", "knowledge".
- **Character:** An editorial columnist who also happens to write code. Technical depth delivered with warmth.

## Colors

Titan lives in a warm, dark palette built around rose-coral accents against a deep midnight-blue foundation.

### Core palette

| Token | Hex | Usage |
|-------|-----|-------|
| `bg` | `#05070b` | Primary background. Deep midnight blue, not pure black. |
| `bg-deep` | `#02050a` | Deepest background layer, used for body gradient tail. |
| `panel` | `#10080c` | Translucent panel backgrounds (nav, HUD). |
| `panel-strong` | `#180c11` | Opaque card backgrounds. |

### Text palette

| Token | Hex | Usage |
|-------|-----|-------|
| `ink` | `#f8eef1` | Primary text. Warm white with a hint of rose. |
| `muted` | `#cbaab2` | Secondary text, descriptions, supporting copy. |
| `dim` | `#8f727a` | Tertiary text, metadata, nav links (resting). |
| `faint` | `#604952` | Placeholder text, search box. |

### Accent palette

| Token | Hex | Usage |
|-------|-----|-------|
| `rose` | `#d4687a` | Primary accent. Pulse dots, active indicators, node colors, links. |
| `rose-soft` | `#e8a0b0` | Soft accent. Hover states, section labels, gradient highlights, TUI prompts. |
| `rose-pale` | `#f0c8d0` | Subtle accent. For very gentle highlights. |

### Functional colors

| Token | Hex | Usage |
|-------|-----|-------|
| `violet` | `#8a6dff` | Agent name color in terminal TUI. |
| `green` | `#45ff8d` | Success indicators, tool call icons, live pill dot. |
| `amber` | `#f0c8a0` | Window control dot (yellow), hub node color. |

### Border palette

| Token | Hex | Usage |
|-------|-----|-------|
| `line` | `#3a2028` | Primary border. Subtle rose-tinted line. |
| `line-strong` | `#7a4754` | Emphasized border. CTA buttons, HUD panels. |

### Grid pattern

| Token | Value | Usage |
|-------|-------|-------|
| `grid` | `#182337` | Background grid lines. Cool blue-gray for contrast with warm rose palette. |
| Grid size | 30px × 30px | Subtle blueprint grid on the body background. |

## Typography

### Source Serif 4 — The only typeface

The site uses **one font family** for everything: Source Serif 4, a literary serif by Frank Grießhammer for Adobe. It replaces the typical developer-tool approach of using a sans-serif for UI and a monospace for code. Here, even the terminal output is rendered in Source Serif 4, creating a unified, authored voice.

> **Why a serif?** Memory is a human concept. A serif typeface signals authorship, warmth, and permanence — qualities that matter when you're asking an agent to remember your work.

**Font loading:** Preconnected from Google Fonts (`fonts.googleapis.com`). Loads weights 300, 400, 500, 600, 700 including italic 400.

#### Scale

| Level | Size | Weight | Letter-spacing | Use |
|-------|------|--------|----------------|-----|
| hero-headline | clamp(62px, 10vw, 142px) | 400 (Regular) | -0.065em | Hero title |
| section-headline | clamp(42px, 5.5vw, 78px) | 400 (Regular) | -0.05em | Section headings |
| final-headline | clamp(54px, 8vw, 108px) | 400 (Regular) | -0.05em | Final CTA heading |
| section-label | 12px | — | 0.08em | Uppercase section labels |
| hero-copy | clamp(19px, 2.2vw, 27px) | 400 (Regular) | — | Hero subtitle |
| section-copy | clamp(18px, 2vw, 22px) | 400 (Regular) | — | Section descriptions |
| body | 15px | 400 (Regular) | — | Standard text |
| small | 12px | — | — | Footer, metadata |
| memory-card | 14px | 400 (Regular) | — | Memory chip text |
| nav-link | 12px | — | 0.04em | Navigation links |

#### Special treatments

- **Emphasis in headlines:** Italic (`<em>`) is used sparingly in the hero headline ("remember") to add warmth and emphasis — the only italic usage on the site.
- **Lowercase labels:** Section labels (e.g., "what titan does", "memory graph") use `text-transform: lowercase` with `letter-spacing: 0.08em` — intentionally lowercase but spaced out for a modern editorial feel.

> **Design constraint:** Never introduce a second typeface for the marketing site. Source Serif 4 carries the entire voice. The only exception is the monospace used inside the terminal demo component for code-like content.

### Terminal monospace (demo only)

Inside the animated terminal demo, a system monospace stack is used:

```
font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace
font-size: 13px
line-height: 1.45
```

This is the **only** place monospace appears. It creates contrast between the "product" (terminal, code) and the "brand" (serif, editorial).

## Layout & Spacing

### Grid

The site uses a **single-column fluid layout** with a maximum content width of **1180px** (sections) or **1500px** (graph section). Content is centered with auto margins.

### Section padding

- Desktop: **108px** top and bottom
- Mobile (≤680px): **82px** top and bottom
- Side padding: **16px** (on the `.section` wrapper, using `calc(100vw - 32px)`)

### Nav

Fixed position at top, **88px** height on desktop, **72px** on mobile.

Contains:
- **Left:** Logo mark (80×80px SVG, white with drop-shadow glow) + "titan memory" wordmark (15px, 600 weight, 0.03em letter-spacing)
- **Center:** Navigation links — "how", "graph", "start" (12px, lowercase, 0.04em spacing)
- **Right:** "github" CTA pill (rose-tinted background, rounded 999px)

The nav background uses:
```css
background: rgba(8, 4, 7, 0.74);
backdrop-filter: blur(16px);
border-bottom: 1px solid var(--line);
```

### Section layout pattern

Each content section follows this structure:
1. **Section label** — lowercase, small (12px), letter-spaced (0.08em), in `--rose-soft`
2. **Heading** — large serif headline
3. **Copy** — muted, 18-22px, 1.45 line-height
4. **Content block** — varies (terminal demo, graph shell, install terminal, CTA)

The `.section-head` uses a two-column grid on desktop (0.7fr + 0.5fr) that collapses to single column on mobile (≤980px).

## Backgrounds & Atmosphere

### Body background

The body uses a layered background system:

1. **30px grid** — subtle 1px lines in `--grid` color (cool blue-gray)
2. **Radial gradients** — warm rose glow at hero position, violet glow lower down
3. **Vertical gradient** — from `--bg` to `--bg-deep`
4. **Starfield dots** — pseudo-element with randomly positioned 1px white dots at varying opacities, creating a starfield effect

### Section background images

Each section has a **landscape photograph** as a background layer, blended behind the content:

| Section | Image | Brightness | Opacity |
|---------|-------|------------|---------|
| Hero | `oland-first-slide-bg.png` | 1.75 | 0.54 |
| How | `bg-how.png` | 1.75 | 0.42 |
| Graph | `bg-graph.png` | 1.75 | 0.34 |
| Start | `bg-start.png` | 1.75 | 0.40 |
| Final | `bg-graph.png` | 1.75 | 0.44 |

Each background image is overlaid with:
- A **radial gradient vignette** that darkens the edges and keeps content readable
- A **linear gradient** fading into the page background at top and bottom

The effect: landscape photography bleeds through like a barely-remembered dream — present but not distracting.

## Elevation & Depth

The site does not use conventional material elevation. Depth is created atmospherically rather than mechanically:

- **Background image layers** with brightness/contrast adjustments and vignette overlays create emotional depth.
- **Translucent panels** with backdrop blur provide UI structure without breaking the atmospheric surface.
- **Rose glows** behind accent elements suggest state, memory, or attention.
- **Box shadows** are deep (`0 26px 82px rgba(0, 0, 0, 0.42)`) but rarely used outside the memory demo, graph shell, and terminal.

## Shapes

Titan's shapes are softly rounded but not bubbly:

| Level | Value | Usage |
|-------|-------|-------|
| Pill | 9999px | Buttons, CTA pills, status pills |
| Card | 18px | Memory demo shell, graph controls, HUD panels |
| Terminal | 24px | Quick-start install terminal |
| Graph shell | 28px | Knowledge graph preview container |
| Memory card | 14px | Individual memory chips |
| Search box | 10px | Search/filter inputs |

## Components

### Navigation
Fixed glassy bar. Contains brand and links. All lowercase.

### Hero
Full-viewport atmospheric entry. Headline, subtitle, two CTAs. Background image with vignette.

### Memory demo
Split-panel component showing a simulated Pi session (left terminal feed, right memory cards). The centerpiece product proof.

### Graph preview
Image of the Titan memory knowledge graph inside a rounded shell.

### Quick start
Two-column: copy + install terminal.

### Final CTA
Centered closing statement with CTAs.

### Footer
Minimal bar with brand, tagline, and GitHub link.

### Music toggle
Floating pill in bottom-right corner. Toggles ambient YouTube audio on/off.

## Do's and Don'ts

### Do
- Use Source Serif 4 as the sole typeface for the marketing site.
- Write lowercase labels with letter-spacing.
- Keep the palette warm: rose-coral accents on a midnight-blue field.
- Use photography as atmosphere, not decoration.
- Keep the page calm, dark, and editorial.

### Don't
- Introduce a sans-serif UI font. The serif is the brand.
- Use cold blue/purple as the primary accent.
- Make the page feel like a typical SaaS landing page.
- Over-explain technical features above the fold.
- Let the Product Hunt badge dominate the footer.
