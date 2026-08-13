---
version: alpha
name: Titan Memory Website
description: >-
  Visual design system for titanmemz.in, the hosted Titan Memory marketing
  website. A warm, dark, editorial landing page for a local-first persistent
  memory layer for AI agents.
colors:
  primary: "#d4687a"
  secondary: "#cbaab2"
  tertiary: "#8a6dff"
  neutral: "#f8eef1"
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
  white: "#ffffff"
typography:
  heroHeadline:
    fontFamily: Source Serif 4
    fontSize: 142px
    fontWeight: 400
    lineHeight: 0.98
    letterSpacing: -0.065em
  heroHeadlineMobile:
    fontFamily: Source Serif 4
    fontSize: 62px
    fontWeight: 400
    lineHeight: 0.98
    letterSpacing: -0.065em
  sectionHeadline:
    fontFamily: Source Serif 4
    fontSize: 78px
    fontWeight: 400
    lineHeight: 0.96
    letterSpacing: -0.05em
  sectionHeadlineMobile:
    fontFamily: Source Serif 4
    fontSize: 42px
    fontWeight: 400
    lineHeight: 0.96
    letterSpacing: -0.05em
  finalHeadline:
    fontFamily: Source Serif 4
    fontSize: 108px
    fontWeight: 400
    lineHeight: 0.96
    letterSpacing: -0.05em
  navBrand:
    fontFamily: Source Serif 4
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1
    letterSpacing: 0.03em
  navLink:
    fontFamily: Source Serif 4
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1
    letterSpacing: 0.04em
  eyebrow:
    fontFamily: Source Serif 4
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: 0.08em
  sectionLabel:
    fontFamily: Source Serif 4
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: 0.08em
  heroCopy:
    fontFamily: Source Serif 4
    fontSize: 27px
    fontWeight: 400
    lineHeight: 1.38
  sectionCopy:
    fontFamily: Source Serif 4
    fontSize: 22px
    fontWeight: 400
    lineHeight: 1.45
  body:
    fontFamily: Source Serif 4
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.6
  bodySmall:
    fontFamily: Source Serif 4
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
  memoryTitle:
    fontFamily: Source Serif 4
    fontSize: 24px
    fontWeight: 500
    lineHeight: 1.05
    letterSpacing: -0.03em
  memoryChip:
    fontFamily: Source Serif 4
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.35
  terminal:
    fontFamily: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.45
  installTerminal:
    fontFamily: Source Serif 4
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.75
spacing:
  xxs: 4px
  xs: 7px
  sm: 10px
  md: 14px
  lg: 18px
  xl: 22px
  xxl: 28px
  xxxl: 34px
  sectionGap: 44px
  sectionPadMobile: 82px
  sectionPad: 108px
  finalPadTop: 110px
  finalPadBottom: 130px
  navHeightMobile: 72px
  navHeight: 88px
  pageGutter: 16px
  maxWidth: 1180px
  graphMaxWidth: 1500px
rounded:
  none: 0px
  sm: 10px
  md: 13px
  card: 14px
  lg: 18px
  terminal: 24px
  graph: 28px
  pill: 999px
