# Vision Analysis Integration and Performance Validation

## Goal

Complete cross-stage integration, prove cache/performance behavior, validate representative exports, and decide when legacy duplicate scanning can be disabled.

## Requirements

- Wire normal postprocess order so visual analysis runs before segmenter/highlights.
- Enable the stage through the `publish` preset while leaving default mode opt-in.
- Update reset/status/quality report and documentation/specs.
- Compare shared vs legacy timer boundaries, KDA events, SFX timing, and death/result behavior.
- Measure initial run and cached rerun cost on segmented and non-segmented recordings.
- Compare initial wall time against the legacy combined timer/KDA baseline with a maximum accepted ratio of 1.25x.
- Verify the default union of local refinement ranges stays within 15% of source duration.
- Re-export representative matches and perform human review of death transitions, endings, and SFX timing.

## Acceptance Criteria

- [ ] Full test and compile checks pass.
- [ ] One coarse decode pass serves all enabled coarse detectors.
- [ ] Cached downstream reruns perform zero coarse OCR calls.
- [ ] Refinement frame count is bounded and attributable to candidate windows.
- [ ] Initial representative publish wall time is <=1.25x the legacy combined timer/KDA scan baseline.
- [ ] Refinement union is <=15% of source duration unless an explicit override is tested and reported.
- [ ] No regression in boundary count, KDA coverage, SFX timing, or export warnings.
- [ ] Human-reviewed samples improve death/ending continuity.
- [ ] A documented rollout decision enables or defers removal of legacy scans.
- [ ] Missing/degraded assets exercise the legacy timer/KDA fallback during the rollout release.

## Out of Scope

- Adding second-wave OCR detectors.
