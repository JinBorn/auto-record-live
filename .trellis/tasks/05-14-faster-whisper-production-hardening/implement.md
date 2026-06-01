# Implement: faster-whisper subtitles production hardening

3 PRs.

## PR1 — Optional dep + model cache dir

### Files

- `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  subtitles = ["faster-whisper>=1.0,<2"]
  ```
- `src/arl/config.py`:
  - `SubtitleSettings` gains `model_cache_dir: Path = Path("data/tmp/whisper-models")`.
  - `load_settings()` reads `ARL_WHISPER_MODEL_CACHE_DIR` (defaults to the path string above).
- `src/arl/subtitles/service.py`:
  - In `_load_whisper_model()`, before the lazy `from faster_whisper import ...`:
    ```python
    cache_dir = self.settings.subtitles.model_cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))
    ```
- Three `scripts/windows-{agent,orchestrator,recorder}-loop.ps1`:
  - `pip install -e .` → `pip install -e ".[subtitles]"`.
  - `.deps-ready` sentinel: instead of just creating an empty file, write the install spec string to it (e.g. `Set-Content -Path $depsReady -Value "pip install -e .[subtitles]"`). On startup, if file exists AND content matches → skip install; else re-install.
- `README.md` — install command updated to `pip install -e .[subtitles]` in quick-start.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[subtitles]"
# Verify HF_HOME respected:
$env:ARL_WHISPER_MODEL_CACHE_DIR = "data/tmp/whisper-models-test"
.\.venv\Scripts\python.exe -m arl.cli subtitles   # first run; downloads model under custom dir
ls data/tmp/whisper-models-test/  # expect HF cache structure

# Existing tests:
.\.venv\Scripts\python.exe -m pytest -q  # 300 baseline still green
```

### Commit

```
feat(subtitles): optional faster-whisper extra + NTFS-local model cache
```

---

## PR2 — Language-confidence quality gate

**Depends on**: PR1 merged.

### Files

- `src/arl/config.py`:
  - `SubtitleSettings` gains `min_language_probability: float = 0.5`.
  - `load_settings()` reads `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY`, clamped to `[0.0, 1.0]`.
- `src/arl/subtitles/service.py`:
  - `_transcribe_boundary()` keeps `info` from `transcribe()` (drop the `_` discard).
  - After successful transcribe but before iterating segments:
    ```python
    threshold = self.settings.subtitles.min_language_probability
    if info.language_probability < threshold and self.settings.subtitles.language:
        log("subtitles", f"low language confidence ...")
        return []  # falls through to placeholder
    ```
  - Capture `info.language` + `info.language_probability` for the upcoming PR3 audit; consider returning a small dataclass `TranscribeOutcome(entries, language, probability, reason, reason_detail)` to keep audit-emission in one place.
- New tests in `tests/pipeline/test_subtitles_service.py`:
  - `test_low_language_probability_falls_back_to_placeholder`: stub WhisperModel returns `info.language_probability=0.3`; result is placeholder SRT.
  - `test_high_language_probability_emits_real_srt`: probability=0.95 → real SRT with segments.
  - `test_threshold_disabled_when_language_setting_empty`: `settings.subtitles.language = ""` → no gate, low probability still passes (escape hatch).
  - `test_env_overrides_threshold`: `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY=0.7` → 0.6 probability fails the gate.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py -v
.\.venv\Scripts\python.exe -m pytest -q  # 300 + 4 = 304
```

### Commit

```
feat(subtitles): language-confidence gate before accepting transcription
```

---

## PR3 — Audit JSONL + spec/README

**Depends on**: PR2 merged.

### Files

- `src/arl/subtitles/models.py` (create if absent, or extend the existing `SubtitleStateFile` file):
  ```python
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
- `src/arl/subtitles/service.py`:
  - `__init__` gains `self.audit_path = settings.storage.temp_dir / "subtitles-events.jsonl"`.
  - New `_emit_subtitle_audit(event_type, *, session_id, match_index, **fields)` that builds + appends one `SubtitleAuditEvent`.
  - In `run()`, after `_write_subtitle()` and before `processed_match_keys.append(key)`, emit one audit row using the `TranscribeOutcome` returned by `_transcribe_boundary` (success vs fallback shapes diverge).
- New test class `SubtitleAuditTest` in `tests/pipeline/test_subtitles_service.py`:
  - `test_success_emits_succeeded_audit_with_language_fields`
  - `test_missing_recording_emits_fallback_reason_missing_recording`
  - `test_unsupported_suffix_emits_fallback_reason_unsupported_suffix`
  - `test_model_unavailable_emits_fallback_reason_model_unavailable`
  - `test_transcribe_exception_emits_fallback_reason_transcribe_failed`
  - `test_low_language_probability_emits_fallback_reason_low_language_confidence`
- `.trellis/spec/backend/orchestration-contracts.md` — 3 edits per design.md (audit file in produced-files block; env-vars block; new "Subtitles audit divergence" subsection; +2 validation-matrix rows).
- `.trellis/spec/backend/quality-guidelines.md` — new Common Mistake: "Adding subtitle failures to `CORE_DECISION_EVENT_TYPES` — don't; subtitles audit deliberately keeps its own minimal schema."
- `README.md` — remove the faster-whisper bullet from "未实现"; add "字幕生成与排查" subsection with install/cache/threshold env vars + grep recipe.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py::SubtitleAuditTest -v
.\.venv\Scripts\python.exe -m pytest -q  # 304 + 6 = 310
```

### Commit

```
feat(subtitles): structured audit JSONL parallel to recorder/exporter
```

---

## Risky files / rollback points

- `pyproject.toml` — additive change; revert is safe.
- `scripts/windows-*-loop.ps1` — install spec change. If `[subtitles]` install hangs on a slow network, operator can run `pip install -e .` manually and the launcher's content-compare logic will reinstall correctly on next start.
- `src/arl/subtitles/service.py` — every PR touches this. PR1 isolated to `_load_whisper_model`. PR2 in `_transcribe_boundary`. PR3 across `__init__` + `run` + new `_emit_subtitle_audit`.

## End-to-end verification after all 3 PRs

1. Fresh venv, `pip install -e .[subtitles]`, run `arl subtitles` with a real `.mp4` recording → `data/tmp/whisper-models/` populates, `data/tmp/subtitles-events.jsonl` gets one `subtitle_transcribe_succeeded` row, SRT contains real segments.
2. Run the same with `ARL_WHISPER_MIN_LANGUAGE_PROBABILITY=0.99` → `subtitle_fallback_placeholder reason=low_language_confidence` row + placeholder SRT.
3. `pytest -q` total 310 green.

## Follow-ups (out of scope)

- GPU/CUDA auto-detection with CPU fallback (D4).
- Long-video chunking + per-chunk retry (D5).
- Launcher preflight model warmup (D6) — symmetric to E1.
