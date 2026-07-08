# ASR quality upgrade

## Goal

Raise subtitle transcription quality and coverage on the local GTX 1650 4GB
machine: upgrade the default whisper model tier, normalize output to
Simplified Chinese, inject domain vocabulary, and tune VAD so long
no-subtitle gaps shrink.

## User Value

Subtitles are both a viewer-facing feature and the upstream input for
copywriting, teaser scoring, and highlight keywords. Current output (whisper
`small`) shows traditional-Chinese mojibake ("堆場式", "對面", "為什麼"),
wrong domain words, 39-47% active coverage, and gaps up to 42.5s.

## Hardware Assessment (measured 2026-07-06)

- GTX 1650 4096MiB, ~630MiB already used by display; ctranslate2 4.7.2 sees
  the CUDA device; faster-whisper 1.2.1 installed.
- whisper `medium` int8_float16 needs roughly 1.5-2GB VRAM: safe default.
- `large-v3` int8 needs roughly 3GB+: does NOT fit reliably alongside display
  and possible concurrent NVENC recording; opt-in only, with automatic
  fallback on CUDA OOM using the existing disabled-config fallback chain.

## Requirements

- Default model tier for the publish flow becomes `medium` when a CUDA device
  is present, retaining `small` for CPU-only machines; explicit
  `ARL_WHISPER_MODEL_SIZE` always wins. Fallback chain on load/OOM failure:
  configured tier -> medium -> small (existing mechanism extended, with clear
  logs).
- Simplified-Chinese normalization: convert zh output to zh-Hans (OpenCC,
  added to the `subtitles` optional dependency extra). Applies to SRT/ASS
  content and everything downstream (copywriter excerpts, teaser scoring).
  Skipped gracefully when the dependency or conversion fails.
- Domain vocabulary prompt: pass an `initial_prompt` assembled from a
  user-editable file (proposed: `data/asr/initial-prompt.txt`) seeded with LoL
  terms (英雄/装备/技能/播报词) plus streamer catchphrases; capped to the
  model's prompt budget; absent file means no prompt (current behavior).
- Post-ASR term-fix map: `data/asr/term-fixes.json` (exact string -> replacement)
  applied to cue text before persisting SRT; ships with a small default set of
  observed errors; user-extensible; idempotent.
- VAD/segment tuning: expose (env) and tune `vad_filter` parameters
  (min silence duration, speech pad) plus `beam_size` so that silent
  gameplay stretches still yield nearby speech cues; document chosen defaults
  and their measured effect.
- Performance guardrail: one measured transcription run of a >=10min match on
  this machine recording the realtime factor and peak VRAM (as observable);
  postprocess remains usable while recorder may be active (no VRAM
  overcommit by default).
- No model downloads inside tests; normalization/term-fix/prompt logic tested
  on text fixtures.

## Out Of Scope

- Fine-tuned or third-party zh whisper checkpoints (e.g. Belle) — revisit only
  if `medium` + normalization still misses the coverage target.
- Speaker diarization, punctuation models, karaoke timing.

## Acceptance Criteria

- [ ] Regenerating subtitles for one 07-02 validation session shows: subtitle
      active ratio >=55% (was 39-47%), zero traditional-only characters in
      output cues, and a reduced longest no-subtitle gap, as measured by the
      quality-report CLI.
- [ ] CUDA OOM (simulated in tests) falls back down the model chain without
      crashing the subtitles stage.
- [ ] `pip install -e ".[subtitles]"` pulls OpenCC; a missing OpenCC at
      runtime degrades to un-normalized output with a single warning log.
- [ ] Term-fix map and initial-prompt file are documented in README (paths,
      format, examples) and applied when present.
- [ ] Existing subtitle service tests keep passing; new tests cover
      normalization, term fixes, prompt assembly, and fallback ordering.

## Notes

- Complex task: add `design.md` (model selection/fallback flow, normalization
  insertion point, file formats) and `implement.md` before start.
- Coordinate with `07-06-export-quality-report-cli` for the coverage metrics
  used in acceptance.
