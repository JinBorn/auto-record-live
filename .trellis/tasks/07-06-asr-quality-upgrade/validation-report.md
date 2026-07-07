# ASR quality upgrade validation report

## Automated checks

- `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py tests/test_config.py`
  - Result: 88 passed.
- `.\.venv\Scripts\python.exe -m pytest tests`
  - Result: 659 passed.
- `.\.venv\Scripts\python.exe -m compileall src tests`
  - Result: passed.
- `.\.venv\Scripts\python.exe -m arl.cli show-config`
  - Result: passed; resolved ASR settings include prompt path, term-fix path,
    OpenCC enablement, beam size, and VAD controls.

## Live media validation

Attempted:

```powershell
.\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --all-latest --top-gaps 5
```

Result: timed out after 120 seconds. The two residual Python processes were
stopped after confirming they were the timed-out quality-report command.

No new real ASR regeneration was run in this session because the local
`faster-whisper-medium` cache appears incomplete and running the publish
default could trigger a large model download plus long GPU transcription.

## Follow-up validation target

Use a scoped 07-02 match once a longer runtime window is available:

```powershell
.\.venv\Scripts\python.exe -m arl.cli subtitles --force-reprocess --session-id session-20260702092321-bc90812b --match-index 2
.\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-index 2 --top-gaps 5
```

Record active subtitle ratio, longest no-subtitle gap, model/device/compute,
elapsed time, and any observable VRAM notes before closing the acceptance
criterion that requires measured media output.
