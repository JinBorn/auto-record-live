# ASR quality upgrade implementation plan

## Checklist

1. Config and dependencies
   - Add OpenCC to `pyproject.toml` `subtitles` extra.
   - Add subtitle settings for prompt path/max chars, term-fix path, OpenCC
     enablement, `beam_size`, and VAD parameters.
   - Load new env vars in `load_settings()`.
   - Update `apply_publish_preset()` so CUDA/auto publish runs default to
     `medium` only when `ARL_WHISPER_MODEL_SIZE` was not explicitly set.
   - Add config tests for env parsing/clamping and publish preset behavior.

2. Normalization and term fixes
   - Add a subtitle-local normalization module.
   - Implement optional OpenCC `t2s` conversion with one-warning fallback.
   - Implement exact term-fix JSON loading and idempotent replacement.
   - Add unit tests for conversion success, missing OpenCC, invalid JSON,
     missing file, and replacement order.

3. Prompt assembly
   - Add helper to read `data/asr/initial-prompt.txt` with UTF-8.
   - Trim/cap prompt text by `initial_prompt_max_chars`.
   - Pass `initial_prompt` only when non-empty.
   - Add transcribe-kwargs tests.

4. Model fallback ordering
   - Extend `WhisperModelConfig` or candidate representation to include
     `model_size`.
   - Generate ordered candidates:
     configured/preset model -> `medium` -> `small`, deduped, each combined
     with existing device/compute candidates.
   - Keep CUDA failure disabling and CPU fallback semantics intact.
   - Ensure model cache key includes model size.
   - Add tests for CUDA init/OOM fallback from `large-v3` or `medium` to
     smaller tier and CPU fallback in auto mode.

5. VAD/beam tuning
   - Pass `beam_size`, `vad_filter`, and `vad_parameters` to faster-whisper.
   - Use conservative defaults:
     - `beam_size=5`
     - `vad_filter=True`
     - `vad_min_silence_duration_ms=300`
     - `vad_speech_pad_ms=250`
   - Add tests asserting kwargs are passed and can be disabled by env.

6. Documentation
   - Update `.env.example` with new ASR settings and file paths.
   - Update `README.md` with prompt/term-fix file formats and publish ASR
     defaults.
   - Update backend spec with the executable ASR quality contract.

7. Validation
   - Run focused tests:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py tests/test_config.py
     ```
   - Run full suite:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests
     ```
   - If a suitable validation recording is available, regenerate subtitles for
     one scoped session/match and run quality report before/after. Record active
     ratio, longest no-subtitle gap, model/device/compute, elapsed time, and any
     observable VRAM notes in `validation-report.md`.

## Review Gate Before Start

- Confirm this task should implement the publish/CUDA default model upgrade to
  `medium`, while leaving CPU-only default at `small`.
- Confirm `large-v3` remains opt-in and fallback-only, not a publish default.
- Confirm exact string term fixes are sufficient for v1.

## Risky Files

- `src/arl/subtitles/service.py`
  - Model/cache fallback logic is already subtle; keep changes targeted and
    covered by tests.
- `src/arl/config.py`
  - Publish preset should not override explicit operator env/config choices.
- `pyproject.toml`
  - Optional dependency updates should stay under `[project.optional-dependencies].subtitles`.

## Rollback Points

- Revert publish-preset model defaulting first if performance regresses.
- Disable OpenCC and term fixes independently via env/path overrides.
- Revert VAD defaults to disabled if validation shows cue loss.

