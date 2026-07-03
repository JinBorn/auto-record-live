# Design: Condensed Highlight Duration Tightening

## Scope

This task changes the highlight planning/edit planning path, not exporter encoding. The primary files are expected to be:

- `src/arl/highlights/service.py`
- `src/arl/highlights/window_optimizer.py`
- `src/arl/config.py`
- tests under `tests/pipeline/test_highlight_planner_service.py` and `tests/highlights/test_window_optimizer.py`
- `.trellis/spec/backend/export-configuration.md` after behavior is finalized

## Current Problem Shape

The existing planner protects KDA changes and speech boundaries, but it tends to keep large continuous `condensed_key_event` spans. Continuity bridges now prevent the previous KDA/time-jump bugs, but they can also add too much low-value footage. Subtitle cues are useful for narration density, but silent fights must remain protected by KDA and visual/action signals.

## Proposed Architecture

### 0. Dynamic Duration Policy

Use a dynamic target duration instead of a fixed 7-9 or 7-11 minute target. The planner should estimate a target in the approximate 7-20 minute range from:

- source match duration;
- KDA kill/death event count and spacing;
- visual fight/action density;
- subtitle/narration density;
- objective or tactical cue density;
- amount of required continuity needed to avoid source-time/KDA jumps.

Long, exciting, high-density matches may legitimately stay longer. The goal is not to hit a fixed minute count; the goal is to remove low-value time while preserving all high-signal gameplay and narration.

### 1. Composite Retention Signals

Introduce a local planning concept such as `RetentionSignal` or `ProtectedInterval` derived from:

- KDA kill/death cue intervals.
- Subtitle classified cues (`highlight_keyword`, `key_event`, `tactical`).
- Visual action samples around candidate windows, using existing visual analysis hooks where possible.
- Death-like frame detection already used by continuity protection.

The planner should compute whether a time range is:

- `hard_protected`: KDA kill/death moment, death transition, active speech boundary.
- `soft_protected`: likely fight/objective/action from visual density or tactical cue.
- `compressible`: no subtitle, no KDA, no visual action, no edge context requirement.

### 2. Internal Window Compression

Add a post-processing pass after KDA restoration and speech protection that can split long retained windows at compressible gaps.

Rules:

- Only split inside long windows, initially `condensed_key_event` or `condensed_tactical`.
- Compress no-subtitle gaps only when they are not protected by KDA or visual action.
- Keep a short context tail/head around remaining pieces, for example 2-4s.
- Never split inside a speech cue or within the KDA death lead-in guard.
- Run final continuity bridging after splitting.

This pass should be deterministic and testable from synthetic cue/window inputs.

### 3. KDA Preroll/Tail Tightening

Tighten default event windows without removing the event:

- Kill-only default preroll target: about 12-18s.
- Death default preroll target: about 25-35s.
- Postroll target: about 4-8s.

Implementation may expose env values, but the safer first step is to add tests and choose defaults that still preserve the known failure examples.

### 4. Continuity Budget

Keep max source-time gap safety, but reduce bridge cost:

- Bridge snippets should usually be 2-4s, not full edge-context-length windows.
- Total continuity duration should target under 10% of rendered duration.
- If source-gap safety and continuity budget conflict, source-gap safety wins, but the plan should log/report the exception.

### 5. Edge Context Reduction

Reduce match-start/match-end context when it is low-signal:

- Keep short start/end context by default, around 8-15s.
- Allow longer context when subtitle/KDA/visual signal exists near the edge.
- Exporter currently requires a plan to cover match start and match end; this design keeps that contract but makes edge windows shorter.

### 6. Validation Metrics

Add a plan analysis helper or test utility that reports:

- rendered duration;
- duration by window reason;
- subtitle-active ratio on retimed output;
- count and max length of no-subtitle gaps;
- max adjacent source-time gap;
- KDA cue count and uncovered count;
- continuity duration and ratio;
- oversized key-event segments and exception reasons.

This can start as a test/helper script and later become operator telemetry.

## Data Flow

1. `SubtitleAsset` and KDA OCR produce classified cues.
2. `optimize_windows` creates initial windows.
3. Existing passes run: death trimming, action-resolution extension, speech-boundary protection, continuity bridge, duration budget, KDA restoration.
4. New compression pass splits low-value interiors using composite protected intervals.
5. Final passes re-run:
   - speech-boundary protection;
   - continuity bridge;
   - KDA restoration;
   - clamp/merge.
6. `HighlightPlanAsset` persists the latest windows.
7. `EditingPlannerService` builds the edit plan from latest windows.
8. Exporter continues to apply edit-plan subtitle retiming and fixed-bitrate encoding.

## Compatibility

- Existing highlight/edit/export JSONL contracts can remain unchanged.
- New config values should have conservative defaults and be documented.
- Old plans remain valid but should be treated as stale only if shape/config checks are extended deliberately.

## Risks

- Over-trimming silent fights if visual/action protection is weak.
- Reintroducing KDA jumps if internal compression splits too close to KDA transitions.
- Re-growing duration if final continuity bridging adds too many snippets after compression.
- Manual validation can be misleading if it only checks subtitles; the validation report must include KDA and source-gap metrics.

## Rollback

Keep the new compression pass behind a config flag initially, for example `ARL_HIGHLIGHT_CONDENSED_COMPOSITE_TRIM_ENABLED=1` under publish preset only. If validation fails, disable the flag and fall back to the current conservative planner.