components:
  body:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
  nav:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    height: "{spacing.navHeight}"
  navBrand:
    textColor: "{colors.ink}"
    typography: "{typography.navBrand}"
  navLink:
    textColor: "{colors.dim}"
    typography: "{typography.navLink}"
  navLinkHover:
    textColor: "{colors.ink}"
  navCta:
    backgroundColor: "{colors.panelStrong}"
    textColor: "{colors.rosePale}"
    rounded: "{rounded.pill}"
    padding: 7px 14px
  hero:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    padding: 104px 20px 70px
  heroEyebrow:
    textColor: "{colors.muted}"
    typography: "{typography.eyebrow}"
  heroHeadline:
    textColor: "{colors.ink}"
    typography: "{typography.heroHeadline}"
  heroHeadlineEmphasis:
    textColor: "{colors.roseSoft}"
    typography: "{typography.heroHeadline}"
  heroCopy:
    textColor: "{colors.muted}"
    typography: "{typography.heroCopy}"
  buttonPrimary:
    backgroundColor: "{colors.panelStrong}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: 10px 20px
    height: 48px
  buttonSecondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    rounded: "{rounded.pill}"
    padding: 10px 20px
    height: 48px
  section:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    padding: "{spacing.sectionPad}"
    width: "{spacing.maxWidth}"
  sectionLabel:
    textColor: "{colors.roseSoft}"
    typography: "{typography.sectionLabel}"
  sectionHeadline:
    textColor: "{colors.ink}"
    typography: "{typography.sectionHeadline}"
  sectionCopy:
    textColor: "{colors.muted}"
    typography: "{typography.sectionCopy}"
  memoryDemo:
    backgroundColor: "{colors.panelStrong}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
  memoryTerminalTop:
    backgroundColor: "{colors.panelStrong}"
    textColor: "{colors.ink}"
    typography: "{typography.terminal}"
    padding: 7px 18px
  terminalFeed:
    backgroundColor: "{colors.bgDeep}"
    textColor: "{colors.muted}"
    typography: "{typography.terminal}"
    padding: 18px
  terminalMemoryPanel:
    backgroundColor: "{colors.bgDeep}"
    textColor: "{colors.muted}"
    padding: 22px
  memoryChip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    typography: "{typography.memoryChip}"
    rounded: "{rounded.card}"
    padding: 13px 14px
  graphShell:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.graph}"
  installTerminal:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.muted}"
    typography: "{typography.installTerminal}"
    rounded: "{rounded.terminal}"
  musicToggle:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.rosePale}"
    rounded: "{rounded.pill}"
    padding: 10px 14px
  footer:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
  searchBox:
    backgroundColor: "{colors.panelStrong}"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: 10px 12px
  terminalAgent:
    backgroundColor: "{colors.bgDeep}"
    textColor: "{colors.violet}"
    typography: "{typography.terminal}"
  terminalTool:
    backgroundColor: "{colors.bgDeep}"
    textColor: "{colors.green}"
    typography: "{typography.terminal}"
  terminalWindowDot:
    backgroundColor: "{colors.amber}"
    size: 9px
    rounded: "{rounded.pill}"
  graphGrid:
    backgroundColor: "{colors.grid}"
    textColor: "{colors.muted}"
  logoMark:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.white}"
  strongLine:
    backgroundColor: "{colors.lineStrong}"
    size: 1px
  subtleLine:
    backgroundColor: "{colors.line}"
    size: 1px
  accentDot:
    backgroundColor: "{colors.rose}"
    size: 7px
    rounded: "{rounded.pill}"
  neutralText:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.neutral}"
  faintSwatch:
    backgroundColor: "{colors.faint}"
    size: 10px
---

## Overview

Titan Memory's hosted website (`titanmemz.in`) is a **warm, dark, editorial marketing site** for a local-first memory layer for AI agents. It intentionally avoids the cold SaaS/developer-tool default. The page feels like a digital chapbook about memory: literary, atmospheric, quietly technical, and emotionally legible.

The site is defined by five core decisions:

1. **One serif typeface for the whole brand.** Source Serif 4 carries every headline, label, nav link, button, and most product UI text. This makes the page feel authored rather than generated.
2. **A deep midnight canvas.** The site is almost black, but never pure black. It uses a blue-black base with warm rose light and faint star/grid texture.
3. **Rose-coral as the memory signal.** The accent color marks action, emphasis, pulses, links, highlights, and memory nodes.
4. **Atmospheric background images.** The hero and sections use dark landscape/room imagery as emotional context, filtered behind gradients and vignettes.
5. **Product proof through simulated memory.** The page's central demo shows a coding session being turned into structured, saved memory cards.

The brand sentence is: **Your coding agent should remember.**

The implementation source for the hosted site is `assets/web/titanwebsite/index.html`; the live deployment redirects from `https://titanmemz.in` to `https://www.titanmemz.in/` on Vercel.

## Colors

The color system is a dark, warm, memory-like palette: midnight blue/black foundations, rose-coral accents, muted rose text, and a few functional neon colors inside the product demo.

### Core background colors

| Token | Value | Usage |
|---|---:|---|
| `bg` | `#05070b` | Main page background. A deep midnight blue-black. |
| `bgDeep` | `#02050a` | Deepest background used in page gradients and terminal interiors. |
| `panel` | `#10080c` | Base for translucent nav, footer, cards, and dark overlays. |
| `panelStrong` | `#180c11` | Stronger reddish-black panel for terminal bars and elevated blocks. |

