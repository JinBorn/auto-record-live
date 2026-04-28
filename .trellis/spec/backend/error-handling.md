# Error Handling

> How errors are handled in this project.

---

## Overview
This project currently handles errors in a resilience-first local pipeline.
The primary design goal is to keep long-running loops making forward progress while preserving operator visibility through logs and audit events.

---

## Current Rule Set

The current MVP backend prefers resilient local batch processing over fail-fast behavior for malformed runtime data.

- Invalid JSONL lines from externalized runtime logs are skipped, counted, and surfaced through logs instead of crashing the orchestrator loop.
- Missing local state files are treated as "empty state" bootstrap conditions.
- Unknown event types are audited and ignored unless the caller explicitly needs strict validation.

## Error Types

Current code uses three categories rather than a custom exception hierarchy:

- Validation errors from `pydantic` model parsing
- Parse errors from `json.loads`
- File lifecycle conditions such as "file does not exist yet" or "log was truncated"

---

## Error Handling Patterns

- For file-backed input streams, continue past bad rows and preserve forward progress.
- For persistent state, deserialize the whole state file into a typed model before using it.
- When a runtime anomaly should remain visible to operators, append an audit event and log a short structured message.
- Do not silently coerce unknown `event_type` values into known states.

---

## Local Runtime Error Surface

The current MVP has no HTTP API yet. Runtime observability is exposed through:

- stdout log lines via `arl.shared.logging.log`
- orchestrator audit JSONL entries
- durable state JSON snapshots

---

## Common Mistakes

### Common Mistake: Failing the whole loop on one malformed line

**Symptom**: One bad JSONL event blocks all later valid events.

**Cause**: Parsing the event file as one monolithic JSON document or re-raising parse errors during line iteration.

**Fix**: Process the file line-by-line, count invalid rows, and continue.

**Prevention**: Keep append-only JSONL as the interchange format for local agents.

### Common Mistake: Treating missing state files as fatal

**Symptom**: First run crashes before any session state exists.

**Cause**: Assuming persisted state was already bootstrapped.

**Fix**: Return an empty typed state model when the file is absent.

**Prevention**: All local durable state stores must support zero-state startup.
