# Export Configuration Guidelines

> Export quality configuration patterns, FFmpeg encoding strategies, and quality preservation contracts.

---

## Overview

The exporter service (`src/arl/exporter/service.py`) transcodes match boundaries from raw recordings into final deliverable videos. This document covers:

1. Quality control configuration (CRF vs fixed bitrate)
2. Hardware vs software encoding tradeoffs
3. Highlight condensing vs full match export
4. Common quality preservation patterns

---

## Quality Control Modes

### Mode 1: Fixed Bitrate (Quality Preservation)

**When to use**: Preserve source recording quality without degradation

**Configuration**:
```bash
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
ARL_EXPORT_FFMPEG_PRESET=p7  # NVENC preset
ARL_EXPORT_USE_HARDWARE_ENCODING=1
```

**FFmpeg args generated**:
```bash
-b:v 4000k -maxrate 5000k -bufsize 8M -preset p7
```

**Use case**: 
- Source recordings at 3-4 Mbps need output at same or higher bitrate
- User wants broadcast-quality exports
- File size is less important than quality

**Behavior**:
- Output bitrate guaranteed ≥ configured value
- Complex scenes can burst up to maxrate
- Predictable file sizes: ~30 MB/min at 4 Mbps

### Mode 2: CRF (Size Optimization)

**When to use**: Balance quality and file size, prefer smaller files

**Configuration**:
```bash
# Don't set ARL_EXPORT_FFMPEG_BITRATE
ARL_EXPORT_FFMPEG_CRF=18
ARL_EXPORT_FFMPEG_PRESET=slow
```

**FFmpeg args generated**:
```bash
-crf 18 -preset slow
```

**Use case**:
- Storage constrained
- Acceptable quality loss (~20-40%)
- Variable bitrate acceptable

**Behavior**:
- Output bitrate varies based on scene complexity
- Smaller files than fixed bitrate mode
- Quality varies: simple scenes use less bitrate, complex scenes use more

---

## Decision Rule: When to Use Which Mode

```python
def choose_quality_mode(requirements):
    if requirements.preserve_source_quality:
        return "fixed_bitrate"  # Set ARL_EXPORT_FFMPEG_BITRATE
    elif requirements.minimize_file_size:
        return "crf"  # Use default CRF
    else:
        return "fixed_bitrate"  # Default to quality preservation
```

### Common Scenario Matrix

| Source Bitrate | Target Use Case | Recommended Mode | Config |
|----------------|-----------------|------------------|--------|
| 3-4 Mbps | Broadcast/Upload | Fixed 4 Mbps | `BITRATE=4000k` |
| 3-4 Mbps | Archive/Storage | CRF 18 | `CRF=18` |
| 2-3 Mbps | Web sharing | Fixed 3 Mbps | `BITRATE=3000k` |
| 2-3 Mbps | Low bandwidth | CRF 23 | `CRF=23` |

---

## Hardware Encoding Considerations

### NVENC (NVIDIA GPU Encoding)

**Key Differences from CPU Encoding**:

1. **CRF behavior differs**: NVENC CRF mode often produces **lower bitrate** than CPU libx264 at the same CRF value
   - Example: libx264 CRF 18 → 3.5 Mbps, h264_nvenc CRF 18 → 2.2 Mbps
   - **Recommendation**: Use fixed bitrate mode with NVENC for quality preservation

2. **Preset names differ**:
   - CPU: `ultrafast`, `veryfast`, `fast`, `medium`, `slow`, `slower`, `veryslow`
   - NVENC: `p1` (fastest) through `p7` (highest quality)
   - **Always use `p7` with NVENC** for quality work

3. **Speed advantage**: 10-20x faster than CPU encoding
   - 30-minute video: NVENC ~3 min, CPU ~30-60 min

**Configuration**:
```bash
ARL_EXPORT_USE_HARDWARE_ENCODING=1
ARL_EXPORT_FFMPEG_VIDEO_CODEC=h264
ARL_EXPORT_FFMPEG_PRESET=p7
```

**When hardware encoding fails**: The service falls back to CPU automatically (not implemented in current version, but codec selection supports both)

---

## Highlight Condensing vs Full Match Export

### Full Match Export (Default)

**Configuration**:
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=0
ARL_EXPORT_USE_HIGHLIGHT_PLANS=0
```

**Behavior**:
- Exports complete match from `started_at_seconds` to `ended_at_seconds`
- No time windows applied
- Duration = full match length (15-40 minutes typical)

**Use case**: 
- User wants complete game recordings
- Review full gameplay
- Upload to platforms that handle long videos

### Highlight Condensing

**Configuration**:
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=1
ARL_EXPORT_USE_HIGHLIGHT_PLANS=1
```