The background should never become flat black. Use `bg` plus layered texture, radial glow, or a faint grid.

### Text colors

| Token | Value | Usage |
|---|---:|---|
| `ink` | `#f8eef1` | Primary text: hero, section headlines, high-emphasis copy. |
| `muted` | `#cbaab2` | Secondary text: subtitles, descriptions, terminal text. |
| `dim` | `#8f727a` | Tertiary text: nav links, metadata, inactive labels. |
| `faint` | `#604952` | Placeholder text, quiet UI chrome, inactive search fields. |

The text colors are all rose-tinted. Do not use cold gray for body copy on this website.

### Accent colors

| Token | Value | Usage |
|---|---:|---|
| `rose` | `#d4687a` | Primary accent: pulse dot, active glow, button gradients, memory node base. |
| `roseSoft` | `#e8a0b0` | Softer accent: italic hero word, section labels, selected UI, links. |
| `rosePale` | `#f0c8d0` | Pale accent: CTA text, command text, delicate highlights. |
| `violet` | `#8a6dff` | Secondary atmospheric glow and terminal agent identity. |
| `green` | `#45ff8d` | Success/tool-call color in terminal demo and local status pills. |
| `amber` | `#f0c8a0` | Warm functional accent, used for terminal window control dot. |

### Line and grid colors

| Token | Value | Usage |
|---|---:|---|
| `line` | `#3a2028` | Subtle rose-tinted borders. In CSS this often appears as `rgba(200, 120, 140, 0.15)`. |
| `lineStrong` | `#7a4754` | Stronger rose border. Used for CTA outline, HUD cards, and graph controls. |
| `grid` | `#182337` | Cool blue-gray grid lines. In CSS this appears as `rgba(96, 126, 166, 0.085)`. |

### Atmosphere and opacity

The live CSS relies heavily on translucent versions of the tokens above. When implementing new sections, use these opacity patterns:

- Nav surface: `rgba(8, 4, 7, 0.74)` with blur.
- Panels: `rgba(16, 8, 12, 0.74)` to `rgba(24, 12, 17, 0.92)`.
- Borders: `rgba(200, 120, 140, 0.15)` for subtle, `rgba(226, 156, 172, 0.34)` for strong.
- Accent glow: rose at 10–24% opacity.
- Grid: cool blue-gray at 7–9% opacity.

## Typography

### Primary typeface: Source Serif 4

The entire hosted site uses **Source Serif 4** from Google Fonts:

```html
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,300;0,8..60,400;0,8..60,500;0,8..60,600;0,8..60,700;1,8..60,400&display=swap" rel="stylesheet" />
```

This is the defining design choice. Titan Memory is about memory, context, and continuity; a serif communicates authorship, permanence, and warmth better than a generic developer-tool sans.

**Do not introduce Space Grotesk, Plus Jakarta Sans, Inter, or a UI sans into the hosted marketing site.** Those may be useful for other Titan artifacts, but `titanmemz.in` is Source Serif 4-first.

### Type scale

| Role | CSS value | Weight | Usage |
|---|---|---:|---|
| Hero headline | `clamp(62px, 10vw, 142px)` | 400 | Main line: “Your coding agent should remember.” |
| Section headline | `clamp(42px, 5.5vw, 78px)` | 400 | Major section headings. |
| Final headline | `clamp(54px, 8vw, 108px)` | 400 | Final CTA. |
| Hero copy | `clamp(19px, 2.2vw, 27px)` | 400 | Hero subtitle. |
| Section copy | `clamp(18px, 2vw, 22px)` | 400 | Section explanatory text. |
| Nav brand | `15px` | 600 | “titan memory” wordmark. |
| Nav link | `12px` | 400 | how / graph / start / github. |
| Section label | `12px` | 400 | Lowercase label above section headings. |
| Memory title | `24px` | 500 | “What Titan keeps” in memory demo. |
| Memory chip | `14px` | 400 | Saved memory cards. |
| TUI terminal | `13px` monospace | 400–700 | Coding terminal demo only. |
| Install terminal | `16px` Source Serif 4 | 400 | Quick-start command block. |

