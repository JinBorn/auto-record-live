# Design — Fix KDA OCR false-kill events and cover text using title

## Scope

Three code changes plus fixtures/tests/spec:

1. `src/arl/vision/kda_ocr.py` — real-font glyph templates (R1)
2. `src/arl/vision_analysis/builtin_detectors.py` — refinement anti-flicker (R2)
3. `src/arl/copywriter/service.py` (+ helper) — title-based cover text (R3)

## 1. Real-font glyph templates (R1)

### Current behavior

`_templates()` synthesizes `0-9` + `/` from `cv2.FONT_HERSHEY_SIMPLEX` at six
font scales × two thicknesses. Game HUD glyphs (LOL zh 1080p) differ enough
that on real frames `'6'` vs `'8'` IoU margin can be ~0.004 (measured
0.7596 vs 0.7554 on the misread frame), flipping with compression noise.

### Change

- Add binary glyph assets harvested from human-verified recording frames:
  `src/arl/vision/templates/lol_zh_1080p/<char>.png` for `0-9` (and `slash`
  for `/`), stored as the thresholded (>145) binary crop of each character
  box, tight-cropped like `_extract_char_boxes` output.
- Harvest source: `data/raw/session-20260617073649-4b5ec478/recording-source.mp4`
  (and earlier readings for digits not present near the investigated window).
  Every glyph frame is visually verified before cropping. Digits available in
  this session: 0-9 across K/D/A columns over the match timeline.
- `_templates()` returns real glyphs first, then the existing Hershey set as
  fallback candidates (merged candidate list, same IoU scoring). Real glyphs
  for the supported layout score far higher on true digits (~0.9 IoU), so
  they dominate matching; Hershey remains a safety net for glyph variants we
  did not capture.
- Loading: `Path(__file__).parent / "templates" / "lol_zh_1080p"`, read with
  `cv2.imread(..., IMREAD_GRAYSCALE)` + threshold, inside the existing
  `lru_cache` wrapper. Missing directory → Hershey-only (no crash).
- `_recognize_narrow_char` (fast-path for `/` and `1`) is unchanged.

### Non-goals

- No per-char confidence floor change (0.32 stays); real templates change the
  margin structure enough.
- No OCR of other HUD regions.

## 2. Refinement anti-flicker (R2)

### Current behavior

`KdaVisionDetector._stable_refined_timestamp` scans refined readings in
`[previous.ts, current.ts]`, returns `first_target_at` as soon as the target
value is seen 3 consecutive times after any baseline read (early return).
`analyze()` confirms transitions incrementally per refined frame; once all
active keys confirm, `refinement_range_complete()` short-circuits remaining
refined frames. A 3-frame misread run (~1.35s) therefore confirms a false
transition and stops looking.

### Change

`_stable_refined_timestamp` becomes a full-range monotonicity check:

- Scan all refined values in `[previous.ts, current.ts]` chronologically.
- Find the first run of ≥3 consecutive target reads that occurs after at
  least one baseline read (as today), candidate = run start timestamp.
- Continue scanning to the end of collected values: if any baseline read
  appears at `t > candidate` → flicker → return `None` (no early return).
  Non-baseline non-target reads (misreads of other digits) keep the current
  semantics: they reset the consecutive counter before a candidate is found
  and are ignored after the candidate (they are not evidence the counter
  reverted).
- Streaming gate in `analyze()`: only attempt confirmation when the latest
  refined value timestamp is within `1.5s` of `current.ts` (range-end
  coverage), so confirmation cannot happen before potential reversion frames
  are seen. `finalize()` evaluates without the gate (partial coverage — e.g.
  refinement cap exhausted — still confirms if the covered span shows a
  stable, non-reverting run).

### Effect on the investigated false event

Refined reads in [4120, 4130] contain true-6 frames after the 3-frame
misread-8 run → baseline reappears after candidate → event rejected. Real
transitions (e.g. 8→9 at ~4158) show no baseline after the target run →
confirmed unchanged.

### Cost

Ranges with an active kda key now decode to range end instead of stopping at
the confirming frame. Bounded by the existing refinement cap (15% of source);
`refinement_cap_exhausted` metric unchanged.

## 3. Title-based cover text (R3)

### Current behavior

`CopywriterService._render_cover_if_possible` passes `package.cover_lines`
(LLM-generated fragments) to `render_cover`, which draws up to 4 fitted lines.

### Change

- New helper `_title_cover_lines(title: str) -> list[str]` in
  `copywriter/service.py`:
  - Split the title on CJK/ASCII sentence punctuation
    (`：` `！` `？` `，` `。` `、` `:` `!` `?` `,` `;` `；`), dropping the
    separators, trimming empties.
  - If any segment exceeds 12 chars, re-chunk that segment into ≤12-char
    pieces (CJK titles; `_fit_cover_text` handles final pixel fitting).
  - Cap at 4 lines (renderer limit). Empty/whitespace title → fall back to
    `package.cover_lines` (defensive, keeps covers rendering).
- `_render_cover_if_possible` calls the renderer with
  `_title_cover_lines(package.recommended_title)` instead of
  `package.cover_lines`.
- `cover_lines` stays in `PublishingPackage` / upload.txt metadata unchanged
  (LLM prompt untouched).

Example: `觅渡炒股哲学：缩哈不如快乐直播！小丑前排肉到没人敢打` →
`["觅渡炒股哲学", "缩哈不如快乐直播", "小丑前排肉到没人敢打"]`.

## Compatibility / rollout

- Schema/asset contracts unchanged (readings, events, plans, packages).
- `config_fingerprint` of vision analysis does not include template content;
  regeneration is forced explicitly with `--force-reprocess` for the target
  session. Other sessions keep cached assets until forced.
- Rollback: revert the three files + delete template assets; no data
  migration involved.

## Test plan

- `tests/vision/test_kda_ocr_real_templates.py` (or extend existing kda_ocr
  tests, following current test layout):
  - Fixture crops (85×32 BGR PNGs) committed under the tests fixture dir:
    verified 6/7/8/9 frames including the previously-misread 4132-frame, plus
    frames covering remaining digits.
  - Assert `read_kda` parses each fixture to its labeled truth.
- Detector tests (extend existing vision_analysis detector tests):
  - Flickered refined sequence (baseline reappears after 3-run) → no event.
  - Monotone refined sequence → event at first stable timestamp (unchanged).
  - Partial-coverage finalize path still confirms monotone runs.
  - Streaming gate: no confirmation before range-end coverage.
- Copywriter tests:
  - `_title_cover_lines` splitting/chunking/cap/fallback cases.
  - Cover render call receives title-derived lines (existing render tests
    pattern with stub renderer).
