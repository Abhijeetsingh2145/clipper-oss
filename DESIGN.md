# Design

<!-- impeccable:design-schema 1 -->

## World

"Grading bay": Clipper reads as a colorist's grading suite, not a SaaS dashboard. Every AI judgment the product makes — a clip score, a sub-score, a job's pass/fail state — renders as a live instrument reading beside the footage, with its measured evidence captioned directly under it, never a bare number in a rounded card. The surround stays a genuinely neutral gray rather than the near-black-plus-neon-accent look common to AI product UI, the way a real grading bay stays neutral so color judgment isn't biased by a tinted background.

Direction contract: `.impeccable/surfaces/clipper-static.md`.

## Palette

Strategy: Restrained (neutrals carry the surface; one accent marks the actionable/interactive layer; three further signal colors are reserved for verdict state, never decorative).

- `--bg #17191a` / `--bg-2 #101212` — neutral mid-dark ground, no blue/purple tint.
- `--panel #1d2020` / `--panel-2 #242827` / `--panel-3 #2c302f` — panel layers.
- `--line #343938` / `--line-2 #454b49` — hairline dividers (panels are flush/bordered, never floating shadow cards).
- `--text #e8eae7` / `--muted #9ba39e` / `--faint #74817b`.
- `--accent #d9803f` — "patch cable orange," the one interactive/primary-action color. Used for active nav, primary buttons, focus rings, active tabs, selected transcript word, active layer-sheet edge.
- `--ok #74c98e` (scope green) / `--warn #d9ae4b` (amber) / `--bad #d9655a` (signal red) — diegetic verdict colors, doing double duty as both scope reading state and score verdict (pill/dot/meter-zone colors). Never used decoratively.

## Type

- `--sans`: IBM Plex Sans — workhorse grotesk for prose/labels/body text.
- `--mono`: IBM Plex Mono — every number without exception: scores, timecodes, durations, pill text, table headers, job timestamps. This is a firm rule, not a style choice left to taste: a number in the sans face is a bug.
- Labels (`label.f`, `h2`) are small, tracked, uppercase — instrument-panel legends, not SaaS section headers.

## Components

- **`.card`** — a "scope module": hairline frame, flush (no shadow, no radius beyond 3px). A card containing a reading (`.score`, `.bar`, `.meter`, `.timeline`) gets the tick-graticule background (`--tick`, a `repeating-linear-gradient`); a plain list/form card does not — the graticule marks an instrument, it is never blanket decoration.
- **`.bar` (meter bridge)** — the signature component. A zone-banded track (red 0–40% / amber 40–70% / green 70–100%, printed as a background gradient, like a VU meter's dial) with a bright `<i>` fill reading as a needle/LED position, not a generic progress bar. Used for the clip score and every one of its measured sub-scores (`.parts .p`), each captioned with its real evidence string directly beneath (`.ev`).
- **`.btn` / `.toggle` / `input[type=range]`** — deck hardware: rectangular hairline buttons, a rocker-switch toggle, a tick-scaled fader slider. No pill buttons, no soft shadows.
- **`.tabs` / `.panel-tabs`** — a channel-select strip: flush segments, active channel marked with a 2px accent underline, not a filled pill.
- **`.layer-stack` / `.layer-sheet`** — the clip editor's Visuals panel renders each edit layer (captions, zooms, B-roll, logo, progress bar, punch-ins) as an independently addressable sheet with its own live reading (count/state), the active ones lit with an accent left-edge. Donated from the darkroom exposure-record challenger during direction selection: overlapping, inspectable layers rather than a plain settings list.
- **`nav.side`** — the module rack: hairline-divided rows, active route marked with a left accent bar, a tally-light status dot in the footer.

## Motion

Short, utilitarian transitions only (`.15–.3s`, a flat ease) — a view fades/lifts 4px on entry, a tally dot pulses softly to read as "live." No springy/bouncy motion; this is an instrument panel, not a marketing surface.

## Provenance

No generated imagery ships in this surface — every visual element is CSS/SVG built from the direction contract, not a produced raster. Icons are inline SVG (existing icon set, one addition: `#i-eraser` for Caption remover).

## Known deliberate exception

The detector's `repeating-stripes-gradient` advisory fires on `--tick`, the literal measurement-graticule pattern this whole direction is built on (real grading-suite scopes and meter dials are tick-marked). It is scoped to instrument surfaces only (nav, timeline, drop zone, reading-bearing cards) after one tightening pass, and kept deliberately rather than removed — stripping it would strip the system's own device to satisfy a pattern that can't distinguish purposeful ticks from decorative filler.
