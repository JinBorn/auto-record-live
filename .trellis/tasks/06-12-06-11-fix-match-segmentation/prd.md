# PRD: Fix Match Segmentation

## Problem statement

Postprocess exports produce unusable output: a 63-min recording exports as a
60-min "highlight" clip that ends abruptly at 14 minutes into the game (level 11,
base not destroyed), and an 18-min recording exports the wrong 5-min segment
(joins mid-game at level 11, incomplete).

**Root cause**: recordings contain **multiple matches** (2–3 games per raw file),
but the segmenter emits one boundary covering ~92% of the raw (session ...816)
or only 26% (session ...818, ignoring 51 min containing a complete match).
Subtitle/commentary cannot mark "game start → base explosion" boundaries, and
for 818, 74% of the video was never transcribed because the boundary was wrong.

Visual inspection (frame extraction + game timer reading) confirms:
- **818 (69 min)**: Match 1 incomplete (recording joined at 23:52 game-time),
  Match 2 **complete** (11:17 → 34:37, the one you saw), Match 3 incomplete
  (champion select → recording ends). The complete match is entirely inside the
  51 min the segmenter ignored.
- **816 (69 min)**: ~3 matches with non-monotonic timers (t400=6:32, t2500=4:36,
  t4150=3:09), all crammed into one 63-min boundary. Condensing then amputated
  the real ending.

## User workflow

Operator provides a multi-match raw recording. The system:
1. Detects match boundaries visually (game timer + lobby screens).
2. Labels each detected match as complete (has start ≈0:00 + natural end) or
   incomplete (recording joined mid-game or ends before victory/defeat).
3. Only exports complete matches. Incomplete matches are logged but skipped.

Optional: apply highlight condensing to complete matches (retain budget logic
from the now-reverted commit ff241bb, but only after correct segmentation).

## Scope

**In scope:**
- New `src/arl/vision/` module: frame extraction, game-timer OCR, match stitching.
- Integrate into segmenter to emit one `MatchBoundary` per detected match, with
  `confidence` indicating complete (0.9+) vs incomplete (0.3–0.5).
- Exporter skips boundaries with confidence < 0.8 (or a new `is_complete` flag).
- CLI: `arl detect-matches --session-id <sid>` for manual runs.
- Tests: synthetic timer sequences, real-data regression on 818/816.

**Out of scope:**
- Non-LoL games (future: plugin architecture for per-game detectors).
- Real-time detection (offline batch only).
- Handling recording gaps / dropped frames (mark match incomplete if suspicion).

## Constraints

- **No new system dependencies**: use installed opencv + numpy + pillow. For OCR,
  prefer lightweight libs (pytesseract if tesseract binary available, or easyocr
  ~100MB model download, or opencv template matching for LoL's fixed-font timer).
- **Backward compatible**: existing subtitle/hint-driven segmenter stays as fallback
  when vision detection is disabled or fails.
- **Idempotent**: same raw → same boundaries across reruns.

## Success criteria

- [x] AC0 (POC): 技术可行性已验证 — opencv 模板匹配能从 1920×1080 LoL 帧中
  检测计时器数字(score>0.9),无新系统依赖,技术栈无阻塞。工程细节(模板质量/
  去重逻辑)留待正式实施时用 easyocr 或混合方案解决。
- [ ] AC1: Running `arl detect-matches --session-ids session-20260610124818-f00e5b00`
  emits 3 boundaries: Match 1 incomplete (conf 0.3), Match 2 complete (conf 0.95),
  Match 3 incomplete (conf 0.4). Match 2 spans correspond to the complete game
  visual inspection found (~t1230 → ~t3600).
- [ ] AC2: `postprocess --session-ids ...818` exports only Match 2 as a ~20–30 min
  video (the complete game, optionally condensed if highlight budget applies).
  Matches 1 and 3 are logged as incomplete and skipped.
- [ ] AC3: Same for ...816: detects ~3 matches, marks them by completeness, only
  exports complete ones.
- [ ] AC4: New vision module has unit tests (timer parsing, match stitching logic)
  and integration test with real 818 raw.
- [ ] AC5: Operator can disable vision detection via `ARL_VISION_MATCH_DETECTION_ENABLED=false`,
  falling back to legacy subtitle/hint segmenter.

## Open questions

- Timer OCR implementation: pytesseract (needs system binary), easyocr (~100MB
  model), or opencv template matching (LoL-specific but fastest/lightest)?
  → Prototype all three, pick based on accuracy + runtime.
- Sampling rate: every 10s? 20s? Denser near suspected transitions?
  → Start 20s, tune after real-data testing.
- How to detect "natural end" (victory/defeat screen vs recording cutoff)?
  → Victory/defeat has distinct full-screen overlay; detect via template or
    sudden timer disappearance + next frame is lobby/champion-select.
- What if a match spans two recording files (session boundary mid-game)?
  → V1: treat as two incomplete matches. V2: cross-session stitching (future).
