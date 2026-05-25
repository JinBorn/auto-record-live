# LoL semantic stage detection — research / spike

## Goal

**Not** to productionize LoL semantic stage detection in this task. Instead,
build a measurement foundation: a labeled fixture corpus + an evaluation
harness + two cheap signal-source prototypes (template matching, OCR), so a
future productionization task can pick its approach on **data** instead of
intuition.

## User Value

After this task:

- We have ≥ 1 labeled fixture (~30 min recording with 3-5 matches) anchored in real `data/raw/` content.
- We have a reusable evaluation script that compares any "stage hints producer" against ground-truth hints and prints precision / recall / confusion matrix / latency.
- We have empirical numbers on two cheap approaches (template matching, OCR) on real data.
- We have a written research report recommending the next productionization step (which approach, which threshold, which scope) — or a documented "neither hit precision > 0.7 / recall > 0.6, go back to drawing board" decision.

## Confirmed Facts (from code inspection)

- The signal → hint → boundary plumbing is **already in place** (`src/arl/segmenter/`). What's missing is robust **signal sources**, not the downstream machinery.
- Current signal sources: `stage-signals-from-subtitles` (subtitle text) + `arl stage-signal` manual CLI. Both are low-fidelity for visual stage transitions.
- `SemanticStageHintService` consumes `MatchStageSignal` rows + recording assets → emits `MatchStageHint`, which `SegmenterService` consumes to emit `MatchBoundary` (`src/arl/segmenter/semantic_hints.py`, `src/arl/segmenter/service.py`).
- `MatchStageHint` schema: `{session_id, stage, at_seconds | detected_at}`. Reusable as ground-truth format.
- Existing recordings live at `data/raw/<session>/recording-source.mp4` (or `.txt` placeholder).

## Decisions (Q1 + Q2, 2026-05-14)

- ~~Q1 task granularity~~ — **C-research** only; productionization deferred to a follow-up task that uses this task's report as input.
- ~~Q2-a fixture source~~ — **use an existing `data/raw/<session>/recording-source.mp4`** picked manually for "contains 3-5 complete matches"; fixture metadata in git, video binary not in git.
- ~~Q2-b labeling format~~ — **reuse `MatchStageHint` jsonl schema** as ground-truth.
- ~~Q2-c prototype signal sources~~ — **template matching (cv2.matchTemplate) + OCR (tesseract or paddle-ocr)** as the first two; (γ) color/histogram dropped; (δ) CNN deferred.
- ~~Q2-d evaluation metrics~~ — **per-stage precision/recall with ±10 s tolerance + confusion matrix + ms-per-frame inference + total wall-clock per 30 min**.

## Requirements

- **R1 (fixture)**: Operator-curated fixture(s) under `.trellis/tasks/05-14-lol-semantic-stage-detection-production/research/fixtures/<session_id>/`:
  - `metadata.yaml`: fields `recording_path` (absolute or repo-relative path to the .mp4), `duration_seconds`, `match_count`, `notes`.
  - `ground-truth-hints.jsonl`: `MatchStageHint` rows for every stage transition observed by the human annotator. Each match contributes 4 rows (champion_select / loading / in_game / post_game).
  - `.gitignore` entry blocking the .mp4 itself; only metadata + jsonl in git.
- **R2 (evaluator)**: `.trellis/tasks/.../research/eval.py` — standalone script (not part of `arl` package; OK to depend on optional research libs):
  - Inputs: `--ground-truth path/to/ground-truth-hints.jsonl`, `--predictions path/to/predicted-hints.jsonl`, `--tolerance 10.0`.
  - Outputs: per-stage `precision` / `recall` / `f1`, full confusion matrix, total TP/FP/FN counts, and a one-line summary.
  - Tolerance window: a predicted hint counts as TP if there exists a ground-truth hint of the **same stage** within `±tolerance` seconds and that ground-truth hint hasn't already been matched.
- **R3 (template-matching prototype)**: `research/prototype_template_matching.py` — standalone script:
  - Input: `--recording path/to/recording-source.mp4`, `--templates research/templates/` (a dir of cropped UI snapshots labeled by stage), `--output path/to/predicted-hints.jsonl`.
  - Implementation: per-second frame sample via `cv2.VideoCapture` + `cv2.matchTemplate`; emits one `MatchStageHint` per detection above a confidence threshold (per-template).
  - Output: same schema as ground truth; consumable by eval.py.
- **R4 (OCR prototype)**: `research/prototype_ocr.py` — same I/O contract as R3:
  - Implementation: per-2-second frame sample via OpenCV; crop a region-of-interest (post-game banner area / scoreboard area); run tesseract or paddle-ocr; map detected keywords to stages via `classify_stage_from_text()` from existing `stage_text.py` (reuses existing keyword map).
  - Output: same schema as ground truth.
- **R5 (research report)**: `research/report.md` — captures:
  - Fixture description (which session, how many matches, total minutes).
  - Per-prototype evaluation table: precision / recall / f1 per stage, confusion matrix, ms/frame, total wall-clock.
  - Failure-mode analysis: top 3 confusion patterns per prototype.
  - Recommendation: "Productionize template matching with threshold X" / "Productionize OCR with ROI Y" / "Neither met precision > 0.7 / recall > 0.6; try CNN next" / "Hybrid required, see follow-up task definition".
  - Explicit follow-up: a 1-paragraph PRD seed for the productionization task that this report enables.
- **R6 (CLI accessibility, optional)**: If runtime is < 1 minute per fixture, expose the evaluator as `arl semantic-eval --fixture <path>` for ergonomics. Skip if implementation cost is high.

## Acceptance Criteria

- [ ] 1 fixture with `metadata.yaml` + `ground-truth-hints.jsonl` committed to git; the underlying `.mp4` is git-ignored.
- [ ] `python research/eval.py --ground-truth <fixture>/ground-truth-hints.jsonl --predictions <prototype-output>` runs without errors and prints a metrics block.
- [ ] Both prototypes run end-to-end on the fixture and emit a `predicted-hints.jsonl`.
- [ ] `research/report.md` contains the evaluation table + a written recommendation block.
- [ ] Recommendation block names the next concrete task (e.g. "Open task 05-XX-lol-semantic-template-matching-production with config X").
- [ ] No production code changes — `src/arl/` untouched.
- [ ] `arl semantic-eval` (R6) is optional; skip if it costs more than 30 min to wire.

## Out of Scope (deferred to a follow-up productionization task)

- Wiring any prototype into `SemanticStageHintService` as a real signal source.
- Adding new env vars / CLI commands beyond R6.
- Color/histogram prototype (Q2-c, option γ).
- CNN / ML-based classifier (Q2-c, option δ).
- Multi-fixture statistical robustness (this task: 1 fixture suffices to make the next call; broader corpus is a follow-up).
- Multi-source fusion logic (visual signal vs subtitle signal disagreement).
- Real-time / on-the-fly signal extraction during recording (post-hoc only).

## Open Questions

All blockers resolved.

## Notes

- Research/spike task: no production code; deliverable is the report + reusable evaluator + two prototype scripts.
- PRD + `implement.md` only. No `design.md` — the design IS the research, captured in the eventual report.
- If both prototypes fail to meet precision > 0.7 / recall > 0.6, the report's recommendation is **"don't productionize yet, run a follow-up CNN spike"** — that's still a successful task outcome (negative result = saved engineering weeks).
