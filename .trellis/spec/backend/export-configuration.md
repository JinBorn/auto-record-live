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
When hardware encoding is enabled for H.264/H.265 with a fixed bitrate, the
exporter must also add NVENC CBR controls (`-rc cbr -cbr_padding 1`) so upload
exports do not drift far below the requested bitrate on visually simple scenes.

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
- Duration = sum of retained highlight windows; publish condensed defaults target a dynamic 7-20 minute range based on composite density and required continuity

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
      ass_font_size: int = 32
      ass_margin_v: int = 110
      ass_outline: int = 2
      ass_max_chars_per_line: int = 18
      ass_max_lines: int = 2
  ```
- Environment:
  ```bash
  ARL_EXPORT_BURN_SUBTITLES=1
  ARL_EXPORT_USE_ASS_SUBTITLES=1
  ARL_EXPORT_ASS_FONT_NAME=SimHei
  ARL_EXPORT_ASS_FONT_SIZE=32
  ARL_EXPORT_ASS_MARGIN_V=110
  ARL_EXPORT_ASS_OUTLINE=2
  ARL_EXPORT_ASS_MAX_CHARS_PER_LINE=18
  ARL_EXPORT_ASS_MAX_LINES=2
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
- ASS style defaults are bottom-centered white text with black outline at `PlayResX=1280`, `PlayResY=720`, font size `32`, margin V `110`, outline `2`, hard wrapping after `18` characters per visual line, and at most `2` lines per displayed Dialogue event. The raised/smaller default keeps burned subtitles above bottom HUD/game information, while wrapping prevents a single subtitle line from spanning most of the screen. If a cue wraps beyond the max-line limit, split it into consecutive Dialogue events over the original cue duration instead of rendering a dense 3+ line subtitle block. Use `ARL_EXPORT_ASS_MARGIN_V`, `ARL_EXPORT_ASS_FONT_SIZE`, `ARL_EXPORT_ASS_MAX_CHARS_PER_LINE`, and `ARL_EXPORT_ASS_MAX_LINES` to tune per layout.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| `ARL_EXPORT_USE_ASS_SUBTITLES=0` | Preserve existing SRT burn-in or soft-subtitle behavior |
| Burn disabled and ASS enabled | Do not generate `.ass`; use stream-copy + `mov_text` for real SRT |
| Placeholder SRT and ASS enabled | Do not generate `.ass`; do not add `subtitles=` |
| Real SRT contains valid cues | Write/overwrite `match-NN.ass` and pass it to `subtitles=` |
| SRT is missing before export | Existing exporter missing-subtitle skip/defer behavior applies |
| SRT has no valid cues during ASS conversion | Defer the export instead of writing a broken FFmpeg command |
| Numeric ASS env values are below minimum | Clamp `font_size >= 1`, `margin_v >= 0`, `outline >= 0`, `max_chars_per_line >= 1`, and `max_lines >= 1` |

### 5. Good/Base/Bad Cases
- Good: `burn_subtitles=1` and `use_ass_subtitles=1` converts a real SRT to `match-01.ass`, then the FFmpeg command contains `-vf subtitles='.../match-01.ass'`.
- Base: `burn_subtitles=1` and `use_ass_subtitles=0` keeps the existing SRT `subtitles='.../match-01.srt'` command.
- Bad: Exporter appends `.ass` rows to `subtitle-assets.jsonl`, burns placeholder subtitles, or uses a separate unescaped subtitle filter path.

### 6. Tests Required
- Unit: ASS helper emits `[Script Info]`, `[V4+ Styles]`, `[Events]`, expected style fields, and `Dialogue:` rows.
- Unit: ASS helper preserves SRT cue timing, text, multiline breaks, Chinese text, common formatting-tag cleanup, deterministic long-line wrapping, and max-lines splitting.
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
  ARL_HIGHLIGHT_CONDENSED_TARGET_DURATION_RANGE=7,20
  ARL_HIGHLIGHT_CONDENSED_COMPOSITE_TRIM_ENABLED=1
  ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_TRIM_SECONDS=8
  ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_KEEP_SECONDS=3
  ARL_HIGHLIGHT_CONDENSED_CONTINUITY_BRIDGE_SECONDS=3
  ARL_HIGHLIGHT_CONDENSED_START_EDGE_SECONDS=1
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
- Condensed target duration is dynamic, not a fixed 7-9 or 7-11 minute target. Defaults map low/mid/high density to roughly 7-20 minutes: `condensed_low_density_duration_range=(7,11)`, `condensed_mid_density_duration_range=(10,16)`, `condensed_high_density_duration_range=(16,20)`, with `condensed_target_duration_range=(7,20)` as the continuous-span cap.
- Condensed optimization must preserve every `key_event` cue even when duration reduction would otherwise drop a lower-positioned key-event window. The optimizer may exceed the target duration slightly to restore missing key-event windows.
- Condensed planning must treat detected player KDA kill/death increases from the top-right HUD as synthetic `key_event` cues. This KDA pass is best-effort OCR: unreadable frames must not block plan generation, but valid non-decreasing K/D/A changes must be preserved like subtitle-derived key events.
- KDA event windows must cover the interval from before the previous stable KDA reading through shortly after the changed reading, not only the changed/death-wait sample. Death changes need more pre-roll than kill-only changes because the lead-up to being killed is usually more valuable than the waiting-to-respawn segment.
- KDA default context should stay tight enough for highlight density: kill-only events default to `15s` preroll, death events default to `30s` preroll, and postroll defaults to `5s`. Operators may increase these via env when OCR sampling is sparse, but longer values make oversized `condensed_key_event` windows more likely.
- KDA-derived kill-only changes after a death are still key events by default. A non-zero `condensed_kda_post_death_kill_suppression_seconds` is an explicit operator override for known HUD catch-up noise; the default must preserve these changes because real post-death kill credit can happen before respawn.
- Final budgeting must re-check every synthetic `kda_change` cue after speech protection, continuity bridging, clamp/merge, and duration capping. Each `kda_change` cue must be fully covered by a retained `condensed_key_event` or `highlight_keyword` window; scattered `condensed_continuity` snippets inside the cue interval do not satisfy KDA preservation because they can still skip the kill/death moment.
- Death-event windows may be split by subtitle-free gaps inside the post-death wait. A silent gap of at least `condensed_kda_death_silent_gap_trim_seconds`, searched within `condensed_kda_death_silent_trim_lookback_seconds` before the death observation, should be removed when both resulting pieces remain at least `condensed_min_window_duration_seconds`.
- Death trimming must preserve a short post-death reaction tail. Even when the following respawn wait is low-value, keep `condensed_kda_death_reaction_tail_seconds` at the start of a removable silent gap or after the death observation so the edit does not hard-cut immediately after the player dies.
- Death trimming must not remove a subtitle-free gap whose end is within the death lead-in guard (`max(5s, condensed_kda_death_reaction_tail_seconds)`) before the KDA death observation. KDA OCR is sampled, so the actual death may happen a few seconds before the changed KDA is read; cutting that gap causes the visible "alive/farming -> death countdown" jump.
- After a death observation, condensed mode may drop `condensed_context` windows and shift later non-KDA `condensed_key_event` windows within `condensed_kda_death_wait_trim_seconds` to the first non-KDA subtitle key/tactical cue. This trims silent respawn waits and walking-back-to-lane lead-in while retaining the next meaningful fight/objective. Any window that overlaps a KDA kill/death cue must remain protected.
- Key/tactical windows must preserve short action-resolution narration. When a gank, chase, fight, or other attempt has no KDA change, viewers still need the outcome. If meaningful non-KDA subtitle cues continue shortly after a `condensed_key_event` or `condensed_tactical` window, extend that window up to `condensed_action_resolution_tail_seconds`, stopping when subtitle gaps exceed `condensed_action_resolution_gap_seconds` or the next planned window begins.
- After KDA restoration and speech-boundary protection, the composite internal trim pass may split oversized `condensed_key_event` and `condensed_tactical` windows at subtitle-free gaps of at least `condensed_internal_gap_trim_seconds`. The pass must keep `condensed_internal_gap_keep_seconds` on both sides of a removed gap and must never remove ranges overlapping KDA cues, death lead-in guards, or non-low-value classified cue intervals. This protects silent fights/objectives represented by KDA or visual/action cues even when there is no subtitle.
- Composite internal trimming must re-run speech-boundary protection, full KDA restoration, final continuity bridging, and death-like continuity protection before persisting the plan.
- Exception: when preserving every `key_event` would collapse content into a full-match span, condensed mode must choose the densest target-duration continuous content window instead of writing a full-span plan that the exporter will ignore.
- Condensed optimization must preserve match-start and match-end context. A condensed plan that does not cover both the beginning and end of the source boundary is invalid for export.
- Publish condensed mode may preserve match-start with a shorter boundary marker than match-end context. The start marker keeps edit/export contract validity without forcing several seconds of low-value fountain, scoreboard, or loading-adjacent footage into the opening.
- Short publish start-context windows must not be extended merely because an SRT cue starts at `0.0`; opening ASR often captures game announcer, music, or stale text. Speech-boundary protection still applies normally to key/tactical/content windows after the opening marker.
- Condensed large-gap collapse applies to content windows before mandatory match-start/match-end context is added. Edge context is editorial framing; it must not force the optimizer to collapse the entire match into one full-span window.
- After edge context is added, condensed optimization must insert short continuity bridge windows when adjacent output windows would otherwise leave a source-time gap larger than `condensed_boring_gap_threshold_seconds`. The bridge snippet length is controlled by `condensed_continuity_bridge_seconds` and must not implicitly use full edge-context length. These bridge windows prevent visible game-clock/KDA/level jumps while keeping the edit shorter than a full match.
- Continuity bridging must include a lead-in that ends at the next retained window start, so the viewer sees the approach to a key/tactical/death segment instead of jumping directly from unrelated farming or lane state into a death timer, fight result, or post-event wait.
- A single lead-in is not sufficient for arbitrarily large source-time gaps. If the gap from the previous retained window to the next lead-in would still exceed `condensed_boring_gap_threshold_seconds`, insert short progression continuity snippets through the gap before the lead-in. The persisted plan must not contain adjacent windows whose source-time gap exceeds the threshold.
- Any later condensed post-processing that can move or remove windows, including KDA death-wait trimming, action-resolution extension, speech-boundary protection, and clamp/merge, must run a final continuity bridge pass before the `HighlightPlanAsset` is persisted.
- Final continuity bridge entries must be checked for death-like frames. If a `condensed_continuity` window starts on a gray death/respawn screen and the preceding gap is within the normal edge/bridge duration, extend that bridge back to the previous window end so the export includes the transition into death instead of opening on the death timer.
- Speech-boundary protection and continuity bridging are secondary to the condensed duration budget. They may extend a cut to finish the current subtitle thought or add a short lead-in before the next retained event, but they must not turn a medium/short condensed export into a near-full-match video. When final post-processing exceeds the condensed duration budget, the planner must keep required match-edge context, choose the densest budgeted content span, then re-run speech-boundary protection and short lead-in bridging on that reduced plan.
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
| Final budgeting or continuity bridging leaves a `kda_change` cue covered only by `condensed_continuity` snippets, or not covered at all | Restore a full `condensed_key_event` window for that KDA cue before writing the plan |
| Kill-only KDA increase occurs after a death and suppression is disabled | Add a synthetic KDA key-event cue; post-death kill credit is still valuable content |
| Kill-only KDA increase occurs within a non-zero post-death suppression window | Do not add a KDA cue for that increase; keep the previous reading baseline moving forward |
| Death-event window contains a subtitle-free gap longer than the silent-gap trim threshold near the death observation | Split/remove the silent range if the retained pieces satisfy the minimum window duration |
| Removing a death-event silent gap would start exactly at the reaction moment | Preserve the configured reaction tail first, then remove only the remaining gap if it still reaches the silent-gap trim threshold |
| Subtitle-free gap ends within the death lead-in guard before the KDA death observation | Preserve the gap so the exported timeline includes the death setup/transition |
| A death-event window ends at or before the death observation | Extend the retained death window to at least `current_at + condensed_kda_death_reaction_tail_seconds`, capped by the KDA cue end |
| A context-only window starts soon after a death observation | Drop it as low-value death wait context |
| A later key-event window starts soon after a death observation and overlaps any KDA cue | Keep the window; KDA kill/death changes override low-value wait trimming |
| A later non-KDA key-event window starts soon after a death observation but first meaningful subtitle key/tactical cue is later | Shift the window start to the first meaningful cue minus context padding |
| A key/tactical window is followed by continuous meaningful narration before the next planned window | Extend the current window to preserve the action outcome/explanation, capped by action-resolution tail/gap settings |
| A key/tactical window is followed by a subtitle-free gap larger than the action-resolution gap setting | Do not extend for later unrelated narration |
| `condensed_composite_trim_enabled=0` | Skip internal low-value gap compression; keep the existing KDA/speech/continuity passes |
| A `condensed_key_event` or `condensed_tactical` window contains a long subtitle-free gap with no KDA, death guard, or non-low-value classified cue overlap | Remove the middle of the gap, leaving configured keep-context on both sides, then re-run speech, KDA restoration, and continuity bridge passes |
| A subtitle-free internal gap overlaps a synthetic KDA event or death lead-in guard | Preserve the protected interval; KDA must remain fully covered by `condensed_key_event` or `highlight_keyword` |
| A subtitle-free internal gap overlaps a visual/action/tactical classified cue but has no speech | Preserve that cue interval and trim only unprotected low-value sides if they remain large enough |
| Short continuity bridge length is configured with `condensed_continuity_bridge_seconds` | Use that snippet length for final bridge windows while still enforcing `condensed_boring_gap_threshold_seconds` |
| Duration reduction drops a key event | Re-add a `condensed_key_event` window around that cue, then merge/clamp windows |
| Preserving all key events produces one full-match content window | Trim to the densest target-duration continuous content window before adding edge context |
| Condensed plan does not include start and end context | Exporter ignores the plan and falls back to full-boundary export behavior |
| Discontinuous content windows exceed the continuous-span cap | Preserve the windows and let the bridge pass insert continuity context instead of dropping key events |
| Edge context and selected content leave a source-time gap larger than `condensed_boring_gap_threshold_seconds` | Insert `condensed_continuity` bridge windows until every adjacent source gap is within the threshold, including one bridge ending at the next retained window start |
| KDA death trimming, speech-boundary protection, or clamp/merge creates a new large gap after optimization | Run the final continuity bridge pass and re-clamp before writing `HighlightPlanAsset` |
| A continuity bridge starts on a death-like gray respawn frame after a short preceding gap | Extend the bridge start to the previous window end |
| Speech-boundary/action-resolution/continuity post-processing pushes retained duration far beyond the analyzed condensed target | Re-budget the plan: preserve match-edge context, select the densest target-duration content span, then re-apply speech-boundary and short lead-in protection |
| Publish start context is configured shorter than normal edge context | Keep a short `condensed_match_context` window at source `0.0` for plan validity, do not extend it for a cue that starts at `0.0`, and let the next retained/continuity window carry the real opening action |
| One plan window covers the full boundary | Exporter ignores the plan and uses the full-boundary export path |
| Optimized window would exceed match duration | Clamp the window end to match duration before persisting |
| Existing plan needs regeneration after logic changes | Use `highlight-planner --force-reprocess` with session/match filters |
| No plan exists or plan is stale | Exporter falls back to full-boundary export unless highlight plans are enabled and valid |

