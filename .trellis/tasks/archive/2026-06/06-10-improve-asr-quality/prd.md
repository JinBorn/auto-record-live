# Improve ASR quality

## Goal

Improve subtitle transcript quality for highlight planning, copywriting, and editing decisions while keeping final video exports free of burned-in subtitles by default.

## Requirements

- Keep ASR as an internal planning signal; final exports should continue to default to no burned-in subtitles via `ARL_EXPORT_BURN_SUBTITLES=0`.
- Improve local `faster-whisper` quality on the current GTX 1650 4GB machine without making the unattended pipeline fragile.
- Support a GPU-friendly CUDA compute configuration that can use quantized inference while preserving CPU fallback behavior.
- Add optional ASR audio preprocessing before transcription when enabled. The preprocessing must be scoped to the match boundary and must degrade to the original recording input if `ffmpeg` or preprocessing fails.
- Keep subtitle generation optional and resilient: provider mismatch, missing dependencies, GPU failures, media errors, or preprocessing failures must not crash postprocess.
- Evaluate third-party ASR APIs as a possible future provider, but do not make an external service the default dependency in this task.

## Acceptance Criteria

- [x] Operators can configure a separate CUDA compute type for Whisper without forcing the same compute type onto CPU fallback.
- [x] Operators can enable ASR preprocessing through env config.
- [x] When preprocessing succeeds, `faster-whisper` transcribes the preprocessed audio clip instead of the full video path while retaining relative boundary timestamps in SRT output.
- [x] When preprocessing is disabled, missing, or fails, existing transcription behavior remains compatible.
- [x] Tests cover CUDA compute selection, env loading, preprocessing success, and preprocessing fallback.
- [x] Full test suite passes.

## Notes

- Current code already uses `word_timestamps=True`, `clip_timestamps=[boundary.start, boundary.end]`, CUDA-first auto mode, and CPU fallback after CUDA init/lazy runtime failures.
- Current default `ARL_WHISPER_MODEL_SIZE=small` is fast but too inaccurate for visible subtitles.
- Third-party ASR can be useful after extracting/enhancing audio, but "free" APIs usually have quota, privacy, upload latency, or reliability limits. It should be implemented as an explicit provider with credentials and timeout/fallback controls, not as a silent default.
