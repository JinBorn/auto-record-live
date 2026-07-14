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

- [x] Full test and compile checks pass. (769 passed)
- [x] One coarse decode pass serves all enabled coarse detectors. (single coarse schedule feeds timer/scene/kda/match_result/respawn)
- [x] Cached downstream reruns perform zero coarse OCR calls. (cache_hit=compatible_asset; segmenter/highlight force-reprocess consumed shared_asset with no coarse decode)
- [x] Refinement frame count is bounded and attributable to candidate windows. (refinement_max_frames cap + per-range candidate attribution)
- [x] Initial representative publish wall time is <=1.25x the legacy combined timer/KDA scan baseline. (1.09x / 1.02x / 0.96x across three sessions)
- [x] Refinement union is <=15% of source duration unless an explicit override is tested and reported. (15.0% at cap, cap_exhausted persisted)
- [x] No regression in boundary count, KDA coverage, SFX timing, or export warnings. (boundaries 3/4/4 parity; KDA equal-or-higher; quality report only pre-existing subtitle-gap warning)
- [x] Human-reviewed samples improve death/ending continuity. (accepted in sibling death/respawn task; shadow-only this release)
- [x] A documented rollout decision enables or defers removal of legacy scans. (validation-report.md §7: enable schema-3 in publish, keep legacy fallback this release)
- [x] Missing/degraded assets exercise the legacy timer/KDA fallback during the rollout release. (segmenter falls back to legacy timer scan when shared evidence missing/degraded/empty; spec: orchestration-contracts.md)

See `validation-report.md` for full evidence.

## Out of Scope

- Adding second-wave OCR detectors.
