# brainstorm: next development iteration

## Goal

Define the next implementation milestone for `auto-record-live` and converge on one executable MVP scope (single iteration) before coding.

## What I already know

- Previous task `04-26-continue-dev-progress` is completed and archived on 2026-04-27.
- Current backend pipeline is functional end-to-end for local MVP: windows-agent -> orchestrator -> recorder -> segmenter -> subtitles -> exporter.
- Reliability hardening and regression coverage are already substantial (latest local gate: `python unittest` and `npm test:probe` green).
- You selected next milestone priority: ffmpeg retry/recovery production hardening.
- README explicitly lists remaining production-grade gaps:
  - direct-stream acquisition hardening against Douyin page changes / anti-bot variance
  - semantic stage-hint producer hardening for LoL phases
  - offline ASR (`faster-whisper`) hardening
  - retry/recovery hardening around ffmpeg failures
- Existing implementation already includes:
  - in-run ffmpeg retries + cross-run retry scheduling
  - 4xx non-retryable classification and short-circuit logic
  - manual recovery action dispatch/resolve/fail and requeue gating on latest effective action set
  - orchestrator monotonic recorder-event handling and retry/manual recovery routing

## Milestone Selected

- **Primary milestone**: production-grade ffmpeg retry/recovery hardening.
- **Rationale**:
  - This is the most direct path to reduce failed recordings and operator intervention.
  - Current code already has a stable baseline, so this iteration can focus on robustness and observability depth.

## Assumptions (temporary)

- Next iteration should focus on one primary milestone to keep risk and validation scope bounded.
- We should prioritize production reliability over adding brand-new feature surfaces.
- Existing CLI-first workflow and file-backed contract model remain unchanged in this iteration.

## Open Questions

- (none currently)

## Candidate MVP Boundaries

### Option A: Decision Consistency + Observability (Recommended)

- Focus:
  - tighten retry/non-retry classification into explicit, auditable decision paths
  - standardize recorder/recovery/orchestrator event reason semantics
  - improve operator diagnostics (`why retried`, `why fallback`, `why manual`) via structured logs/summary fields
- Pros:
  - low blast radius and fast delivery
  - directly reduces misclassification and troubleshooting cost
- Cons:
  - does not change retry timing/cadence strategy

### Option B: Option A + Retry Policy Hardening

- Focus:
  - include Option A
  - add stricter retry policy controls (for example category-specific retry budget/cooldown)
- Pros:
  - improves both correctness and retry behavior quality
- Cons:
  - medium complexity; more state-transition regression cases

### Option C: Option B + Recovery Automation Guardrails

- Focus:
  - include Option B
  - add manual-recovery flow guardrails (for example stale-pending observability/escalation helpers)
- Pros:
  - strongest operator experience in long-running environments
- Cons:
  - highest complexity and larger scope for one iteration

## MVP Boundary Selected

- **Chosen**: Option A (Decision Consistency + Observability)
- **In scope**:
  - make retry/non-retry/manual decision paths deterministic and auditable
  - align recorder/recovery/orchestrator reason semantics and transition logs
  - improve operator diagnostics and summary visibility for retry/fallback/manual routes
- **Out of scope for this iteration**:
  - retry cadence/policy redesign (budget by category, cooldown scheduling)
  - new recovery automation or escalation workflow features
  - broad architecture changes beyond existing file-backed contracts

## Observability Deliverable Selected

- **Chosen**: log-field standardization as first priority.
- **In scope**:
  - normalize retry/recovery decision context in recorder/recovery/orchestrator logs
  - make reason/category/recoverable/decision semantics consistent and machine-checkable
  - add regression assertions for critical transition log payloads
- **Out of scope (this round)**:
  - building new aggregated summary/reporting surfaces as a primary deliverable
  - expanding CLI UX beyond what is needed to keep log semantics consistent

## Compatibility Strategy Selected