**Behavior**:
- Reads `HighlightPlanAsset` from `data/tmp/highlight-plans.jsonl`
- Exports only time windows marked as highlights
- Uses FFmpeg select filters to concatenate segments
- Duration = sum of highlight windows (3-8 minutes typical)

**Use case**:
- Social media clips
- Quick replay summaries
- Bandwidth-constrained sharing

## Scenario: ASS Subtitle Burn-In Sidecar

### 1. Scope / Trigger
- Trigger: Exporter subtitle burn-in can use a generated ASS render sidecar while keeping SRT as the canonical subtitle asset format.
- Trigger: This path spans `ExportSettings`, SRT subtitle files under `storage.processed_dir`, and FFmpeg `subtitles=` filter command construction.

### 2. Signatures
- Config:
  ```python
  class ExportSettings(BaseModel):
      burn_subtitles: bool = False
      use_ass_subtitles: bool = False
      ass_font_name: str = "SimHei"
      ass_font_size: int = 36
      ass_margin_v: int = 20
      ass_outline: int = 2
  ```
- Environment:
  ```bash
  ARL_EXPORT_BURN_SUBTITLES=1
  ARL_EXPORT_USE_ASS_SUBTITLES=1
  ARL_EXPORT_ASS_FONT_NAME=SimHei
  ARL_EXPORT_ASS_FONT_SIZE=36
  ARL_EXPORT_ASS_MARGIN_V=20
  ARL_EXPORT_ASS_OUTLINE=2
  ```
- Helper module:
  ```python
  write_ass_from_srt(srt_path: Path, ass_path: Path, style: AssSubtitleStyle) -> Path
  ```

### 3. Contracts
- `SubtitleAsset(format="srt")` remains the durable ASR interchange contract. Do not append a second `SubtitleAsset` row for the derived `.ass` file.
- ASS sidecars are generated only when `burn_subtitles=True`, `use_ass_subtitles=True`, and the SRT is not the deterministic placeholder subtitle.
- The generated sidecar lives next to the source SRT as `match-NN.ass` under `storage.processed_dir/<session_id>/`.
- Default behavior remains unchanged:
  - burn disabled: stream-copy video/audio and mux real SRT as `mov_text`
  - burn enabled with ASS disabled: use the existing SRT path in `subtitles=`
  - placeholder SRT: do not burn subtitles and do not generate ASS
- ASS sidecars must use the same `_subtitle_filter_arg()` escaping path as SRT subtitles so Windows drive letters and forward slashes stay valid for FFmpeg.
- ASS style defaults are bottom-centered white text with black outline at `PlayResX=1280`, `PlayResY=720`, font size `36`, margin V `20`, and outline `2`.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| `ARL_EXPORT_USE_ASS_SUBTITLES=0` | Preserve existing SRT burn-in or soft-subtitle behavior |
| Burn disabled and ASS enabled | Do not generate `.ass`; use stream-copy + `mov_text` for real SRT |
| Placeholder SRT and ASS enabled | Do not generate `.ass`; do not add `subtitles=` |
| Real SRT contains valid cues | Write/overwrite `match-NN.ass` and pass it to `subtitles=` |
| SRT is missing before export | Existing exporter missing-subtitle skip/defer behavior applies |
| SRT has no valid cues during ASS conversion | Defer the export instead of writing a broken FFmpeg command |
| Numeric ASS env values are below minimum | Clamp `font_size >= 1`, `margin_v >= 0`, and `outline >= 0` |

### 5. Good/Base/Bad Cases
- Good: `burn_subtitles=1` and `use_ass_subtitles=1` converts a real SRT to `match-01.ass`, then the FFmpeg command contains `-vf subtitles='.../match-01.ass'`.
- Base: `burn_subtitles=1` and `use_ass_subtitles=0` keeps the existing SRT `subtitles='.../match-01.srt'` command.
- Bad: Exporter appends `.ass` rows to `subtitle-assets.jsonl`, burns placeholder subtitles, or uses a separate unescaped subtitle filter path.

### 6. Tests Required
- Unit: ASS helper emits `[Script Info]`, `[V4+ Styles]`, `[Events]`, expected style fields, and `Dialogue:` rows.
- Unit: ASS helper preserves SRT cue timing, text, multiline breaks, Chinese text, and common formatting-tag cleanup.
- Config: `tests/test_config.py` asserts ASS env values load and numeric values clamp.
- Exporter: command tests assert `.ass` is used only when burn-in and ASS are both enabled.
- Exporter: regression tests assert SRT burn-in, soft-subtitle stream-copy, and placeholder no-burn behavior remain unchanged.

