# Design: faster-whisper subtitles production hardening

## Architecture

Three additive layers in `src/arl/subtitles/` + supporting changes in
`pyproject.toml`, `src/arl/config.py`, three PowerShell launchers, and spec docs.

```
SubtitleService.run()
  ├── per boundary loop
  │     ├── _resolve_recording_path()           (unchanged)
  │     ├── _transcribe_boundary()              ← (L1) HF_HOME env set; (L2) confidence gate
  │     │     ├── os.environ.setdefault("HF_HOME", ...)
  │     │     ├── from faster_whisper import WhisperModel (lazy)
  │     │     ├── model.transcribe(...) → (segments, info)
  │     │     ├── if info.language_probability < threshold → return []
  │     │     └── return entries
  │     ├── _write_subtitle()                   (unchanged shape; placeholder vs real)
  │     └── _emit_subtitle_audit()              ← (L3) new method
  └── ...
```

- **L1 (cache dir)**: pin `HF_HOME` to `ARL_WHISPER_MODEL_CACHE_DIR` before the lazy import. Use `setdefault` so an operator-set `HF_HOME` wins.
- **L2 (quality gate)**: `_transcribe_boundary()` now keeps `info`; the `language_probability` comparison runs after a successful transcribe but before returning entries. Below threshold → log + return `[]` → placeholder branch in `_write_subtitle()`.
- **L3 (audit)**: every match emits exactly one `SubtitleAuditEvent` to `subtitles-events.jsonl`. Success = `subtitle_transcribe_succeeded` with `language`+`language_probability`; any fallback path = `subtitle_fallback_placeholder` with the matching `reason`.

## Data flow / contracts

### `SubtitleSettings` extension (`src/arl/config.py:129`)

```python
class SubtitleSettings(BaseModel):
    enabled: bool = True
    provider: str = "faster-whisper"
    model_size: str = "small"
    language: str = "zh"
    # NEW
    model_cache_dir: Path = Path("data/tmp/whisper-models")
    min_language_probability: float = 0.5
```

`load_settings()` reads `ARL_WHISPER_MODEL_CACHE_DIR` and
`ARL_WHISPER_MIN_LANGUAGE_PROBABILITY` (clamped to `[0.0, 1.0]`).

### `pyproject.toml` change

```toml
[project]
# core dependencies unchanged
dependencies = ["pydantic>=2.7,<3", "httpx>=0.27,<1"]

[project.optional-dependencies]
subtitles = ["faster-whisper>=1.0,<2"]
```

### New `SubtitleAuditEvent` (`src/arl/subtitles/models.py`)

```python
from datetime import datetime
from pydantic import BaseModel

class SubtitleStateFile(BaseModel):
    # existing
    processed_match_keys: list[str] = Field(default_factory=list)

class SubtitleAuditEvent(BaseModel):
    event_type: str
    session_id: str
    match_index: int
    language: str | None = None
    language_probability: float | None = None
    reason: str | None = None
    reason_detail: str | None = None
    created_at: datetime
```

Deliberate omissions vs recorder/exporter audit events:

- No `decision`, `failure_category`, `is_retryable`, `reason_code`.
- No `stderr_excerpt`, `stderr_log_path` (no subprocess; transcription is in-process).
- No `attempt` / `max_attempts` (no retry yet).

Spec gains a short paragraph explaining this divergence so future contributors
don't try to "fix" it by retroactively conforming subtitles to the ffmpeg
canonical tuple.

### Reason codes for `subtitle_fallback_placeholder`

| `reason` | When | `reason_detail` example |
|----------|------|------------------------|
| `model_unavailable` | `_load_whisper_model()` returns `None` (import error or init error) | `import_error:ModuleNotFoundError` |
| `unsupported_suffix` | recording path suffix not in `_TRANSCRIBE_SUFFIXES` (e.g. `.txt` placeholder from recorder) | `unsupported_suffix:.txt` |
| `missing_recording` | no `RecordingAsset` exists for `session_id` | `no_recording_asset_for_session=<id>` |
| `transcribe_failed` | `model.transcribe(...)` raises | `transcribe_exc:RuntimeError:CUDA error` |
| `low_language_confidence` | `info.language_probability < threshold` | `language=ko probability=0.42 threshold=0.5` |

`language_probability` field is populated for `subtitle_transcribe_succeeded` AND for `low_language_confidence` fallback rows (so the operator can `grep low_language_confidence subtitles-events.jsonl | jq .language_probability` to see the distribution).

### Audit emission paths in `SubtitleService`

Helper `_emit_subtitle_audit(event_type, *, session_id, match_index, **fields)` appends a `SubtitleAuditEvent` via `append_model(self.audit_path, ...)`. New attribute:

```python
self.audit_path = settings.storage.temp_dir / "subtitles-events.jsonl"
```

Emission sites — exactly one per match iteration in `SubtitleService.run()`:

1. After `_transcribe_boundary()` succeeds AND entries non-empty:
   `_emit_subtitle_audit("subtitle_transcribe_succeeded", session_id=..., match_index=..., language=info.language, language_probability=info.language_probability)`