### 5. Good/Base/Bad Cases
- Good: A long recording with real cues produces dynamic 7-20 minute condensed windows around key/tactical moments; long walking/farming gaps inside a retained key-event window are compressed, while silent visual/KDA fights remain covered.
- Base: A long recording with real subtitles uses cue-based key/tactical windows and visual scoring only as supporting signal.
- Bad: Placeholder subtitles produce a visual-activity montage; or a subtitle-only trimmer deletes a silent fight/KDA transition; or a continuity bridge uses 30s edge context snippets until the edit becomes near-full-match length.

### 6. Tests Required
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts placeholder subtitles emit no condensed plan and do not mark the match processed.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts `--force-reprocess` service semantics append a replacement plan.
- Unit: `tests/highlights/test_window_optimizer.py` asserts key-event windows are restored after duration reduction and windows are clamped to match duration.
- Unit: `tests/highlights/test_window_optimizer.py` asserts condensed plans preserve match edge context.
- Unit: `tests/highlights/test_window_optimizer.py` asserts start-edge context can be shorter than end-edge context.
- Unit: `tests/highlights/test_window_optimizer.py` asserts condensed plans bridge large source-time gaps after edge context is added.
- Unit: `tests/vision/test_kda_ocr.py` asserts lightweight KDA OCR reads `K/D/A` crops and rejects blank crops.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts condensed plans preserve KDA-derived kill/death key events.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts final KDA preservation restores a full key-event window when only continuity snippets overlap a `kda_change` cue.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts post-death kill-only KDA changes are preserved by default.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts post-death low-value trimming does not shift away windows that overlap KDA kill/death cues.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts silent subtitle gaps inside death-event windows are removed while retaining the configured reaction tail.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts death-event windows are extended to preserve the configured post-death reaction tail.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts post-death low-value context is dropped and later key windows shift to the first meaningful cue.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts action-resolution narration after a failed gank/chase extends the preceding key/tactical window.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts action-resolution extension stops at large subtitle gaps.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts composite internal trimming splits a long no-signal subtitle-free gap.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts composite internal trimming preserves silent visual/action cue intervals without depending on subtitles.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts composite internal trimming keeps KDA event coverage intact.
- Unit: `tests/highlights/test_window_optimizer.py` asserts a short configured continuity bridge still keeps adjacent source gaps within threshold without growing bridge duration.
- Config: `tests/test_config.py` asserts dynamic condensed duration ranges and composite trim/bridge env keys load and clamp through `load_settings()`.
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

#### Wrong
```python
for gap in subtitle_free_gaps(window):
    remove(gap)  # drops silent fights and KDA transitions
```

#### Correct
```python
protected = kda_intervals + death_guards + non_low_value_classified_intervals
for gap in subtitle_free_gaps(window):
    remove_only_unprotected_middle(gap, protected, keep_seconds=3.0)
windows = protect_speech_boundaries(windows)
windows = restore_missing_kda_event_windows(windows)
windows = bridge_highlight_windows(windows, bridge_window_seconds=3.0)
```

---

## Scenario: Edit-Plan Teaser Rendering

### 1. Scope / Trigger
- Trigger: The pipeline needs upload-style presentation timelines where teaser
  clips can be duplicated before the full validated match.
- Trigger: This spans shared contracts, edit-planner state, CLI/postprocess
  ordering, exporter FFmpeg command construction, status, and reset cleanup.

### 2. Signatures
- Shared contract:
  ```python
  class TimelineVideoTransform(BaseModel):
      kind: str = "none"
      scale: float = 1.0
      x_anchor: float = 0.5
      y_anchor: float = 0.5
      target: str | None = None

  class TimelineSegment(BaseModel):
      role: str  # "teaser" | "transition" | "main"
      source_path: str | None = None
      source_start_seconds: float = 0.0
      source_end_seconds: float = 0.0
      transform: TimelineVideoTransform | None = None
      reason: str
      text: str | None = None
      duration_seconds: float | None = None

  class AudioBed(BaseModel):
      source_path: str
      timeline_start_seconds: float = 0.0
      timeline_end_seconds: float | None = None
      gain_db: float = -28.0
      loop: bool = True
      reason: str = "background_music"

  class SoundEffectHit(BaseModel):
      source_path: str
      at_seconds: float
      gain_db: float = -12.0
      reason: str

  class EditPlanAsset(BaseModel):
      session_id: str
      match_index: int
      source_boundary_start_seconds: float
      source_boundary_end_seconds: float
      timeline: list[TimelineSegment]
      audio_beds: list[AudioBed] = Field(default_factory=list)
      sound_effects: list[SoundEffectHit] = Field(default_factory=list)
      created_at: datetime
  ```
- Stage state:
  ```python
  class EditPlannerStateFile(BaseModel):
      processed_match_keys: list[str] = Field(default_factory=list)
  ```
- Files:
  - `data/tmp/edit-plans.jsonl`
  - `data/tmp/editing-state.json`
- CLI:
  ```bash
  python -m arl.cli edit-planner --session-id <session_id>
  python -m arl.cli edit-planner --session-id <session_id> --match-index <n> --force-reprocess
  python -m arl.cli exporter --session-id <session_id> --force-reprocess
  ```

### 3. Contracts
- Environment:
  ```bash
  ARL_EDIT_PLANNER_ENABLED=1
  ARL_EDIT_TEASER_MAX_SEGMENTS=2
  ARL_EDIT_TEASER_MAX_TOTAL_SECONDS=45
  ARL_EDIT_TEASER_MIN_SEGMENT_SECONDS=3
  ARL_EDIT_TEASER_DYNAMIC_BUDGET_ENABLED=1
  ARL_EDIT_TEASER_BUDGET_FRACTION_MIN=0.08
  ARL_EDIT_TEASER_BUDGET_FRACTION_MAX=0.12
  ARL_EDIT_TEASER_BUDGET_MIN_SECONDS=20
  ARL_EDIT_TEASER_BUDGET_MAX_SECONDS=90
  ARL_EDIT_TEASER_CANDIDATE_REASONS=highlight_keyword,condensed_key_event
  ARL_EDIT_TEASER_FALLBACK_ENABLED=1
  ARL_EDIT_TRANSITION_MODE=none
  ARL_EDIT_TRANSITION_DURATION_SECONDS=1.25
  ARL_EDIT_TRANSITION_TEXT="Back to match start"
  ARL_EDIT_TRANSITION_SFX_PATH=
  ARL_EDIT_TRANSITION_SFX_GAIN_DB=-12
  ARL_EDIT_ZOOM_ENABLED=0
  ARL_EDIT_ZOOM_MODE=closeup
  ARL_EDIT_ZOOM_TARGET=chat
  ARL_EDIT_ZOOM_SCALE=1.2
  ARL_EDIT_ZOOM_X_ANCHOR=0.5
  ARL_EDIT_ZOOM_Y_ANCHOR=0.5
  ARL_EDIT_ZOOM_MAX_SEGMENTS=1
  ARL_EDIT_ZOOM_CLOSEUP_SECONDS=6
  ARL_EDIT_ZOOM_EASE_SECONDS=0.4
  ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS=25
  ARL_EDIT_ZOOM_CHAT_BURST_ENABLED=1
  ARL_EDIT_ZOOM_CHAT_BURST_SAMPLE_INTERVAL_SECONDS=0.5
  ARL_EDIT_ZOOM_CHAT_BURST_THRESHOLD=0.08
  ARL_EDIT_ZOOM_MAX_DURATION_SECONDS=30
  ARL_EDIT_AUDIO_MIXING_ENABLED=0
  ARL_EDIT_BGM_LIBRARY_PATH=
  ARL_EDIT_BGM_PATH=
  ARL_EDIT_BGM_GAIN_DB=-28
  ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS=600
  ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS=60
  ARL_EDIT_BGM_CROSSFADE_SECONDS=2
  ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS=2
  ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD=0.60
  ARL_EDIT_SFX_PATH=
  ARL_EDIT_SFX_GAIN_DB=-12
  ARL_EDIT_SFX_LIBRARY_PATH=data/sfx/library.json
  ARL_EDIT_SFX_TIMING_OFFSET_SECONDS=0
  ARL_EDIT_SFX_MIN_INTERVAL_SECONDS=20
  ARL_EDIT_SFX_MAX_HITS=6
  ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED=1
  ARL_EDIT_SFX_MULTIKILL_WINDOW_SECONDS=8
  ARL_EXPORT_USE_EDIT_PLANS=1
  ```
- Defaults must keep current exports unchanged:
  - `EditingSettings.enabled=False`
  - `EditingSettings.zoom_enabled=False`
  - `EditingSettings.audio_mixing_enabled=False`
  - `ExportSettings.use_edit_plans=False`
- Postprocess order must be:
  `stage-hints-semantic -> segmenter -> subtitles -> highlight-planner -> edit-planner -> exporter -> copywriter`.
- Edit planner reads complete `MatchBoundary` rows and matching
  `HighlightPlanAsset` rows. It may emit high-confidence teaser segment(s), then
  `main` segment(s) copied from the validated highlight/condensed windows.
  Main segments must cover both the start and the end of the source boundary,
  but they must not be a single full-boundary `full_validated_match` segment.
- Teasers are optional and must use explicit high-confidence highlight windows
  only (`highlight_keyword`). Generic `condensed_key_event` or
  `condensed_tactical` windows are useful main-edit material, but they are not
  sufficient by themselves for a cold-open teaser. If no valid
  `highlight_keyword` teaser remains, write a valid main-only edit plan.
