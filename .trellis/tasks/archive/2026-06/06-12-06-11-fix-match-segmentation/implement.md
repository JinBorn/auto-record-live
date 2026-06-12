# Implementation Plan: Vision-Based Match Detection

## Phase 1: Core Vision Module (timer OCR + frame sampling)

### 1.1 Scaffold vision package
- [ ] Create `src/arl/vision/__init__.py`
- [ ] Create `src/arl/vision/models.py` with `TimerReading`, `MatchSegment` dataclasses
- [ ] Add `VisionSettings` to `src/arl/config.py` with defaults

### 1.2 Frame sampler
- [ ] Implement `src/arl/vision/frame_sampler.py::sample_frames()`
  - Use `cv2.VideoCapture`, seek to each sample point
  - Return `[(timestamp_s, bgr_frame), ...]`
  - Optional: write debug PNGs if `output_dir` provided
- [ ] Unit test: synthetic 60s video, sample every 20s → 4 frames

### 1.3 Timer OCR — template matcher (LoL-specific, fastest)
- [ ] Extract 0-9 + ":" digit templates from a known LoL timer frame
  - Crop each digit individually, save as `data/vision/lol_timer_digits/{0..9,colon}.png`
- [ ] Implement `src/arl/vision/timer_ocr.py::read_timer_template()`
  - Crop top-right 150×50px from frame
  - Threshold to isolate white text
  - matchTemplate each digit, assemble MM:SS string
  - Return `TimerReading(timestamp, game_time_text, confidence)`
- [ ] Unit test: known timer crops → "22:57", "03:09", None (lobby frame)

### 1.4 Timer OCR fallbacks (optional, for non-LoL future)
- [ ] Implement `read_timer_tesseract()` if `shutil.which("tesseract")`
- [ ] Implement `read_timer_easyocr()` as last resort (~100MB model download)
- [ ] `read_timer(detector="auto")` tries template → tesseract → easyocr

## Phase 2: Match Stitcher

### 2.1 Core stitching logic
- [ ] Implement `src/arl/vision/match_stitcher.py::stitch_matches()`
  - Group consecutive in-game readings (game_time_text not None)
  - Detect match start: first timer ≤ 2:00 in a span
  - Detect match end: timer present → absent for ≥40s (2 samples)
  - Mark `is_complete = has_start and has_end`
  - Assign `confidence`: 0.95 if complete, 0.3–0.5 if incomplete
- [ ] Unit test: hand-crafted timer sequences
  - Complete match: [None, "00:34", "05:12", ..., "34:56", None, None] → is_complete=True
  - Incomplete head: ["23:45", "28:12", None] → incomplete_no_start
  - Incomplete tail: [None, "01:23", "15:00"] (no ending None) → incomplete_no_end

### 2.2 Edge cases
- [ ] Handle non-monotonic timer (recording glitch / OBS dropout) → mark suspicious, reduce confidence
- [ ] Handle very short in-game spans (<60s) → likely false positive, discard or mark low-conf

## Phase 3: Integration into Segmenter

### 3.1 Vision detector service
- [ ] Implement `src/arl/vision/detector.py::VisionMatchDetector`
  - `detect(video_path: Path) -> list[MatchSegment]`
  - Orchestrates: sample_frames → read_timer → stitch_matches
  - Cache results to `data/tmp/vision-match-detection.jsonl` (one row per session)
- [ ] Add `--force-reprocess` flag to re-detect even if cached

### 3.2 Segmenter integration
- [ ] Update `src/arl/segmenter/service.py::SegmenterService.run()`
  - Check `settings.vision.match_detection_enabled`
  - If True: call `VisionMatchDetector.detect(recording_path)`
  - Convert `MatchSegment` → `MatchBoundary` (one per segment, sequential `match_index`)
  - On exception: log warning, fall back to legacy segmentation
- [ ] Legacy segmentation stays unchanged (stage-hints path)

### 3.3 CLI command
- [ ] Add `arl detect-matches --session-id <sid> [--force-reprocess]`
  - Directly invokes vision detector, prints segments
  - Useful for debugging / manual verification

## Phase 4: Exporter Filter