### 7. Wrong vs Correct
#### Wrong
```python
append_model(subtitle_assets_path, SubtitleAsset(path=str(ass_path), format="ass"))
command.extend(["-vf", f"subtitles='{ass_path}'"])
```

#### Correct
```python
subtitle_filter_path = subtitle_path
if burn_subtitles and settings.export.use_ass_subtitles:
    subtitle_filter_path = write_ass_from_srt(subtitle_path, subtitle_path.with_suffix(".ass"), style)
command.extend(["-vf", self._subtitle_filter_arg(subtitle_filter_path)])
```

## Scenario: Condensed Requires Continuity Signals

### 1. Scope / Trigger
- Trigger: Condensed exports are publishable long-form edits. They must not be built from disconnected visual activity peaks alone because that creates abrupt level/score jumps and can mix multiple games when segmentation is wrong.
- Trigger: This path spans subtitle assets, recording assets, highlight plans, and exporter plan consumption.

### 2. Signatures
- CLI:
  ```bash
  python -m arl.cli highlight-planner --session-id <session_id>
  python -m arl.cli highlight-planner --session-id <session_id> --match-index <n> --force-reprocess
  python -m arl.cli exporter --session-id <session_id> --force-reprocess
  ```
- Environment:
  ```bash
  ARL_HIGHLIGHT_PLANNER_ENABLED=1
  ARL_HIGHLIGHT_MODE=condensed
  ARL_HIGHLIGHT_CONDENSED_VISUAL_SAMPLE_INTERVAL_SECONDS=10
  ARL_EXPORT_USE_HIGHLIGHT_PLANS=1
  ```
- Deprecated/invalid plan window reason:
  ```text
  condensed_visual_activity
  ```

