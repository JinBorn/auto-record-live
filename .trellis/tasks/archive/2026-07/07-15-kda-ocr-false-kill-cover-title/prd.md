# Fix KDA OCR false-kill events and cover text using title

## Goal

1. Eliminate false `kda_change` events caused by KDA OCR digit misreads so kill
   SFX (coin) never plays where no kill happened.
2. Render publish cover text from the recommended title instead of separate
   LLM `cover_lines`.

## Background (verified 2026-07-15)

Session `session-20260617073649-4b5ec478` match 2, export output 591.6s
(user-reported 09:52) plays a coin SFX with no combat on screen.

Root cause chain (frame-verified against `recording-source.mp4`):

- Scoreboard truth: 6/2/2 from 4100s through ~4132s; real kills at ~4136s
  (6→7), ~4140s (7→8), ~4158s (8→9).
- `read_kda` template matching uses synthetic OpenCV Hershey glyphs that do
  not match the LOL zh 1080p HUD font. On misread frames `'8'` scores 0.7596
  vs `'6'` 0.7554 — a 0.004 margin flipped by compression noise.
- Coarse sample at 4130s misread 6→8, creating a 6→8 transition; refinement
  over [4120, 4130] confirmed on 3 consecutive misread frames (~1.35s) and
  early-exited, emitting a false event at 4124.416s → coin at output 591.6s,
  12s before the real kills. The real 6→7 and 7→8 transitions were swallowed
  by the corrupted coarse baseline.

Cover: current cover text renders LLM `cover_lines` (fragmented, low quality
per user). User decision: cover text = recommended title.

## Requirements

### R1: Real-font glyph templates (root fix)

- Harvest LOL zh 1080p HUD digit glyphs (`0-9`, `/`) from real, human-verified
  recording frames and use them as the primary template set in the KDA OCR
  recognizer.
- Synthetic Hershey templates may remain as fallback candidates, but the real
  glyph set must dominate matching for the supported layout.
- Misread margin check: on the captured regression frames, `'6'` must beat
  `'8'` decisively (see AC2).

### R2: Refinement anti-flicker guard (defensive fix)

- `KdaVisionDetector._stable_refined_timestamp` must not confirm a transition
  when the baseline value reappears after the candidate stable run within the
  refinement range (monotonicity check to range end).
- Early-exit on first 3-consecutive run is no longer sufficient; a transition
  confirms only if, after the first stable target run, no baseline reading
  occurs through the end of the refinement range.
- Unconfirmable transitions stay dropped (existing behavior when refinement
  yields None).

### R3: Cover text from title

- Publish cover rendering must use the recommended title as the text source,
  wrapped into display lines (split on title punctuation such as `：` `！`
  `？` `，` `。` `!` `?` `:` `,`; fall back to fixed-width chunking when a
  segment is still too long for the cover font box).
- LLM `cover_lines` remain in the publishing package metadata (upload.txt)
  but are no longer drawn onto the cover image.
- No LLM prompt/schema change required this task.

### R4: Regression coverage

- Unit tests cover: real-template recognition on captured true-positive and
  previously-misread crops; anti-flicker rejection of a flickered transition;
  acceptance of a clean monotone transition; cover line derivation from
  titles with/without punctuation.

## Acceptance Criteria

- [x] AC1: After `vision-analysis --force-reprocess` on
      `session-20260617073649-4b5ec478`, no `kda_change` event exists whose
      kills delta contradicts frame-verified scoreboard truth in
      [4120, 4132]; the false 6→8 event at ~4124.4s is gone.
      (Verified: t=4120/4130 now reject to None; only real 8→9 @4157.8 remains.)
- [x] AC2: On the captured regression crops (true 6/7/8/9 frames incl. the
      previously-misread 4132 frame), `read_kda` returns the true digits.
      (11/11 true frames correct; t4132 degraded frame rejected as missing.)
- [x] AC3: Re-exported match 2 has no coin SFX near output ~591.6s unless a
      real kill maps there; coins align with real kill events only.
      (6 coins map to source kill-only events at 0.0s distance; 591.6s
      ncc=0.004 == control baseline.)
- [x] AC4: Regenerated cover image text equals the recommended title
      (wrapped), not LLM cover_lines.
- [x] AC5: Full pytest suite passes. (787 passed.)
- [x] AC6: Backend spec updated (editing-quality, export-configuration,
      orchestration-contracts).

## Constraints

- Never dispatch trellis-implement/trellis-check sub-agents; implement and
  verify inline (standing user rule 2026-07-06).
- Full re-validation chain after code fix:
  `vision-analysis --force-reprocess` (~1h for the 92-min recording) →
  `highlight-planner --force` → `edit-planner --force` → `exporter --force`
  → `copywriter --force` → `quality-report`, publish preset via `.env`
  (`ARL_POSTPROCESS_PRESET=publish`).
- Cover-only change can be validated cheaply via
  `copywriter --force-reprocess` before the long vision rerun.
