# orchestrator recorder event hardening

## Goal

Harden orchestrator ingestion of `recorder-events.jsonl` so recording job state does not regress when events arrive out-of-order or are replayed.

## What I already know

* Orchestrator now consumes both windows-agent events and recorder audit events.
* Recorder emits retry/failure/success events with `created_at`.
* Current orchestrator implementation updates job status directly from each recorder event, without stale-event filtering.
* Without ordering guards, older recorder events can override newer terminal state.

## Requirements (evolving)

* Recorder-event handling must be monotonic per job by event timestamp.
* Events older than or equal to the last applied recorder event for a job must be ignored.
* Ignored stale events should be auditable.
* Existing status transitions (`retrying`, `failed`, `stopped`) must keep current behavior for fresh events.
* Backward compatibility: state file should remain loadable when the new monotonic metadata is absent.

## Acceptance Criteria (evolving)

* [x] Out-of-order older `recording_retry_scheduled` cannot move a job from `failed` back to `retrying`.
* [x] Duplicate recorder events (same timestamp) are ignored idempotently.
* [x] Audit log contains a clear marker when stale recorder events are ignored.
* [x] Existing orchestrator tests remain green.
* [x] New unit tests cover stale-event ignore behavior.

## Out of Scope

* Cross-process distributed ordering guarantees.
* New queueing system or broker.
* Changes to recorder event schema beyond current fields.
