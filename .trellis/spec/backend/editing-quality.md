# Editing Quality Guidelines

> Publish-export acceptance thresholds, the quality-report workflow, and
> integration lessons from the demo2 quality-parity program (2026-07).

---

## Overview

Publish exports are accepted against measurable per-match thresholds, not
visual spot checks. The `quality-report` CLI
(see [Export Configuration](./export-configuration.md), scenario
"Export Quality Report CLI") is the acceptance instrument; this guide records
the thresholds, the regeneration workflow that produces comparable numbers,
and cross-layer lessons that repeatedly caused quality gaps.

---

## Publish Acceptance Thresholds

Enforced by `QualityReportSettings` (env `ARL_QUALITY_REPORT_*`); the report
emits warnings (exit 1 with `--strict`) when violated:

| Metric | Threshold | Rationale |
|---|---|---|
| Subtitle active ratio | >= 0.55 | Whisper `medium` + zh-Hans normalization + display smoothing reaches 0.80+ on real streams; below 0.55 means ASR degraded |
| Long no-subtitle gap | count gaps >= 8s | Long silent stretches are only acceptable inside protected fight/objective spans |
| Max adjacent source gap | <= 45s | Larger jumps produce visible game-clock/KDA teleports |
| Teaser segments | 1-3 | 0 = teaser pipeline broken; >3 = fragmented cold open. Counted as distinct source spans (zoom close-ups split one teaser into adjacent timeline segments) |
| SFX hits | <= 6 kill hits | Rate-limit keeps coin accents special; transition whoosh and teaser impact are counted separately |
| Zoom segments | 1-4 | 0 = close-up triggers broken; >4 = visual fatigue |
| KDA uncovered count | 0 target | Every detected kill/death must fall inside a retained window |
| Title | not raw leading subtitle | `title_equals_raw_leading_subtitle=false`; LLM or scored heuristic required |
| Main duration vs budget | main <= max(target×1.25, target+60s) | Condensed retention budget; teaser cold open re-plays content and has its own budget. Plans that bottom out on protected content carry `budget_exception_reason` and are reported as detail, not warning |

2026-07-09 reference run (2 sessions / 4 matches, publish preset, whisper
medium, DeepSeek copywriting): subtitle active 0.84-0.87 (baseline 0.39-0.47),
teaser 4/4 present, BGM beds 3-5, zoom 3 per export, all titles/cover lines
LLM-generated.

2026-07-10 duration-tuning run (same 4 matches + fresh-streamer bc90812b
m01-03): exports 11.7-14.6 min (was 17.7-23.7 on the 4 main samples), 5/7 in
budget + 2 protected-floor exceptions, KDA uncovered 0 everywhere, 0 warnings.

## Condensed Duration Budget

`analyze_content_density` picks a per-match target (7-20 min dynamic range);
the plan budget is `max(target*1.25, target+60s)` (module function
`condensed_duration_budget`, single source shared by planner and
quality-report). The planner persists `target_duration_seconds` /
`budget_seconds` / `budget_exception_reason` on `HighlightPlanAsset`, and the
report prefers those persisted values so config drift between runs cannot
skew the check.

Convergence is enforced by `_shrink_windows_to_budget`, the FINAL condensed
pipeline stage (after the KDA-restore/bridge fixpoint):

- Trims lowest value-density windows first (`condensed_priority_*` weights ×
  cue overlap).
- Full KDA cue spans (`kda_change`, preroll + OCR reading gap + postroll) are
  never cut — the quality-report coverage check requires the whole span.
- Every cut snaps to a speech-chain boundary: extend to sentence end first,
  retreat to sentence start when dense speech blocks the tail (0.6s chain
  gap). Never cuts mid-sentence.
- Continuity windows shrink tail-only (their head anchors death-screen entry
  protection); floor is bridge size.
- Boundary edge anchors are locked: the edit planner rejects plans whose
  windows do not touch both boundary edges (`no_valid_main_windows`).
- Post-shrink speech protection is extension-capped (<=3s, never retreats a
  boundary — a retreat could cut back into a protected KDA span).
- When protected content + 45s-gap bridging exceed the budget, the plan keeps
  quality guarantees and records `budget_exception_reason` instead.

Env knobs: `ARL_HIGHLIGHT_CONDENSED_BUDGET_SHRINK_ENABLED` (rollback switch),
`..._BUDGET_TRIM_STEP_SECONDS` (15), `..._BUDGET_MAX_SPEECH_EXTENSION_SECONDS`
(3), `ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED`.

## Lesson: Mid-Pipeline Budget Caps Get Re-Inflated

