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
| Teaser segments | 1-3 | >3 = fragmented cold open. Counted as distinct source spans (zoom close-ups split one teaser into adjacent timeline segments). 0 is acceptable ONLY with `EditPlanAsset.teaser_omitted_reason` recorded — LLM recommendations must anchor on a KDA span or teaser keyword signal (human review 2026-07-10: unanchored picks made messy cold opens; main-only beats a bad teaser) |
| SFX hits | <= 6 kill hits | Rate-limit keeps coin accents special; transition whoosh and teaser impact are counted separately. Publish mode refines coarse KDA changes to the first stable video frame and uses zero fixed timing offset; kill accents render at -7dB (human review 2026-07-10: coarse OCR timestamps landed late and the old gain was too quiet) |
| Zoom segments | 1-4 | >4 = visual fatigue. Zooms must anchor on KDA kills or chat bursts (mid-segment fallback disabled 2026-07-10 — zooms on uninteresting content); 0 is acceptable when the match has no KDA events |
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

2026-07-11 human-review run (same 7 samples): subtitles retuned for <=2 lines
on screen with source-faithful timing; kill SFX uses frame-refined KDA timing at
-7dB; fallback zooms off;
unanchored teasers rejected (main-only allowed with recorded reason); single
cover; titles up to 45 chars. Subtitle-active ratio expected lower than the
0.8+ reference as a deliberate readability trade.

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
- Post-shrink speech protection finishes the current subtitle sentence before
  cutting. Terminal punctuation ends the protected sentence even when the next
  cue begins within the normal speech-chain gap, so long continuous commentary
  remains trimmable between sentences. A 12s configurable safety cap bounds
  punctuation-poor/pathological ASR chains; protection never retreats a
  boundary — a retreat could cut back into a protected KDA span).
- When protected content + 45s-gap bridging exceed the budget, the plan keeps
  quality guarantees and records `budget_exception_reason` instead.
- Combat continuity is a hard protection source alongside KDA. Combat-related
  key/tactical windows seed adaptive encounter intervals; candidate-local frame
  activity and nearby narration determine how far protection extends. Internal-gap
  trimming and final budget shrinking consume the same merged protection set.
- Combat release uses hysteresis (`enter > release`) and several consecutive low
  samples. Fixed durations are debounce/lookaround/safety controls, not the primary
  encounter boundary. If video evidence is unavailable, the containing retained
  combat window remains protected using cue/KDA evidence.

Env knobs: `ARL_HIGHLIGHT_CONDENSED_BUDGET_SHRINK_ENABLED` (rollback switch),
`..._BUDGET_TRIM_STEP_SECONDS` (15), `..._BUDGET_MAX_SPEECH_EXTENSION_SECONDS`
(12, pathological sentence-chain safety cap),
`ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED`.

Combat continuity knobs: `ARL_HIGHLIGHT_CONDENSED_COMBAT_CONTINUITY_ENABLED`,
`..._COMBAT_SAMPLE_INTERVAL_SECONDS`, `..._COMBAT_ENTER_ACTIVITY_THRESHOLD`,
`..._COMBAT_RELEASE_ACTIVITY_THRESHOLD`, `..._COMBAT_LOOKAROUND_SECONDS`,
`..._COMBAT_RELEASE_SAMPLES`, and `..._COMBAT_SAFETY_CAP_SECONDS`.

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

## Scenario: Shared LLM Story Semantics and Highlight Finalization

### 1. Scope / Trigger

- Trigger: one per-match LLM result is consumed by highlight planning, edit
  planning, publishing copy, status, reset, and shadow validation.
- Goal: semantic ranking may improve story quality, but it must never invent
  source timestamps or bypass KDA, boundary, source-gap, or duration contracts.

### 2. Signatures

- `CopywriterService.run_semantic(..., force_reprocess=False)` writes
  `copywriter-semantic-assets.jsonl` and, in shadow mode,
  `copywriter-semantic-shadow-reports.jsonl`.