- When multiple valid `highlight_keyword` teaser candidates exist, rank them by
  overlapping subtitle/event strength before chronological order. Kill/KDA,
  multi-kill, strong gameplay topic (`电刀AP机器人`, `清线快伤害高`), rank/routine
  (`韩服千分套路`), and recognition/punchline cues should outrank an earlier
  generic setup subtitle. If no candidate has a positive subtitle/event score,
  keep the deterministic chronological fallback.
- Current teaser rule: candidate reasons are configured by
  `ARL_EDIT_TEASER_CANDIDATE_REASONS` (default
  `highlight_keyword,condensed_key_event`), and LLM semantic teaser
  recommendations may override heuristic ranking when they overlap an existing
  highlight window. `highlight_keyword` keeps tie priority.
- Current teaser budget rule: when
  `ARL_EDIT_TEASER_DYNAMIC_BUDGET_ENABLED=1`, use the midpoint of the configured
  8-12% fraction range against planned edit duration, clamp to
  `ARL_EDIT_TEASER_BUDGET_MIN_SECONDS` /
  `ARL_EDIT_TEASER_BUDGET_MAX_SECONDS`, then apply
  `ARL_EDIT_TEASER_MAX_TOTAL_SECONDS` as an operator cap.
- Current fallback rule: if no candidate has a positive subtitle/event score
  and fallback is enabled, select the top valid candidate with reason
  `teaser_fallback_top_scored`.
- Current transition rule: when `ARL_EDIT_TRANSITION_MODE=black_card` and a
  teaser exists, insert one `transition` segment between the final teaser and
  first main segment. Its `duration_seconds` is clamped, `reason` is
  `transition_black_card`, and `text` is
  `CopywriterSemanticAsset.result.hook_line` when present or
  `ARL_EDIT_TRANSITION_TEXT` otherwise. `crossfade` is reserved and must not be
  treated as an implicit black-card mode.
- Planner segment times are always relative to the validated match boundary.
  Do not mutate `MatchBoundary` or treat teaser windows as canonical match
  starts.
- Exporter selection precedence:
  1. If `use_edit_plans=True` and a valid edit plan exists, render the edit plan.
  2. Else if `use_highlight_plans=True` and a valid highlight plan exists, render
     the highlight plan.
  3. Else render the full validated boundary.
- Renderer supports local timeline segments (`source_path is None`), roles
  `teaser`, `transition`, and `main`, optional local audio instructions, and
  transforms with `kind in {"none", "punch_in"}`. `transition` segments are
  rendered as generated black video plus silent audio inside the same concat
  filtergraph; they do not add media inputs and therefore must not shift BGM/SFX
  input indexes. On Windows, transition `drawtext` must specify a concrete
  `fontfile` such as `C:/Windows/Fonts/msyh.ttc`; relying on Fontconfig's
  default discovery can fail with `Cannot load default config file` and force an
  otherwise valid export into fallback placeholder behavior. If no known font
  file exists, render a plain black card instead of failing the export.
- Punch-in zoom is opt-in at planner time. In the default
  `ARL_EDIT_ZOOM_MODE=closeup`, the planner chooses short close-up windows
  inside eligible `teaser` or `main` segments, then replaces the source segment
  with adjacent untransformed / transformed / untransformed timeline pieces.
  Total source duration must be preserved exactly across the split.
- Close-up triggers are deterministic: KDA kill cues from subtitle
  `kda_change ... kills=a->b ... current_at=<seconds>` first, chat-burst frame
  differences in the bottom-left chat region second, and reason-based fallback
  segment midpoints last. `ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS` spaces selected
  candidates globally, and `ARL_EDIT_ZOOM_MAX_SEGMENTS` caps the number of
  transformed close-up pieces. Publish preset raises this cap to `3` unless
  `ARL_EDIT_ZOOM_MAX_SEGMENTS` is explicitly set.
- KDA close-ups default to center focus when the operator kept the default
  `ARL_EDIT_ZOOM_TARGET=chat`; explicit `center` or `custom` zoom targets are
  still honored. Chat-burst close-ups always use the bottom-left chat anchor
  (`x_anchor=0.0`, `y_anchor=1.0`). Fallback close-ups use
  `ARL_EDIT_ZOOM_TARGET`, where `chat` maps to the bottom-left anchor.
- `ARL_EDIT_ZOOM_CLOSEUP_SECONDS` caps each close-up window and is clamped to
  `3..8` seconds. Segments shorter than 3 seconds are not transformed in
  close-up mode. `ARL_EDIT_ZOOM_EASE_SECONDS` is stored on
  `TimelineVideoTransform.ease_in_seconds` / `ease_out_seconds` and clamped to
  `0..1`.
- `ARL_EDIT_ZOOM_MODE=legacy` restores the old whole-segment static punch-in
  behavior for rollback comparisons. In legacy mode,
  `ARL_EDIT_ZOOM_MAX_DURATION_SECONDS` caps `main` segment eligibility and
  generated transforms set ease seconds to `0`.
- Punch-in transforms must use safe values: `1.0 < scale <= 1.5`, anchors inside
  `[0.0, 1.0]`, and ease seconds inside `[0.0, 1.0]`. Exporter renders ease
  `0` as the historical static `scale` + `crop` filters; positive ease uses
  `zoompan` with `in_time` expressions, probed source width/height, and
  `x`/`y` anchoring tied to the current `zoom` value. If the source video
  profile cannot be probed, exporter falls back to the historical static
  transform instead of emitting invalid FFmpeg filters.
- Audio mixing is opt-in at planner time. When
  `ARL_EDIT_AUDIO_MIXING_ENABLED=1`, the planner may emit:
  - one BGM `AudioBed` when `ARL_EDIT_BGM_PATH` is set and exists as a file
  - matched local library BGM tracks when `ARL_EDIT_BGM_PATH` is unset and
    `ARL_EDIT_BGM_LIBRARY_PATH` points at a JSON manifest. The manifest may be a
    list of track objects or `{"tracks": [...]}`; each track supports `path`
    (relative to the manifest file or absolute), `tags`, `mood`, `energy`, and
    `phase`. The planner infers context tags from subtitle text, highlight
    reasons, and streamer name, then scores library tracks by tag overlap,
    phase (`laning` / `momentum` / `climax` plus legacy `early` aliases),
    mood, and energy. Common Chinese aliases in
    `tags`, `mood`, and `phase` are normalized before scoring, so operators may
    write manifests with Chinese-only values such as `机器人`, `套路`, `前期`,
    `高潮`, `俏皮`, or `高燃`. Medium two-phase edits may select one
    early/laning track plus one climax track.
    At or above `ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS`, library-backed BGM may
    request `laning -> momentum -> climax` tracks. The planner must never repeat
    the same source path merely to satisfy a phase count; small libraries
    degrade to fewer phases/switches. For full three-phase behavior, operators
    should keep at least two usable tracks per phase bucket.
    Library loading must log a compact diagnostic summary when a manifest path
    is configured, including loaded track count and skipped malformed or
    missing-file entries; no-match logs must include inferred context tags and
    available track count without dumping transcript text.
  - generated default WAV BGM assets under `data/tmp/editing-audio/` when
    `ARL_EDIT_BGM_PATH` is unset and the library is missing, invalid, empty, or
    has no positive match; long edit plans should split BGM into a playful early
    bed and a higher-energy later bed. Generated fallback BGM remains two-phase
    unless a real third generated asset is added and tested.
  - `SoundEffectHit` rows may use either an explicit existing
    `ARL_EDIT_SFX_PATH` file, local SFX library tracks from
    `ARL_EDIT_SFX_LIBRARY_PATH`, or the deterministic generated default
    `data/tmp/editing-audio/coin.wav` when no usable kill SFX library track is
    available. The default SFX must be a short coin/gold accent, not a generic
    `wow.wav` transition sound, because transition noise can make gameplay cuts
    feel artificial, especially before death screens or while the streamer is
    still talking.
  - The SFX library manifest may be a list of track objects or
    `{"tracks": [...]}`. Each track supports `category`, `path` relative to the
    manifest or absolute, and optional `gain_db`. Supported v1 categories are
    `kill_coin`, `multi_kill`, `transition_whoosh`, and `teaser_impact`.
    Missing, invalid, or malformed manifests must not block edit planning.
  - Kill SFX should align to `kda_change` cue timestamps when
    `ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED=1`. The planner parses kill increases,
    maps `current_at` source seconds onto the rendered teaser/main timeline,
    applies `ARL_EDIT_SFX_TIMING_OFFSET_SECONDS`, and selects `multi_kill` when
    the kill delta is at least 2 or nearby subtitle text contains a multi-kill
    announcement. Death-only KDA changes must not emit coin SFX.
  - Segment-start SFX is only a fallback for eligible teaser/main segments
    (`highlight_keyword` and `condensed_key_event`) with no `kda_change` cue
    inside the segment. `condensed_tactical` setup windows must stay SFX-free by
    default. The planner must rate-limit kill SFX with
    `ARL_EDIT_SFX_MIN_INTERVAL_SECONDS` and cap kill hits with
    `ARL_EDIT_SFX_MAX_HITS`; transition whoosh hits are independent of that
    kill-SFX rate limit.
- BGM starts at the first main segment, not at the beginning of optional
  leading teaser or transition segments. For teaser-first timelines, every BGM
  `AudioBed` must be offset by the total leading non-main duration; for
  main-only timelines, BGM starts at `0.0`. This preserves the demo2 convention
  where the cold-open teaser/card has no added BGM and the music enters with
  the main video.
- BGM phase switches should prefer content-aware rendered timeline positions
  from mapped `kda_change` cues and high-signal highlight windows while
  respecting `ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS`. Flat or unmappable signals
  fall back to proportional switch points: roughly 55% for two phases, and
  roughly 40% / 75% for three phases. Adjacent phases represent crossfades as
  overlapping `AudioBed` rows with total overlap
  `ARL_EDIT_BGM_CROSSFADE_SECONDS`; exporter validation must allow that overlap
  and still sidechain each bed before `amix`.
- When `ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC=1` (default), the planner must
  inspect the matching source `RecordingAsset` audio before adding any BGM bed.
  Detectors should expose confident source-music sample spans. The planner maps
  those spans through main timeline segments, pads them by
  `ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS`, and subtracts only the rendered
  overlap from planned BGM beds. If mapped source-music coverage is greater than
  `ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD`, or a legacy detector reports
  `has_music=True` with no spans, it skips both configured and default BGM for
  that match while still allowing eligible SFX hits. Missing recording assets,
  missing ffmpeg, decode errors, or low-confidence/no-music samples must not
  block edit planning and must fall back to the normal BGM decision path.
  For segmented recordings, source-music detection must resolve the match
  boundary to `MediaSpan` rows and sample concrete chunk paths with chunk-local
  timestamps; the manifest JSON path must never be passed to FFmpeg as media.
- Missing configured BGM/SFX files must not block edit-plan generation; the
  planner logs the skip and writes the audio-free base plan for that configured
  asset instead of silently replacing an explicit operator path.
- Exporter renders audio instructions only through the edit-plan path. It must
  validate that every audio source exists as a local file, timeline positions
  are inside the rendered output duration, BGM gain is in `[-60, 0]`, and SFX
  gain is in `[-60, 6]`. Invalid audio instructions make the edit plan
  unsupported, so exporter falls back to highlight/full export.
- Audio-enabled edit-plan FFmpeg commands concatenate original segment audio to
  `[basea]`, add BGM/SFX asset inputs after the recording input, apply `volume`,
  short BGM `afade` in/out, plus `adelay` where needed, and mix with
  `amix=inputs=N:duration=first:dropout_transition=0[a]`. Audio-free edit-plan
  commands keep the existing `[v][a]` concat output shape.
- `ARL_EXPORT_AUDIO_LOUDNORM_ENABLED=1` appends the configured loudness filter
  to export audio. Highlight-plan direct exports append the filter to `-af`;
  edit-plan and span-concat exports append `[a]<filter>[aout]` to
  `filter_complex` and map `[aout]`. Default behavior stays unchanged with
  loudnorm disabled.
- `postprocess-reset` must remove target-session `edit-plans.jsonl` rows and
  `editing-state.json` processed keys. `status` must report `edit_plans` and
  `editing.processed_matches`.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| `ARL_EXPORT_USE_EDIT_PLANS=0` | Exporter does not load or apply `edit-plans.jsonl` |