The original budget enforcement ran mid-pipeline; the KDA-restore, uncapped
speech-boundary protection, and gap bridging that followed re-inflated plans
to 1.7-2.2x budget with no re-check (17.7-23.7 min exports vs ~10.3 min
targets). The ASR upgrade was the trigger: at 84-87% subtitle coverage,
uncapped speech protection can extend almost any window. Duration control
must be the LAST stage that can grow a plan, or every later stage needs a
budget-aware cap.

## Regeneration Workflow For Comparable Numbers

Stage order matters; skipping a stage leaves stale downstream assets that
corrupt the comparison:

```powershell
$env:ARL_POSTPROCESS_PRESET='publish'
python -m arl.cli subtitles --session-id <id> --match-indices <n,..> --force-reprocess
python -m arl.cli highlight-planner --session-id <id> --match-indices <n,..> --force-reprocess
python -m arl.cli copywriter --session-id <id> --match-indices <n,..> --force-reprocess  # semantic hints BEFORE edit-planner
python -m arl.cli edit-planner --session-id <id> --match-indices <n,..> --force-reprocess
python -m arl.cli exporter --session-id <id> --match-indices <n,..> --force-reprocess
python -m arl.cli copywriter --session-id <id> --match-indices <n,..>                    # plain: repair packages, semantic cache hit
python -m arl.cli quality-report --session-id <id> --match-indices <n,..>
```

- The first `copywriter` run must precede `edit-planner` so LLM teaser
  recommendations exist when teasers are selected; the final plain run reuses
  the cached semantic asset (same input fingerprint), so copy does not drift
  between the edit plan's transition hook line and the published package.
- Long ffmpeg stages on operator laptops should run detached (Task Scheduler)
  so an editor/agent session crash cannot kill a 30+ minute export batch.

## Lesson: Keyword Tables Overfit To Reference Content

`CopywriterService._summary_headline`'s hardcoded keyword-phrase tables were
tuned on demo1/demo2 vocabulary. On any other streamer they degraded to raw
ASR excerpts with traditional-Chinese mojibake (e.g.
`堆場式是咋的 就對面的人他也會...` as a published title). Content-specific
keyword tables must never be load-bearing for publish copy:

- The LLM semantic layer (`ARL_LLM_ENABLED=1`) is the primary title/cover
  path; heuristics are a fallback, and the fallback must score and compose
  rather than echo the first subtitle line.
- Acceptance must include at least one session from a streamer whose
  vocabulary the keyword tables never saw.
- `quality-report` flags `title_equals_raw_leading_subtitle` per match.

## Lesson: In-Memory Cues Are Not A Cross-Stage Contract

The SFX/zoom KDA alignment shipped reading `kda_change ...` cues from SRT
files, but no stage ever wrote such lines into an SRT: the highlight planner
generated them as in-memory `ClassifiedCue`s only. Unit tests on both sides
passed (each hand-fed its own cues) while the integrated pipeline silently
fell back to segment-start SFX for every real export.

- Synthetic planner events consumed downstream must be persisted on a durable
  asset. `HighlightPlanAsset.kda_events` (additive, default `[]`) now carries
  them; the edit planner merges plan events with subtitle cues in
  `_kda_kill_events` / `_kda_event_timestamps`.
- Never burn machine cues into SRT files: burned-in subtitles render every SRT
  line, so `kda_change` rows in subtitle files would appear on screen.
- Regression: `test_audio_mixing_aligns_sfx_to_highlight_plan_kda_events`
  drives alignment purely from plan events with a clean SRT.

## Lesson: Out-Of-Order Concat Branches Explode Memory

A teaser whose source position is late in the match, rendered through a
single-input `trim`-branch `filter_complex`, forces ffmpeg to decode the whole
boundary before the first output frame while every retained main-segment frame
queues unconsumed — 23GB RSS on a 22-minute boundary froze the whole machine
(and the GPU "disappearing" from Task Manager was an OOM side effect, not a
driver defect; the freshly reinstalled driver crashed the same way until the
graph was fixed).

- `_edit_plan_ffmpeg_command` gives every teaser segment its own seeked input
  (`-ss <abs_start> -t <dur+1> -i recording`); main segments stay on shared
  input 0 because their source order matches concat consumption order.
- Symptom signature for future triage: export output stalls at a few dozen MB,
  ffmpeg RSS grows unbounded, stderr fills with `get_buffer() failed` /
  `Cannot allocate memory (-12)`.
- Any new timeline feature that plays source content out of order must either
  use a dedicated seeked input or prove the buffered span is small.

---

## Related Documentation

- [Export Configuration](./export-configuration.md) — full scenario contracts
  for the exporter, edit plans, quality-report CLI, and env reference.

---

**Last Updated**: 2026-07-10 (Task: batch-review-duration-tuning)