- `HighlightPlannerService.run(..., force_reprocess=True)` is the active
  `highlight-finalize` pass after semantic analysis.
- `semantic_reference_id(prefix, *parts)` is the single stable-ID helper used
  by producers and consumers.

### 3. Contracts

- Semantic results contain `story_status`, optional `primary_angle`, candidate
  decisions, evidence references, unified publishing copy, and teaser candidate
  IDs. Arbitrary LLM timestamps are not accepted for story ranking.
- Stable candidate IDs hash session, match, source start/end, and reason.
  Subtitle/KDA evidence IDs hash their durable source fields.
- Cross-stage consumers use `arl.shared.semantic_contracts.SemanticAssetView`;
  upstream highlight code must not import downstream copywriter models.
- Environment:
  - `ARL_LLM_STORY_ANALYSIS_ENABLED` (default off)
  - `ARL_LLM_STORY_SHADOW_MODE` (default on)
  - `ARL_HIGHLIGHT_SEMANTIC_WEIGHT` (default 0.25, clamped 0-1)
  - `ARL_LLM_SEMANTIC_SCHEMA_VERSION` (default 2)
- Shadow assets are report-only: edit-planner and publishing must ignore
  non-legacy story assets while shadow mode is enabled.

### 4. Validation & Error Matrix

- Unknown/duplicate candidate ID -> reject result and retry/fallback.
- Missing required key-event candidate decision -> reject result.
- Unknown subtitle/KDA evidence -> reject result.
- Unsupported double/triple/quadra/penta claim vs maximum KDA kill delta ->
  reject result.
- Weak teaser without KDA evidence or strong emotion + clear outcome -> remove
  teaser candidate without rejecting the rest of the story.
- `no_strong_story` -> no teaser; active edit plan records the omission reason.
- LLM timeout/invalid JSON/schema -> keep deterministic pipeline; export must
  not fail solely because semantic analysis failed.

### 5. Good/Base/Bad Cases

- Good: active mode performs deterministic planning, one semantic call, then a
  forced finalization pass whose value density uses bounded semantic overlap.
- Base: story analysis disabled, shadow enabled, or semantic weight zero follows
  the legacy finalizer and publishing behavior.
- Bad: delete windows directly from a completed plan, consume story assets in
  shadow mode, or import `arl.copywriter.models` from `arl.highlights`.

### 6. Tests Required

- Stable IDs match between prompt producer and highlight consumer.
- Weight zero produces no semantic references and multiplier 1.0.
- Drop semantics lower density but remain positive; hard protection tests stay
  green.
- Shadow assets do not affect edit plans or publishing packages.
- Active postprocess calls highlight finalization with
  `force_reprocess=True` and preserves session filters.
- Full pytest and compileall pass; three representative shadow reports are
  reviewed before active rollout.

### 7. Wrong vs Correct

#### Wrong

```python
# Downstream-owned model leaks into the upstream planner, and shadow results
# can silently change production output.
from arl.copywriter.models import CopywriterSemanticAsset
plan.windows = [window for window in plan.windows if llm_says_keep(window)]
```

#### Correct

```python
from arl.shared.semantic_contracts import SemanticAssetView

# Candidate discovery remains deterministic. Semantic overlap affects only the
# final budget value density, and only in active mode.
reference = planner._semantic_reference_for_plan(existing_plan, semantic_asset)
planner._active_semantic_reference = reference
final_windows, reason = planner._finalize_condensed_windows(...)
```

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

## Lesson: Coarse KDA Samples Are Not Frame-Timed Events

- The regular KDA scan is only a coarse detector. Its `current_at` timestamp
  must not be used directly for frame-timed sound effects.
- In publish mode, rescan every frame between the last stable reading and the
  changed coarse reading. Accept the change only after the same new KDA is
  visible for at least three consecutive frames, and use the first frame in
  that stable run as the event timestamp.
