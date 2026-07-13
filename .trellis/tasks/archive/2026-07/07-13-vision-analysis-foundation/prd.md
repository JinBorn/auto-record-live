# Vision Analysis Asset and Shared Scanner

## Goal

Build the reusable recording-scoped visual-analysis stage, typed assets, shared coarse/refinement decoder, caching, telemetry, CLI/state/reset/status integration, and detector plugin boundary.

## Requirements

- One decoded coarse frame is reusable by multiple due detectors.
- Support segmented and ordinary recordings on the recording-relative source timeline.
- Persist versioned readings/events with input and detector-config fingerprints.
- Merge overlapping bounded refinement requests.
- Enforce a configurable default refinement-union cap of 15% of match/source duration and persist cap-exhaustion telemetry.
- Best-effort per-detector health and failure isolation.
- Add feature flags, CLI force/target filters, state, reset, status, and tests.
- Provide timer/KDA adapter seams, but do not migrate their production consumers in this child.
- Define a versioned 1920x1080 LoL Chinese-client layout profile and reject unsupported frame geometry rather than applying unvalidated crop scaling.

## Acceptance Criteria

- [x] Synthetic detectors prove one decode schedule can serve multiple regions.
- [x] Cache hit avoids video decoding; changed input/config invalidates correctly.
- [x] Chunk-boundary timestamps and merged refinement ranges are tested.
- [x] Detector exceptions degrade one detector without losing other results.
- [x] Cost telemetry is persisted and visible through status/logs.
- [x] Adding detector callbacks does not cause duplicate coarse frame decoding.

## Out of Scope

- Switching segmenter/highlight production consumers.
- New death/result recognition logic.
- 720p and other resolution profiles.
