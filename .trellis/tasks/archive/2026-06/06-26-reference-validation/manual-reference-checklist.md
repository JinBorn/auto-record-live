# Manual Reference Checklist

Use `data/demo1` and `data/demo2` as local human references only. Do not copy,
commit, or process the full demo videos as automated fixtures.

## Scope

Validated in this task:

- publishing metadata and optional cover image
- ASS subtitle sidecar and burn-in command path
- teaser-before-main edit-plan timeline
- local BGM/SFX instructions and conservative gains
- punch-in zoom transform on high-signal teaser segments

Intentionally excluded:

- external reference inserts / "引经据典" clips
- automatic full-demo visual/audio comparison
- internet-sourced film or meme clips

## Artifact Checks

1. Publishing package
   - Run `arl copywriter --session-id <session_id>` after subtitle/export assets
     exist.
   - Inspect `data/processed/<session_id>/match-NN-publishing.json`.
   - Confirm `recommended_title`, `summary`, `cover_lines`, `tags`, and
     `evidence` exist.
   - Confirm the first evidence/title signal comes from a high-signal highlight
     cue when a highlight plan exists, not only the first ordinary subtitle cue.

2. Cover image
   - If a recording file is present and ffmpeg/Pillow are available, confirm
     `cover_path` points to `match-NN-cover.jpg`.
   - Open the cover and compare against the reference style:
     large readable headline, gameplay frame background, strong contrast.
   - Do not require pixel-perfect matching to `demo1` or `demo2`.

3. ASS subtitles
   - Run exporter with:
     `ARL_EXPORT_BURN_SUBTITLES=1` and `ARL_EXPORT_USE_ASS_SUBTITLES=1`.
   - Confirm `match-NN.ass` is created next to the source SRT.
   - Confirm the exporter ffmpeg command uses the `.ass` path in `subtitles=`.
   - Reference expectation: bottom-centered, readable white text with dark
     outline and low bottom margin.

4. Teaser-before-main edit plan
   - Run `arl edit-planner --session-id <session_id>` after a valid highlight
     plan exists.
   - Inspect `data/tmp/edit-plans.jsonl`.
   - Confirm timeline order is one or more `teaser` segments followed by exactly
     one `main` segment.
   - Confirm the `main` segment covers `[0.0, boundary_duration]`.
   - Confirm no `insert` role or `source_path` is required for this scope.

5. BGM/SFX
   - Enable only with explicit local paths:
     `ARL_EDIT_AUDIO_MIXING_ENABLED=1`, `ARL_EDIT_BGM_PATH`, and/or
     `ARL_EDIT_SFX_PATH`.
   - Confirm missing local assets are skipped and the base edit plan still
     exists.
   - Confirm emitted gains stay conservative:
     BGM near `-24.0 dB`, SFX near `-12.0 dB` unless intentionally configured.

6. Punch-in zoom
   - Enable with `ARL_EDIT_ZOOM_ENABLED=1`.
   - Confirm only high-signal teaser segments receive
     `TimelineVideoTransform(kind="punch_in")`.
   - Confirm scale stays modest, at or below `1.5`.
   - In rendered output, compare against demo behavior: zoom should emphasize
     the moment without hiding core HUD context.

## Validation Commands

Focused regression:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\pipeline\test_reference_validation.py tests\pipeline\test_copywriter_service.py tests\pipeline\test_subtitles_service.py tests\pipeline\test_editing_service.py tests\pipeline\test_ffmpeg_resilience.py tests\pipeline\test_postprocess_service.py tests\test_config.py -q
```

Broad regression:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
.\.venv\Scripts\python.exe -m pytest tests\pipeline tests\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```
