# faster-whisper subtitles production hardening

## Goal

Move the subtitles stage from "permanently degraded to placeholder SRT in the
default install" to "actually works, observable, and doesn't silently emit
garbage when the model loses confidence". Three additive layers:

1. **D1**: Make faster-whisper installable via project optional-dep, with NTFS-local model cache.
2. **D2**: Refuse low-language-confidence transcriptions; fall back to placeholder explicitly.
3. **D3**: Subtitles emits its own audit JSONL parallel to recorder/exporter, with its own minimal schema (no canonical ffmpeg decision tuple).

## User Value

- Operator running `pip install -e .[subtitles]` (instead of `pip install -e .`) gets a working subtitle stage out of the box. No more "why is every SRT a placeholder?" surprise.
- Model files land under `data/tmp/whisper-models/` on the same NTFS volume as everything else — consistent with the project's local-first philosophy; no OneDrive footguns.
- Garbage-detection: if Whisper is 40% sure the audio is Korean but the configured language is `zh`, the SRT becomes a placeholder + an audit row, instead of an SRT full of wrong-language hallucinations downstream consumers can't filter.
- `data/tmp/subtitles-events.jsonl` joins `recorder-events.jsonl` + `exporter-events.jsonl` as a third structured audit lane; oncall grep recipes work uniformly.

## Confirmed Facts (from code inspection)

- `pyproject.toml` core `dependencies` is `pydantic` + `httpx` only; no faster-whisper.
- Local venv check: `import faster_whisper` raises `ModuleNotFoundError` — confirms "default install doesn't include it".
- `SubtitleService._load_whisper_model()` already does lazy import + graceful degradation; returns `None` if import / init fails (`src/arl/subtitles/service.py:232-248`).
- `model.transcribe(...)` returns `(segments, info)`; the second value contains `language` + `language_probability` (faster-whisper 1.0+ API). Existing code discards `info` via `segments, _ = model.transcribe(...)` (`src/arl/subtitles/service.py:199`).
- `SubtitleSettings` has `enabled`, `provider`, `model_size`, `language` (`src/arl/config.py:129-134`). No cache dir field, no quality threshold field.
- Three launchers run `pip install -e .` (or `pip install -e .[ARL_WIN_INSTALL_MODE]`); none install extras today.
- No `subtitles-events.jsonl` audit file exists. The subtitle stage produces `subtitle-assets.jsonl` (the asset manifest, not an audit log).

## Decisions (Q1 + Q2, 2026-05-14)

- ~~Scope (Q1)~~ — **D1 + D2 + D3 only**. D4 (GPU fallback), D5 (long-video chunking), D6 (launcher preflight) deferred.
- ~~Dependency declaration (Q2-a)~~ — **`[project.optional-dependencies] subtitles = ["faster-whisper>=1.0,<2"]`**; three launchers switch to `pip install -e .[subtitles]`.
- ~~Model cache directory (Q2-b)~~ — **`ARL_WHISPER_MODEL_CACHE_DIR` env var, default `data/tmp/whisper-models/`**; `SubtitleService` sets `os.environ["HF_HOME"]` before importing faster-whisper.
- ~~Audit schema (Q2-c)~~ — **`SubtitleAuditEvent` with minimal schema**, NOT added to `CORE_DECISION_EVENT_TYPES`; no canonical ffmpeg decision tuple; events: `subtitle_transcribe_succeeded` + `subtitle_fallback_placeholder` (with `reason`).
- Default quality threshold — **`ARL_WHISPER_MIN_LANGUAGE_PROBABILITY=0.5`** (conservative; only rejects very-low confidence). Operators can raise to 0.7+ for aggressive filtering.

## Requirements