| Edit planner disabled | `edit-planner` logs disabled and writes no state/manifest rows |
| Boundary incomplete or confidence `<0.8` | Edit planner skips; exporter already skips incomplete boundaries |
| Missing or stale highlight plan | Edit planner skips without marking the match processed |
| Highlight window is negative, reversed, out of bounds, or shorter than teaser minimum | Window is ignored for teaser selection |
| No `highlight_keyword` teaser exists and fallback/candidate expansion is disabled | Edit planner keeps condensed key/tactical windows in the main timeline only and writes a main-only edit plan |
| No candidate clears the teaser signal threshold but fallback is enabled and a valid candidate exists | Edit planner emits the top valid candidate as `teaser_fallback_top_scored` |
| No valid teaser candidates remain but main windows are valid | Edit planner writes a main-only edit plan and marks processed |
| Highlight windows do not cover both source start and source end | Edit planner writes no edit plan and does not mark processed |
| Highlight windows collapse to one full-boundary main segment | Edit planner writes no edit plan; use full/highlight export instead |
| Existing processed key but manifest row is missing | Edit planner compacts state and can regenerate |
| Existing edit plan contains `full_validated_match` | Edit planner treats it as stale and can append a replacement; exporter ignores it and falls back |
| Existing edit plan no longer matches current zoom settings, lacks required close-up pieces, uses stale ease values, or exceeds the close-up budget | Edit planner treats it as stale and can append a replacement |
| Existing close-up-mode edit plan has a transformed segment longer than `ARL_EDIT_ZOOM_CLOSEUP_SECONDS`; existing legacy-mode edit plan has a transformed `main` segment longer than `ARL_EDIT_ZOOM_MAX_DURATION_SECONDS` | Edit planner treats it as stale and can append a replacement with only valid short close-up pieces or valid legacy transforms |
| Existing edit plan teaser segments differ from current strict `highlight_keyword` teaser selection and subtitle/event scoring | Edit planner treats it as stale and can append a replacement |
| Existing edit plan lacks the expected `transition` segment or has stale transition text/duration | Edit planner treats it as stale and can append a replacement |
| Existing edit plan audio instructions differ from the current timeline/library/source-music decision in path, start/end timing, gain, loop, or reason | Edit planner treats it as stale and can append a replacement so BGM starts at the first main segment, after any leading teaser |
| `--force-reprocess` is used | Edit planner appends a replacement row; downstream latest row wins |
| Edit plan source boundary differs from current boundary by more than 1 second | Exporter ignores the edit plan and falls back |
| Timeline has no main segment or teaser appears after main | Exporter ignores the edit plan and falls back |
| Transition role is malformed, appears after main, appears before any teaser, has source media, transform, unknown reason, or non-positive duration | Exporter ignores the edit plan and falls back |
| Timeline starts with main and contains only main segments | Exporter accepts the main-only edit plan |
| Main segment sequence lacks a segment starting at `0.0` or ending at boundary duration | Exporter ignores the edit plan and falls back |
| Main segment sequence is a single full-boundary `full_validated_match` segment | Exporter ignores the edit plan and falls back |
| Teaser appears after main, insert role appears, or `source_path` is set | Exporter ignores the edit plan and falls back |
| Transform kind is not `none` or `punch_in`, punch-in scale is outside `(1.0, 1.5]`, anchors are outside `[0.0, 1.0]`, or ease seconds are outside `[0.0, 1.0]` | Exporter ignores the edit plan and falls back |
| Audio source path is missing, not a file, out of output range, reversed, or has gain outside the safety range | Exporter ignores the edit plan and falls back |
| `ARL_EXPORT_AUDIO_LOUDNORM_ENABLED=0` | Exporter keeps existing audio filter/map behavior |
| `ARL_EXPORT_AUDIO_LOUDNORM_ENABLED=1` and filter is blank | Config normalizes the filter to `loudnorm=I=-16:TP=-1.5:LRA=11` |
| `ARL_EDIT_AUDIO_MIXING_ENABLED=1` but configured BGM/SFX files are missing | Edit planner writes the base audio-free edit plan for those explicit missing assets and logs missing-file skips |
| `ARL_EDIT_BGM_LIBRARY_PATH` is set and contains matching local tracks | Edit planner emits library-backed `AudioBed` rows before falling back to generated default BGM |
| BGM library manifest contains malformed rows or missing local files | Edit planner logs loaded/skipped counts once, ignores invalid rows, and keeps processing valid tracks |
| `ARL_EDIT_BGM_LIBRARY_PATH` is missing, invalid JSON, has missing files, or has no positive match | Edit planner keeps the normal generated default BGM behavior |
| `ARL_EDIT_AUDIO_MIXING_ENABLED=1`, BGM path/library are unset, and `ARL_EDIT_SFX_PATH` is unset | Edit planner may generate deterministic default BGM WAV assets plus default `coin.wav`, and emits KDA-aligned/rate-limited SFX only for kill events or fallback `highlight_keyword` / `condensed_key_event` segments |
| `ARL_EDIT_AUDIO_MIXING_ENABLED=1`, `ARL_EDIT_SFX_LIBRARY_PATH` contains `kill_coin` and/or `multi_kill` tracks | Edit planner uses library tracks for kill SFX before falling back to generated `coin.wav`; malformed/missing rows are skipped with compact logs |
| `ARL_EDIT_AUDIO_MIXING_ENABLED=1` and `ARL_EDIT_SFX_PATH` points at an existing file | Edit planner may emit KDA-aligned/rate-limited `SoundEffectHit` rows from the configured file for kill events or eligible fallback segments |
| A `kda_change` cue increases deaths only | Edit planner emits no coin SFX for that KDA segment |
| `ARL_EDIT_AUDIO_MIXING_ENABLED=1` and a segment reason is `condensed_tactical` | Edit planner must not emit SFX for that segment; tactical/setup windows are not sound-effect moments by default |
| Source recording audio already has a persistent music bed and skip-source-music protection is enabled | Edit planner emits no BGM `AudioBed` for that match, may keep eligible configured/default `SoundEffectHit` rows, and logs the skip confidence |
| Source recording is segmented | Source-music detection resolves chunk spans and samples chunk-local windows before deciding whether to skip BGM |
| Source music detection cannot run or is inconclusive | Edit planner keeps the normal configured/default BGM behavior |
| Burn-in subtitles are enabled | Exporter writes an edit-plan subtitle sidecar retimed to the edited output timeline, then applies `subtitles=` after video `concat` so punch-in transforms do not scale subtitle text |
| Edit-plan source media resolves to chunk spans | Exporter expands every timeline segment into chunk-local media spans, preserves the segment transform on each expanded span, and starts BGM/SFX inputs after all media inputs |

### 5. Good/Base/Bad Cases
- Good: A complete match with a valid condensed/highlight plan emits optional
  teaser clips plus multiple `main` segments from the same validated edit
  windows. Exporter with `ARL_EXPORT_USE_EDIT_PLANS=1` builds a
  `filter_complex` graph with per-segment `trim` / `atrim` / `concat`.
- Good: With explicit existing BGM/SFX paths and audio mixing enabled, the edit
  plan carries typed audio instructions and exporter mixes them under original
  segment audio with `amix=duration=first`.
- Good: With audio mixing enabled and no configured audio paths, the edit plan
  references generated low-volume BGM WAV files plus the generated `coin.wav`
  SFX. Long edits switch from playful BGM to a higher-energy BGM later in the
  timeline with a short fade-out/fade-in transition instead of a hard cut, and
  SFX remains limited to key highlight-event moments.
- Good: Default BGM beds use `gain_db=-28.0`, keeping music peaks below normal
  gameplay/commentary presence in publish exports while still allowing an
  explicit `ARL_EDIT_BGM_GAIN_DB` override for special cases.
- Good: A teaser-first edit plan has no added BGM during the teaser; its first
  BGM bed starts when the first main segment begins. A main-only edit plan may
  start BGM at `0.0`.
- Good: Given two `highlight_keyword` windows, a subtitle like
  `上单电刀AP机器人 清线快伤害高 单杀打开局面` becomes the first teaser before an
  earlier generic farming/setup subtitle.
- Good: Main-only edit plans can receive a small number of SFX hits from either
  an explicit SFX asset or generated `coin.wav` when the retained segment reason
  is `highlight_keyword` or `condensed_key_event`; tactical/setup windows remain
  free of transition sounds.
- Good: With close-up zoom enabled, a KDA kill inside a retained
  `condensed_key_event` segment splits that segment around the kill timestamp;
  only the short transformed piece carries
  `TimelineVideoTransform(kind="punch_in", target="center", ease_in_seconds=0.4,
  ease_out_seconds=0.4, ...)`.
- Good: A long merged `condensed_key_event` main segment can still receive one
  short close-up piece, while the surrounding timeline pieces remain
  untransformed and preserve the original total source duration.
- Good: A main segment from `8.0` to `12.0` seconds on a recording split at
  `10.0` seconds becomes two FFmpeg inputs with local trims `8.0..10.0` and
  `0.0..2.0`, then rejoins through the same edit-plan concat graph.
- Base: Edit plans are generated for analysis, but `ARL_EXPORT_USE_EDIT_PLANS=0`
  keeps the current full-boundary or explicit highlight export behavior.
- Bad: A teaser-only plan or a plan whose main segment starts mid-game is
  rendered as a publishable export. This hides segmentation defects and must
  fall back instead.

### 6. Tests Required
- Unit: edit planner writes teaser-first plus multi-main plans from explicit
  highlight windows.
- Unit: edit planner writes main-only multi-main plans when generic condensed
  key/tactical windows provide no high-confidence teaser.
- Unit: edit planner treats legacy `full_validated_match` edit plans as stale
  and appends a replacement without `--force-reprocess`.
- Unit: edit planner splits eligible long high-signal main segments into
  duration-preserving close-up pieces, and treats legacy no-main-zoom plans as
  stale when close-up zoom is enabled.
- Unit: KDA kill subtitles produce a short center-target close-up, chat-burst
  synthetic frame changes produce a bottom-left chat-target close-up, and
  `ARL_EDIT_ZOOM_MODE=legacy` preserves whole-segment static transforms.
- Unit: edit planner treats legacy long transformed main segments as stale and
  replaces them with valid short close-up pieces in close-up mode.
- Unit: edit planner skips missing/stale/invalid highlight input without marking
  processed.
- Unit: edit planner supports session/match filters and `--force-reprocess`.
- Config: edit planner env values load and clamp; edit-plan export defaults off.
- Config: audio mixing env values load, default off, and gain values clamp to
  safe ranges.
- Unit: edit planner generates deterministic default BGM WAV assets and a
  default `coin.wav` SFX asset when audio mixing is enabled and no explicit
  audio paths are configured; SFX hits still require eligible high-signal
  segment reasons.
- Unit: edit planner selects local BGM library tracks from subtitle/highlight
  context when `ARL_EDIT_BGM_LIBRARY_PATH` is configured, and replans older
  default-BGM edit plans when the current library match differs.
- Unit: edit planner treats stale audio timing as stale, including legacy
  teaser-first plans whose first BGM bed starts at output `0.0` instead of
  after the teaser.
- Unit: edit planner orders multiple valid teaser windows by overlapping
  high-signal subtitle/event text before falling back to chronological order.
- Unit: BGM library matching accepts Chinese-only `tags`, `phase`, and `mood`
  aliases and normalizes them before selecting early and climax tracks.
- Unit: BGM library loading logs configured-manifest diagnostics for loaded
  tracks, malformed rows, missing paths, missing files, and no-match context.
- Unit: edit planner skips BGM when source music detection reports an existing
  persistent music bed, may keep eligible configured/default SFX hits, and
  treats older BGM-bearing edit plans as stale in that condition.
- Unit: edit planner resolves segmented recordings to chunk-local source-music
  detector calls instead of passing the manifest JSON path as media.
- Unit: edit planner emits rate-limited SFX hits from explicit configured SFX
  files or generated `coin.wav` only for concrete highlight-event reasons,
  including main-only edit plans without a teaser, and keeps tactical/setup
  windows SFX-free.
- Config: zoom env values load, default off, mode aliases normalize, and
  scale/anchor/max-segment/close-up/ease/chat-burst values clamp to safe ranges;
  max duration clamps to at least one second; default zoom target is `chat`.
- CLI/postprocess: parser includes `edit-planner`; postprocess order includes it
  between `highlight-planner` and `exporter`.
- Exporter: edit plans are ignored by default, valid teaser-first and main-only edit plans build
  `filter_complex`, invalid plans fall back, and valid edit plans take
  precedence over highlight plans only when enabled.
- Exporter: chunked highlight plans resolve each highlight window to concrete
  chunk inputs and retime burned subtitles to the post-concat output timeline.
- Exporter: edit-plan subtitle burn-in retimes SRT cues to the edited output
  timeline and applies `subtitles=` after concat, not before per-segment
  punch-in transforms.
