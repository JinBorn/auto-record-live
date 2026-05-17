# Implement: LoL semantic stage detection — research / spike

3 phases. No production code; everything under `.trellis/tasks/05-14-lol-semantic-stage-detection-production/research/`. Single PR per phase or one batched PR — operator's choice.

## Phase 1 — Fixture + evaluator

### Files

- `.trellis/tasks/.../research/fixtures/.gitignore`: blocks `*.mp4` and any other binary video formats.
- `.trellis/tasks/.../research/fixtures/<session_id>/metadata.yaml`:
  ```yaml
  recording_path: D:/code/auto-record-live/data/raw/<session>/recording-source.mp4
  duration_seconds: 1830.0
  match_count: 4
  resolution: "1920x1080"
  source_type: direct_stream
  platform: bilibili     # or douyin
  notes: |
    Picked because it contains 4 complete matches from champion-select to post-game
    with no mid-stream disconnects. SESSDATA was fresh; quality was qn=400 (1080P).
  ```
- `.trellis/tasks/.../research/fixtures/<session_id>/ground-truth-hints.jsonl` — operator manually scrubs the video and writes one row per stage transition:
  ```json
  {"session_id":"...","stage":"champion_select","at_seconds":0.0}
  {"session_id":"...","stage":"loading","at_seconds":48.5}
  {"session_id":"...","stage":"in_game","at_seconds":81.3}
  {"session_id":"...","stage":"post_game","at_seconds":1875.2}
  ...
  ```
  Tooling for this: a simple PowerShell helper or just open the video in VLC / mpv and read timestamps by eye. Expected effort: ~20-30 minutes per match × 4 matches = ~1.5 hr.
- `.trellis/tasks/.../research/eval.py`:
  - argparse: `--ground-truth`, `--predictions`, `--tolerance` (default 10.0).
  - Loads both jsonl files via the same shape as `MatchStageHint`.
  - Matching algorithm:
    1. Group both lists by stage.
    2. For each stage, greedy-match each prediction to the nearest unmatched ground-truth hint within tolerance; TP if matched, FP if not.
    3. Unmatched ground-truth hints in that stage → FN.
  - Outputs:
    ```
    stage                 precision  recall  f1     TP  FP  FN
    champion_select       0.82       0.75    0.78   3   1   1
    loading               ...
    in_game               ...
    post_game             ...
    overall               ...

    confusion matrix (predicted x actual):
                  CS  LD  IG  PG
    CS            ...
    LD            ...
    ...
    ```

### Validation

```powershell
.\.venv\Scripts\python.exe -m pip install pyyaml  # for metadata.yaml; if not already
python .\.trellis\tasks\05-14-lol-semantic-stage-detection-production\research\eval.py `
  --ground-truth .\.trellis\tasks\05-14-lol-semantic-stage-detection-production\research\fixtures\<sid>\ground-truth-hints.jsonl `
  --predictions  .\.trellis\tasks\05-14-lol-semantic-stage-detection-production\research\fixtures\<sid>\ground-truth-hints.jsonl
# self-comparison should give 1.0 / 1.0 / 1.0 across the board (sanity check)
```

### Commit

```
research(semantic-stage): fixture + evaluator harness
```

---

## Phase 2 — Two prototypes

### Files

- `.trellis/tasks/.../research/templates/<stage>/<label>.png` — UI screenshot crops, labeled by stage. Example:
  - `templates/champion_select/banner-cs.png` (the "champion select" panel header)
  - `templates/loading/loading-bar.png` (the load-bar shape)
  - `templates/post_game/continue-button.png` (the post-game "继续" button)
  Use ffmpeg to extract frames from the fixture: `ffmpeg -ss 30 -i recording-source.mp4 -frames:v 1 frame.png`, then crop in any editor.
- `.trellis/tasks/.../research/prototype_template_matching.py`:
  - Reads `--recording`, `--templates`, `--output`.
  - `cv2.VideoCapture(recording)`; loop frames at 1 fps (frame skip = fps).
  - For each frame: per template, `cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)`; if max-val ≥ threshold (default 0.7), emit a `MatchStageHint` for that stage at the frame's timestamp.
  - Dedup: same stage within ±5 s collapses to the first hit.
- `.trellis/tasks/.../research/prototype_ocr.py`:
  - Reads `--recording`, `--output`.
  - `cv2.VideoCapture`; loop frames at 0.5 fps (one frame every 2 s).
  - Per frame: crop a fixed ROI (e.g. center top for banner area, configurable via `--roi x,y,w,h`); run `pytesseract.image_to_string()` (or paddleocr `PaddleOCR().ocr()`).
  - Pipe detected text through `classify_stage_from_text()` from `arl.segmenter.stage_text`. If non-`None`, emit a hint.

### Required python deps (research-only)

```bash
pip install opencv-python pytesseract pyyaml
# OR for paddle-ocr instead of tesseract:
pip install paddleocr
```

These are **not** added to pyproject; install ad-hoc in the operator's venv.

### Validation

```powershell
python ...\prototype_template_matching.py --recording <fixture>.mp4 --templates ...\templates --output ...\predicted-template.jsonl
python ...\eval.py --ground-truth ...\ground-truth-hints.jsonl --predictions ...\predicted-template.jsonl
# capture metrics output

python ...\prototype_ocr.py --recording <fixture>.mp4 --output ...\predicted-ocr.jsonl
python ...\eval.py --ground-truth ...\ground-truth-hints.jsonl --predictions ...\predicted-ocr.jsonl
# capture metrics output
```

### Commit

```
research(semantic-stage): template-matching + OCR prototypes
```

---

## Phase 3 — Report + recommendation

### Files

- `.trellis/tasks/.../research/report.md`:
  - Section "Fixture": metadata summary.
  - Section "Template matching results": metrics table + observed failure modes (which templates miss / false-match).
  - Section "OCR results": metrics table + observed failure modes (which ROI / OCR engine / language model).
  - Section "Comparison": side-by-side precision/recall, ms/frame, total wall-clock.
  - Section "Recommendation": one of:
    - "Productionize template matching; threshold = X; templates corpus = Y; integrate as new signal source via stage_text equivalent". Includes a PRD seed paragraph for the follow-up productionization task.
    - "Productionize OCR; ROI = X; engine = Y; sampling rate = Z fps". Same.
    - "Hybrid: template for high-precision (cs, post_game banners), OCR for in_game-vs-loading disambiguation". Same.
    - "Neither met threshold; open a CNN spike task. Recommended corpus collection plan: ...". Same.

### Validation

- Report has actual numbers (no placeholders).
- Recommendation is concrete enough that the follow-up task could be created without more research.

### Commit

```
research(semantic-stage): report + productionization recommendation
```

---

## Risky files / rollback points

- All changes under `.trellis/tasks/05-14-lol-semantic-stage-detection-production/research/`; never touches `src/arl/` or `tests/`. Reverting any phase has zero impact on production behavior.

## Follow-ups (out of scope for this task)

- Productionization task — opened only after this task's report exists. PRD seed lives in the report. Likely shape: new `src/arl/segmenter/visual_signals.py` consuming the winning prototype as a real signal source, emitting `MatchStageSignal` rows just like `signals_from_subtitles.py` does today; new `arl stage-signals-from-video` CLI parallel to `arl stage-signals-from-subtitles`.
- Multi-fixture statistical robustness (this task gets one fixture to make the call; broader corpus is the next phase).
- Multi-source fusion (visual signal vs subtitle signal conflict resolution).
- Real-time / streaming inference during recording (post-hoc only for now).