- **Chosen**: direct replacement (breaking contract) for targeted log fields.
- **In scope**:
  - replace legacy log-field semantics with one standardized schema on recorder/recovery/orchestrator critical events
  - update tests/specs/docs to new canonical field contract in the same iteration
- **Out of scope (this round)**:
  - dual-write compatibility layer
  - runtime switch for old/new log schema

## Migration Support Selected

- **Chosen**: migration note documentation only.
- **In scope**:
  - document breaking log-field changes in README/spec with before/after examples
  - include operator-facing upgrade notes for impacted parsing/diagnostic workflows
- **Out of scope (this round)**:
  - historical log conversion script/tooling
  - automated migration runner

## Event Coverage Selected

- **Chosen**: minimal core event set only.
- **In scope events**:
  - `recording_retry_scheduled`
  - `ffmpeg_record_failed`
  - `ffmpeg_fallback_placeholder`
  - `recording_manual_recovery_required`
  - `manual_recovery_action_dispatched`
  - `manual_recovery_action_resolved`
  - `manual_recovery_action_failed`
- **Out of scope (this round)**:
  - broad orchestrator-side `recording_job_*` event schema replacement
  - full event-family migration across all pipeline audit rows

## Canonical Field Set Selected

- **Chosen**: unified decision fields.
- **Canonical fields**:
  - `decision`
  - `failure_category`
  - `is_retryable`
  - `reason_code`
  - `reason_detail`
- **Notes**:
  - these fields become the source of truth for the selected core event set
  - legacy/ambiguous semantics (`reason`, `recoverable` as primary meaning) are replaced on targeted events

## Reason Code Governance Selected

- **Chosen**: strict enum only.
- **In scope**:
  - `reason_code` must be one of predefined canonical values on the selected core event set
  - unknown or free-form values are treated as contract violations and must fail regression tests
- **Out of scope (this round)**:
  - runtime compatibility aliases for legacy/free-form reason codes
  - relaxed parsing that silently accepts unknown reason codes

## Unknown-Classification Fallback Selected

- **Chosen**: fail closed.
- **In scope**:
  - when classification evidence is insufficient, emit `reason_code=unknown_unclassified`
  - for this fallback path, set `is_retryable=false`
  - route directly to manual recovery path without automatic retry
- **Out of scope (this round)**:
  - one-shot guarded retry for unknown classifications
  - preserving legacy fallback decision behavior for unknown failures

## Failure Category Baseline Selected

- **Chosen**: minimal 5-category taxonomy.
- **In scope categories**:
  - `http_4xx_non_retryable`
  - `http_5xx_retryable`
  - `network_timeout_retryable`
  - `ffmpeg_process_error_retryable`
  - `unknown_unclassified_non_retryable`
- **In scope guarantees**:
  - deterministic mapping from representative failure inputs to these categories
  - retryability decisions are consistent with category suffix semantics
- **Out of scope (this round)**:
  - expanding taxonomy to additional operational categories beyond the core 5

## Unknown-Spike Escalation Selected

- **Chosen**: per-recording escalation threshold.
- **In scope**:
  - when `unknown_unclassified_non_retryable` occurs `>=3` times for the same `recording_id` within 30 minutes, emit a dedicated escalation signal
  - escalation signal must be machine-checkable (structured log/event payload) and operator-visible in existing diagnostics flow
- **Out of scope (this round)**:
  - introducing external alert integrations (pager/chat/webhook)
  - global-only aggregation without per-recording threshold detection

## Migration Documentation Location Selected

- **Chosen**: README + backend spec.
- **In scope**:
  - add concise operator-facing breaking-change migration note in `README.md`
  - add canonical old/new field mapping details in `.trellis/spec/backend/logging-guidelines.md`
- **Out of scope (this round)**:
  - migration documentation only in a single location
  - separate migration tooling docs outside README/spec

## Validation Strategy

- **Chosen**: local/offline validation only.
- **Validation baseline**:
  - Python unit/integration test suite
  - Node probe test suite
  - static quality gate (lint + type-check)
