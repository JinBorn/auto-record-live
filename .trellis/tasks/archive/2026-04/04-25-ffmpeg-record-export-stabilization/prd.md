# brainstorm: ffmpeg recording and export stabilization

## Goal

Stabilize the real ffmpeg-based path in the post-live pipeline so recorder and exporter can run on real media inputs with deterministic fallback behavior, then validate with tests and spec-aligned contracts.

## What I already know

* The repository already has optional ffmpeg switches for recorder and exporter.
* Current pipeline still relies on placeholder artifacts for many paths.
* User confirmed next focus is:
  * real recording path stabilization
  * real export path landing
* Existing backend contracts and quality specs are documented under `.trellis/spec/backend/`.
* Current runtime is backend-only Python (no frontend runtime module).

## Assumptions (temporary)

* This task should remain local-first and avoid cloud dependencies.
* ffmpeg failure policy is `fallback-and-continue` for both recorder and exporter.
* MVP priority is operational reliability over maximal feature breadth.

## Open Questions

* None for current MVP scope.

## Requirements (evolving)

* Recorder should use real ffmpeg capture when prerequisites are met.
* Exporter should use real ffmpeg clip + subtitle burn-in when prerequisites are met.
* Both stages should produce deterministic fallback artifacts when prerequisites fail.
* Fallback path should not stop the pipeline and must still emit manifest records.
* Failure reasons must be observable through structured logs.
* Idempotency guarantees must remain intact for repeated runs.
* Tests must cover success path and fallback path contracts.

## Acceptance Criteria (evolving)

* [ ] Recorder runs ffmpeg path when stream URL + ffmpeg availability + config toggle are all satisfied.
* [ ] Recorder writes fallback artifact when ffmpeg path cannot execute.
* [ ] Exporter runs ffmpeg path when valid video input + subtitle + ffmpeg availability + config toggle are all satisfied.
* [ ] Exporter writes fallback artifact when ffmpeg path cannot execute.
* [ ] Logging includes clear, grep-friendly reason phrases for skip/failure/fallback.
* [ ] Unit tests cover idempotency plus fallback behavior under missing prerequisites.

## Definition of Done (team quality bar)

* Tests added or updated for changed runtime behavior.
* Lint and type-check pass for project commands that exist in repo.
* Spec docs updated if contracts or conventions changed.
* Failure and rollback behavior is explicit and test-backed.

## Out of Scope (explicit)

* Full semantic LoL stage classifier upgrades.
* Cloud-distributed media processing.
* New frontend UI implementation.
* End-to-end publishing workflow.

## Technical Notes

* Existing relevant modules:
  * `src/arl/recorder/service.py`
  * `src/arl/exporter/service.py`
  * `src/arl/config.py`
  * `src/arl/shared/contracts.py`
* Existing tests:
  * `tests/pipeline/test_post_live_pipeline.py`
  * `tests/orchestrator/test_service.py`
* Existing contract anchor:
  * `.trellis/spec/backend/orchestration-contracts.md`
