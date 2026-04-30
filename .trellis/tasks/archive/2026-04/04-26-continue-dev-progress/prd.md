# brainstorm: continue development progress

## Goal

Deliver the next reliability increment for `auto-record-live` by hardening recovery requeue readiness when the same recorder job enters multiple manual-recovery cycles.

## What I already know

* The previous task `04-25-continue-dev-progress` is archived and completed.
* Current pipeline has recorder -> recovery -> orchestrator end-to-end retry/manual-recovery behavior with regression tests.
* Recovery currently tracks action state by `action_key` and supports job-level and action-level status updates.
* Recovery maintains historical actions in active files unless explicit `maintenance` is run.

## Milestone Selected

* **Primary milestone**: multi-cycle manual-recovery readiness for same `job_id`.
* **Rationale**:
* Operators may run several manual-recovery cycles for one recording job.
* Historical failed actions should not permanently block requeue once a newer corrective action cycle is fully resolved.
* This is a bounded backend reliability fix with high impact and low blast radius.

## Requirements (evolving)

* Recovery requeue gating must evaluate readiness against the latest effective action set for a job (not raw historical accumulation).
* A newer action entry for the same job/action_type must supersede older terminal history when deciding whether requeue can be emitted.
* Existing safety behavior remains:
* unresolved/pending current actions still block requeue
* current failed actions still block requeue
* recovery status/audit events remain traceable by `action_key`
* Behavior changes must be covered by regression tests in recovery pipeline tests.
* Contract docs under `.trellis/spec/backend/orchestration-contracts.md` must stay synchronized.
* Subtitle stage should support optional real transcription path and deterministically fall back to placeholder subtitles when provider/dependency/media preconditions are not met.
* Windows-agent detection should fall back to HTTP probe path on Playwright probe errors and still attempt direct-stream URL extraction.
* Windows-agent direct-stream extraction should recognize percent-encoded stream URL payloads from page/network probe content to reduce false offline snapshots.
* Windows-agent direct-stream extraction should also tolerate multi-layer percent-encoded payloads (`%25` wrapped) and `\xNN` escaped URL fragments from script/network payloads.
* Recorder/Orchestrator ffmpeg failure classification should treat clear HTTP 4xx input-side failures as non-recoverable to avoid noisy cross-run retry scheduling.
* Recorder should short-circuit in-run ffmpeg retries when failure reason is already classified non-recoverable.
* Recorder manual-recovery action mapping should infer actionable `failure_category/action_type` from `stop_reason/recovery_hint` when category is missing, to reduce generic inspect-only actions.
* Segmenter should support optional LoL stage hints (`match-stage-hints.jsonl`) and derive multi-match boundaries from `in_game` anchors while preserving single-boundary fallback when hints are absent/invalid.
* CLI should support typed manual stage-hint append (`arl stage-hint`) so operators can feed segmenter without hand-editing JSONL.
* Pipeline should support heuristic auto-seeding of `in_game` hints (`arl stage-hints-auto`) for sessions without anchors.
* Pipeline should support semantic auto-seeding of stage hints (`arl stage-hints-semantic`) that emits champion_select/loading/in_game/post_game cycles for unseeded sessions.
* Semantic stage-hint generation should prefer signal-driven classification from `match-stage-signals.jsonl` when usable `in_game` signals exist, with template fallback otherwise.
* CLI should support typed manual stage-signal append (`arl stage-signal`) for semantic generation input.
* Pipeline should support subtitle-driven auto extraction of semantic stage signals (`arl stage-signals-from-subtitles`) from `subtitle-assets.jsonl` referenced SRT files with idempotent ingest-state tracking.
* Subtitles stage should auto-trigger best-effort subtitle-to-signal ingest after subtitle assets are emitted, without letting ingest failures break subtitle stage completion.
* `stage-hints-semantic` should run best-effort subtitle-to-signal ingest before loading signals, so subtitle-derived signals can be consumed in the same command run.
* Stage text classification should support bilingual (English + Chinese) LoL stage cues to improve signal-driven semantic coverage.
* Stage text keyword lists should support external override via config file (`ARL_STAGE_KEYWORDS_PATH`) and apply consistently across subtitle ingest + semantic hint classification.
* Invalid stage-keyword override payloads should be observable via explicit logs while retaining non-blocking fallback to built-in defaults.
* Stage-keyword override should also support command-level CLI path injection for `stage-hints-semantic`, `stage-signals-from-subtitles`, and `subtitles`, with precedence `CLI arg > ARL_STAGE_KEYWORDS_PATH > built-in defaults`.
* `stage-signals-from-subtitles` should support operator-triggered forced reprocess (`--force-reprocess`) while preserving append-only history and duplicate-signal suppression.
* Subtitle-signal ingest state should be auto-compacted against current subtitle manifest keys to prevent stale-key accumulation in long-running environments.
* `stage-signals-from-subtitles` should support targeted ingest filters (`session id` / `subtitle path`) so operators can reprocess specific scope without scanning full subtitle manifest.
* `stage-signals-from-subtitles` should support `match_index` targeted filters so operators can reprocess specific matches without scanning all subtitle rows in a session.
* `subtitles` command should support targeted generation filters (`session id` / `match_index`) so operators can regenerate only selected boundaries instead of rescanning all matches.
* `subtitles` targeted generation should emit explicit filter summary telemetry (`total_boundaries`, `matched_boundaries`) and no-match logs when filters match zero boundaries.
* Auto-triggered subtitle stage-signal ingest (inside `subtitles` command) should inherit the same subtitles filter scope (`session id` / `match_index`) to prevent unrelated subtitle-asset scans during targeted runs.
* Subtitle stage-signal extraction should tolerate both comma and dot timestamp separators in subtitle cue rows to reduce parser brittleness across subtitle emitters.
* Targeted ingest should emit explicit no-match observability logs when filters match zero subtitle assets.
* Targeted ingest should also emit filter summary telemetry (`total_assets`, `matched_assets`) for each filtered run.
* Ingest summary should expose skip-category counters (`skipped_already_processed`, `skipped_missing_subtitle`) for zero/low-yield diagnostics.