- Exporter: audio-enabled edit plans add asset inputs, `volume`, BGM `afade`,
  `adelay`, and `amix`; missing/stale audio asset paths fall back.
- Exporter: chunked edit plans expand cross-chunk timeline windows into
  concrete chunk inputs and offset BGM/SFX input indexes by media input count.
- Exporter: static punch-in edit plans add `scale`/`crop` filters, eased
  punch-ins use `zoompan` with `in_time` and probed output dimensions, and invalid transform
  rows fall back.
- Exporter: legacy full-main edit plans are ignored and, when enabled, valid
  highlight plans are used as the fallback path.
- Status/reset: counts and cleanup include `edit-plans.jsonl` and
  `editing-state.json`.

### 7. Wrong vs Correct
#### Wrong
```python
plan = edit_plan_map.get((boundary.session_id, boundary.match_index))
command = render_edit_plan(plan)  # applies teaser timelines whenever a row exists
```

#### Correct
```python
edit_plan = (
    valid_edit_plan(edit_plan_map.get(key), boundary)
    if settings.export.use_edit_plans
    else None
)
highlight_plan = (
    valid_highlight_plan(highlight_plan_map.get(key), boundary)
    if edit_plan is None and settings.export.use_highlight_plans
    else None
)
```

---

## Scenario: Publish Edit Preset

### 1. Scope / Trigger
- Trigger: The pipeline has multiple publish-quality features (condensed
  highlight planning, edit plans, ASS subtitle burn-in, punch-in zoom, BGM/SFX),
  but leaving each behind a separate env flag makes real exports easy to run in
  a half-enabled state.
- Trigger: The preset spans config loading, CLI settings resolution,
  postprocess orchestration, edit-planner generation, and exporter plan
  consumption.

### 2. Signatures
- Config helper:
  ```python
  def apply_publish_preset(settings: Settings) -> Settings: ...
  ```
- Environment:
  ```bash
  ARL_POSTPROCESS_PRESET=publish
  # Back-compat/boolean alias:
  ARL_POSTPROCESS_PUBLISH_PRESET=1
  ```
- CLI:
  ```bash
  python -m arl.cli postprocess --once --publish
  python -m arl.cli postprocess --once --session-id <session_id> --publish
  ```

### 3. Contracts
- The publish preset is explicit opt-in. Plain `load_settings()` and plain
  `postprocess --once` must preserve full-boundary/diagnostic defaults unless
  `ARL_POSTPROCESS_PRESET=publish`, `ARL_POSTPROCESS_PUBLISH_PRESET=1`, or
  `postprocess --publish` is present.
- `apply_publish_preset(settings)` must return a copied settings object; it must
  not mutate the object it receives.
- The preset enables:
  - `highlights.enabled=True`
  - `highlights.mode="condensed"`
  - `editing.enabled=True`
  - `editing.zoom_enabled=True`
  - `editing.audio_mixing_enabled=True`
  - `editing.bgm_library_path=Path("data/bgm/library.json")` when neither
    `ARL_EDIT_BGM_LIBRARY_PATH` nor `ARL_EDIT_BGM_PATH` is explicitly set
  - `export.enable_ffmpeg=True`
  - `export.burn_subtitles=True`
  - `export.use_ass_subtitles=True`
  - `export.use_edit_plans=True`
  - `export.use_highlight_plans=True`
  - `export.ffmpeg_video_codec="h264"` when the source setting is `auto`
  - `export.ffmpeg_bitrate` at least `"8000k"` when the existing setting is
    missing or lower
  - `export.ffmpeg_max_bitrate` at least `"10000k"` when the existing setting
    is missing or lower
  - `export.audio_loudnorm_enabled=True`
- The preset uses the normal strict edit-planner teaser rules: only
  `highlight_keyword` windows can become teaser clips. Valid main-only edit
  plans remain allowed when no teaser candidate exists.
- The preset must prefer the operator's configured BGM library over generated
  fallback WAV files. If no explicit BGM library/path is configured, it points
  the edit planner at `data/bgm/library.json`; if that manifest is absent or has
  no positive match, the normal generated default-BGM fallback still applies.
- Subtitle burn-in still follows the exporter placeholder guard: placeholder SRT
  files are not burned even when the preset enables burn-in.
- Missing ffmpeg or missing recording prerequisites must defer export through
  the existing exporter behavior, not crash postprocess.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| No publish preset env/flag | Defaults remain compatible: edit plans, burn-in, zoom, and audio mixing stay disabled |
| `ARL_POSTPROCESS_PRESET=publish` | `load_settings()` returns settings with the publish pipeline enabled |
| `ARL_POSTPROCESS_PRESET` is any other value | Preset is not applied |
| `ARL_POSTPROCESS_PUBLISH_PRESET=1` | Boolean alias applies the same preset |
| `postprocess --publish` | CLI applies the preset before constructing `PostProcessService` |
| `postprocess --publish` and ffmpeg is missing | Exporter defers/skips with `reason=missing_binary`; postprocess does not crash |
| Placeholder subtitles under publish preset | Exporter skips subtitle burn-in for that match |
| Existing config has no explicit BGM path/library | Preset sets `editing.bgm_library_path` to `data/bgm/library.json` so local library matching is attempted before generated default BGM |
| Existing config has explicit `ARL_EDIT_BGM_LIBRARY_PATH` or `ARL_EDIT_BGM_PATH` | Preset preserves the operator-provided BGM input |

### 5. Good/Base/Bad Cases
- Good: A user runs `postprocess --once --session-id <id> --publish` and gets
  condensed edit-plan export, raised ASS subtitles, chat-target punch-in where
  a real teaser exists, low-volume default audio, copywriting, cover, and
  friendly published aliases from one command.
- Base: A background postprocess loop keeps using plain defaults until the
  operator sets `ARL_POSTPROCESS_PRESET=publish`.
- Bad: Turning `ExportSettings.use_edit_plans=True` globally without enabling
  `editing.enabled` or `highlights.mode="condensed"` produces full-length
  exports and makes users think the 14-minute edit feature regressed.

### 6. Tests Required
- Config: `tests/test_config.py` asserts normal defaults remain non-publish.
- Config: `tests/test_config.py` asserts `ARL_POSTPROCESS_PRESET=publish` and
  `ARL_POSTPROCESS_PUBLISH_PRESET=1` enable the publish pipeline fields.
- Config: `tests/test_config.py` asserts publish preset uses
  `data/bgm/library.json` only when no explicit BGM path/library is configured.
- CLI: `tests/pipeline/test_cli_unattended.py` asserts `postprocess --publish`
  parses and the `PostProcessService` receives publish-enabled settings.
- Exporter regression: existing edit-plan, ASS, placeholder-subtitle, zoom, and
  audio-mixing tests remain the behavioral gate for the enabled preset.

### 7. Wrong vs Correct
#### Wrong
```python
class ExportSettings(BaseModel):
    use_edit_plans: bool = True
    burn_subtitles: bool = True
```

#### Correct
```python
settings = load_settings()
if args.publish:
    settings = apply_publish_preset(settings)
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

## Scenario: Vision Post-Game Tail Refinement

### 1. Scope / Trigger
- Trigger: Vision match detection writes `MatchBoundary.ended_at_seconds`, and condensed exports trust that boundary when selecting final windows.
- Trigger: League post-game honor/client screens can be classified as `loading` or `other` immediately after the final in-game frame, causing exports to include desktop/client UI after the match.

### 2. Signatures
- CLI:
  ```bash
  python -m arl.cli segmenter --session-id <session_id> --force-reprocess
  ```
- Vision API:
  ```python
  VisionMatchDetector.detect(video_path: Path) -> list[MatchSegment]
  VisionMatchDetector._find_trailing_non_game_start(scenes, current_end=...)
  ```

### 3. Contracts
- `stitch_scene_readings` must keep an abrupt `loading` frame after `in_game` as a possible match end when gameplay does not resume. If later `in_game` appears, it remains a death/respawn false-split guard and must not end the match.
- `VisionMatchDetector` must resample complete segment tails at the configured fine interval and trim `ended_at_seconds` to the first trailing non-`in_game` scene plus one fine sample interval. This preserves a short victory/end reaction while avoiding client/desktop bleed.
- `segmenter --force-reprocess` may replace existing `match-boundaries.jsonl` rows for the matched session and clear only the matching segmenter processed-state keys. It must not delete raw recordings, subtitles, exports, or publishing packages.
- Condensed highlight planning must clip subtitle cues and windows to the current boundary duration, then merge overlapping windows before persisting the latest plan row.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| Abrupt `loading` appears after `in_game`, then `in_game` resumes | Treat as death/respawn; do not split or end the match |
| Abrupt `loading` appears after `in_game`, then only `other/loading` remains | Use that point as the candidate natural end |
| Fine tail scan sees `in_game -> other/loading` near the boundary | Trim the boundary to first trailing non-game scene plus one fine interval |
| Fine tail scan has no trailing non-game scene | Keep the coarse boundary |
| Trimming would make the match shorter than `min_match_duration_seconds` | Keep the coarse boundary |
| Existing boundaries are stale for a target session | `segmenter --force-reprocess` rewrites only that session's boundary rows |

### 5. Good/Base/Bad Cases
- Good: A final nexus explosion at `4050s` followed by League client/honor UI at `4055s` produces a boundary ending at `4050s`.
- Base: A death/respawn overlay misclassified as loading at `80s`, followed by gameplay at `100s`, remains one continuous match.
- Bad: A condensed export includes the League queue/client desktop after the victory sequence, or repeats overlapping tail windows after clamping.

### 6. Tests Required
- Unit: `tests/vision/test_match_stitcher.py` asserts abrupt loading becomes an end only when gameplay does not resume.
- Unit: `tests/vision/test_detector.py` asserts trailing non-game tail detection chooses the first post-game scene and ignores middle non-game gaps.
- Unit: `tests/pipeline/test_segmenter_service.py` asserts force reprocess replaces existing boundary rows without duplication.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts condensed helper code clips cues/windows to boundary duration and merges overlaps.
- Unit: `tests/highlights/test_window_optimizer.py` asserts medium/large condensed gaps get a next-segment lead-in bridge and final post-processing gaps can be repaired before persistence.
- Unit: `tests/highlights/test_window_optimizer.py` asserts huge opening jumps such as `00:38 -> 16:36` are split into progression continuity snippets and the final adjacent source-time gap is no greater than `condensed_boring_gap_threshold_seconds`.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts death-like continuity entries are extended back to the previous retained window end.
- Unit: `tests/pipeline/test_highlight_planner_service.py` asserts final condensed duration budgeting keeps dense content short instead of preserving scattered windows that produce near-full-match exports.

### 7. Wrong vs Correct
#### Wrong
```python
if pending_other_start is None and direct_gap <= 90.0:
    continue  # always ignore abrupt loading after gameplay
```

#### Correct
```python
if pending_other_start is None and direct_gap <= 90.0:
    pending_abrupt_loading_end = pending_abrupt_loading_end or reading.timestamp_seconds
    continue
```

#### Wrong
```python
windows = extend_action_resolution_windows(windows, classified_cues)
append_plan(windows)  # may exceed a refined boundary or overlap itself
```

#### Correct
```python
windows = extend_action_resolution_windows(windows, classified_cues)
windows = clamp_and_merge_highlight_windows(windows, boundary_duration)
append_plan(windows)
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

## Scenario: Export Quality Report CLI

### 1. Scope / Trigger
- Trigger: Publish-export acceptance now has a repeatable report stage that reads existing export/edit/subtitle/copy assets and writes per-match diagnostics without regenerating videos.
- Trigger: This path spans CLI selection, file-backed JSONL manifests, FFprobe metadata, subtitle retiming, edit-plan audio/video annotations, and report artifacts under `storage.processed_dir`.

### 2. Signatures
- CLI:
  ```bash
  python -m arl.cli quality-report --session-id <session_id> --match-index <n>
  python -m arl.cli quality-report --session-id <session_id> --match-indices 2,3,4
  python -m arl.cli quality-report --session-ids <a,b> --all-latest --strict
  ```
- Report files:
  ```text
  data/processed/<session_id>/reports/match-NN-quality-report.md
  data/processed/<session_id>/reports/match-NN-quality-report.json
  ```
- Service entrypoint:
  ```python
  QualityReportService(settings).run(
      session_ids=set[str] | None,
      match_indices=set[int] | None,
      all_latest=bool,
      strict=bool,
      top_gaps=int | None,
  )
  ```