- **R1 (optional dep)**: `pyproject.toml` gains `[project.optional-dependencies]` table with `subtitles = ["faster-whisper>=1.0,<2"]`. Three launcher scripts switch their pip-install command to `pip install -e .[subtitles]`. The `.deps-ready` sentinel changes from a flag-file presence test to a marker file whose contents include the install spec used, so `[subtitles]` re-installs trigger when the launcher mode changes.
- **R2 (model cache)**: `SubtitleSettings` gains `model_cache_dir: Path = Path("data/tmp/whisper-models")`. `load_settings()` reads `ARL_WHISPER_MODEL_CACHE_DIR`. `SubtitleService._load_whisper_model()` calls `os.environ.setdefault("HF_HOME", str(self.settings.subtitles.model_cache_dir.resolve()))` BEFORE the lazy import, and `mkdir(parents=True, exist_ok=True)` on the path.
- **R3 (quality gate)**: `SubtitleService._transcribe_boundary()` accepts the `info` from `transcribe()` (no longer `segments, _`). When `info.language_probability < settings.subtitles.min_language_probability` AND a configured `settings.subtitles.language` is set, treat as transcribe-failed (don't return entries; fall through to placeholder). `SubtitleSettings` gains `min_language_probability: float = 0.5`. Env: `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY` (0.0..1.0).
- **R4 (audit model)**: New file `src/arl/subtitles/models.py` gains `SubtitleAuditEvent(BaseModel)`:
  ```python
  class SubtitleAuditEvent(BaseModel):
      event_type: str   # subtitle_transcribe_succeeded | subtitle_fallback_placeholder
      session_id: str
      match_index: int
      language: str | None = None             # populated on success + low_language_confidence
      language_probability: float | None = None
      reason: str | None = None               # set on fallback only
      reason_detail: str | None = None
      created_at: datetime
  ```
- **R5 (audit emission)**: `SubtitleService` appends one row per match to `data/tmp/subtitles-events.jsonl`:
  - Success path → `subtitle_transcribe_succeeded` with `language` + `language_probability`.
  - Each fallback path → `subtitle_fallback_placeholder` with `reason` ∈ `{model_unavailable, unsupported_suffix, missing_recording, transcribe_failed, low_language_confidence}` + `reason_detail` (e.g. `language_probability=0.42 below 0.5`).
- **R6 (spec)**: `.trellis/spec/backend/orchestration-contracts.md`:
  - Section "files produced by stages" gains `data/tmp/subtitles-events.jsonl`.
  - New env vars block entries: `ARL_WHISPER_MODEL_CACHE_DIR`, `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY`.
  - New "Subtitles audit" subsection explains the divergence: "`SubtitleAuditEvent` deliberately omits the canonical `decision` / `failure_category` / `is_retryable` / `reason_code` tuple because subtitles failure domains (model unavailable / language-low-confidence / unsupported suffix) do not map cleanly onto the ffmpeg subprocess taxonomy. Subtitles audit is observability-only; orchestrator does not consume it."
- **R7 (README)**:
  - "未实现（生产级能力）" section: remove "`faster-whisper` 离线 ASR 的工程化加固" since it's now done.
  - Add new "字幕生成与排查" subsection covering install command (`pip install -e .[subtitles]`), cache dir env var, quality threshold env var, and `grep subtitles-events.jsonl` recipe.
- **R8 (tests)**:
  - New test class `SubtitleAuditTest`: success path emits one `subtitle_transcribe_succeeded`; missing recording emits `subtitle_fallback_placeholder reason=missing_recording`; unsupported suffix → `reason=unsupported_suffix`; model unavailable → `reason=model_unavailable`; raised `transcribe()` exception → `reason=transcribe_failed`; low `language_probability` → `reason=low_language_confidence`.
  - All cases use a stub `WhisperModel` class injected via monkey-patching `_load_whisper_model` (avoids requiring faster-whisper in the test environment).

## Acceptance Criteria

- [ ] `pip install -e .` (without `[subtitles]`) → faster-whisper not installed → subtitles still produces placeholder SRT + audit row `reason=model_unavailable`. Repo stays installable without the heavy dep.
- [ ] `pip install -e .[subtitles]` → faster-whisper installed; model files land under `data/tmp/whisper-models/` (or `$ARL_WHISPER_MODEL_CACHE_DIR` if set).
- [ ] `data/tmp/subtitles-events.jsonl` exists after a subtitles run; each match emits exactly one event row.
- [ ] Stub WhisperModel returning `info.language_probability=0.3` triggers `low_language_confidence` fallback + placeholder SRT.
- [ ] Stub WhisperModel returning `info.language_probability=0.95` produces a real SRT + `subtitle_transcribe_succeeded` audit with language + probability fields populated.
- [ ] pytest baseline → baseline + 6 tests green.
- [ ] orchestration-contracts.md gains the new audit-file path + 2 new env vars + the subtitles-audit divergence subsection.

## Out of Scope

- GPU / CUDA detection + auto fallback (D4). Defer until we know what hardware operators run.
- Long-video / per-match chunking + per-chunk retry (D5).
- Subtitle preflight in launcher (D6) — symmetric to E1 (cookie health gate) and could be a future small task.
- Model warmup / pre-download at launcher startup. faster-whisper downloads lazily on first transcription; operator can `arl subtitles` once after install to warm the cache if desired.

## Open Questions

All blockers resolved.

## Notes

Medium task: PRD + `design.md` (cache dir + audit model are non-trivial schema changes) + `implement.md` (3-PR plan).