## Acceptance Criteria (evolving)

* [x] New milestone selected with explicit reliability rationale.
* [x] Recovery can emit `recording_retry_scheduled` after a newer fully-resolved action cycle supersedes older failed history for the same job/action_type.
* [x] Recovery does not emit requeue if latest effective action set still contains pending/failed statuses.
* [x] Regression tests cover multi-cycle same-job behavior.
* [x] Recovery latest-action selection is deterministic when same `job_id` + `action_type` rows share identical `created_at` values.
* [x] Recovery action-key callbacks keep backward compatibility with legacy key shape after keying upgrade.
* [x] Recovery legacy action-key callback is deterministic when multiple same-timestamp rows share the same legacy key.
* [x] Contract docs updated for latest-action-set readiness rules.
* [x] Subtitle stage can generate SRT from transcription entries when available, while preserving placeholder fallback behavior.
* [x] Windows-agent can recover from Playwright probe errors via HTTP fallback and emit direct-stream snapshots when URL payloads are present.
* [x] Windows-agent direct-stream extraction decodes percent-encoded URL candidates in probe content and can promote unknown pages to live/direct_stream when stream URLs are discoverable.
* [x] Windows-agent direct-stream extraction decodes multi-layer percent-encoded (`%25` wrapped) and `\xNN`-escaped URL candidates in Playwright/HTTP probe content.
* [x] Recorder and Orchestrator classify ffmpeg HTTP 4xx failures as non-recoverable and avoid cross-run retry scheduling for these cases.
* [x] Recorder stops in-run ffmpeg retries early for non-recoverable failures while preserving deterministic fallback output.
* [x] Recorder infers actionable manual-recovery action mapping from failure reason text when `failure_category` is missing.
* [x] Segmenter derives multiple `MatchBoundary` rows from valid `in_game` stage hints and keeps stable sequential `match_index`.
* [x] Segmenter supports `detected_at`-based hints (relative to recording start) and preserves idempotency on reruns.
* [x] Segmenter keeps existing single-boundary fallback behavior when hints are missing/unusable.
* [x] `arl stage-hint` can append validated hint rows for both relative-seconds and absolute timestamp inputs.
* [x] `arl stage-hints-auto` can seed periodic `in_game` hints from recording duration, skip already-anchored sessions, and remain idempotent on reruns.
* [x] `arl stage-hints-semantic` can seed semantic stage cycles for unseeded sessions, skip sessions with existing hints, and remain idempotent on reruns.
* [x] `arl stage-hints-semantic` can switch to signal-driven strategy when stage signals contain usable `in_game` markers, and fallback to template strategy when not.
* [x] `arl stage-signal` can append validated signal rows for both relative-seconds and absolute timestamp inputs.
* [x] `arl stage-signals-from-subtitles` can extract first-per-stage semantic signal rows from SRT subtitles, keep rerun idempotency, and preserve retryability for missing subtitle files.
* [x] `subtitles` stage auto-runs subtitle-to-signal ingest after subtitle asset write and remains stable/idempotent across reruns.
* [x] `stage-hints-semantic` auto-runs subtitle-to-signal ingest before generation and can emit signal-driven hints without manual `stage-signal` pre-write.
* [x] Stage classifier supports Chinese stage cues and keeps expected signal-driven semantic behavior for Chinese signals/subtitles.
* [x] External stage-keyword override file can drive both subtitle signal extraction and semantic stage-hint classification without regressions.
* [x] Invalid stage-keyword overrides are observable in logs and safely fall back to built-in defaults without interrupting stage execution.
* [x] CLI `--stage-keywords-path` override is available on `stage-hints-semantic`, `stage-signals-from-subtitles`, and `subtitles`, and command runtime precedence is `CLI > env > defaults`.
* [x] `stage-signals-from-subtitles --force-reprocess` can rescan processed subtitle rows without duplicating previously emitted identical signals, and can append newly discoverable stage signals when subtitle content changes.
* [x] `stage-signals-from-subtitles` run auto-compacts stale ingest-state keys/fingerprints that are no longer present in current `subtitle-assets.jsonl`.
* [x] `stage-signals-from-subtitles` supports scoped filtering via session-id and subtitle-path options (single and CSV forms), and only matching assets are processed.
* [x] `stage-signals-from-subtitles` supports scoped filtering via `match-index` options (single and CSV forms), and applies intersection semantics with session/path filters.
* [x] `subtitles` supports scoped filtering via `session-id/session-ids` + `match-index/match-indices` (single and CSV forms), with cross-dimension intersection semantics.
* [x] Auto-triggered stage-signal ingest within `subtitles` inherits active subtitles filters and stays scoped to targeted session/match boundaries.
* [x] `stage-signals-from-subtitles` accepts both `HH:MM:SS,mmm` and `HH:MM:SS.mmm` cue timestamps when extracting stage signals.
* [x] Filtered `stage-signals-from-subtitles` run with zero matched assets emits explicit no-match log and keeps zero-result summary output.
* [x] Filtered `stage-signals-from-subtitles` run emits `total_assets`/`matched_assets` summary logs for operator diagnostics.
* [x] `stage-signals-from-subtitles` summary logs include `matched_assets`, `skipped_already_processed`, and `skipped_missing_subtitle` counters.
* [x] Added CLI end-to-end regression coverage for `stage-signals-from-subtitles` combined filter + force-reprocess behavior on real manifest files.
* [x] Added CLI end-to-end regression coverage for `subtitles` combined filters on real boundary manifests, including zero-match observability behavior.
* [x] Full Python and probe test suites pass after segmenter enhancement.