### 3. Contracts
- Placeholder subtitle text (`Placeholder subtitle generated by local pipeline.`) is not meaningful narration and must not create cue-based windows.
- Condensed mode must skip plan generation when no meaningful subtitle cues are available. Visual activity may contribute density scoring for real subtitle/event cues, but it is not sufficient to choose publishable cut windows.
- Condensed optimization must preserve every `key_event` cue even when duration reduction would otherwise drop a lower-positioned key-event window. The optimizer may exceed the target duration slightly to restore missing key-event windows.
- Condensed planning must treat detected player KDA kill/death increases from the top-right HUD as synthetic `key_event` cues. This KDA pass is best-effort OCR: unreadable frames must not block plan generation, but valid non-decreasing K/D/A changes must be preserved like subtitle-derived key events.
- KDA event windows must cover the interval from before the previous stable KDA reading through shortly after the changed reading, not only the changed/death-wait sample. Death changes need more pre-roll than kill-only changes because the lead-up to being killed is usually more valuable than the waiting-to-respawn segment.
- KDA-derived kill-only changes after a death are still key events by default. A non-zero `condensed_kda_post_death_kill_suppression_seconds` is an explicit operator override for known HUD catch-up noise; the default must preserve these changes because real post-death kill credit can happen before respawn.
- Death-event windows may be split by subtitle-free gaps inside the post-death wait. A silent gap of at least `condensed_kda_death_silent_gap_trim_seconds`, searched within `condensed_kda_death_silent_trim_lookback_seconds` before the death observation, should be removed when both resulting pieces remain at least `condensed_min_window_duration_seconds`.
- Death trimming must preserve a short post-death reaction tail. Even when the following respawn wait is low-value, keep `condensed_kda_death_reaction_tail_seconds` at the start of a removable silent gap or after the death observation so the edit does not hard-cut immediately after the player dies.
- After a death observation, condensed mode may drop `condensed_context` windows and shift later non-KDA `condensed_key_event` windows within `condensed_kda_death_wait_trim_seconds` to the first non-KDA subtitle key/tactical cue. This trims silent respawn waits and walking-back-to-lane lead-in while retaining the next meaningful fight/objective. Any window that overlaps a KDA kill/death cue must remain protected.
- Key/tactical windows must preserve short action-resolution narration. When a gank, chase, fight, or other attempt has no KDA change, viewers still need the outcome. If meaningful non-KDA subtitle cues continue shortly after a `condensed_key_event` or `condensed_tactical` window, extend that window up to `condensed_action_resolution_tail_seconds`, stopping when subtitle gaps exceed `condensed_action_resolution_gap_seconds` or the next planned window begins.
- Exception: when preserving every `key_event` would collapse content into a full-match span, condensed mode must choose the densest target-duration continuous content window instead of writing a full-span plan that the exporter will ignore.
- Condensed optimization must preserve match-start and match-end context. A condensed plan that does not cover both the beginning and end of the source boundary is invalid for export.
- Condensed large-gap collapse applies to content windows before mandatory match-start/match-end context is added. Edge context is editorial framing; it must not force the optimizer to collapse the entire match into one full-span window.
- After edge context is added, condensed optimization must insert short continuity bridge windows when adjacent output windows would otherwise leave a source-time gap larger than `condensed_boring_gap_threshold_seconds`. These bridge windows prevent visible game-clock/KDA/level jumps while keeping the edit shorter than a full match.
- Every optimized window must be clamped to `[0, match_duration_seconds]` before it is written to `highlight-plans.jsonl`; exporter plan validation treats out-of-bound windows as invalid and falls back instead of applying them.
- `highlight-planner --force-reprocess` appends a replacement plan for targeted matches even when a boundary-matching plan already exists. Downstream consumers build maps by `(session_id, match_index)`, so the latest row wins.
- Exporter must ignore legacy visual-only plans where every window has `reason="condensed_visual_activity"`.
- Exporter must ignore full-span highlight plans where one window already covers the whole boundary; full-boundary export should use the normal stream-copy/full-export path instead of select/aselect filters.
- `ARL_HIGHLIGHT_CONDENSED_VISUAL_SAMPLE_INTERVAL_SECONDS` controls visual scoring cost only; it must not be treated as a standalone editing strategy.
- Export remains explicit opt-in: the exporter applies these windows only when `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1`.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| Placeholder subtitle only | Skip condensed plan and log `reason=meaningful_subtitle_required` |
| Legacy visual-only plan exists | Exporter ignores the plan and falls back to full-boundary export behavior |
| Real subtitle cues exist | Prefer cue classification/window optimization; visual analysis may contribute density scoring |
| KDA OCR sees kills or deaths increase within the KDA reading-gap limit | Add a synthetic `key_event` covering the previous valid KDA sample through the changed sample |
| KDA OCR is unreadable, decreasing, or implausibly jumpy | Ignore that reading and keep condensed planning best-effort |
| KDA OCR has no stable reading for longer than the reading-gap limit | Treat the later reading as a new baseline instead of creating an oversized event |
| Kill-only KDA increase occurs after a death and suppression is disabled | Add a synthetic KDA key-event cue; post-death kill credit is still valuable content |
| Kill-only KDA increase occurs within a non-zero post-death suppression window | Do not add a KDA cue for that increase; keep the previous reading baseline moving forward |
| Death-event window contains a subtitle-free gap longer than the silent-gap trim threshold near the death observation | Split/remove the silent range if the retained pieces satisfy the minimum window duration |
| Removing a death-event silent gap would start exactly at the reaction moment | Preserve the configured reaction tail first, then remove only the remaining gap if it still reaches the silent-gap trim threshold |
| A death-event window ends at or before the death observation | Extend the retained death window to at least `current_at + condensed_kda_death_reaction_tail_seconds`, capped by the KDA cue end |
| A context-only window starts soon after a death observation | Drop it as low-value death wait context |
| A later key-event window starts soon after a death observation and overlaps any KDA cue | Keep the window; KDA kill/death changes override low-value wait trimming |
| A later non-KDA key-event window starts soon after a death observation but first meaningful subtitle key/tactical cue is later | Shift the window start to the first meaningful cue minus context padding |
| A key/tactical window is followed by continuous meaningful narration before the next planned window | Extend the current window to preserve the action outcome/explanation, capped by action-resolution tail/gap settings |
| A key/tactical window is followed by a subtitle-free gap larger than the action-resolution gap setting | Do not extend for later unrelated narration |
| Duration reduction drops a key event | Re-add a `condensed_key_event` window around that cue, then merge/clamp windows |
| Preserving all key events produces one full-match content window | Trim to the densest target-duration continuous content window before adding edge context |
| Condensed plan does not include start and end context | Exporter ignores the plan and falls back to full-boundary export behavior |
| Discontinuous content windows exceed the continuous-span cap | Preserve the windows and let the bridge pass insert continuity context instead of dropping key events |
| Edge context and selected content leave a source-time gap larger than `condensed_boring_gap_threshold_seconds` | Insert `condensed_continuity` bridge windows until every adjacent source gap is within the threshold |
| One plan window covers the full boundary | Exporter ignores the plan and uses the full-boundary export path |
| Optimized window would exceed match duration | Clamp the window end to match duration before persisting |
| Existing plan needs regeneration after logic changes | Use `highlight-planner --force-reprocess` with session/match filters |
| No plan exists or plan is stale | Exporter falls back to full-boundary export unless highlight plans are enabled and valid |

### 5. Good/Base/Bad Cases
- Good: A 90+ minute recording with real cues produces windows around key/tactical moments, and window gaps do not create incoherent level/score jumps.
- Base: A long recording with real subtitles uses cue-based key/tactical windows and visual scoring only as supporting signal.
- Bad: Placeholder subtitles produce a 6-10 minute visual-activity montage; the output jumps from level 3 to level 9 in one second or mixes multiple games.