### 3. Contracts
- The CLI must require either `--session-id` / `--session-ids` or `--all-latest`.
- The stage reads latest rows by `(session_id, match_index)` from:
  - `match-boundaries.jsonl`
  - `subtitle-assets.jsonl`
  - `highlight-plans.jsonl`
  - `edit-plans.jsonl`
  - `export-assets.jsonl`
  - `copy-assets.jsonl`
  - `publishing-packages.jsonl`
- It must not regenerate export videos. It may run `ffprobe` against existing export paths and best-effort KDA detection against existing recording assets.
- Markdown and JSON reports are overwritten per match under `data/processed/<session_id>/reports/`, never under the repo root.
- Subtitle coverage must retime source SRT cues through the same edit/highlight-plan overlap semantics used by exporter subtitle sidecars.
- No-subtitle gap summary count is the count of "long" gaps, defaulting to gaps `>= 8.0s`; max gap still considers all subtitle-free gaps.
- Report JSON should include copywriter data under `copywriter`, not `copy`, to avoid shadowing Pydantic `BaseModel.copy()`.
- Environment keys:
  - `ARL_QUALITY_REPORT_SUBTITLE_ACTIVE_RATIO_MIN` default `0.55`
  - `ARL_QUALITY_REPORT_LONG_NO_SUBTITLE_GAP_MIN_SECONDS` default `8.0`
  - `ARL_QUALITY_REPORT_MAX_SOURCE_GAP_SECONDS` default `45.0`
  - `ARL_QUALITY_REPORT_TEASER_MIN_SEGMENTS` default `1`
  - `ARL_QUALITY_REPORT_TEASER_MAX_SEGMENTS` default `3`
  - `ARL_QUALITY_REPORT_SFX_MAX_HITS` default `6`
  - `ARL_QUALITY_REPORT_ZOOM_MIN_SEGMENTS` default `1`
  - `ARL_QUALITY_REPORT_ZOOM_MAX_SEGMENTS` default `4`
  - `ARL_QUALITY_REPORT_TOP_NO_SUBTITLE_GAPS` default `5`

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| No session selector and no `--all-latest` | CLI parser error |
| Export file missing | Emit `export_file_missing` warning and still write report artifacts |
| `ffprobe` unavailable or invalid output | Emit `media_probe_failed`; keep other metrics |
| Subtitle asset/file missing | Emit subtitle warning; subtitle active ratio stays `0.0` |
| KDA detection fails | Emit `kda_detection_failed`; KDA uncovered count uses zero detected events |
| Threshold violation and `--strict` unset | Exit `0` with warnings in Markdown/JSON |
| Threshold violation and `--strict` set | Exit `1` with the same artifacts written |

### 5. Good/Base/Bad Cases
- Good: `quality-report --session-id session-... --match-indices 2,3,4` writes one Markdown and JSON file per selected match and prints a table whose duration/bitrate/source-gap/subtitle metrics reproduce the manual validation report within rounding tolerance.
- Base: `quality-report --all-latest` reports the latest export asset for each `(session_id, match_index)` currently present in the export manifest.
- Bad: The command regenerates exports, writes report files into the source tree, counts every tiny subtitle gap as a "long no-subtitle gap", or stores report JSON with a `copy` field that triggers Pydantic shadow warnings.

### 6. Tests Required
- CLI parser tests for `quality-report` selectors, `--strict`, and `--top-gaps`.
- CLI entrypoint test asserting filters are normalized and service exit code is returned.
- Config test asserting all `ARL_QUALITY_REPORT_*` env values load and clamp through `QualityReportSettings`.
- Service unit test on synthetic assets with mocked media probe and KDA provider; assert metric values, warnings, and output paths.
- Strict-mode test asserting threshold warnings produce exit code `1`.
- Acceptance smoke against local validation sessions when assets are available:
  - `session-20260617073649-4b5ec478` m02
  - `session-20260617073651-cf11bf9e` m02-04

### 7. Wrong vs Correct
#### Wrong
```python
row = {"copy": copy_metric}
gap_count = len(all_subtitle_free_gaps)
Path("reports/match-02.md").write_text(markdown)
```

#### Correct
```python
row = QualityReportRow(copywriter=copy_metric)
long_gap_count = sum(
    1
    for gap in all_subtitle_free_gaps
    if gap.duration_seconds >= settings.quality_report.long_no_subtitle_gap_min_seconds
)
(settings.storage.processed_dir / session_id / "reports").mkdir(
    parents=True,
    exist_ok=True,
)
```

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

### Mistake 4: Counting historical exporter fallbacks as current health

**Symptom**: `arl status` reports `exporter_fallbacks` for a session that was
successfully re-exported later.

**Cause**: `exporter-events.jsonl` is append-only. Historical
`ffmpeg_export_fallback_placeholder` rows remain in the audit log after a later
`ffmpeg_export_succeeded` row for the same `(session_id, match_index)`.

**Fix**: Status views must derive the latest terminal exporter outcome per
`(session_id, match_index)` before reporting fallback health. A fallback is
current only when it is the latest match outcome and no later present `.mp4`
`ExportAsset` resolves it.

**Prevention**: Keep status regression tests for fallback-then-success and
duplicate-fallback cases. Missing output files should still be reported through
`missing_exports`; historical fallback rows should not duplicate that signal.

### Mistake 5: Forcing a cold-open teaser from generic condensed windows

**Symptom**: The export starts with a long "highlight" clip that is not actually
the strongest moment, and added BGM plays over that teaser before the main video
begins.

**Cause**: Generic `condensed_key_event` or `condensed_tactical` windows were
promoted into teaser slots as a fallback, and BGM beds started at output `0.0`
instead of the first main segment.

**Fix**: Only `highlight_keyword` windows can become teaser clips. When no such
window exists, emit a main-only edit plan. If a teaser exists, offset BGM beds by
the leading teaser duration so music starts with the main content.

**Prevention**: Keep edit-planner regressions for main-only condensed key-event
plans and teaser-first BGM timing.

### Mistake 6: Cutting condensed windows through active speech

**Symptom**: A condensed export cuts to the next segment while the streamer is
still mid-sentence, or drops the next subtitle cue that begins immediately after
the window end.

**Cause**: Window optimization and continuity bridge insertion work from
highlight/event timing first. If the final window edge lands inside an SRT cue,
or less than a short tolerance before the next cue in the same utterance, FFmpeg
will cut the narration even though the visual event was preserved.

**Fix**: Before persisting a condensed `HighlightPlanAsset`, run the windows
through speech-boundary protection. The protection must:
- move a window start back to the beginning of an overlapping cue
- extend a window end to the end of any cue it cuts through
- extend across immediately adjacent subtitle cues in the same thought
- cap the result to the current match boundary duration, then use the existing
  clamp/merge pass

**Prevention**: Keep highlight-planner regressions asserting no window end falls
inside a subtitle cue or within the configured immediate-cue tolerance after
planning.

### Mistake 7: BGM library ties always choose the same files

**Symptom**: Every exported video receives the same two BGM tracks even though
the library contains multiple tracks with equivalent style/energy matches.

**Cause**: Library selection sorted equal-score candidates by path only. This is
deterministic, but it makes same-style videos reuse the alphabetically earliest
track pair forever.

**Fix**: Keep scoring style-first, then apply deterministic tie rotation only
among tracks with the best score. The rotation key should include
`session_id`, `match_index`, normalized context tags, and highlight reasons.
When the selection key follows the normal `<session_id>:<match_index>:<context>`
shape, include the match index as an explicit modulo offset after hashing the
rest of the key, so adjacent matches in one session do not accidentally hash
into the same equal-score candidate forever.

Do not let context-tag score alone make the early bed choose a climax/high-energy
track when early/laning/playful/chill candidates are available. The intended
publish shape is still development/laning BGM first, then a higher-energy
climax bed later.

**Prevention**: Keep edit-planner regressions where several equal-score tracks
are available and different selection keys choose more than one candidate
without selecting a lower-score track, including adjacent match indices in one
session. Keep a phase-priority regression where a high-scoring climax track is
available but the early bed still chooses the suitable laning/chill track.

### Mistake 8: Short weak titles lack enough context

**Symptom**: A title such as `被粉丝认出来` or a raw short subtitle line is
technically related to the excerpt but too short to explain what the video is
about.

**Cause**: Copywriter headline generation can produce a single compact phrase,
or fall back to the first scored subtitle cue, even when the phrase is not a
strong standalone gameplay/topic summary.

**Fix**: Treat compact titles without strong topic markers as context-needing.
Append high-signal excerpt phrases until the title is self-contained, while
preserving concise strong titles such as `电刀AP机器人`, `装没钱人设 炒股经济学`,
or `清线快伤害高`.

**Prevention**: Keep copywriter regressions for both pure weak short titles and
single short theme titles that need one or two supporting context phrases.

### Mistake 9: Treating one video's loud timestamp as a global BGM check

**Symptom**: A manual review finds BGM too loud at a timestamp such as `05:04`,
then later validations reuse that exact timestamp on unrelated exports.

**Cause**: Condensed exports have different timelines, selected windows, BGM
switch points, and speech density. A timestamp that catches loud BGM in one
video may be silent, low-value, or unrelated in another.

**Fix**: Enforce voice/gameplay priority in the mix itself, then validate with
content-aware samples:
- Exporter BGM beds must be sidechain-ducked against the concatenated base
  audio before `amix`, so BGM is automatically reduced when the source audio is
  active.
- Post-export checks should map SRT cues through the edit-plan timeline and
  sample speech-dense output windows for that specific video.
- Fixed timestamp checks are valid only when the feedback explicitly names that
  video and timestamp.

**Prevention**: Keep exporter command tests asserting BGM filters include
`sidechaincompress` with the base audio as sidechain input. Manual validation
reports should record the video path plus timestamp instead of promoting the
timestamp into a general rule.

### Mistake 10: Validating death jumps without checking source-time continuity

**Symptom**: A condensed export appears normal at output `00:32` or `00:38`,
then the next second jumps from early jungle/lane state to a much later
in-game clock such as `16:42` or `17:33`, with KDA/level already changed.

**Cause**: Validation checked whether the cut opened directly on a death-like
gray respawn frame, but did not inspect the edit/highlight plan's adjacent
source-time gaps. A single short lead-in before the later retained event still
leaves the preceding cut as a huge game-clock/KDA jump.

**Fix**: Treat adjacent source-time gaps as a first-class publishability check.
For final condensed highlight/edit plans, compute every
`next.source_start_seconds - previous.source_end_seconds`; any gap above
`condensed_boring_gap_threshold_seconds` must be split with short progression
continuity snippets or fixed by choosing a more continuous budget span before
export. This gap check is necessary but not sufficient: every detected
`kda_change` cue must also be fully covered by a real key-event window, because
a short continuity snippet can still skip the actual kill/death.

**Prevention**: Keep optimizer regression tests for huge opening jumps and
include both max-source-gap summaries and KDA uncovered counts in manual
validation reports for regenerated exports.

## Scenario: Publishing Cover Candidates

### 1. Scope / Trigger
- Trigger: Final publishing packages may contain multiple generated cover
  candidates for manual selection while keeping one default cover path for
  existing upload flows.
- Trigger: This spans `PublishingPackage`, copywriter cover rendering,
  published package directory layout, upload metadata, and postprocess reset.

### 2. Signatures
- Model additions:
  ```python
  class CoverCandidate(BaseModel):
      path: str
      rank: int
      source_timestamp_seconds: float = 0.0
      score: float = 0.0
      reasons: list[str] = Field(default_factory=list)
      published_path: str | None = None

  class PublishingPackage(BaseModel):
      cover_path: str | None = None
      cover_candidates: list[CoverCandidate] = Field(default_factory=list)
      published_cover_path: str | None = None
  ```
- Generated processed files:
  ```text
  data/processed/<session_id>/match-NN-cover-01.jpg
  data/processed/<session_id>/match-NN-cover-02.jpg
  data/processed/<session_id>/match-NN-cover-03.jpg
  ```
- Published package files:
  ```text
  cover.jpg        # default rank-1 cover for legacy upload workflow
  cover-01.jpg
  cover-02.jpg
  cover-03.jpg
  upload.txt
  video.mp4
  ```

### 3. Contracts
- The schema change is additive. Existing publishing rows without
  `cover_candidates` must load with an empty list.
- `cover_path` always points at the top-ranked successfully rendered candidate.
  `published_cover_path` always points at `cover.jpg`, a copy of the same
  rank-1 candidate inside the published package directory.
- `cover_candidates[*].path` records every successfully rendered processed
  candidate. `cover_candidates[*].published_path` records the matching
  `cover-XX.jpg` copy after publishing.