## Definition of Done

* Code changes merged into recovery readiness logic.
* Regression tests added/updated and passing.
* Spec contracts synchronized with behavior.
* No regressions in existing retry/recovery suites.

## Out of Scope

* New queue/broker architecture.
* New frontend/operator UI.
* New media pipeline features beyond recovery readiness semantics.

## Technical Notes

* Current task path: `.trellis/tasks/04-26-continue-dev-progress/`
* Primary implementation targets: `src/arl/recovery/service.py`, `src/arl/segmenter/service.py`, `src/arl/segmenter/auto_hints.py`, `src/arl/segmenter/semantic_hints.py`, `src/arl/segmenter/signals.py`, `src/arl/segmenter/signals_from_subtitles.py`, `src/arl/segmenter/stage_text.py`, `src/arl/segmenter/models.py`, `src/arl/subtitles/service.py`, `src/arl/cli.py`
* Primary tests: `tests/pipeline/test_recovery_service.py`, `tests/pipeline/test_segmenter_service.py`, `tests/pipeline/test_auto_stage_hint_service.py`, `tests/pipeline/test_semantic_stage_hint_service.py`, `tests/pipeline/test_stage_hint_writer.py`, `tests/pipeline/test_stage_signal_writer.py`, `tests/pipeline/test_stage_signals_from_subtitles_service.py`, `tests/pipeline/test_stage_text_classifier.py`, `tests/pipeline/test_subtitles_service.py`, `tests/pipeline/test_cli_stage_hint.py`, `tests/pipeline/test_cli_stage_signals_from_subtitles_e2e.py`, `tests/pipeline/test_cli_subtitles_e2e.py`
* Core contracts: `.trellis/spec/backend/orchestration-contracts.md`, `.trellis/spec/backend/quality-guidelines.md`