### 6. Tests Required
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts placeholder subtitles emit no condensed plan and do not mark the match processed.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts `--force-reprocess` service semantics append a replacement plan.
- Unit: `tests/highlights/test_window_optimizer.py` asserts key-event windows are restored after duration reduction and windows are clamped to match duration.
- Unit: `tests/highlights/test_window_optimizer.py` asserts condensed plans preserve match edge context.
- Unit: `tests/highlights/test_window_optimizer.py` asserts condensed plans bridge large source-time gaps after edge context is added.
- Unit: `tests/vision/test_kda_ocr.py` asserts lightweight KDA OCR reads `K/D/A` crops and rejects blank crops.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts condensed plans preserve KDA-derived kill/death key events.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts post-death kill-only KDA changes are preserved by default.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts post-death low-value trimming does not shift away windows that overlap KDA kill/death cues.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts silent subtitle gaps inside death-event windows are removed while retaining the configured reaction tail.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts death-event windows are extended to preserve the configured post-death reaction tail.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts post-death low-value context is dropped and later key windows shift to the first meaningful cue.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts action-resolution narration after a failed gank/chase extends the preceding key/tactical window.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts action-resolution extension stops at large subtitle gaps.
- Unit: exporter tests assert legacy `condensed_visual_activity` plans are ignored even when `ARL_EXPORT_USE_HIGHLIGHT_PLANS=1`.
- Unit: exporter tests assert incomplete condensed plans and full-span plans are ignored.
- Integration: exporter highlight-plan tests assert `ARL_EXPORT_USE_HIGHLIGHT_PLANS` is required before select/aselect filters are applied.
- E2E/manual: run `highlight-planner` and `exporter` against real long recordings; verify `ffprobe` duration roughly equals retained window duration and output bitrate matches export quality settings.

### 7. Wrong vs Correct
#### Wrong
```python
meaningful_cues = [cue for cue in cues if not is_placeholder(cue.text)]
if not meaningful_cues and video_path is not None:
    windows = plan_visual_activity_windows(video_path, ...)
```

#### Correct
```python
meaningful_cues = [cue for cue in cues if not is_placeholder(cue.text)]
if not meaningful_cues:
    return None
```

#### Wrong
```python
drafts = reduce_to_target_duration(drafts)
write_plan(drafts)  # may omit a key event or exceed the match boundary
```

#### Correct
```python
drafts = reduce_to_target_duration(drafts)
drafts = ensure_key_events_preserved(drafts, classified_cues)
drafts = clamp_windows_to_match(drafts, match_duration_seconds)
write_plan(drafts)
```

---

## Scenario: Vision Timer Completeness Gate

### 1. Scope / Trigger
- Trigger: Vision match detection writes `MatchBoundary.is_complete`, which downstream subtitles/highlights/exporter use to decide whether a match is publishable.
- Trigger: Real recordings can begin mid-game or cut away before game end while scene classification still sees an `in_game -> other` span that looks complete.
- Trigger: Condensed exports can hide this defect by jumping across source time, so timer OCR must participate before export planning.

### 2. Signatures
- Config:
  ```python
  class VisionSettings(BaseModel):
      match_start_threshold_seconds: float = 120.0
      min_match_duration_seconds: float = 360.0
      min_complete_timer_seconds: float = 900.0
  ```
- Environment:
  ```bash
  ARL_VISION_MIN_COMPLETE_TIMER_SECONDS=900
  ```
- Vision API:
  ```python
  stitch_scene_readings(..., min_complete_timer_seconds=900.0, timer_readings=readings)
  ```

### 3. Contracts
- Timer OCR must read the right-top in-game clock from the upper HUD row, not FPS/latency or nearby score/gold digits.
- A scene segment marked complete must be downgraded when the first valid timer inside the segment is greater than `match_start_threshold_seconds`; reason is `incomplete_no_start`.
- A scene segment marked complete must be downgraded when valid timers exist but no timer reaches `min_complete_timer_seconds`; reason is `incomplete_timer_too_early`.
- Exporter must continue to skip boundaries where `is_complete=False` or `confidence < 0.8`.
- Condensed windows must not preserve disconnected source-time jumps. If optimized windows have a gap larger than `condensed_boring_gap_threshold_seconds`, collapse them into one continuous span when it fits the condensed max duration; otherwise preserve the selected/key-event windows and insert `condensed_continuity` bridge windows so adjacent source gaps stay within threshold.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| First valid timer in segment is `11:56` | Downgrade boundary to `incomplete_no_start` |
| Segment starts near `00:32` but max timer is `12:52` and threshold is 900s | Downgrade to `incomplete_timer_too_early` |
| No valid timer readings are available | Do not downgrade solely from missing OCR; preserve scene-based confidence |
| Condensed plan windows jump by more than 120s and continuous span <= max condensed duration | Write one continuous window |
| Condensed plan windows jump by more than 120s and continuous span > max condensed duration | Preserve selected/key-event windows and insert continuity bridge windows |