### 4.1 Confidence gating
- [ ] Update `src/arl/exporter/service.py::ExporterService.run()`
  - Before processing boundary, check `boundary.confidence < 0.8`
  - If True: log skip reason, emit audit row `export_skipped_incomplete_match`, continue
- [ ] Add audit event type to `src/arl/shared/contracts.py`

### 4.2 Status reporting
- [ ] Update `arl status` to show:
  - Total boundaries per session
  - Complete vs incomplete count
  - Exported count (complete only)

## Phase 5: Testing & Validation

### 5.1 Unit tests
- [ ] `tests/vision/test_frame_sampler.py` — synthetic video sampling
- [ ] `tests/vision/test_timer_ocr.py` — known timer crops → expected text
- [ ] `tests/vision/test_match_stitcher.py` — synthetic timer sequences → segments

### 5.2 Integration test (real data)
- [ ] `tests/pipeline/test_vision_match_detection.py`
  - Use `session-20260610124818-f00e5b00` raw as fixture (copy to test data or mock path)
  - Assert: detects 3 segments
  - Assert: segment[1] is_complete=True, spans ~1230→3600
  - Assert: segments[0] and [2] is_complete=False

### 5.3 Manual verification (acceptance)
- [ ] Run `arl detect-matches --session-ids session-20260610124818-f00e5b00,session-20260610124816-1e462fc3`
- [ ] Check emitted boundaries:
  - 818: 3 boundaries, match_index=1 incomplete, =2 complete, =3 incomplete
  - 816: 2–3 boundaries, at least one marked complete
- [ ] Run `arl postprocess --session-ids ...818`
- [ ] Verify export: only one .mp4 for 818 (match 2), duration ~20–30 min, complete game
- [ ] Manually watch first 30s and last 30s → starts near 0:00, ends with victory/defeat

### 5.4 Regression suite
- [ ] Ensure existing tests (non-vision sessions) still pass
- [ ] Add fallback test: vision disabled → legacy segmenter runs

## Phase 6: Documentation & Polish

### 6.1 Operator docs
- [ ] Update README or operator guide:
  - How to enable/disable vision detection
  - What "incomplete match" means and why it's skipped
  - How to debug: `arl detect-matches`, check `vision-match-detection.jsonl`
  - Env var reference: `ARL_VISION_*`

### 6.2 Spec update
- [ ] Update `.trellis/spec/backend/orchestration-contracts.md`:
  - New `MatchBoundary.confidence` semantics (0.95=complete, 0.3–0.5=incomplete)
  - Vision detection cache format
  - Exporter skip audit event

### 6.3 Known limitations doc
- [ ] Record in spec or PRD:
  - LoL-specific (1920×1080, fixed timer position)
  - Cannot stitch across session boundaries
  - Requires ≥20s samples (configurable but not adaptive yet)
  - Future: per-game detector plugins

## Rollback Plan

If vision detection causes regressions:
1. Set `ARL_VISION_MATCH_DETECTION_ENABLED=false` (legacy segmenter)
2. Clear `vision-match-detection.jsonl` cache
3. Rerun `arl segmenter` → falls back to stage-hints
4. If code is broken: revert commits, restore from `git reflog`

## Checkpoints & Review Gates

- After Phase 2: review stitching logic with sample timer traces (unit tests green)
- After Phase 3.2: manual dry-run on 818 raw → check detected segments JSON
- Before Phase 5.3: code review — ensure fallback path is tested, no silent failures
- After AC2 passes: final review → merge or iterate

## Time Estimate (rough)

- Phase 1: 2–3 hours (frame sampling + template OCR prototype)
- Phase 2: 1–2 hours (stitching logic + unit tests)
- Phase 3: 2–3 hours (segmenter integration + CLI)
- Phase 4: 1 hour (exporter filter)
- Phase 5: 2–3 hours (tests + real-data validation)
- Phase 6: 1 hour (docs)
- **Total: ~10–13 hours** (can parallelize some, or split across sessions)

Priority: get Phase 1–3 working end-to-end first (detect → boundaries), then verify on real 818 before polishing exporter/tests.
