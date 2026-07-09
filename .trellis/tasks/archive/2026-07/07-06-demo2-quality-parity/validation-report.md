# Validation Report — demo2-quality-parity (final integration review)

Generated: 2026-07-10

Scope: cross-child acceptance criteria from `prd.md`, executed after all 8
child tasks shipped and archived.

- `session-20260617073649-4b5ec478` m02, `session-20260617073651-cf11bf9e`
  m02-04, publish preset, whisper `medium` (CUDA int8_float16), DeepSeek LLM
  copywriting, user-supplied SFX/BGM libraries.
- Fresh-streamer check: `session-20260702092321-bc90812b` (挖机牧魂人) m01-03.

## Acceptance Results

| Criterion | Threshold | Result |
|---|---|---|
| Teaser present | >= 3 of 4 samples | **4/4** (1-3 segments each, 36-45s budget) |
| Subtitle active ratio | >= 55% | **83.8% - 87.3%** (07-02 baseline: 39-47%) |
| BGM beds (no dominant source music) | >= 2 | **3-5 per export**, multi-phase + crossfade |
| Kill SFX vs `kda_change` | within ±1s | **all coin hits delta ≈ 0.0s** (library `coin-single.wav`) |
| Close-ups per export | >= 2 where triggers exist | **3 per export**, eased punch-in |
| Titles / cover lines | not raw ASR excerpts | **4/4 `llm_generated`**, `title_equals_raw_leading_subtitle=false` |
| Fresh streamer copy (LLM path) | readable | **3/3 `llm_generated`** (e.g. 「13分钟180刀！打野教学局」) |
| pytest | pass, no regression | **686 passed** |
| KDA uncovered | 0 | **0 on all 4 samples** |
| Quality-report warnings | none | **0 warnings on all 4 samples** |

Final per-sample metrics (quality-report 2026-07-10):

| Sample | Export min | Subtitle active | Teaser | BGM | SFX | Zoom | KDA uncovered | Warnings |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 4b5ec478 m02 | 17.74 | 84.5% | 2 | 5 | 7 | 3 | 0/9 | 0 |
| cf11bf9e m02 | 17.77 | 85.0% | 2 | 3 | 7 | 3 | 0/8 | 0 |
| cf11bf9e m03 | 22.86 | 87.3% | 3 | 4 | 3 | 3 | 0/6 | 0 |
| cf11bf9e m04 | 23.70 | 83.8% | 1 | 3 | 7 | 3 | 0/11 | 0 |

Example copy upgrade (vs 07-02 mojibake baseline):
`堆場式是咋的 就對面的人他也會...` → 「小丑王者怒喷猫队友：你解不掉E？」
(cover: 猫队友 / 解不掉E？ / 气到破防).

## Integration Defects Found And Fixed During This Review

1. **Late-teaser ffmpeg memory explosion** (`exporter/service.py`): a teaser
   sourced near the boundary end on the shared single input made ffmpeg buffer
   every retained main frame (23GB RSS, froze the whole machine 3 times; the
   GPU "disappearing" from Task Manager was an OOM side effect, initially
   misdiagnosed as a driver failure). Teaser segments now get dedicated seeked
   inputs; export runs at <1GB RSS and ~4 min per sample.
2. **`kda_change` cue pipeline gap** (`shared/contracts.py`,
   `highlights/service.py`, `editing/service.py`): SFX/zoom KDA alignment read
   `kda_change` cues from SRT files, but no stage ever wrote them — planner
   OCR events lived only in memory, so alignment silently never worked on real
   exports. Events are now persisted as `HighlightPlanAsset.kda_events` and
   merged by the edit planner.
3. **Segment-start SFX fallback noise** (`editing/service.py`): with real kill
   events available, filler segment-start coins played 35-277s away from any
   kill. Fallback now applies only when no kill event maps onto the timeline.
4. **Quality-report false positives** (`quality_report/service.py`): KDA
   coverage did not merge zoom-split adjacent spans (uncovered false alarms at
   every close-up), and transition whoosh counted against the kill-SFX limit.
5. **`data/sfx/library.json` trailing comma** (user asset, fixed in place):
   manifest parse error silently degraded all SFX to synthetic coin.

Notes:
- Plan durations (17.7-23.7 min) exceed the analyzed condensed target
  (~10.3 min) because speech-boundary protection and KDA preservation extend
  windows; no threshold violation is emitted for this and the demo2 reference
  is itself long-form. Recorded as future tuning headroom, not a defect.
- Spec: quality thresholds, the keyword-overfit lesson, and the cross-stage
  lessons above are captured in `.trellis/spec/backend/editing-quality.md`.
