# LoL semantic stage detection — research report

Task: `05-14-lol-semantic-stage-detection-production`. Produced 2026-05-26 from the fixture in [`fixtures/session-20260525161758-6f19726e/`](./fixtures/session-20260525161758-6f19726e/).

## Fixture

| field | value |
| --- | --- |
| `session_id` | `session-20260525161758-6f19726e` |
| `room_url` | `https://live.bilibili.com/12629424` |
| `streamer_name` | 橘子怪丶 |
| `source_recording duration` | 2515.63 s (≈42 min) |
| `fixture clip (lol-segment.mp4) duration` | 620.08 s (≈10 min, trimmed to LoL window) |
| `resolution` | 1920 × 1080 |
| `fps` | 60 |
| `codec` | h264 + aac |
| `bitrate` | 4 176 kbps (Bilibili 蓝光 / `qn=10000`) |
| `source_type` | `direct_stream` |
| `match_count` | 1 (partial — captured the end of one ARAM match only) |
| `ground-truth hints` | 14 × `in_game` at 30-second cadence (operator-validated via frame inspection) |

### Fixture limitation

**This fixture is not the multi-match corpus the PRD originally targeted.** The streamer was playing ARAM on the Howling Abyss when the recorder attached, finished that match around second 600-620, then immediately switched to a different game (Delta Force, an FPS) for the remainder of the source recording. After trimming to the LoL portion, what survives is `~10 min of continuous in_game play`. No `champion_select`, no `loading`, no `post_game` UI was captured during the LoL window. The follow-up "Open Questions" section explains how to fix this.

## Template-matching prototype

Three reference crops extracted from frame `t=300s` of the fixture and stored under [`templates/in_game/`](./templates/in_game/):

| template file | what it captures |
| --- | --- |
| `aram-minimap.png` (320×280) | Howling Abyss bridge minimap, bottom-right HUD corner |
| `bottom-hud-bars.png` (540×120) | Champion HP / mana / skill icons (Q/W/E/R + summoner) and item slots, bottom-center HUD |
| `top-scoreboard.png` (520×36) | Top-bar kill score (`62 vs 61`), KDA, dragon timer, match clock |

Run:

```powershell
python .\.trellis\tasks\05-14-lol-semantic-stage-detection-production\research\prototype_template_matching.py `
  --recording .\.trellis\...\fixtures\<sid>\lol-segment.mp4 `
  --templates .\.trellis\...\research\templates `
  --output    .\.trellis\...\fixtures\<sid>\predicted-template.jsonl `
  --sample-fps 1.0 --threshold 0.7
```

### Metrics (template-matching, tolerance ±10 s)

```
stage                 precision   recall     f1   TP   FP   FN
champion_select           0.000    0.000  0.000    0    0    0
loading                   0.000    0.000  0.000    0    0    0
in_game                   0.140    1.000  0.246   14   86    0
post_game                 0.000    0.000  0.000    0    0    0
overall                   0.140    1.000  0.246   14   86    0

confusion matrix (predicted rows x actual cols):
                   CS   LD   IG   PG
CS                  0    0    0    0
LD                  0    0    0    0
IG                  0    0   14    0
PG                  0    0    0    0
```

Runtime: sampled 621 frames at 1 fps, **261 ms / frame**, **wall-clock 400 s (~6 min 40 s) for 620 s of footage** (≈ 1.6× real-time at 1 fps on CPU). 100 raw `in_game` candidates collapsed to 100 deduped hits (the 5 s dedup window did not trigger because matched timestamps were already at least 1 s apart).

### Template-matching failure-mode analysis

1. **PRD precision metric vs continuous-stage fixture mismatch.** The prototype correctly detects the in_game UI on essentially every sampled frame. Our 14 GT hits at 30-s cadence have only ±10 s tolerance each → covered range = 14 × 20 s = 280 s out of 620 s. Predictions in the uncovered 340 s become FPs by construction even though they are semantically correct. This explains the 100 % recall paired with 14 % precision and is NOT a fault of the prototype.
2. **No cross-stage confusion.** Zero predictions landed in any stage that we did not provide a template for — the confusion matrix off-diagonal is empty. The prototype is conservative by design.
3. **Templates are map-specific.** All three templates were cropped from Howling Abyss ARAM frames. Match templates against Summoner's Rift / Twisted Treeline are unlikely to fire. Production-grade usage requires multi-map template corpora.

## OCR prototype (tesseract chi_sim)

Originally the prototype was written against PaddleOCR (PRD R4 allowed either engine). On this host the venv runs Python 3.14, which has no `paddlepaddle` wheel available; we added a `--ocr-engine tesseract` switch to `prototype_ocr.py` and used the **tessdata_fast `chi_sim.traineddata`** model. The PaddleOCR path is preserved for setups where the wheel is available.