- **Not required in this iteration**:
  - real Douyin live-stream smoke/e2e acceptance

## Requirements (evolving)

- Produce a clear, implementation-ready PRD for exactly one next milestone.
- Define explicit in-scope vs out-of-scope items for the selected milestone.
- Define testable acceptance criteria aligned with current Trellis quality gate.
- For ffmpeg retry/recovery hardening, define target behavior for:
  - strict `reason_code` enum enforcement on selected core events (no free-form values)
  - unknown-classification fallback must fail closed (`unknown_unclassified`, non-retryable, manual recovery path)
  - representative failure mapping must deterministically cover the selected 5-category baseline
  - unknown failure spikes must trigger per-recording escalation at `>=3` occurrences within 30 minutes
  - breaking-change migration notes must be published in both `README.md` and backend logging spec
  - retry eligibility classification (deterministic, auditable)
  - retry budget/cadence policy (out of scope in this iteration; keep current behavior stable)
  - manual-recovery handoff trigger and requeue safety
  - operator-facing observability via standardized logs (why retried / why stopped / why manual)
  - canonical field set on selected events: `decision/failure_category/is_retryable/reason_code/reason_detail`
- Validation scope is local/offline only; acceptance must be fully test-automatable in repo.

## Acceptance Criteria (evolving)

- [x] One milestone selected with explicit rationale.
- [x] MVP scope boundaries are explicit (included/excluded).
- [x] Validation scope (local/offline) is explicit.
- [ ] Acceptance criteria are testable and map to concrete checks/tests.
- [ ] Key risks and fallback strategy are stated.
- [ ] Retry classification outcomes are deterministic for representative failure reasons and covered by regression tests.
- [ ] Representative failure mapping covers the selected 5-category baseline with deterministic assertions for category + retryability outcomes.
- [ ] Recorder/recovery/orchestrator logs expose consistent reason semantics across retry/fallback/manual transitions.
- [ ] Targeted log events emit only standardized fields/semantics; tests and contracts are updated accordingly.
- [ ] `reason_code` values are enforced by a closed enum on selected core events, with regression tests proving unknown values fail validation.
- [ ] Unknown-classification fallback is deterministic and fail-closed: `unknown_unclassified`, `is_retryable=false`, and manual-recovery routing with regression coverage.
- [ ] Per-recording unknown-failure escalation triggers deterministically at `>=3` `unknown_unclassified_non_retryable` occurrences within 30 minutes, with regression coverage.
- [ ] Breaking-change impact is documented with explicit operator migration notes.
- [ ] Migration notes are delivered in both `README.md` (operator summary) and `.trellis/spec/backend/logging-guidelines.md` (canonical mapping detail).
- [ ] Migration notes include concrete old/new field mapping examples for operators.
- [ ] Schema replacement is limited to the selected core event set, with no unintended expansion.
- [ ] Selected core events expose canonical fields with deterministic value mapping assertions.

## Definition of Done (team quality bar)

- Requirements confirmed by user.
- PRD finalized and implementation-ready.
- Proposed milestone can be implemented in incremental PRs.
- Quality gate expectations remain explicit (lint, type-check, tests, spec sync).

## Out of Scope (explicit)

- Implementing multiple major milestones in the same iteration.
- Frontend/UI feature development.
- Architecture migration away from current file-backed MVP contracts.
- Retry policy/cadence redesign and automation-heavy recovery workflow changes.
- Backward-compatible dual-write or runtime schema-switching for standardized logs.
- Historical log backfill/conversion tooling.
- Non-core event-family schema replacement beyond the selected minimal set.

## Technical Notes

- New task path: `.trellis/tasks/04-27-next-dev-iteration-prd/`
- Current repo status reference: `README.md` sections "Development Status" and "Not implemented yet".
- Current pipeline modules: `src/arl/{windows_agent,orchestrator,recorder,recovery,segmenter,subtitles,exporter}`.