### Headline behavior

Headlines are large, light, and literary:

- Hero headline uses `line-height: 0.98` and `letter-spacing: -0.065em`.
- Section headlines use `line-height: 0.96` and `letter-spacing: -0.05em`.
- Weight stays at 400. Avoid heavy/bold headlines.
- The hero breaks into two block spans: “Your coding agent” and “should remember.”
- The emphasized word “remember” is italic and `roseSoft`.

### Labels

Labels are lowercase, not uppercase:

- `local-first memory for ai agents`
- `what titan does`
- `memory graph`
- `quick start`
- `the point`

They use letter spacing (`0.08em`) and rose-soft color. Do not turn them into aggressive all-caps SaaS labels.

### Monospace exception

Monospace is only allowed inside the simulated Pi terminal feed in the memory demo. The quick-start install terminal deliberately uses Source Serif 4 to keep the brand voice unified.

## Layout & Spacing

### Page structure

The live site is a single-page landing page with anchored sections:

1. Fixed nav
2. Hero
3. “What Titan does” memory demo
4. Memory graph
5. Quick start
6. Final CTA
7. Footer
8. Floating music toggle

### Main content widths

- Standard section width: `min(1180px, calc(100vw - 32px))`.
- Graph section width: `min(1500px, calc(100vw - 32px))`.
- Hero inner width: `min(980px, 100%)`.
- Final CTA width: `min(900px, calc(100vw - 32px))`.

Use centered containers with 16px side gutters on small screens.

### Vertical rhythm

- Desktop section padding: `108px 0`.
- Mobile section padding: `82px 0`.
- Final CTA: `110px 0 130px`.
- Hero: `104px 20px 70px`, full viewport height.
- Section head bottom margin: `44px`.
- Hero eyebrow bottom margin: `28px`.
- Hero copy top margin: `34px`.
- Hero action top margin: `42px`.

### Section header layout

Most sections use a two-column `.section-head`:

```css
grid-template-columns: minmax(0, 0.7fr) minmax(280px, 0.5fr);
gap: 52px;
align-items: end;
```

The left column contains the label and heading. The right column contains the copy. On mobile, collapse to a single column.

### Navigation

The nav is fixed, glassy, centered, and quiet:

- Height: 88px desktop, 72px mobile.
- Background: dark translucent `rgba(8, 4, 7, 0.74)`.
- Backdrop blur: 16px.
- Border bottom: subtle rose line.
- Inner width: 1180px.
- Brand mark: 80×80 SVG, white, with soft white drop-shadow.
- Nav links: lowercase, 12px, muted rose.
- CTA: rounded pill, rose-tinted outline and background.

## Elevation & Depth

The site does not use conventional material elevation. Depth comes from atmosphere:

1. **Background images** — filtered and partially visible behind each major section.
2. **Vignettes** — radial and linear overlays darken edges and keep copy readable.
3. **Translucent panels** — panels feel embedded in the page, not floating above it.
4. **Soft rose glows** — used sparingly for emphasis and memory/state signals.
5. **Box shadows** — deep black shadows such as `0 26px 82px rgba(0, 0, 0, 0.42)`.

### Background system

The body background has layered texture:

- 30px grid in cool blue-gray.
- Rose radial glow near top center.
- Violet radial glow around the upper-right/mid-page.
- Deep vertical gradient from `bg` to `bgDeep`.
- Fixed starfield pseudo-element with tiny white dots at low opacity.

### Section images

Hosted section backgrounds:

| Section | Asset | Position | Brightness | Opacity | Purpose |
|---|---|---|---:|---:|---|
| Hero | `oland-first-slide-bg.png` | center | 1.75 | 0.54 | Night workstation atmosphere. |
| How | `bg-how.png` | 58% center | 1.75 | 0.42 | Memory demo atmosphere. |
| Graph | `bg-graph.png` | 46% center | 1.75 | 0.34 | Knowledge map atmosphere. |
| Start | `bg-start.png` | 50% center | 1.75 | 0.40 | Installation/setup atmosphere. |
| Final | `bg-graph.png` | 46% center | 1.75 | 0.44 | Closing memory atmosphere. |

