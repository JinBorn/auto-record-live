# Design: Vision-Based Match Detection

## Architecture

```
┌─────────────────┐
│  Raw Recording  │  (multi-match .mp4, e.g. 69 min)
└────────┬────────┘
         │
         v
┌─────────────────────────────────────┐
│  VisionMatchDetector                │
│  - sample frames every 20s          │
│  - OCR game timer from top-right    │
│  - detect lobby/select screens      │
│  - stitch into match segments       │
└────────┬────────────────────────────┘
         │
         v
┌─────────────────────────────────────┐
│  Match Segments                     │
│  [{start_s, end_s, timer_trace,     │
│    is_complete, confidence}]        │
└────────┬────────────────────────────┘
         │
         v
┌─────────────────────────────────────┐
│  Segmenter (updated)                │
│  - emit one MatchBoundary per       │
│    detected match                   │
│  - confidence → exporter filter     │
└─────────────────────────────────────┘
```

## Core Components

### 1. `src/arl/vision/frame_sampler.py`

```python
def sample_frames(
    video_path: Path,
    interval_seconds: float = 20.0,
    output_dir: Path | None = None
) -> list[tuple[float, np.ndarray]]:
    """Extract frames at regular intervals.
    Returns [(timestamp_seconds, bgr_frame), ...]
    If output_dir provided, also writes PNGs for debugging.
    """
```

Uses opencv `cv2.VideoCapture` + seek to each sample point.

### 2. `src/arl/vision/timer_ocr.py`

```python
@dataclass
class TimerReading:
    timestamp_seconds: float  # recording time
    game_time_text: str | None  # "22:57" or None if not in-game
    confidence: float

def read_timer(frame: np.ndarray, detector: str = "auto") -> TimerReading:
    """Crop top-right 150×50px, OCR the MM:SS timer.
    detector: "tesseract" | "easyocr" | "template" | "auto"
    Returns TimerReading with game_time_text=None if lobby/select screen.
    """
```

**OCR Implementation Priority** (pick first available):
1. `template` — opencv matchTemplate against 0-9 + ":" digit templates extracted
   from LoL font. Fastest, no model download, LoL-specific.
2. `tesseract` — pytesseract if `tesseract` binary on PATH. Good for digits.
3. `easyocr` — downloads ~100MB model on first run, pure Python, works offline.

Config: `ARL_VISION_TIMER_OCR_DETECTOR` (default "auto" tries template → tesseract → easyocr).

### 3. `src/arl/vision/match_stitcher.py`

```python
@dataclass
class MatchSegment:
    start_seconds: float
    end_seconds: float
    timer_trace: list[tuple[float, str]]  # [(rec_time, "MM:SS"), ...]
    is_complete: bool
    confidence: float
    reason: str  # "complete", "incomplete_no_start", "incomplete_no_end"

def stitch_matches(readings: list[TimerReading]) -> list[MatchSegment]:
    """Group timer readings into match segments.
    Logic:
    - In-game span = consecutive readings with game_time_text.
    - Match start = first reading where game_time ≈ "00:XX" (< 2 min).
    - Match end = last reading before timer disappears + next is lobby/None.
    - is_complete = has_start AND has_end.
    - confidence = 0.95 if complete, 0.3–0.5 if incomplete.
    """
```

**Heuristics**:
- Match start: game timer first appears at ≤ 2:00 (allows for loading screen delay).
- Match end: timer present → timer absent for ≥ 2 consecutive samples (40s gap
  suggests lobby/results screen).
- Incomplete head: first timer reading > 5:00 → recording joined mid-game.
- Incomplete tail: last timer reading present but recording ends (no transition
  to lobby) → match was cut off.

### 4. Integration into `src/arl/segmenter/service.py`

```python
class SegmenterService:
    def run(self, *, session_ids: set[str] | None = None) -> None:
        # ...
        if self.settings.vision.match_detection_enabled:
            try:
                segments = self._detect_matches_visually(recording_asset)
                boundaries = self._segments_to_boundaries(segments, recording_asset)
            except Exception as e:
                log("segmenter", f"vision detection failed: {e}, falling back")
                boundaries = self._legacy_segmentation(recording_asset)
        else:
            boundaries = self._legacy_segmentation(recording_asset)
```

Legacy segmentation = existing stage-hints-based logic (one boundary, possibly
wrong, but backward compatible).

### 5. Exporter filter

```python
# in ExporterService.run():
for boundary in boundaries:
    if boundary.confidence < 0.8:
        log("exporter", f"skip incomplete match confidence={boundary.confidence}")
        continue
    # ... proceed with export
```

## Config

New `VisionSettings` in `src/arl/config.py`:

```python
class VisionSettings(BaseModel):
    match_detection_enabled: bool = True
    frame_sample_interval_seconds: float = 20.0
    timer_ocr_detector: str = "auto"  # "auto" | "template" | "tesseract" | "easyocr"
    timer_crop_region: tuple[int, int, int, int] = (1770, 5, 150, 50)  # x,y,w,h for 1920×1080
    match_start_threshold_seconds: float = 120.0  # timer ≤ 2min = match start
    lobby_gap_threshold_seconds: float = 40.0  # 2 consecutive None = match end

class Settings(BaseModel):
    # ...
    vision: VisionSettings = VisionSettings()
```

Env vars: `ARL_VISION_MATCH_DETECTION_ENABLED`, `ARL_VISION_FRAME_SAMPLE_INTERVAL_SECONDS`, etc.

## Data Flow

1. `arl segmenter --session-id <sid>` (or `postprocess`)
2. Loads `recording-assets.jsonl`, finds raw .mp4
3. `VisionMatchDetector.detect(raw_path)`:
   - Sample frames every 20s
   - OCR timer from each frame
   - Stitch into match segments
4. Emit one `MatchBoundary` per segment to `match-boundaries.jsonl`, with:
   ```json
   {
     "session_id": "...",
     "match_index": 2,
     "started_at_seconds": 1230.4,
     "ended_at_seconds": 3587.2,
     "confidence": 0.95,
     "metadata": {"is_complete": true, "reason": "complete"}
   }
   ```
5. Exporter reads boundaries, skips `confidence < 0.8`
6. Only complete matches export

## Rollback / Compatibility

- Set `ARL_VISION_MATCH_DETECTION_ENABLED=false` → legacy segmenter.
- If vision module throws, automatically falls back to legacy.
- Existing boundaries (one per session) coexist with new multi-match boundaries
  (different `match_index` range).

## Testing Strategy

- **Unit**: `test_vision/test_timer_ocr.py` with synthetic cropped timer images.
- **Unit**: `test_vision/test_match_stitcher.py` with hand-crafted timer sequences.
- **Integration**: `test_pipeline/test_vision_match_detection.py` with real
  session-20260610124818-f00e5b00 raw, assert 3 segments (1 complete, 2 incomplete).
- **Regression**: after implementing, rerun on 816 and 818, manually verify export
  outputs are complete games.

## Known Limitations

- LoL-specific (timer position, font). Other games need separate detectors.
- Assumes 1920×1080 raw (or rescale crop region proportionally).
- Cannot stitch matches across recording files (session boundary mid-game).
- Relies on ≥20s samples; very short matches (<60s) might be missed (not a
  realistic LoL scenario).

## Future Enhancements

- Template library for victory/defeat overlays → more robust end detection.
- Cross-session stitching (match spans two raw files).
- Per-game detector plugins (Dota, Valorant, etc.).
- Adaptive sampling (dense near transitions, sparse during stable gameplay).