- Final copywriter publishing may read a valid matching `EditPlanAsset` for
  teaser/high-signal source windows, but cover generation must still work when
  edit plans are missing or stale.
- Candidate frame scoring is deterministic and best-effort. It may use KDA cue
  timestamps, edit-plan teaser/high-signal windows, highlight windows, scene
  classification, sharpness/brightness, and bottom-left chat-region activity.
  It must not OCR chat text or derive new cover copy from raw subtitles.
- Cover text consumes `PublishingPackage.cover_lines` as produced by heuristic
  or LLM copywriting. The cover renderer must not create its own title text.
- Typography defaults to stacked left-aligned yellow (`#FFEE00`) headline lines
  with a heavy black stroke, fitted inside conservative safe margins for a
  1920x1080 JPEG at quality 92.
- Missing recording/export media, ffmpeg, Pillow, cv2, fonts, or frame-sampling
  failures degrade to the existing single fallback timestamp or to no cover
  output. The copywriter stage still writes publishing metadata.
- Output completeness checks must treat missing processed candidate files or
  missing published candidate copies as incomplete so reruns repair the package.
- `postprocess-reset` must delete candidate processed paths and published
  candidate copies referenced by removed `PublishingPackage` rows when those
  paths are under generated roots.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| Existing row lacks `cover_candidates` | Load with `cover_candidates=[]` |
| Recording media can be sampled | Render up to 3 ranked `cover-XX.jpg` candidates |
| Sampling/scoring fails | Render one legacy fallback candidate if normal cover prerequisites work |
| Only export media is available | Render one export-time fallback candidate at `0.0` |
| Some candidate renders fail | Keep successful candidates; default points at the first successful candidate |
| Published `cover-02.jpg` is deleted | Next copywriter run treats package outputs as incomplete and repairs |

### 5. Good/Base/Bad Cases
- Good: Recording media is available, frame scoring selects distinct source
  timestamps, `cover_path` points to rank 1, and the package directory contains
  both `cover.jpg` and `cover-XX.jpg` candidates.
- Base: Only exported media is available, so copywriter renders one fallback
  candidate at export time `0.0` and still writes the publishing JSON and
  upload metadata.
- Bad: A rerun sees `copywriter-state.json` marked processed while
  `cover-02.jpg` or its published copy is missing, then skips the match instead
  of rebuilding the package artifacts.

### 6. Tests Required
- Model: legacy `PublishingPackage` rows default `cover_candidates=[]`.
- Copywriter: ranked candidates preserve `cover_path` / `published_cover_path`
  defaults, publish `cover-XX.jpg`, and list candidates in JSON and `upload.txt`.
- Copywriter: export-only fallback remains a single candidate at `0.0`.
- Cover helper: synthetic frame scoring covers sharpness/brightness, scene
  penalty/bonus, chat activity, event priority, spacing, and degraded sampling.
- Reset: candidate processed and published paths are deleted with other
  generated publishing artifacts.

### 7. Wrong vs Correct
#### Wrong
```python
package = package.model_copy(update={"cover_path": str(rank_1_path)})
```

This only preserves the legacy default and leaves operators with no ranked
candidate list, no published `cover-XX.jpg` copies, and no missing-output repair
signal for non-default covers.

#### Correct
```python
package = package.model_copy(
    update={
        "cover_path": candidates[0].path,
        "cover_candidates": candidates,
    }
)
```

Then publishing fills `published_cover_path` with `cover.jpg` for rank 1 and
`cover_candidates[*].published_path` with the matching `cover-XX.jpg` copies.

---

## Scenario: LLM Copywriter Semantic Assets

### 1. Scope / Trigger
- Trigger: Copywriting now has a cloud-LLM semantic phase before edit planning
  and a final publishing phase after export. This spans env config, subtitle and
  highlight inputs, a durable JSONL asset, edit-plan teaser selection, reset,
  status, and final publishing packages.

### 2. Signatures
- Postprocess order:
  ```text
  stage-hints-semantic -> segmenter -> subtitles -> highlight-planner
    -> copywriter-semantic -> edit-planner -> exporter -> copywriter
  ```
- Semantic manifest:
  ```text
  data/tmp/copywriter-semantic-assets.jsonl
  ```
- Service entrypoints:
  ```python
  CopywriterService.run_semantic(session_ids=None, match_indices=None, force_reprocess=False)
  CopywriterService.run_publishing(session_ids=None, match_indices=None, force_reprocess=False)
  CopywriterService.run(...)  # semantic phase, then publishing phase for CLI compatibility
  ```

### 3. Contracts
- Env:
  - `ARL_LLM_ENABLED`, default `0`; publish preset must not force-enable it.
  - `ARL_LLM_BASE_URL`, default `https://api.deepseek.com/v1`.
  - `ARL_LLM_API_KEY`, required only when `ARL_LLM_ENABLED=1`.
  - `ARL_LLM_MODEL`, default `deepseek-chat`.
  - `ARL_LLM_TIMEOUT_SECONDS`, clamped to at least `1.0`.
  - `ARL_LLM_MAX_RETRIES`, clamped to at least `0`.
  - `ARL_LLM_MAX_INPUT_CUES`, clamped to at least `20`.
  - `ARL_LLM_TEMPERATURE`, clamped to `[0.0, 1.5]`.
- Provider wire shape is OpenAI-compatible chat completions:
  ```http
  POST {base_url}/chat/completions
  Authorization: Bearer <ARL_LLM_API_KEY>
  Content-Type: application/json
  ```
- `CopywriterSemanticAsset` rows must include:
  `session_id`, `match_index`, `source_subtitle_path`,
  `source_highlight_plan_path`, `provider`, `model`, `prompt_fingerprint`,
  `input_fingerprint`, `result`, `token_usage`, `status`, `created_at`.
- `LlmCopywritingResult` constraints:
  - exactly 3 `title_candidates`
  - `recommended_title` <= 30 compact chars and not a raw leading subtitle copy
  - `cover_lines` count 2-4, each <= 10 compact chars
  - `summary` <= 96 compact chars
  - `tags` count 5-8
  - up to 3 `teaser_recommendations`
- Edit-planner may consume only
  `result.teaser_recommendations[*].source_start_seconds`,
  `source_end_seconds`, `hook_reason`, plus optional `result.hook_line`.
  Teaser windows must overlap an existing highlight window and be clipped or
  expanded inside that highlight window to the configured teaser minimum.

### 4. Validation & Error Matrix
| Condition | Behavior |
|-----------|----------|
| `ARL_LLM_ENABLED=0` | Skip semantic generation; use existing heuristic copy path. |
| Enabled but API key missing | Log `llm semantic skipped reason=missing_api_key`; do not crash. |
| Network/auth/provider failure | Retry up to configured limit, log fallback, and continue with heuristics. |
| Invalid JSON/schema from provider | Retry up to configured limit; no semantic asset is appended on exhaustion. |
| Recommended title equals raw leading subtitle excerpt | Treat as provider failure and retry/fallback. |
| Existing semantic asset has same model, prompt fingerprint, and input fingerprint | Reuse cache; do not re-call provider unless `force_reprocess=True`. |
| Teaser recommendation has no highlight-window overlap | Ignore it and use the existing teaser fallback path. |
| Postprocess reset targets a session | Remove matching semantic rows alongside edit/export/copy rows. |

### 5. Good/Base/Bad Cases
- Good: LLM enabled with a fake provider writes one semantic asset before
  edit-planner, edit-planner emits `llm_teaser`, and final copywriter package
  uses the LLM title, cover lines, summary, description, and tags.
- Base: LLM disabled by default keeps previous heuristic titles and existing
  tests unchanged.
- Bad: Calling the provider during final publishing when a matching semantic
  asset already exists, or letting a bad provider response crash postprocess.

### 6. Tests Required
- Config: defaults, env loading/clamping, and publish preset not enabling LLM.
- Copywriter: fake provider success, title not raw excerpt, semantic cache,
  `force_reprocess`, invalid schema fallback, and default disabled behavior.
- Editing: valid semantic teaser recommendations are used; invalid/no-overlap
  recommendations fall back to `highlight_keyword`.
- Postprocess: stage order includes `copywriter-semantic` before
  `edit-planner` and final `copywriter` after `exporter`.
- Reset/status: semantic rows are removed by reset and counted by status.

### 7. Wrong vs Correct
#### Wrong
```python
# Final publishing re-calls the provider and edit-planner has no semantic hints.
CopywriterService(settings).run()
EditingPlannerService(settings).run()
```