### 5. Good/Base/Bad Cases
- Good: A recording that starts at game timer `00:03` and ends after `20:00` can produce complete boundaries and continuous condensed plans.
- Base: A recording with usable scene transitions but no timer OCR keeps existing scene behavior and remains reviewable through confidence/reason.
- Bad: A recording that begins at level 11 / timer `13:36` is exported as a complete match, or a condensed export jumps from game timer `05:50` to `15:53` in one second.

### 6. Tests Required
- Unit: `tests/vision/test_timer_ocr.py` asserts the template OCR reads the upper right timer and ignores lower FPS/latency digits.
- Unit: `tests/vision/test_match_stitcher.py` asserts mid-game starts downgrade to `incomplete_no_start`.
- Unit: `tests/vision/test_match_stitcher.py` asserts complete-looking scene spans with max timer below `min_complete_timer_seconds` downgrade to `incomplete_timer_too_early`.
- Unit: `tests/highlights/test_window_optimizer.py` asserts discontinuous condensed windows collapse to a single span when feasible and bridge when too long.
- Config: `tests/test_config.py` asserts `ARL_VISION_MIN_COMPLETE_TIMER_SECONDS` loads.

### 7. Wrong vs Correct
#### Wrong
```python
if has_start and has_natural_end:
    return MatchSegment(..., is_complete=True, reason="complete")
```

#### Correct
```python
segments = _validate_segment_starts_with_timer(segments, timer_by_ts, ...)
segments = _validate_complete_segments_with_timer(
    segments,
    timer_by_ts,
    min_complete_timer_seconds=settings.vision.min_complete_timer_seconds,
)
```

#### Wrong
```python
windows = [opening_fight, baron_fight, nexus_fight]  # source gaps of several minutes
```

#### Correct
```python
windows = collapse_large_gaps_to_one_continuous_span(windows)
```

---

## Quality Preservation Contract

### Signatures

```python
# Config schema
class ExportSettings(BaseModel):
    ffmpeg_bitrate: str | None = None          # e.g., "4000k"
    ffmpeg_max_bitrate: str | None = None      # e.g., "5000k"
    ffmpeg_crf: int = 18                       # Fallback when bitrate not set
    ffmpeg_preset: str = "slow"                # CPU or NVENC preset
    use_hardware_encoding: bool = False
    use_highlight_plans: bool = False
```

```python
# Service method
def _video_quality_args(self) -> list[str]:
    """Generate quality control arguments: bitrate or CRF mode."""
    args = ["-preset", self.settings.export.ffmpeg_preset]
    
    if self.settings.export.ffmpeg_bitrate:
        # Fixed bitrate mode (quality preservation)
        args.extend(["-b:v", self.settings.export.ffmpeg_bitrate])
        if self.settings.export.ffmpeg_max_bitrate:
            args.extend(["-maxrate", self.settings.export.ffmpeg_max_bitrate])
            args.extend(["-bufsize", "8M"])
    else:
        # CRF mode (size optimization)
        args.extend(["-crf", str(self.settings.export.ffmpeg_crf)])
    
    return args
```

### Validation & Error Matrix

| Condition | Error / Behavior |
|-----------|------------------|
| `ffmpeg_bitrate` set without `ffmpeg_max_bitrate` | Valid - uses only average bitrate |
| `ffmpeg_bitrate` and `ffmpeg_crf` both set | Bitrate takes precedence, CRF ignored |
| Neither `ffmpeg_bitrate` nor `ffmpeg_crf` set | Falls back to CRF=18 (default) |
| `use_hardware_encoding=1` but no GPU available | FFmpeg command fails, service falls back to placeholder |
| `bufsize` too small for `maxrate` | FFmpeg encoding may stall; use `bufsize ≥ 2 * maxrate` |

### Good/Base/Bad Cases

**Good - Quality Preservation**:
```bash
# Source: 3.5 Mbps
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
# Output: 4.0-4.5 Mbps (quality preserved)
```

**Base - Balanced**:
```bash
# Source: 3.5 Mbps  
ARL_EXPORT_FFMPEG_CRF=18
# Output: ~2.8 Mbps (minor quality loss, smaller file)
```

