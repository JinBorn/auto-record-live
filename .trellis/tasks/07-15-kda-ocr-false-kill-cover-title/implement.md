# Implement — Fix KDA OCR false-kill events and cover text using title

## Order of work

Cover fix first (cheap validation), then OCR templates, then anti-flicker,
then the long revalidation chain.

## Checklist

### Phase A: Title-based cover text (R3)

- [ ] A1. Add `_title_cover_lines` helper to `src/arl/copywriter/service.py`
      (punctuation split → ≤12-char chunking → ≤4 lines → fallback to
      `cover_lines` when title empty).
- [ ] A2. Use it in `_render_cover_if_possible` (replace
      `package.cover_lines` argument).
- [ ] A3. Unit tests for `_title_cover_lines` + render-call wiring test.
- [ ] A4. Validate: `pytest tests/copywriter -q` (or matching path), then
      `./.venv/Scripts/python.exe -m arl.cli copywriter --session-id
      session-20260617073649-4b5ec478 --match-index 2 --force-reprocess`;
      view regenerated `match-02-cover-01.jpg` — text equals wrapped title
      (AC4).

### Phase B: Real-font glyph templates (R1)

- [ ] B1. Harvest glyphs: extract verified frames from
      `data/raw/session-20260617073649-4b5ec478/recording-source.mp4`
      covering digits 0-9 + `/` in the KDA crop region (1665,0,85,32);
      visually verify each digit label against the frame before cropping.
- [ ] B2. Save tight binary glyph crops to
      `src/arl/vision/templates/lol_zh_1080p/{0..9,slash}.png`.
- [ ] B3. Extend `_templates()` in `src/arl/vision/kda_ocr.py`: load real
      glyphs first (threshold >145 binary), append Hershey synthetics as
      fallback; missing dir → Hershey-only.
- [ ] B4. Commit test fixture crops (85×32) for 6/7/8/9 incl. the
      previously-misread 4132 frame + remaining digits; add
      `read_kda` truth-table test (AC2).
- [ ] B5. Sanity margin check on the misread fixture: `'6'` wins with a
      clear margin (assert top-1 char correct; log margin in test output).

### Phase C: Refinement anti-flicker (R2)

- [ ] C1. Rework `_stable_refined_timestamp` (full-scan, reject on baseline
      reversion after candidate run, no early return).
- [ ] C2. Add range-end coverage gate (1.5s) to the streaming confirmation
      path in `analyze()`; `finalize()` evaluates without the gate.
- [ ] C3. Detector unit tests: flicker-reject, monotone-confirm,
      partial-coverage finalize, streaming gate timing.

### Phase D: Full validation

- [ ] D1. Full suite: `./.venv/Scripts/python.exe -m pytest -q` (AC5).
- [ ] D2. `vision-analysis --session-id session-20260617073649-4b5ec478
      --force-reprocess` (long, ~1h — run detached/background per OOM/
      heartbeat memory rules). Verify in the new asset: no kda_change event
      in [4120, 4132] window claiming 6→8; real transitions present near
      ~4136/4140 (possibly merged by coarse sampling) and ~4158 (AC1).
- [ ] D3. Downstream chain for the match:
      `highlight-planner --force-reprocess` → `edit-planner
      --force-reprocess` → `exporter --force-reprocess` (background,
      heartbeat) → `copywriter --force-reprocess` → `quality-report`.
- [ ] D4. Verify final export: no coin near ~591.6s unless a real kill maps
      there; coins align with real kills (cross-correlation spot-check as
      needed) (AC3). Verify cover text = title (AC4).
- [ ] D5. Update backend spec (vision-analysis / editing-quality contracts)
      for real-font templates, anti-flicker refinement semantics, and
      title-based cover text (AC6).

## Validation commands

```bash
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m arl.cli vision-analysis --session-id session-20260617073649-4b5ec478 --force-reprocess
./.venv/Scripts/python.exe -m arl.cli highlight-planner --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
./.venv/Scripts/python.exe -m arl.cli edit-planner --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
./.venv/Scripts/python.exe -m arl.cli exporter --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
./.venv/Scripts/python.exe -m arl.cli copywriter --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
./.venv/Scripts/python.exe -m arl.cli quality-report --session-id session-20260617073649-4b5ec478 --match-index 2
```

## Review gates / rollback

- Gate 1 after Phase A: cover visual OK before touching detector code.
- Gate 2 after Phase B+C: full pytest green before starting the ~1h vision
  rerun.
- Rollback: `git checkout` the three source files, delete
  `src/arl/vision/templates/` and new tests; cached vision assets for other
  sessions are untouched.

## Notes

- Inline implementation only — no trellis-implement/check sub-agent dispatch
  (standing rule).
- Publish preset comes from `.env` (`ARL_POSTPROCESS_PRESET=publish`); no
  extra flags needed on the worker commands.
- 60s progress heartbeats during vision rerun and export.