#### Correct
```python
copywriter = CopywriterService(settings)
copywriter.run_semantic()
EditingPlannerService(settings).run()
ExporterService(settings).run()
copywriter.run_publishing()
```

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
| `ARL_POSTPROCESS_PRESET` | string | "" | Set to `publish` to enable the publish edit preset during settings load |
| `ARL_POSTPROCESS_PUBLISH_PRESET` | bool | False | Boolean alias for the publish edit preset |
| `ARL_LLM_ENABLED` | bool | False | Enable the cloud LLM copywriter semantic phase; publish preset does not force this on |
| `ARL_LLM_BASE_URL` | string | `https://api.deepseek.com/v1` | OpenAI-compatible provider base URL |
| `ARL_LLM_API_KEY` | string | "" | Bearer token for the configured provider; required only when LLM is enabled |
| `ARL_LLM_MODEL` | string | `deepseek-chat` | Provider model name |
| `ARL_LLM_TIMEOUT_SECONDS` | float | `30.0` | Provider request timeout, clamped to at least `1.0` |
| `ARL_LLM_MAX_RETRIES` | int | `2` | Additional retries for provider/schema failures, clamped to at least `0` |
| `ARL_LLM_MAX_INPUT_CUES` | int | `160` | Maximum subtitle cues sent to the LLM prompt, clamped to at least `20` |
| `ARL_LLM_TEMPERATURE` | float | `0.4` | Provider temperature, clamped to `[0.0, 1.5]` |
| `ARL_EXPORT_FFMPEG_BITRATE` | string | None | Fixed average bitrate (e.g., "4000k") |
| `ARL_EXPORT_FFMPEG_MAX_BITRATE` | string | None | Maximum burst bitrate (e.g., "5000k") |
| `ARL_EXPORT_FFMPEG_CRF` | int | 18 | CRF value when bitrate not set |
| `ARL_EXPORT_FFMPEG_PRESET` | string | "slow" | CPU preset or NVENC p1-p7 |
| `ARL_EXPORT_USE_HARDWARE_ENCODING` | bool | False | Use NVENC if available |
| `ARL_EXPORT_AUDIO_LOUDNORM_ENABLED` | bool | False | Apply the configured audio loudness normalization filter during export |
| `ARL_EXPORT_AUDIO_LOUDNORM_FILTER` | string | `loudnorm=I=-16:TP=-1.5:LRA=11` | FFmpeg loudnorm filter string used when export audio loudnorm is enabled; blank values normalize to the default |
| `ARL_EXPORT_BURN_SUBTITLES` | bool | False | Burn subtitles into video instead of muxing soft subtitles |
| `ARL_EXPORT_USE_ASS_SUBTITLES` | bool | False | Convert real SRT subtitles to ASS sidecars for burn-in |
| `ARL_EXPORT_ASS_FONT_NAME` | string | "SimHei" | ASS style font name for burned subtitles |
| `ARL_EXPORT_ASS_FONT_SIZE` | int | 32 | ASS style font size, clamped to at least 1 |
| `ARL_EXPORT_ASS_MARGIN_V` | int | 110 | ASS vertical bottom margin, clamped to at least 0 |
| `ARL_EXPORT_ASS_OUTLINE` | int | 2 | ASS text outline width, clamped to at least 0 |
| `ARL_EXPORT_ASS_MAX_CHARS_PER_LINE` | int | 18 | ASS hard-wrap character count per visual line, clamped to at least 1 |
| `ARL_EXPORT_ASS_MAX_LINES` | int | 2 | Maximum ASS visual lines per Dialogue event before splitting the cue, clamped to at least 1 |
| `ARL_EXPORT_USE_EDIT_PLANS` | bool | False | Apply teaser-before-main edit plans |
| `ARL_EXPORT_USE_HIGHLIGHT_PLANS` | bool | False | Apply highlight condensing |
| `ARL_QUALITY_REPORT_SUBTITLE_ACTIVE_RATIO_MIN` | float | `0.55` | Minimum subtitle active ratio before the report emits a warning |
| `ARL_QUALITY_REPORT_LONG_NO_SUBTITLE_GAP_MIN_SECONDS` | float seconds | `8.0` | Minimum no-subtitle gap duration counted in the report's long-gap summary |
| `ARL_QUALITY_REPORT_MAX_SOURCE_GAP_SECONDS` | float seconds | `45.0` | Maximum retained source-time gap before the report emits a warning |
| `ARL_QUALITY_REPORT_TEASER_MIN_SEGMENTS` | int | `1` | Minimum teaser segment count before the report emits a warning |
| `ARL_QUALITY_REPORT_TEASER_MAX_SEGMENTS` | int | `3` | Maximum teaser segment count before the report emits a warning |
| `ARL_QUALITY_REPORT_SFX_MAX_HITS` | int | `6` | Maximum SFX hit count before the report emits a warning |
| `ARL_QUALITY_REPORT_ZOOM_MIN_SEGMENTS` | int | `1` | Minimum zoom/punch-in segment count before the report emits a warning |
| `ARL_QUALITY_REPORT_ZOOM_MAX_SEGMENTS` | int | `4` | Maximum zoom/punch-in segment count before the report emits a warning |
| `ARL_QUALITY_REPORT_TOP_NO_SUBTITLE_GAPS` | int | `5` | Number of longest no-subtitle gaps included in report details |
| `ARL_EDIT_PLANNER_ENABLED` | bool | False | Run edit-plan generation stage |
| `ARL_EDIT_TEASER_MAX_SEGMENTS` | int | 2 | Maximum teaser segments prepended before the main match |
| `ARL_EDIT_TEASER_MAX_TOTAL_SECONDS` | float | 45.0 | Maximum total teaser duration |
| `ARL_EDIT_TEASER_MIN_SEGMENT_SECONDS` | float | 3.0 | Minimum retained teaser segment duration |
| `ARL_EDIT_TEASER_DYNAMIC_BUDGET_ENABLED` | bool | True | Compute teaser budget from planned edit duration before applying max-total cap |
| `ARL_EDIT_TEASER_BUDGET_FRACTION_MIN` | float | 0.08 | Lower bound of dynamic teaser budget fraction |
| `ARL_EDIT_TEASER_BUDGET_FRACTION_MAX` | float | 0.12 | Upper bound of dynamic teaser budget fraction |
| `ARL_EDIT_TEASER_BUDGET_MIN_SECONDS` | float | 20.0 | Minimum dynamic teaser budget before operator max-total cap |
| `ARL_EDIT_TEASER_BUDGET_MAX_SECONDS` | float | 90.0 | Maximum dynamic teaser budget before operator max-total cap |
| `ARL_EDIT_TEASER_CANDIDATE_REASONS` | CSV | `highlight_keyword,condensed_key_event` | Highlight window reasons eligible for teaser selection |
| `ARL_EDIT_TEASER_FALLBACK_ENABLED` | bool | True | Use the top valid candidate when no teaser candidate clears the signal threshold |
| `ARL_EDIT_TRANSITION_MODE` | string | `none` | `none`, `black_card`, or reserved `crossfade`; publish preset defaults to `black_card` when unset |
| `ARL_EDIT_TRANSITION_DURATION_SECONDS` | float | 1.25 | Black-card transition duration, clamped to `[0.1, 10.0]` |
| `ARL_EDIT_TRANSITION_TEXT` | string | `Back to match start` | Fallback transition card text when LLM hook line is unavailable |
| `ARL_EDIT_TRANSITION_SFX_PATH` | path | None | Optional whoosh SFX file for transition start |
| `ARL_EDIT_TRANSITION_SFX_GAIN_DB` | float | -12.0 | Transition SFX gain in dB, clamped to `[-60.0, 6.0]` |
| `ARL_EDIT_ZOOM_ENABLED` | bool | False | Emit safe punch-in transforms for high-signal teaser/main segments |
| `ARL_EDIT_ZOOM_MODE` | string | `closeup` | `closeup` splits eligible segments into short transformed pieces; `legacy` restores whole-segment static punch-ins |
| `ARL_EDIT_ZOOM_TARGET` | string | "chat" | Punch-in target preset: `chat`, `center`, or `custom` |
| `ARL_EDIT_ZOOM_SCALE` | float | 1.2 | Punch-in scale, clamped to `[1.0, 1.5]` |
| `ARL_EDIT_ZOOM_X_ANCHOR` | float | 0.5 | Horizontal zoom crop anchor, clamped to `[0.0, 1.0]` |
| `ARL_EDIT_ZOOM_Y_ANCHOR` | float | 0.5 | Vertical zoom crop anchor, clamped to `[0.0, 1.0]` |
| `ARL_EDIT_ZOOM_MAX_SEGMENTS` | int | 1 | Maximum transformed close-up pieces or legacy transformed segments; publish preset defaults this to at least `3` when env is unset |
| `ARL_EDIT_ZOOM_CLOSEUP_SECONDS` | float | 6.0 | Close-up window duration cap in close-up mode, clamped to `3..8` seconds |
| `ARL_EDIT_ZOOM_EASE_SECONDS` | float | 0.4 | Ease-in and ease-out duration stored on punch-in transforms, clamped to `[0.0, 1.0]` |
| `ARL_EDIT_ZOOM_MIN_INTERVAL_SECONDS` | float | 25.0 | Minimum spacing between selected close-up trigger timestamps |
| `ARL_EDIT_ZOOM_CHAT_BURST_ENABLED` | bool | True | Enable best-effort bottom-left chat-region frame-diff close-up triggers |
| `ARL_EDIT_ZOOM_CHAT_BURST_SAMPLE_INTERVAL_SECONDS` | float | 0.5 | Frame sampling interval for chat-burst detection, clamped to at least `0.1` |
| `ARL_EDIT_ZOOM_CHAT_BURST_THRESHOLD` | float | 0.08 | Mean grayscale crop-diff threshold for chat-burst triggers, clamped to `[0.0, 1.0]` |
| `ARL_EDIT_ZOOM_MAX_DURATION_SECONDS` | float | 30.0 | Legacy-mode maximum punch-in duration on a main timeline segment, clamped to at least 1 second |
| `ARL_EDIT_AUDIO_MIXING_ENABLED` | bool | False | Emit local BGM/SFX audio instructions into edit plans |
| `ARL_EDIT_SKIP_BGM_WHEN_SOURCE_HAS_MUSIC` | bool | True | Skip adding edit BGM when the source recording already appears to contain a persistent music bed |
| `ARL_EDIT_BGM_LIBRARY_PATH` | path | None | Optional JSON manifest for automatic local BGM matching; publish preset defaults it to `data/bgm/library.json` when no explicit BGM path/library is configured |
| `ARL_EDIT_BGM_PATH` | path | None | Explicit local background-music file path |
| `ARL_EDIT_BGM_GAIN_DB` | float | -28.0 | BGM gain in dB, clamped to `[-60.0, 0.0]` |
| `ARL_EDIT_BGM_MULTI_PHASE_MIN_SECONDS` | float | `600.0` | Minimum BGM-active duration before library-backed BGM may request `laning -> momentum -> climax` phases |
| `ARL_EDIT_BGM_SWITCH_MIN_GAP_SECONDS` | float | `60.0` | Minimum gap from BGM edges and adjacent BGM phase switches, clamped to at least 0 |
| `ARL_EDIT_BGM_CROSSFADE_SECONDS` | float | `2.0` | Total overlap between adjacent BGM phase beds, clamped to `[1.0, 2.0]` |
| `ARL_EDIT_BGM_SOURCE_MUSIC_PADDING_SECONDS` | float | `2.0` | Padding added around mapped source-music avoidance spans before subtracting BGM beds |
| `ARL_EDIT_BGM_SOURCE_MUSIC_MAJORITY_THRESHOLD` | float | `0.60` | Rendered BGM-active source-music coverage above which the planner skips BGM for the match, clamped to `[0.0, 1.0]` |
| `ARL_EDIT_SFX_PATH` | path | None | Explicit local sound-effect file path; when unset, audio mixing can use generated `coin.wav` for eligible SFX hits |
| `ARL_EDIT_SFX_GAIN_DB` | float | -12.0 | SFX gain in dB, clamped to `[-60.0, 6.0]` |
| `ARL_EDIT_SFX_LIBRARY_PATH` | path | `data/sfx/library.json` | Optional JSON manifest for local SFX categories (`kill_coin`, `multi_kill`, `transition_whoosh`, `teaser_impact`) |
| `ARL_EDIT_SFX_TIMING_OFFSET_SECONDS` | float | `0.0` | Offset applied after mapping a KDA source timestamp into rendered timeline seconds |
| `ARL_EDIT_SFX_MIN_INTERVAL_SECONDS` | float | `20.0` | Minimum interval between kill SFX hits, clamped to at least 0 |
| `ARL_EDIT_SFX_MAX_HITS` | int | `6` | Maximum kill SFX hits per edit plan, clamped to at least 0 |
| `ARL_EDIT_SFX_KDA_ALIGNMENT_ENABLED` | bool | True | Align kill SFX to parsed `kda_change` timestamps before falling back to segment starts |
| `ARL_EDIT_SFX_MULTIKILL_WINDOW_SECONDS` | float | `8.0` | Subtitle keyword search window around a KDA kill event for multi-kill variant selection |
| `ARL_HIGHLIGHT_PLANNER_ENABLED` | bool | False | Run highlight detection stage |
| `ARL_HIGHLIGHT_CONDENSED_TARGET_DURATION_RANGE` | `min,max` minutes | `7,20` | Global dynamic condensed target span and max continuous content cap |
| `ARL_HIGHLIGHT_CONDENSED_HIGH_DENSITY_DURATION_RANGE` | `min,max` minutes | `16,20` | Target duration range for high composite density matches |
| `ARL_HIGHLIGHT_CONDENSED_MID_DENSITY_DURATION_RANGE` | `min,max` minutes | `10,16` | Target duration range for mid composite density matches |
| `ARL_HIGHLIGHT_CONDENSED_LOW_DENSITY_DURATION_RANGE` | `min,max` minutes | `7,11` | Target duration range for low composite density matches |
| `ARL_HIGHLIGHT_CONDENSED_BORING_GAP_THRESHOLD_SECONDS` | float | 45.0 | Maximum source-time gap allowed between adjacent condensed output windows before continuity bridges are inserted |
| `ARL_HIGHLIGHT_CONDENSED_COMPOSITE_TRIM_ENABLED` | bool | True | Enable internal low-value gap compression after KDA/speech protection |
| `ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_TRIM_SECONDS` | float | 8.0 | Minimum subtitle-free internal gap considered for composite compression |
| `ARL_HIGHLIGHT_CONDENSED_INTERNAL_GAP_KEEP_SECONDS` | float | 3.0 | Context kept on each side of a removable internal gap and around protected internal cues |
| `ARL_HIGHLIGHT_CONDENSED_CONTINUITY_BRIDGE_SECONDS` | float | 3.0 | Short continuity bridge/lead-in snippet length, independent of edge context length |
| `ARL_HIGHLIGHT_CONDENSED_START_EDGE_SECONDS` | float or unset | unset normally; publish preset sets `1.0` | Optional shorter match-start context. When unset, start context follows `ARL_HIGHLIGHT_KEEP_EDGE_SECONDS`; publish uses a short marker to avoid long fountain/scoreboard openings while preserving edit-plan edge validity |
| `ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_TAIL_SECONDS` | float | 40.0 | Maximum short narration tail retained after key/tactical action windows |
| `ARL_HIGHLIGHT_CONDENSED_ACTION_RESOLUTION_GAP_SECONDS` | float | 8.0 | Maximum subtitle gap considered continuous action-resolution narration |
| `ARL_HIGHLIGHT_CONDENSED_KDA_EVENT_DETECTION_ENABLED` | bool | True | Preserve detected KDA kill/death changes as condensed key events |
| `ARL_HIGHLIGHT_CONDENSED_KDA_CROP_REGION` | `x,y,w,h` | `1665,0,85,32` | 1080p top-right player KDA crop |
| `ARL_HIGHLIGHT_CONDENSED_KDA_SAMPLE_INTERVAL_SECONDS` | float | 10.0 | Sampling interval for KDA event detection |
| `ARL_HIGHLIGHT_CONDENSED_KDA_MAX_READING_GAP_SECONDS` | float | 120.0 | Maximum gap between stable KDA readings that may still create a kill/death event |
| `ARL_HIGHLIGHT_CONDENSED_KDA_KILL_PREROLL_SECONDS` | float | 15.0 | Extra context before a kill-only KDA change |
| `ARL_HIGHLIGHT_CONDENSED_KDA_DEATH_PREROLL_SECONDS` | float | 30.0 | Extra context before a death KDA change |
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

**Last Updated**: 2026-06-27 (Task: publish-edit-preset)