**Bad - Excessive Quality Loss**:
```bash
# Source: 3.5 Mbps
ARL_EXPORT_FFMPEG_CRF=23  # Default in older versions
ARL_EXPORT_FFMPEG_PRESET=veryfast
# Output: ~1.5 Mbps (40%+ quality loss, visible artifacts)
```

### Tests Required

**Unit tests** (in `tests/test_config.py`):
- [ ] `ffmpeg_bitrate` loads from env correctly
- [ ] `ffmpeg_max_bitrate` loads from env correctly  
- [ ] Defaults preserved when env not set

**Integration tests** (in `tests/pipeline/test_ffmpeg_resilience.py`):
- [ ] Fixed bitrate mode generates correct FFmpeg args
- [ ] CRF mode generates correct FFmpeg args when bitrate unset
- [ ] Hardware encoding uses NVENC codec when enabled
- [ ] Exported bitrate ≥ configured bitrate (tolerance ±10%)

**E2E verification** (manual):
- Export real recording and verify `ffprobe` bitrate matches config
- Compare visual quality against source

### Wrong vs Correct

#### Wrong - Using CRF with NVENC for Quality Preservation

```bash
# NVENC CRF produces lower bitrate than expected
ARL_EXPORT_USE_HARDWARE_ENCODING=1
ARL_EXPORT_FFMPEG_CRF=10
# Output: 2.2 Mbps from 3.5 Mbps source (quality loss)
```

**Why it's wrong**: NVENC CRF behavior differs from CPU encoding; same CRF value produces lower bitrate

#### Correct - Using Fixed Bitrate with NVENC

```bash
# Fixed bitrate guarantees output quality
ARL_EXPORT_USE_HARDWARE_ENCODING=1
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
ARL_EXPORT_FFMPEG_PRESET=p7
# Output: 4.0-4.5 Mbps (quality preserved, 10x faster)
```

---

## Common Mistakes

### Mistake 1: Expecting CRF Mode to Preserve Quality

**Symptom**: Exported videos have visible quality degradation despite high source bitrate

**Cause**: CRF mode optimizes for file size, not quality preservation

**Fix**: 
```bash
# Before (quality loss)
ARL_EXPORT_FFMPEG_CRF=18

# After (quality preserved)
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
```

**Prevention**: Default to fixed bitrate mode for user-facing exports; use CRF only for archival/storage

### Mistake 2: Forgetting to Disable Highlight Planner

**Symptom**: Exported videos are only 3-4 minutes instead of full 20-30 minute matches

**Cause**: `ARL_HIGHLIGHT_PLANNER_ENABLED` defaults to enabled in older versions, and `use_highlight_plans` defaults to True

**Fix**:
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=0
ARL_EXPORT_USE_HIGHLIGHT_PLANS=0
```

**Prevention**: Always explicitly set both flags based on user intent

### Mistake 3: Using Fast Presets with NVENC

**Symptom**: Lower quality than expected despite fixed bitrate

**Cause**: NVENC preset affects quality-per-bitrate; p1-p3 sacrifice quality for speed

**Fix**:
```bash
# Before
ARL_EXPORT_FFMPEG_PRESET=p1  # Fast but low quality

# After
ARL_EXPORT_FFMPEG_PRESET=p7  # Highest quality
```

**Prevention**: Always use `p7` with NVENC; the speed gain from p1 is minimal compared to the quality loss

---

## Design Decisions

### Decision: Prefer Fixed Bitrate Over CRF for Quality Preservation

**Context**: Users reported exported videos had "极差" (extremely poor) quality compared to source recordings. Investigation showed CRF mode producing 2.2 Mbps from 3.5 Mbps sources.

**Options Considered**:
1. Tune CRF value lower (e.g., CRF=10)
2. Switch to fixed bitrate mode
3. Use two-pass encoding

**Decision**: We chose fixed bitrate mode (#2) because:
- **Predictable**: Output bitrate guaranteed to match or exceed configured value
- **Simpler**: One-pass encoding, no need for two-pass overhead  
- **NVENC-compatible**: Works identically with hardware encoding
- **User-friendly**: "4000k" is easier to understand than "CRF 10"

**Tradeoff**: Larger file sizes than CRF (typically 20-30% larger), but quality preservation was the primary requirement

**Implementation**:
```python
def _video_quality_args(self) -> list[str]:
    if self.settings.export.ffmpeg_bitrate:
        # Prefer fixed bitrate when configured
        return ["-b:v", self.settings.export.ffmpeg_bitrate, ...]
    else:
        # Fallback to CRF for backward compatibility
        return ["-crf", str(self.settings.export.ffmpeg_crf)]