All background images must be overlaid with dark radial and linear gradients. Never place text directly over raw images.

## Shapes

Titan's hosted website uses rounded, soft shapes — unlike the sharper investor one-pager aesthetic.

| Shape | Radius | Usage |
|---|---:|---|
| Pill | `999px` | Buttons, nav CTA, status pills, music toggle. |
| Small card | `10px–14px` | Search boxes, memory cards, memory chips. |
| Demo shell | `18px` | Main memory demo. |
| Terminal | `24px` | Quick-start terminal. |
| Graph shell | `28px` | Knowledge graph preview. |

Rounded shapes should feel organic and quiet, not bubbly. Avoid high-saturation candy UI.

## Components

### 1. Fixed nav

Purpose: persistent orientation without stealing focus from the hero.

Structure:

- Left: logo SVG + `titan memory` lowercase wordmark.
- Right: `how`, `graph`, `start`, `github` pill.

Rules:

- Keep nav translucent and blurred.
- Keep nav links lowercase.
- Keep the GitHub CTA as a modest pill, not a loud button.
- Brand mark is white in nav, not rose, because it reads like a luminous sigil.

### 2. Hero

Live copy:

- Eyebrow: `local-first memory for ai agents`
- Headline: `Your coding agent / should remember.`
- Subtitle: `Titan turns past sessions into local memory, so your agent can recall decisions, preferences, and project context when it needs them.`
- CTAs: `install titan`, `view github`

Design rules:

- Center-align the hero.
- Keep headline huge and low-line-height.
- Emphasize only `remember` in italic rose-soft.
- Hero background image should be visible but subdued by vignette.
- Buttons sit below copy with relaxed spacing.

### 3. Buttons

There are two main button styles.

**Primary button**

- Rounded pill.
- Border: soft rose.
- Background: rose gradient, low opacity.
- Box-shadow: warm low rose shadow.
- Text: warm ink.

**Secondary button**

- Rounded pill.
- Border: subtle line.
- Background: almost transparent white.
- Text: muted.

Hover behavior:

- Translate up by 2px.
- Increase border strength.
- Text becomes `ink`.
- Active state scales to `0.98`.

### 4. Memory demo

The memory demo is the core product storytelling component. It shows an agent session becoming saved memory.

Structure:

- Outer `.memory-demo` shell with rose/violet radial gradients.
- Top terminal bar with window controls and `pi` label.
- Left `.terminal-feed` showing a simulated coding conversation:
  - prompt
  - read action
  - diff
  - follow-up prompt
  - grep action
  - git/deploy action
  - input cursor
- Right `.terminal-memory` panel titled `What Titan keeps` with `local` pill.
- Memory chips:
  - The request
  - The context
  - The fix
  - The deploy
  - The result

Design rules:

- The terminal feed is the only place where monospace is allowed.
- Keep borders left-accented, like a TUI trace.
- Use green only for success/tool calls/local status.
- Animate lines and chips in a loop, but keep motion soft and readable.
- The right panel must look like extracted memory, not another terminal.

### 5. Memory graph

The graph section is deliberately simple in the hosted site:

- Label: `memory graph`
- Heading: `A map of remembered work.`
- Copy: `The graph is not a decoration. It is how memories, sessions, and decisions become inspectable.`
- Visual: `knowledge-graph-o2.png` inside a rounded graph shell.

Rules:

- Use a larger max width (1500px) so the graph image feels expansive.
- Keep the graph shell dark and softly rounded.
- Do not over-explain graph details on the marketing page; let the image carry visual proof.

### 6. Quick start

Quick start is a two-column section:

- Left: label, heading, copy, GitHub CTA.
- Right: terminal card with install commands.

Live commands:

```text
pi install npm:titan-pi-memory
/titan-setup
/titan-status
```

Rules:

- Keep command text Source Serif 4, not monospace, for brand continuity.
- Use `rosePale` for command prefixes.
- Terminal top uses three soft rose dots and a `terminal` label.

### 7. Final CTA

Live copy:

- Label: `the point`
- Heading: `less repeating. more remembering.`
- Copy: `Titan gives your coding agent the context it forgot.`
- CTAs: `install titan`, `view source`

Rules:

