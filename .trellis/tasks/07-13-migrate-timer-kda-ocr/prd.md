# Migrate Timer and KDA OCR

## Goal

Run existing timer and KDA recognition through the shared visual-analysis stage and make segment/highlight consumers prefer the durable asset without changing observable behavior.

## Requirements

- Register timer and KDA detectors with independent due intervals on the shared coarse scan.
- Preserve timer start/end validation and adaptive segment refinement behavior.
- Preserve KDA plausibility, reading-gap, event-delta, post-death and frame-refinement rules.
- Populate compatibility `HighlightPlanAsset.kda_events` from persisted visual events.
- Retain direct-scan fallback while rollout is active and log which source was used.

## Acceptance Criteria

- [ ] Existing timer/KDA OCR and pipeline tests pass against asset-backed consumers.
- [ ] Fixture boundaries and KDA events match legacy behavior within timestamp tolerance.
- [ ] Highlight/edit reruns do not repeat coarse OCR when the visual asset is valid.
- [ ] Fallback behavior works with missing, stale, and partially degraded assets.

## Out of Scope

- Death/result detectors.
- Removing the compatibility KDA field.
