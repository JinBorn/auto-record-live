# Improve ASR quality - implementation

## Checklist

- [x] Read backend subtitle/export specs before editing.
- [x] Add subtitle config fields and env loading:
  - `cuda_compute_type`
  - `preprocess_audio`
  - preprocessing filter/timeout settings as needed.
- [x] Update `SubtitleService` model candidate selection to support separate CUDA and CPU compute types.
- [x] Add optional ffmpeg audio preprocessing helper with fallback to original input.
- [x] Adjust transcription timestamp handling for preprocessed boundary-scoped audio.
- [x] Update `.env.example` with recommended GTX 1650 ASR settings while keeping export burn-in disabled by default.
- [x] Add/extend tests for config loading, compute selection, preprocessing success, and fallback.
- [x] Run targeted tests:
  - `python -m pytest tests/test_config.py tests/pipeline/test_subtitles_service.py -q`
- [x] Run full tests:
  - `python -m pytest tests`
- [x] Run local ASR probe:
  - After CUDA Toolkit 12.8 install, `small` + preprocessing completed a 60-second probe with `device=cuda`, `compute_type=int8_float16`.
  - CTranslate2 reports one CUDA device and supports `int8_float16`.
  - `medium` did not complete a 60-second probe within 15 minutes, so it is not the unattended default.

## Risk points

- `faster-whisper` segment timestamps are absolute for video input with `clip_timestamps`, but relative for extracted boundary WAV. The service must pass the correct timestamp origin into SRT entry construction.
- ffmpeg preprocessing must not become a hard dependency for subtitle generation.
- GPU OOM or CUDA runtime failures must keep current CPU fallback behavior in `auto` mode.