- Center-align.
- Use the same large headline style.
- Keep it quiet and conclusive, not salesy.

### 8. Footer

Footer is minimal:

- `titan memory`
- `local-first agent memory`
- `github`
- Product Hunt badge image

Rules:

- Keep footer dark and small.
- The Product Hunt badge is the only bright external badge. Let it exist but do not design the whole footer around it.

### 9. Music toggle

The live site includes a floating `play music` button in the bottom-right.

Rules:

- Rounded pill.
- Rose icon and label.
- Dark translucent background.
- Must not distract from content.
- Label toggles to `pause music` after playback.
- Keep it optional and non-blocking.

## Motion

Motion is slow, soft, and memory-like. It should feel like recall, not a dashboard loading spinner.

### Existing animations

| Animation | Usage | Behavior |
|---|---|---|
| `pulse` | Eyebrow dot | Dot gently breathes every 2.6s. |
| `terminalLine` | TUI lines | Lines fade/slide in, remain, then fade out in an 18s loop. |
| `memoryChip` | Memory cards | Chips slide in from the right after terminal activity. |
| `cursorBlink` | Terminal caret | Classic blink using steps. |
| `breathe` | Graph nodes in older/alternate graph implementation | Nodes slowly scale up/down. |
| `reveal` | Sections | IntersectionObserver adds visible class on scroll. |

### Motion rules

- Prefer opacity + small translate shifts.
- Avoid bouncy easing.
- Use the site ease: `cubic-bezier(0.16, 1, 0.3, 1)`.
- Never animate large text aggressively.
- Respect readability in the terminal demo; animation should support comprehension.

## Responsive Behavior

The site is responsive by reducing section spacing, collapsing grids, and constraining wide panels.

### Breakpoints in spirit

- Around `980px`: section heads and two-column layouts collapse.
- Around `680px`: section padding reduces, nav height reduces, hero type and section type rely on clamp minimums.
- Small screens: horizontal overflow must be avoided; terminals may need internal scrolling.

### Mobile rules

- Keep gutters at least 16px.
- Preserve the hero's emotional impact, but do not let the giant type overflow.
- Collapse `.section-head` to one column.
- Stack quick-start columns.
- Memory demo may stack terminal feed and memory panel vertically if space is tight.
- Keep nav simple; if links do not fit, reduce gap before hiding content.

## Do's and Don'ts

### Do

- Use **Source Serif 4** as the primary brand voice.
- Keep copy warm, direct, and human.
- Use rose-coral as the memory/accent signal.
- Keep backgrounds atmospheric but heavily vignetted.
- Use lowercase labels and lowercase nav links.
- Let the memory demo tell the product story visually.
- Use soft rounded panels and translucent surfaces.
- Keep the site local-first and memory-first in language.

### Don't

- Do not turn this into a generic AI SaaS landing page.
- Do not use cold blue/purple gradients as the primary brand expression.
- Do not introduce a sans-serif UI system to the hosted site.
- Do not use harsh all-caps labels.
- Do not overuse neon green; it is reserved for local/success/tool-call state.
- Do not place text directly over bright unfiltered images.
- Do not make the Product Hunt badge the dominant footer element.
- Do not over-explain technical internals above the fold.

## Implementation Notes

### Canonical source files

- Hosted website source: `assets/web/titanwebsite/index.html`
- Deployed URL: `https://www.titanmemz.in/`
- Hero background: `assets/web/titanwebsite/oland-first-slide-bg.png`
- Section backgrounds: `bg-how.png`, `bg-graph.png`, `bg-start.png`, `bg-final.png`
- Graph preview: `assets/web/titanwebsite/knowledge-graph-o2.png`
- Logo used by hosted nav: inline SVG inside the HTML

### Relationship to other Titan design systems

Titan has multiple visual layers:

1. **Hosted marketing website (`titanmemz.in`)** — warm editorial serif, atmospheric backgrounds, rose accents.
2. **Investor one-pager artifacts** — premium black architectural, Space Grotesk, sharper cards.
3. **3D/graph memory visualization** — cosmic/neon memory graph, graph nodes, bloom, cluster maps.

Do not collapse these into one generic style. This `DESIGN.md` is specifically for the hosted marketing website.