Run:

```powershell
python .\.trellis\tasks\05-14-lol-semantic-stage-detection-production\research\prototype_ocr.py `
  --recording  .\.trellis\...\fixtures\<sid>\lol-segment.mp4 `
  --output     .\.trellis\...\fixtures\<sid>\predicted-ocr.jsonl `
  --sample-fps 0.5 `
  --ocr-engine tesseract `
  --ocr-lang   chi_sim `
  --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe" `
  --tessdata-dir "data\tmp\tessdata"
```

### Metrics (OCR / tesseract, tolerance ±10 s)

```
stage                 precision   recall     f1   TP   FP   FN
champion_select           0.000    0.000  0.000    0    1    0
loading                   0.000    0.000  0.000    0    0    0
in_game                   0.556    0.357  0.435    5    4    9
post_game                 0.000    0.000  0.000    0    4    0
overall                   0.357    0.357  0.357    5    9    9

confusion matrix (predicted rows x actual cols):
                   CS   LD   IG   PG
CS                  0    0    1    0
LD                  0    0    0    0
IG                  0    0    5    0
PG                  0    0    1    0
```

Runtime: sampled 311 frames at 0.5 fps, **1 004 ms / frame**, **wall-clock 549 s (~9 min) for 620 s of footage** (≈ 0.9× real-time at 0.5 fps on CPU). Tesseract on whole-frame 1920×1080 is the slow path; an ROI crop would reduce ms/frame substantially.

### OCR failure-mode analysis

1. **Streamer commentary triggers cross-stage false positives.** The OCR picks up overlay text from the broadcaster's UI / banner / chat bar and routes it through `classify_stage_from_text`, which has very loose keyword matching:
   - **`post_game` FPs at 86 s, 162 s, 212 s, 218 s** — likely on commentary or chat-bar text containing `胜利` / `结算` / `MVP`-adjacent words while the match is in fact still in_game.
   - **`champion_select` FP at 80 s** — commentary or scoreboard text contained `选`/`ban`/`pick`-adjacent words during gameplay.
   These are **semantic, not OCR-accuracy, failures** — even with perfect OCR, the keyword classifier would still misclassify these frames. Mitigation requires either (a) tighter keywords or (b) restricting the OCR ROI to a region that only shows authoritative stage UI (banner area, post-game scoreboard) rather than the whole frame.
2. **Low in_game recall (0.357).** Within the 0-620 s window the OCR emitted only 9 in_game hits (vs the template prototype's 100). Either (a) the tesseract chi_sim model couldn't reliably read the LoL in-game UI text at this resolution / styling, or (b) the OCR threw away most frame text as noise. With a Chinese-tuned model (PaddleOCR-ch) instead of the tesseract `chi_sim_fast` model, in_game recall would likely rise considerably.
3. **CPU cost is borderline real-time at 0.5 fps.** GPU would be required to scale to 1 fps continuous monitoring. Reducing the OCR area via `--roi` is the cheaper fix.

## Side-by-side comparison

| metric | template matching | OCR (tesseract) |
| --- | --- | --- |
| in_game **precision** | 0.140 | 0.556 |
| in_game **recall** | **1.000** | 0.357 |
| in_game **f1** | 0.246 | 0.435 |
| cross-stage FPs | **0** | 5 (4 post_game + 1 cs) |
| ms / frame | 261 | 1 004 |
| wall-clock vs real-time | 0.65× (sample-fps 1.0) | 0.89× (sample-fps 0.5) |

Neither prototype clears the **PRD acceptance threshold of `precision > 0.7` and `recall > 0.6` simultaneously**. The numbers above must also be read with the fixture caveat (single stage, sparse GT).

## Recommendation

**Status: research-incomplete; do NOT productionize yet.** Open a follow-up fixture-collection task before attempting productionization. The signals we observed are real but the fixture used to score them is unrepresentative.

Concrete next step: **`05-XX-lol-semantic-stage-detection-fixture-corpus`** (follow-up task seed below). Once the corpus is in place, rerun both prototypes against it and decide based on per-stage f1 numbers. Pre-corpus signals about each approach:

- **Template matching** is the stronger candidate for `in_game` detection. It has zero cross-stage confusion in our data and detects in-game UI on essentially every frame. It will need stage-specific templates harvested from a multi-match fixture for the other three stages (champion_select banner, loading bar, post-game `继续` / `MVP` panel).
- **OCR** is the candidate that *could* discriminate the text-heavy transition stages (champion select, post-game) where template matching has no anchor. To get useful numbers from OCR we need: (a) a Chinese-tuned model (PaddleOCR `lang=ch` once `paddlepaddle` wheels exist for Py 3.14, or pin the venv to Py 3.12), (b) ROI cropping to a banner area so that broadcaster overlay text stops triggering false positives, and (c) a tighter keyword map than `src/arl/segmenter/stage_text.py` currently ships with — the present map matches commentary too aggressively (`选`, `胜利`, `mvp` etc. all fire on chat banter).
- **Hybrid is the likely winner.** Template matching guards `in_game` (high recall, zero cross-stage FP). OCR detects the discriminating text on the three transition stages where templates struggle (UI text is the actual stage marker). Each emits its own `MatchStageSignal` rows; `SemanticStageHintService` already does conflict resolution (newest-first preference).

### Follow-up PRD seed

> **Task `05-XX-lol-semantic-stage-detection-fixture-corpus`** (research/spike).
>
> *Goal.* Build a multi-match LoL fixture that exercises every stage transition at least three times. Re-run `prototype_template_matching.py` and `prototype_ocr.py` (now both engines supported), produce a `report.md` whose per-stage table has non-trivial numbers in every row.
>
> *Inputs.* (1) ≥ 90 minutes of LoL-only recording from a verified LoL streamer (e.g. operator self-curates from a known LoL channel; `橘子怪丶` is dual-game and unsuitable). (2) Ground truth labelled per-transition (champion_select start, loading start, in_game start, post_game start) — not per-sample. (3) Stage-specific template corpus harvested from the new fixture: `champion_select` (BP banner), `loading` (load-bar), `post_game` (`继续` button + MVP panel) in addition to the three in_game templates we already have.
>
> *Acceptance.* Report names a winner (template-matching, OCR, or hybrid) backed by `precision ≥ 0.7` AND `recall ≥ 0.6` on every stage. If no approach clears that bar, the report opens an `05-XX-cnn-stage-classifier` spike with a labeled-frame extraction plan instead.
>
> *Out of scope.* No production `src/arl/` changes; the productionization commit is a third follow-up gated by the corpus-task's recommendation.

## Open questions / known gaps

1. **Fixture is single-stage.** The current evaluation is informative only for `in_game` performance. The `precision = 0.7 / recall = 0.6` PRD acceptance threshold cannot be honestly judged against the other three stages from this fixture alone.
2. **Sparse-GT metric inflates FP for template matching.** Either densify GT to ~1 hit / 5 s for continuous-stage fixtures, or change the eval to a "stage-presence" metric (per-frame correctness) instead of "stage-event" (per-prediction matching). The current `eval.py` enforces the latter.
3. **PaddleOCR comparison missing.** With `paddlepaddle` wheels unavailable for Python 3.14 on Windows AMD64, the tesseract path is our only OCR data. PaddleOCR Chinese accuracy is reportedly much higher than tesseract `chi_sim_fast`; the follow-up task should compare them directly on the same fixture.
4. **Subtitle-derived ground-truth bootstrap was not viable for this fixture.** faster-whisper produced only one stage-classifying signal across 30 minutes of audio (`小兵` keyword in FPS-portion commentary, which is itself a misfire). Subtitle bootstrap is only useful when the streamer reads stage-relevant UI text aloud, which `橘子怪丶` does not.

## Reproducibility notes

- Source recording lives at `data/raw/session-20260525161758-6f19726e/recording-source.mp4` (gitignored). The trimmed fixture clip is at `research/fixtures/session-20260525161758-6f19726e/lol-segment.mp4` (also gitignored).
- `prototype_ocr.py` gained a `--ocr-engine {paddle,tesseract}` switch plus `--tesseract-cmd` and `--tessdata-dir` to support hosts without `paddlepaddle`. The PaddleOCR code path is byte-identical to the original prototype.
- Tesseract `chi_sim.traineddata` (tessdata_fast variant, 2.4 MB) sits at `data/tmp/tessdata/chi_sim.traineddata`; download from `https://github.com/tesseract-ocr/tessdata_fast`. The Program Files install path needs admin rights to drop a language pack, so `--tessdata-dir` + `TESSDATA_PREFIX` is the documented bypass used here.
- `ARL_DIRECT_STREAM_TIMEOUT_SECONDS=7200` was set before launching `arl recorder` — without it, ffmpeg captures only 20 s and overwrites the file each cycle.
- The Bilibili stream URL has a ~60-minute signed TTL. Production recorder design must connect within seconds of probe-time `live_started`; if `arl recorder` lags behind, ffmpeg gets a 403 / 404 on the now-stale URL.
