# Improve ASR quality - design

## Current state

- `SubtitleService` owns ASR and emits one `SubtitleAsset` plus one `SubtitleAuditEvent` per match.
- `faster-whisper` is the only real ASR provider. Unsupported providers or failures degrade to deterministic placeholder SRT.
- `ARL_WHISPER_DEVICE=auto` tries CUDA first, then CPU, and disables CUDA for the rest of the batch after CUDA failures.
- `model.transcribe(...)` already receives `word_timestamps=True` and `clip_timestamps=[boundary_start, boundary_end]`.

## Local ASR improvements

### Separate CUDA compute type

Add `SubtitleSettings.cuda_compute_type`, loaded from `ARL_WHISPER_CUDA_COMPUTE_TYPE`.

- Default: `auto`
- When `ARL_WHISPER_COMPUTE_TYPE=auto`, CUDA uses `ARL_WHISPER_CUDA_COMPUTE_TYPE` if set, otherwise `float16`; CPU uses `ARL_WHISPER_CPU_COMPUTE_TYPE`.
- When legacy `ARL_WHISPER_COMPUTE_TYPE` is explicitly set, it remains an override for both CUDA and CPU for backward compatibility.

Recommended stable GTX 1650 configuration:

```env
ARL_WHISPER_MODEL_SIZE=small
ARL_WHISPER_DEVICE=auto
ARL_WHISPER_COMPUTE_TYPE=auto
ARL_WHISPER_CUDA_COMPUTE_TYPE=int8_float16
ARL_WHISPER_CPU_COMPUTE_TYPE=int8
ARL_ASR_PREPROCESS_AUDIO=1
```

`medium` can be tested manually for quality, but should not be the unattended default on this machine unless a short probe finishes quickly.

### Optional ASR audio preprocessing

Add an opt-in preprocessing step controlled by `ARL_ASR_PREPROCESS_AUDIO`.

When enabled and `ffmpeg` exists:

1. Extract only the match boundary audio into `data/tmp/asr-audio/<session>/match-XX.wav`.
2. Apply conservative filters:
   - mono 16 kHz PCM for ASR efficiency
   - high-pass/low-pass to remove rumble and high-frequency noise
   - loudness normalization
   - optional afftdn denoise level
3. Call `faster-whisper` on the generated WAV without `clip_timestamps`, because the WAV is already boundary-scoped.
4. Build SRT entries relative to zero for preprocessed audio; keep original video timestamps for non-preprocessed input.

Fallback:

- Missing `ffmpeg`, command error, timeout, or missing output logs a compact skip reason and transcribes the original video path.
- No subtitle fallback is emitted solely because preprocessing failed.

## Third-party ASR option

Third-party ASR should be a future explicit provider, not part of this local-first default path.

Candidate approaches:

- Cloud provider API with a free tier: good recognition quality, but requires credentials, uploads audio, and has quota/latency constraints.
- Browser or desktop app transcription export: low integration risk, but less automatable.
- Self-hosted services: avoids upload/privacy issues but still requires local compute or a separate machine.

If implemented later, add a provider interface with:

- `ARL_SUBTITLE_PROVIDER=<provider>`
- provider-specific API key/env settings
- timeout and max audio duration controls
- same `SubtitleAsset` and `SubtitleAuditEvent` contracts
- deterministic placeholder fallback on any provider failure

## Compatibility

- Existing default behavior remains unchanged unless new env vars are set.
- Final exporter subtitle burn-in remains independently controlled by `ARL_EXPORT_BURN_SUBTITLES`.
- Audio preprocessing artifacts are runtime files under `data/tmp/` and are not tracked by git.

## Local probe result

- After installing CUDA Toolkit 12.8, CTranslate2 detects one CUDA device and supports `int8_float16`.
- `faster-whisper` CUDA initialization succeeded with the cached tiny model.
- `small` + preprocessing completed a 60-second probe on `device=cuda`, `compute_type=int8_float16`, with no CPU fallback.
- `medium` did not complete a 60-second probe within 15 minutes, so it is not safe as the unattended default on the current setup.
