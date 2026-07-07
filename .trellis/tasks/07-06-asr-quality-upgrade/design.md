# ASR quality upgrade design

## Architecture

This task upgrades the existing `SubtitleService` rather than adding a new
pipeline stage. The subtitle stage remains the single owner of ASR generation,
SRT persistence, subtitle audit rows, and best-effort stage-signal ingestion.

The change has five additive parts:

1. **Publish-aware model selection**
   - Keep explicit `ARL_WHISPER_MODEL_SIZE` as the highest-precedence setting.
   - When publish preset is active and the device is `auto` or `cuda`, default
     the model tier to `medium` instead of `small`.
   - CPU-only default remains `small` to avoid unexpected slow local runs.
   - Model fallback becomes model-tier + device aware:
     configured/preset tier -> `medium` -> `small`, combined with the existing
     CUDA -> CPU device fallback.

2. **Text normalization pipeline**
   - Normalize accepted ASR text before SRT persistence.
   - First apply optional OpenCC Simplified Chinese conversion.
   - Then apply exact term fixes from a user-editable JSON map.
   - The same normalized SRT flows downstream to stage-signal extraction,
     highlight planning, copywriter, edit-planner scoring, and ASS conversion.

3. **Domain prompt**
   - Add a user-editable initial prompt file at `data/asr/initial-prompt.txt`.
   - Pass the assembled prompt to `faster-whisper` as `initial_prompt` when the
     file exists and contains non-empty text.
   - Cap prompt length by config so operators can add terms without accidentally
     sending an oversized prompt.

4. **ASR tuning knobs**
   - Add env-backed `beam_size` and selected VAD controls.
   - Pass them through `model.transcribe(...)` only for faster-whisper.
   - Defaults are conservative and can be adjusted after a measured run.

5. **Validation and documentation**
   - Add deterministic unit tests for prompt assembly, normalization, term
     fixes, fallback ordering, VAD/beam kwargs, and missing OpenCC behavior.
   - Add README/.env documentation for the new files and env keys.
   - Run one measured subtitle regeneration on a validation session and record
     quality-report output in `validation-report.md`.

## Module Boundaries

- `src/arl/config.py`
  - Owns env parsing, default paths, publish preset model override, and clamps.
- `src/arl/subtitles/service.py`
  - Owns prompt assembly, ASR text normalization application, model fallback
    ordering, and `faster-whisper` transcribe kwargs.
- `src/arl/subtitles/normalization.py`
  - Owns optional OpenCC loading/conversion and exact term-fix application.
  - Keeps dependency failures local and testable without loading Whisper.
- `pyproject.toml`
  - Adds OpenCC to the `subtitles` optional dependency extra.
- `README.md` / `.env.example`
  - Documents operator-facing env and file formats.

Do not move subtitle persistence into a shared helper. `SubtitleAsset(format="srt")`
stays the durable ASR interchange contract.

## Data Flow

```text
RecordingAsset + MatchBoundary
  -> resolve_recording_window(...)
  -> optional audio preprocess WAV
  -> faster-whisper transcribe(initial_prompt, beam_size, vad_filter, vad_parameters)
  -> word-timestamp cue extraction
  -> OpenCC zh-Hans normalization
  -> exact term fixes
  -> data/processed/<session>/match-NN.srt
  -> subtitle-assets.jsonl + subtitles-events.jsonl
  -> stage-signals-from-subtitles / highlight-planner / copywriter / exporter
```

## Durable and Config Contracts

New user-editable files:

```text
data/asr/initial-prompt.txt
data/asr/term-fixes.json
```

`term-fixes.json` shape:

```json
{
  "bad term": "good term",
  "wrong champion name": "correct champion name"
}
```

New settings fields:

```python
class SubtitleSettings(BaseModel):
    ...
    initial_prompt_path: Path | None = Path("data/asr/initial-prompt.txt")
    initial_prompt_max_chars: int = 1200
    term_fixes_path: Path | None = Path("data/asr/term-fixes.json")
    opencc_enabled: bool = True
    beam_size: int = 5
    vad_filter: bool = True
    vad_min_silence_duration_ms: int = 300
    vad_speech_pad_ms: int = 250
```

Environment keys:

- `ARL_ASR_INITIAL_PROMPT_PATH`
- `ARL_ASR_INITIAL_PROMPT_MAX_CHARS`
- `ARL_ASR_TERM_FIXES_PATH`
- `ARL_ASR_OPENCC_ENABLED`
- `ARL_WHISPER_BEAM_SIZE`
- `ARL_WHISPER_VAD_FILTER`
- `ARL_WHISPER_VAD_MIN_SILENCE_DURATION_MS`
- `ARL_WHISPER_VAD_SPEECH_PAD_MS`

Publish preset behavior:

- If `ARL_WHISPER_MODEL_SIZE` is explicitly set, preserve it.
- Otherwise, publish preset sets `subtitles.model_size="medium"` when
  `subtitles.device in {"auto", "cuda"}`.
- Publish preset does not force `large-v3`.

## Fallback and Error Behavior

- Missing OpenCC dependency or conversion failure:
  - Log one compact warning per `SubtitleService` instance.
  - Continue with unconverted text and still apply term fixes.
- Missing prompt file:
  - No prompt is passed, preserving current behavior.
- Missing or invalid term-fix file:
  - Log one compact warning and use an empty map.
- Model load/transcribe failure:
  - Preserve the existing placeholder/audit behavior.
  - Try the next model tier/device candidate only for model/runtime failures,
    not media-specific failures such as missing files or invalid media.
- Fallback ordering should avoid duplicate retries:
  - Deduplicate identical `(model_size, device, compute_type)` candidates.
  - Once a CUDA config fails, keep the existing batch-level CUDA disablement.

## Compatibility

- Default non-publish CPU behavior remains compatible with current tests.
- Existing `arl subtitles` CLI options and `SubtitleAsset` rows remain unchanged.
- Downstream stages read normalized SRT text automatically; no downstream model
  schema change is required.
- OpenCC is optional at runtime. It is installed by `pip install -e ".[subtitles]"`
  but code must tolerate it being absent.

## Tradeoffs

- `medium` is limited to publish/CUDA defaulting rather than global defaulting
  because CPU-only unattended runs would become significantly slower.
- Exact term fixes are deliberately simple. Regex or fuzzy replacement would be
  more powerful but risk mutating correct subtitles unpredictably.
- VAD defaults are exposed and tested first; final numeric tuning should be
  based on the validation session rather than guessed globally.

## Rollback

- Set `ARL_WHISPER_MODEL_SIZE=small` to restore the old model tier.
- Set `ARL_ASR_OPENCC_ENABLED=0` to disable OpenCC normalization.
- Point `ARL_ASR_TERM_FIXES_PATH` and `ARL_ASR_INITIAL_PROMPT_PATH` to empty or
  missing files to disable those features.
- Set `ARL_WHISPER_VAD_FILTER=0` to preserve pre-VAD transcribe behavior if the
  measured run regresses cue coverage.