- Never compensate coarse OCR timestamps with a fixed negative offset. HUD
  and sampling latency vary per event, and fixed offsets preserve false hits.
- If refinement cannot confirm a transition, omit the decorative SFX.

## Semantic SFX Selection Contract

- Optional reaction/comedic SFX uses the existing one-call copywriter semantic asset;
  do not add an independent edit-planner LLM call.
- Deterministic code owns stable candidate IDs, subtitle evidence IDs, source anchors,
  source-to-rendered mapping, library paths, gain, spacing, and count limits. The LLM
  may return only a known candidate ID, an available non-reserved category or `none`,
  confidence, evidence references, and a reason.
- Candidate discovery is streamer-centric and subtitle-evidenced. Ordinary teammate or
  opponent behavior and unattributed visual motion do not create MVP candidates.
- Deterministic kill/multi-kill, transition, and teaser effects win every timing
  conflict. Optional semantic categories never fall back to `kill_coin`.
- Defaults are conservative: shadow mode on, confidence >=0.80, at most two optional
  effects per match, at most one per category, and >=8s from any existing effect.
- Missing/invalid LLM output, unavailable categories, trimmed-out candidates, and
  absent credentials preserve the existing deterministic sound-effect list.
- `copywriter-semantic-sfx-shadow-reports.jsonl` is the rollout evidence. Do not enable
  active mode until representative real-match category/timing decisions are reviewed.

## Common Mistake: Treating Mixed Kill/Death KDA Changes As Confirmed Kills

**Symptom**: A coin accent plays immediately after the streamer dies. The KDA
cue shows both counters changing, for example
`kills=2->3 deaths=0->1 previous_at=560.000 current_at=594.000`.

**Cause**: The edit planner checked only that `kills` increased. A coarse OCR
observation interval can contain both a kill credited to the streamer and the
streamer's death, but it does not prove that the detected timestamp is a clean
streamer-kill moment suitable for a decorative coin accent.

**Fix**: `_kda_kill_event_from_cue` may create an SFX event only when kills
increase and deaths do not increase. If deaths increase, omit the SFX even when
kills also increase in the same cue.

```python
# Wrong: mixed kill/death changes become coin hits.
if current_kills > previous_kills:
    emit_kill_sfx()

# Correct: only an unambiguous kill-only KDA change becomes a coin hit.
if current_kills > previous_kills and current_deaths <= previous_deaths:
    emit_kill_sfx()
```

**Prevention**: Keep regressions for pure kill, pure death, and mixed
kill/death cues. The mixed regression must use `HighlightPlanAsset.kda_events`,
because that is the durable event path used by real exports.

## Lesson: Subtitle Timing Fidelity Beats Artificial Coverage

- Do not extend Whisper word-timestamp cues to satisfy a subtitle-active-ratio
  target. Publish defaults use no minimum display duration, no silence-gap
  filling, a 0.15-second trailing hold, and 80ms VAD speech padding.
- The quality-report subtitle active-ratio floor is 0.40 after removing the
  former artificial holds. A lower ratio is expected when silence is rendered
  without stale subtitles.

## Lesson: CUDA_PATH May Point at an Incompatible Toolkit

- `ctranslate2` 4.x requires CUDA 12 cuBLAS (`cublas64_12.dll`) even when the
  NVIDIA installer changes `CUDA_PATH` and PATH to a newer CUDA 13 toolkit.
- Before importing/loading a CUDA Whisper model on Windows, discover and
  register the installed CUDA 12 `bin` and cuDNN 9 `bin` directories with both
  `os.add_dll_directory` and the process PATH. Keep returned directory handles
  alive for the process lifetime.
- Validate the repair with a real CUDA model load and transcription, not only
  `nvidia-smi` or file-existence checks.

---

## Related Documentation

- [Export Configuration](./export-configuration.md) — full scenario contracts
  for the exporter, edit plans, quality-report CLI, and env reference.

---

**Last Updated**: 2026-07-11 (Task: human-review-fixes)
