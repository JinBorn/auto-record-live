# Research: LoL semantic stage detection

Spike workspace for task `05-14-lol-semantic-stage-detection-production`. Everything here is research-only: no `arl.*` imports, no production-code changes. PRD and execution plan live one level up in `prd.md` and `implement.md`.

## Layout

```
research/
├── .gitignore                       # blocks *.mp4 / *.mkv / etc — fixtures stay out of git
├── eval.py                          # standalone evaluator
├── fixtures/
│   └── _template/                   # placeholder schema for new fixtures
│       ├── metadata.yaml
│       └── ground-truth-hints.jsonl
├── prototype_template_matching.py   # Phase 2 — cv2.matchTemplate per stage
├── prototype_ocr.py                 # Phase 2 — PaddleOCR + vendored classify_stage_from_text
├── templates/
│   ├── champion_select/             # operator-supplied UI crops (PNG)
│   ├── loading/
│   ├── in_game/
│   └── post_game/
└── report.md                        # Phase 3 — added once both prototypes have run
```

## Workflow

### 1. Pick a fixture recording

Choose an `.mp4` from `data/raw/<session_id>/recording-source.mp4` that contains 3–5 complete LoL matches (champion-select → loading → in-game → post-game) with no mid-stream disconnects. Note the duration and platform.

If no suitable recording exists, capture a fresh one via the live-recorder (`arl record` flow) against a LoL stream and let it run for the duration of 3–5 matches.

### 2. Create the fixture directory

```powershell
$sid = "<session_id>"   # e.g. "session-20260507171213-44250901"
New-Item -ItemType Directory ".\fixtures\$sid"
Copy-Item ".\fixtures\_template\metadata.yaml" ".\fixtures\$sid\metadata.yaml"
Copy-Item ".\fixtures\_template\ground-truth-hints.jsonl" ".\fixtures\$sid\ground-truth-hints.jsonl"
```

Edit `metadata.yaml` to point at the real `.mp4` path and fill in `duration_seconds`, `match_count`, `resolution`, `source_type`, `platform`, and free-form `notes`.

### 3. Label the recording

Open the `.mp4` in VLC / mpv / similar; scrub through each match and record one `MatchStageHint` row per stage transition in `ground-truth-hints.jsonl`. Each match contributes four rows (champion_select, loading, in_game, post_game). Schema mirrors `arl.segmenter.models.MatchStageHint`:

```jsonl
{"session_id": "<session_id>", "stage": "champion_select", "at_seconds": 0.0}
{"session_id": "<session_id>", "stage": "loading",         "at_seconds": 48.5}
{"session_id": "<session_id>", "stage": "in_game",         "at_seconds": 81.3}
{"session_id": "<session_id>", "stage": "post_game",       "at_seconds": 1875.2}
```

Valid stage values: `champion_select | loading | in_game | post_game` (from `arl.shared.contracts.MatchStage`). `eval.py` skips blank lines and lines starting with `#` so you can keep header comments in the file.

Expected effort: ~20–30 minutes per match × 3–5 matches ≈ 1.5 hours.

### 4. Run the evaluator

Self-test (no fixture required — sanity-checks the matching algorithm with hardcoded synthetic data):

```powershell
python .\eval.py --self-test
# expect: "self-test passed"
```

Real evaluation (compares a prototype's predictions to your labeled ground truth):

```powershell
python .\eval.py `
  --ground-truth .\fixtures\<sid>\ground-truth-hints.jsonl `
  --predictions  .\<prototype-output>.jsonl `
  --tolerance    10.0
```

Output is a per-stage precision/recall/f1/TP/FP/FN table followed by a 4×4 confusion matrix (predicted rows × actual cols; FPs with no nearby actual are off-matrix).

Sanity check that the labeling file itself is internally consistent (same file on both sides should give 1.0/1.0/1.0 across the board):

```powershell
python .\eval.py `
  --ground-truth .\fixtures\<sid>\ground-truth-hints.jsonl `
  --predictions  .\fixtures\<sid>\ground-truth-hints.jsonl
```

### 5. Run the prototypes

Install the research-only deps once (not added to `pyproject.toml`):

```powershell
.\.venv\Scripts\python.exe -m pip install opencv-python paddleocr
```

Self-tests (no fixture / no deps for the classifier path — `cv2` and `paddleocr` are imported lazily, only on real runs):

```powershell
python .\prototype_template_matching.py --self-test
python .\prototype_ocr.py --self-test
# expect: "self-test passed" twice
```

Template-matching prototype (needs `templates/<stage>/*.png` cropped from the fixture):

```powershell
python .\prototype_template_matching.py `
  --recording <path-to-fixture-mp4> `
  --templates .\templates `
  --output    .\fixtures\<sid>\predicted-template.jsonl `
  --sample-fps 1.0 --threshold 0.7
python .\eval.py `
  --ground-truth .\fixtures\<sid>\ground-truth-hints.jsonl `
  --predictions  .\fixtures\<sid>\predicted-template.jsonl
```

OCR prototype (PaddleOCR; lang=ch by default to match the Chinese-heavy keyword corpus):

```powershell
python .\prototype_ocr.py `
  --recording <path-to-fixture-mp4> `
  --output    .\fixtures\<sid>\predicted-ocr.jsonl `
  --sample-fps 0.5 `
  --roi 0,0,1920,300        # optional; e.g. top banner area
python .\eval.py `
  --ground-truth .\fixtures\<sid>\ground-truth-hints.jsonl `
  --predictions  .\fixtures\<sid>\predicted-ocr.jsonl
```

Both prototypes write a `<prototype> summary:` line to stderr (sampled frame count, ms/frame, wall clock, per-stage hits) — this is the data Phase 3's `report.md` will quote.

To extract candidate template images from the fixture for the `templates/<stage>/` dirs:

```powershell
ffmpeg -ss <seconds> -i <fixture-mp4> -frames:v 1 frame.png
# then crop in any image editor and save as templates/<stage>/<descriptive-name>.png
```

## Phase status

| Phase | Deliverable | Status |
| --- | --- | --- |
| 1 | `eval.py` + fixture template | shipped |
| 1 | Real labeled fixture under `fixtures/<sid>/` | operator, out-of-band |
| 2 | `prototype_template_matching.py` | shipped (needs `opencv-python` for `--recording` runs) |
| 2 | `prototype_ocr.py` | shipped (needs `opencv-python` + `paddleocr` for `--recording` runs) |
| 2 | `templates/<stage>/` UI crops | operator, out-of-band (use ffmpeg to extract frames, then crop) |
| 3 | `report.md` with metrics + recommendation | follow-up session, after fixture + templates exist |

Phase 2 deps are research-only and **not** added to `pyproject.toml`; install ad-hoc in your venv.

## References

- PRD: `../prd.md`
- Execution plan: `../implement.md`
- Schema source: `src/arl/segmenter/models.py` (`MatchStageHint`)
- Stage enum: `src/arl/shared/contracts.py` (`MatchStage`)
- Existing keyword classifier (reused by OCR prototype): `src/arl/segmenter/stage_text.py` (`classify_stage_from_text`)

> `prototype_ocr.py` **vendors** `classify_stage_from_text` + the keyword map from `src/arl/segmenter/stage_text.py` to stay `arl.*`-import-free. If the production keyword map changes, mirror the change inside `prototype_ocr.py`'s `_VENDORED_KEYWORDS` block.
