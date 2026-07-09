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
| Teaser segments | 1-3 | 0 = teaser pipeline broken; >3 = fragmented cold open |
| SFX hits | <= 6 kill hits | Rate-limit keeps coin accents special; transition whoosh is counted separately |
| Zoom segments | 1-4 | 0 = close-up triggers broken; >4 = visual fatigue |
| KDA uncovered count | 0 target | Every detected kill/death must fall inside a retained window |
| Title | not raw leading subtitle | `title_equals_raw_leading_subtitle=false`; LLM or scored heuristic required |

2026-07-09 reference run (2 sessions / 4 matches, publish preset, whisper
medium, DeepSeek copywriting): subtitle active 0.84-0.87 (baseline 0.39-0.47),
teaser 4/4 present, BGM beds 3-5, zoom 3 per export, all titles/cover lines
LLM-generated.

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

**Last Updated**: 2026-07-09 (Task: demo2-quality-parity)
