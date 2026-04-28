# brainstorm: continue development progress

## Goal

Deliver the next meaningful MVP increment for `auto-record-live` by hardening the end-to-end retry/recovery orchestration path (recorder -> recovery -> orchestrator), with strict event-flow consistency and regression tests.

## What I already know

* The previous task `04-25-orchestrator-recorder-event-hardening` is complete and archived.
* Current active task is `04-25-continue-dev-progress`; this PRD now defines a concrete milestone.
* The project already has:
* Windows agent event capture
* Orchestrator durable state and recorder-event ingestion
* Recorder/segmenter/subtitles/exporter file-backed pipeline scaffolding
* Optional ffmpeg paths and manual recovery pipeline
* Recent implementation already added:
* Recovery requeue gating (all dispatched actions must be resolved)
* Re-open active recorder job on fresh `recording_retry_scheduled`
* Recorder/recovery/orchestrator recorder-event path unification
* Unknown recorder event compatibility handling (ignored but non-blocking)

## Assumptions (temporary)

* This task should continue MVP progress rather than starting a new unrelated feature.
* We keep scope bounded to retry/recovery hardening and compatibility, not new media capabilities.

## Milestone Selected

* **Primary milestone**: production-grade retry/recovery hardening for orchestrator-recorder event flows.
* **Rationale**:
* This area already has active implementation momentum and concrete tests.
* Reliability defects here can corrupt state and block the full pipeline.
* The scope is implementable in incremental, test-driven slices without major architecture rewrite.

## Requirements (evolving)

* Recorder, recovery, and orchestrator must share one configured recorder-event channel (`orchestrator.recorder_event_log_path`) in all environments.
* Orchestrator recorder-event handling must remain monotonic for recognized transition events while ensuring unknown events cannot block later known transitions.
* Recovery-triggered retry requeue must correctly re-open orchestrator active job pointer when applied.
* New reliability/compatibility behavior must be covered by regression tests at unit and pipeline levels.
* Contract docs under `.trellis/spec/backend/orchestration-contracts.md` must stay synchronized with behavior and tests.

## Acceptance Criteria (evolving)

* [x] Milestone selected with explicit rationale and scope boundaries.
* [x] Recovery requeue emits only under all-resolved action conditions for a job.
* [x] Fresh `recording_retry_scheduled` after terminal failure re-opens `active_recording_job_id`.
* [x] Recorder writes audit events to configured `orchestrator.recorder_event_log_path` (not implicit temp path).
* [x] Unknown recorder events are auditable and do not advance monotonic per-job watermark.
* [x] Regression tests cover the above and pass in full-suite verification.
* [x] Failed jobs still enter manual-recovery routing even if the same `job_id` already exists in recorder `processed_job_ids`.
* [x] Re-opened `retrying` jobs clear stale recorder idempotency markers (`processed_job_ids`, `manual_required_job_ids`) and are processed again.
* [x] Recovery batch resolve/fail events include `action_key` for dispatch-to-status audit correlation.

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* New major milestone areas (direct-stream anti-bot hardening, LoL semantic stage detection, full ASR productization).
* Queue/broker architecture migration or distributed ordering guarantees.
* Frontend UX expansion unrelated to retry/recovery backend reliability.

## Technical Notes

* Current task path: `.trellis/tasks/04-25-continue-dev-progress/`
* Previous completed milestone: `.trellis/tasks/archive/2026-04/04-25-orchestrator-recorder-event-hardening/`
* Core contract: `.trellis/spec/backend/orchestration-contracts.md`
* Core implementation loci:
* `src/arl/orchestrator/service.py`
* `src/arl/recorder/service.py`
* `src/arl/recovery/service.py`
* Core regression suites:
* `tests/orchestrator/test_service.py`
* `tests/pipeline/test_recovery_service.py`
* `tests/pipeline/test_ffmpeg_resilience.py`