```

**Extensibility**: To add two-pass encoding in the future, create a third mode controlled by `ARL_EXPORT_USE_TWO_PASS=1`

### Decision: Separate `use_highlight_plans` from `highlight_planner_enabled`

**Context**: Highlight planner may run for analysis/diagnostics even when user wants full match exports

**Decision**: Two independent flags:
- `ARL_HIGHLIGHT_PLANNER_ENABLED`: Whether to run highlight detection (Stage)
- `ARL_EXPORT_USE_HIGHLIGHT_PLANS`: Whether exporter reads and applies plans

**Why**: Allows highlight analysis without forcing condensed exports

---

## Environment Variable Reference

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `ARL_EXPORT_FFMPEG_BITRATE` | string | None | Fixed average bitrate (e.g., "4000k") |
| `ARL_EXPORT_FFMPEG_MAX_BITRATE` | string | None | Maximum burst bitrate (e.g., "5000k") |
| `ARL_EXPORT_FFMPEG_CRF` | int | 18 | CRF value when bitrate not set |
| `ARL_EXPORT_FFMPEG_PRESET` | string | "slow" | CPU preset or NVENC p1-p7 |
| `ARL_EXPORT_USE_HARDWARE_ENCODING` | bool | False | Use NVENC if available |
| `ARL_EXPORT_BURN_SUBTITLES` | bool | False | Burn subtitles into video instead of muxing soft subtitles |
| `ARL_EXPORT_USE_ASS_SUBTITLES` | bool | False | Convert real SRT subtitles to ASS sidecars for burn-in |
| `ARL_EXPORT_ASS_FONT_NAME` | string | "SimHei" | ASS style font name for burned subtitles |
| `ARL_EXPORT_ASS_FONT_SIZE` | int | 36 | ASS style font size, clamped to at least 1 |
| `ARL_EXPORT_ASS_MARGIN_V` | int | 20 | ASS vertical bottom margin, clamped to at least 0 |
| `ARL_EXPORT_ASS_OUTLINE` | int | 2 | ASS text outline width, clamped to at least 0 |
| `ARL_EXPORT_USE_HIGHLIGHT_PLANS` | bool | False | Apply highlight condensing |
| `ARL_HIGHLIGHT_PLANNER_ENABLED` | bool | False | Run highlight detection stage |
| `ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_TAIL_SECONDS` | float | 40.0 | Maximum short narration tail retained after key/tactical action windows |
| `ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_GAP_SECONDS` | float | 8.0 | Maximum subtitle gap considered continuous action-resolution narration |
| `ARL_HIGHLIGHT_CONDENSED_KDA_EVENT_DETECTION_ENABLED` | bool | True | Preserve detected KDA kill/death changes as condensed key events |
| `ARL_HIGHLIGHT_CONDENSED_KDA_CROP_REGION` | `x,y,w,h` | `1665,0,85,32` | 1080p top-right player KDA crop |
| `ARL_HIGHLIGHT_CONDENSED_KDA_SAMPLE_INTERVAL_SECONDS` | float | 10.0 | Sampling interval for KDA event detection |
| `ARL_HIGHLIGHT_CONDENSED_KDA_MAX_READING_GAP_SECONDS` | float | 120.0 | Maximum gap between stable KDA readings that may still create a kill/death event |
| `ARL_HIGHLIGHT_CONDENSED_KDA_KILL_PREROLL_SECONDS` | float | 30.0 | Extra context before a kill-only KDA change |
| `ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_PREROLL_SECONDS` | float | 60.0 | Extra context before a death KDA change |
| `ARL_HIGHLIGHT_CONDENSED_KDA_POSTROLL_SECONDS` | float | 5.0 | Short context after the changed KDA reading |
| `ARL_HIGHLIGHT_CONDENSED_KDA_POST_DEATH_KILL_SUPPRESSION_SECONDS` | float | 0.0 | Optional override to suppress known kill-only HUD catch-up noise soon after a death; disabled by default so post-death kill credit is preserved |
| `ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_WAIT_TRIM_SECONDS` | float | 120.0 | Window after a death observation where low-value waits can be dropped or shifted |
| `ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_SILENT_GAP_TRIM_SECONDS` | float | 10.0 | Minimum subtitle-free gap inside a death-event window that can be removed |
| `ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_SILENT_TRIM_LOOKBACK_SECONDS` | float | 30.0 | Lookback before the death observation for finding removable silent gaps |
| `ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_REACTION_TAIL_SECONDS` | float | 3.0 | Post-death reaction/transition time retained before trimming respawn waits |

---

## Related Documentation

- [Orchestration Contracts](./orchestration-contracts.md) - Cross-module contracts including exporter state
- [Quality Guidelines](./quality-guidelines.md) - General code quality standards
- [Logging Guidelines](./logging-guidelines.md) - Logging exporter operations

---

**Last Updated**: 2026-06-26 (Task: ass-subtitle-styling)
