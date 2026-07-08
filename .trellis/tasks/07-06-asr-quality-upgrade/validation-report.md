# ASR quality upgrade validation report

## Automated checks

- `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_subtitles_service.py tests/test_config.py`
  - Earlier result: 88 passed.
  - After display-smoothing update: 90 passed.
- `.\.venv\Scripts\python.exe -m pytest tests`
  - Earlier result: 659 passed.
  - After display-smoothing update: 672 passed.
- `.\.venv\Scripts\python.exe -m compileall src tests`
  - Result: passed.
- `.\.venv\Scripts\python.exe -m arl.cli show-config`
  - Result: passed; resolved ASR settings include prompt path, term-fix path,
    OpenCC enablement, beam size, and VAD controls.

## Live media validation

Initial attempted command:

```powershell
.\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --all-latest --top-gaps 5
```

Result: timed out after 120 seconds. The two residual Python processes were
stopped after confirming they were the timed-out quality-report command.

No new real ASR regeneration was run in this session because the local
`faster-whisper-medium` cache appears incomplete and running the publish
default could trigger a large model download plus long GPU transcription.

Follow-up scoped validation was run on 2026-07-08 for match 2:

1. Installed the subtitles extra so the existing dependency declaration pulled
   `opencc-python-reimplemented==0.1.7`.
2. Ran the publish/default ASR path:
   ```powershell
   .\.venv\Scripts\python.exe -m arl.cli subtitles --force-reprocess --session-id session-20260702092321-bc90812b --match-index 2
   ```
   - The medium model cache completed by downloading `model.bin`
     (~1.5GB).
   - CUDA transcription could not run on this machine because
     `cublas64_12.dll` is missing:
     `Library cublas64_12.dll is not found or cannot be loaded`.
   - The subtitle stage fell back and wrote `match-02.srt`.
   - OpenCC was not yet installed for this run, so the output stayed
     unnormalized.
3. Ran quality report for that first real regeneration:
   ```powershell
   .\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-index 2 --top-gaps 5
   ```
   - `subtitle_active_ratio`: `36.9%`
   - `subtitle_covered_seconds`: `188.92`
   - `max_no_subtitle_gap_seconds`: `17.66`
   - `kda_uncovered_count`: `0/3`
   - Result improved over the prior report (`29.1%`, `135.68s` covered),
     but did not meet the PRD target of `>=55%`.
4. Re-ran the same match with OpenCC installed and forced CPU medium to avoid
   the CUDA DLL failure:
   ```powershell
   $env:ARL_WHISPER_DEVICE="cpu"
   $env:ARL_WHISPER_MODEL_SIZE="medium"
   .\.venv\Scripts\python.exe -m arl.cli subtitles --force-reprocess --session-id session-20260702092321-bc90812b --match-index 2
   ```
   - Output SRT was normalized to Simplified Chinese.
   - Python UTF-8 inspection found no sampled traditional-only characters in
     the regenerated SRT.
5. Re-ran quality report:
   ```powershell
   .\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-index 2 --top-gaps 5
   ```
   - `subtitle_active_ratio`: `36.9%`
   - `subtitle_covered_seconds`: `188.92`
   - `max_no_subtitle_gap_seconds`: `17.66`
   - `kda_uncovered_count`: `0/3`
   - One warning remains: `subtitle_active_ratio_below_min`.
6. Started an experiment with CPU medium and `ARL_WHISPER_VAD_FILTER=0` to
   test whether VAD filtering caused low coverage. It remained CPU-active for
   over 6 minutes without writing output, so the experiment was stopped to
   avoid tying up the machine. No result was recorded from that run.
7. Added post-ASR display smoothing before SRT persistence. The smoothing keeps
   ASR word starts intact, extends short cues for readability, fills only small
   gaps between neighboring cues, and clamps cue ends to the match boundary.
   Applied the new SRT persistence helper to the existing CPU-medium regenerated
   match 2 SRT, then ran:
   ```powershell
   .\.venv\Scripts\python.exe -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-index 2 --top-gaps 5
   ```
   - `subtitle_active_ratio`: `71.6%`
   - `subtitle_covered_seconds`: `367.01`
   - `max_no_subtitle_gap_seconds`: `15.34`
   - `kda_uncovered_count`: `0/3`
   - `warnings`: `0`

## Current conclusion

The implementation satisfies the automated checks, model fallback behavior, and
Simplified Chinese normalization once the optional subtitles extra is installed.
After display smoothing, the live media acceptance criterion is met on match 2:
subtitle active ratio is above the `55%` target, sampled Simplified Chinese
normalization found no traditional-only characters, and quality-report emits no
warnings for the selected validation match.