2. Each fallback (returns `[]` from `_transcribe_boundary` or upstream):
   `_emit_subtitle_audit("subtitle_fallback_placeholder", session_id=..., match_index=..., reason=..., reason_detail=...)`

For (2) the `_transcribe_boundary` signature changes from `list[tuple[float, float, str]]` to a dataclass / tuple `(entries: list[...], reason: str | None, reason_detail: str | None, language: str | None, language_probability: float | None)` so the caller can emit the right audit row.

## Module-by-module changes

### `pyproject.toml`

Add `[project.optional-dependencies]` table with `subtitles` extra. Mention in
README and launcher comments.

### `src/arl/config.py`

`SubtitleSettings` gains two fields; `load_settings()` reads two env vars
(see above).

### `src/arl/subtitles/models.py`

New file (currently only `SubtitleStateFile` exists in a `subtitles/models.py`?
Verify before writing — if no `models.py` exists yet, create it; if it exists
with `SubtitleStateFile`, add `SubtitleAuditEvent` alongside).

### `src/arl/subtitles/service.py`

- `__init__`: add `self.audit_path = settings.storage.temp_dir / "subtitles-events.jsonl"`.
- `_load_whisper_model()`: before lazy import, `os.environ.setdefault("HF_HOME", str(self.settings.subtitles.model_cache_dir.resolve()))` + `self.settings.subtitles.model_cache_dir.mkdir(parents=True, exist_ok=True)`.
- `_transcribe_boundary()`: refactor return shape to carry reason/detail/language/probability metadata; perform threshold gate after successful transcribe.
- `run()`: emit one audit row per match (success or fallback) before `processed_match_keys.append(key)`.
- New `_emit_subtitle_audit()` helper as described above.

### Three launcher .ps1 files

`scripts/windows-{agent,orchestrator,recorder}-loop.ps1`:

Current line ~78:
```powershell
& $venvPython -m pip install -e .
```

Becomes:
```powershell
& $venvPython -m pip install -e ".[subtitles]"
```

The `.deps-ready` sentinel today is a flag file. Change it to a text file whose
contents are the install spec (e.g. `pip install -e .[subtitles]`). Bootstrap
re-runs `pip install` if the file is missing OR its contents differ from the
target spec. This way an operator who toggles between base and `[subtitles]`
installs doesn't get a stale install.

### `.trellis/spec/backend/orchestration-contracts.md`

- "files produced by stages" block: add `data/tmp/subtitles-events.jsonl`.
- env vars block: add `ARL_WHISPER_MODEL_CACHE_DIR` + `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY`.
- New subsection "Subtitles audit divergence" explaining why `SubtitleAuditEvent`
  omits the canonical ffmpeg decision tuple (different failure domain;
  observability-only; orchestrator non-consumer).
- Validation matrix: 1-2 rows for subtitle success vs fallback emission shape.

### `README.md`

- Top-level "未实现" list: remove the faster-whisper bullet (or rephrase as "GPU 加速 / 长视频分块 等更高阶能力仍待办").
- New "字幕生成与排查" subsection: install command, cache dir env, quality threshold env, grep recipe.

## Compatibility / migration

- `SubtitleStateFile.processed_match_keys` schema unchanged.
- New audit log is additive; nothing reads it today, no breakage if it's absent.
- `pyproject.toml` change is additive (new extras section).
- Launcher `.deps-ready` sentinel format change is detected by content comparison; old flag-file installs do one re-`pip install`.

## Trade-offs

- **Optional extra vs core dep**: chose optional to keep `pip install -e .` light for dev / CI. Trade-off: operators must use the right command. Mitigated by launcher always using `[subtitles]`.
- **HF_HOME vs faster-whisper-specific cache arg**: `HF_HOME` is the established Huggingface convention; faster-whisper respects it. Pinning at env level avoids leaking the convention into our settings model.
- **Audit schema divergence**: chose minimal subtitle-specific schema over forcing canonical ffmpeg tuple. Trade-off: oncall tools that parse `recorder-events.jsonl` need a parallel parser for `subtitles-events.jsonl`. Acceptable — they were never going to share a parser anyway given the field-level differences.
- **Quality threshold default 0.5**: conservative; only rejects very-low confidence. Operators chasing precision can raise to 0.7+; chasing recall can drop to 0.0 (effectively disable the gate). Threshold-tuning data will inform a future default change.

## Operational notes

- `grep subtitle_fallback_placeholder data/tmp/subtitles-events.jsonl | jq .reason | sort | uniq -c` gives a quick fallback-reason histogram.
- `grep subtitle_transcribe_succeeded data/tmp/subtitles-events.jsonl | jq '[.language, .language_probability]' | sort | uniq -c` surfaces language drift.
- Whisper model first-download is ~500 MB for `small`, ~3 GB for `large-v3`; pinning cache dir on the same NTFS volume means no OneDrive interaction.
