# Cover visual upgrade

## Goal

Bring generated covers to demo2 visual parity: pick dramatic source frames
intelligently, render the stacked yellow/black headline typography, and output
multiple candidate covers per match for manual selection.

## User Value

Demo2's cover is a high-drama frame (chat flooded with banter) with large
stacked yellow headline text outlined in black. Our current cover grabs one
frame near an evidence cue, darkens it, and draws a smaller two-tone layout —
serviceable but visibly weaker, and there is only one take.

## Requirements

- Smart frame selection: score candidate frames sampled around key events
  (KDA changes, chat-burst timestamps from the zoom task's detector when
  available, teaser windows) using cheap heuristics: sharpness, brightness,
  scene class (existing vision scene classifier), chat-region activity.
  Select the top 2-3 distinct timestamps (minimum spacing apart).
- Typography parity with demo2: all headline lines rendered large in yellow
  (approx #FFEE00) bold with a heavy black stroke, stacked and left-aligned;
  optional accent color for the first line stays configurable. Auto-fit font
  sizes for 2-4 lines of <=10 chars (cover lines come from the copywriter/LLM
  output). Keep 1920x1080 JPEG quality 92.
- Safe margins: text avoids the regions Bilibili overlays in feeds (duration
  badge bottom-right, title strip bottom on some surfaces); margins
  documented.
- Multi-candidate output: `cover-01.jpg`, `cover-02.jpg`, ... plus the
  existing default cover path pointing at the top-ranked candidate; publishing
  metadata lists all candidates.
- Degradation: with vision data missing, fall back to the current
  evidence-cue timestamp; with Pillow/fonts missing, keep the current silent
  skip behavior.
- Layout math (line fitting, margins, stroke sizing) is unit-testable without
  golden-image comparisons; rendering smoke test asserts file creation and
  dimensions only.

## Out Of Scope

- Streamer/facecam cutout compositing (no facecam source available).
- Template/sticker asset packs; text-on-shape badges beyond simple accents.
- Automatic A/B ranking of covers by predicted CTR.

## Acceptance Criteria

- [ ] Regenerated validation sessions produce 2-3 candidate covers each with
      distinct source frames; default cover path resolves to the top-ranked
      one; publishing metadata lists all candidates.
- [ ] Typography visually matches the demo2 reference in a side-by-side spot
      check recorded in the task (stacked yellow/black headline, readable at
      feed thumbnail size).
- [ ] Cover lines are consumed from the copywriter output (LLM path when
      enabled), never re-derived from raw subtitles inside the cover module.
- [ ] Frame-selection scoring is unit-tested with synthetic frame metrics;
      no ffmpeg/network in unit tests.
- [ ] Fallback paths (no vision data, no Pillow) keep current behavior.

## Notes

- Medium complexity; `design.md` optional if the frame-scoring approach stays
  heuristic — decide at planning review. `implement.md` recommended.
- Best run after `07-06-llm-copywriting-engine` ships (better cover lines),
  but the rendering/typography work is independent.
