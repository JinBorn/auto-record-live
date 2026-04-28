from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from arl.config import Settings
from arl.recovery.models import RecoveryDispatchEvent, RecoveryStateFile
from arl.recorder.models import RecorderAuditEvent, RecorderRecoveryAction
from arl.shared.failure_contracts import (
    FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE,
    classify_failure_reason,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class RecoveryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.actions_path = settings.storage.temp_dir / "recorder-recovery-actions.jsonl"
        self.dispatch_events_path = settings.storage.temp_dir / "recovery-events.jsonl"
        self.dispatch_archive_path = settings.storage.temp_dir / "recovery-events-archive.jsonl"
        self.state_path = settings.storage.temp_dir / "recovery-state.json"
        self.recorder_events_path = settings.orchestrator.recorder_event_log_path

    def run(self) -> None:
        log("recovery", "starting")
        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)

        processed = 0
        for action in actions:
            key = self._action_key(action)
            if key in state.processed_action_keys:
                continue

            append_model(
                self.dispatch_events_path,
                RecoveryDispatchEvent(
                    event_type="manual_recovery_action_dispatched",
                    session_id=action.session_id,
                    job_id=action.job_id,
                    action_type=action.action_type,
                    status="pending",
                    decision="manual_action_dispatched",
                    failure_category=self._canonical_failure_category(
                        action.failure_category,
                        action.stop_reason,
                    ),
                    is_retryable=self._canonical_is_retryable(
                        action.failure_category,
                        action.recoverable,
                        action.stop_reason,
                    ),
                    reason_code=self._reason_code(action.stop_reason),
                    reason_detail=action.stop_reason,
                    action_key=key,
                    message=self._action_message(action),
                    created_at=datetime.now(timezone.utc),
                ),
            )
            state.processed_action_keys.append(key)
            state.status_by_action_key[key] = "pending"
            processed += 1
            log(
                "recovery",
                "action dispatched "
                f"session_id={action.session_id} job_id={action.job_id} action_type={action.action_type}",
            )

        self._save_state(state)
        log("recovery", f"processed_actions={processed}")

    def mark_resolved(self, job_id: str, message: str | None = None) -> int:
        return self._mark_job_actions(job_id=job_id, status="resolved", message=message)

    def mark_failed(self, job_id: str, message: str | None = None) -> int:
        return self._mark_job_actions(job_id=job_id, status="failed", message=message)

    def mark_jobs_resolved(
        self,
        job_ids: list[str],
        message: str | None = None,
    ) -> dict[str, Any]:
        return self._mark_job_actions_many(
            job_ids=job_ids,
            status="resolved",
            message=message,
        )

    def mark_jobs_failed(
        self,
        job_ids: list[str],
        message: str | None = None,
    ) -> dict[str, Any]:
        return self._mark_job_actions_many(
            job_ids=job_ids,
            status="failed",
            message=message,
        )

    def mark_action_resolved(self, action_key: str, message: str | None = None) -> int:
        return self._mark_action_by_key(
            action_key=action_key,
            status="resolved",
            message=message,
        )

    def mark_action_failed(self, action_key: str, message: str | None = None) -> int:
        return self._mark_action_by_key(
            action_key=action_key,
            status="failed",
            message=message,
        )

    def list_pending_actions(self) -> list[dict[str, Any]]:
        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)

        items: list[dict[str, Any]] = []
        for action in actions:
            key = self._action_key(action)
            if key not in state.processed_action_keys:
                continue
            if state.status_by_action_key.get(key, "pending") != "pending":
                continue
            items.append(
                {
                    "action_key": key,
                    "session_id": action.session_id,
                    "job_id": action.job_id,
                    "action_type": action.action_type,
                    "failure_category": action.failure_category,
                    "recoverable": action.recoverable,
                    "status": "pending",
                    "message": self._action_message(action),
                }
            )
        return items

    def summary(self) -> dict[str, Any]:
        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)

        status_counts: dict[str, int] = {
            "undispatched": 0,
            "pending": 0,
            "resolved": 0,
            "failed": 0,
        }
        by_action_type: dict[str, dict[str, int]] = {}
        by_failure_category: dict[str, dict[str, int]] = {}

        for action in actions:
            key = self._action_key(action)
            if key not in state.processed_action_keys:
                status = "undispatched"
            else:
                status = state.status_by_action_key.get(key, "pending")
            status_counts[status] = status_counts.get(status, 0) + 1

            action_type_bucket = by_action_type.setdefault(
                action.action_type,
                {"total": 0, "pending": 0, "resolved": 0, "failed": 0, "undispatched": 0},
            )
            action_type_bucket["total"] += 1
            action_type_bucket[status] = action_type_bucket.get(status, 0) + 1

            failure_category = action.failure_category or "unknown"
            failure_bucket = by_failure_category.setdefault(
                failure_category,
                {"total": 0, "pending": 0, "resolved": 0, "failed": 0, "undispatched": 0},
            )
            failure_bucket["total"] += 1
            failure_bucket[status] = failure_bucket.get(status, 0) + 1

        return {
            "actions_total": len(actions),
            "actions_dispatched": len(actions) - status_counts.get("undispatched", 0),
            "actions_pending": status_counts.get("pending", 0),
            "actions_resolved": status_counts.get("resolved", 0),
            "actions_failed": status_counts.get("failed", 0),
            "actions_undispatched": status_counts.get("undispatched", 0),
            "by_action_type": by_action_type,
            "by_failure_category": by_failure_category,
        }

    def maintain(self) -> dict[str, Any]:
        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)

        terminal_action_keys = {
            action_key
            for action_key, status in state.status_by_action_key.items()
            if status in {"resolved", "failed"}
        }

        archived_events, kept_events = self._archive_terminal_events(terminal_action_keys)
        removed_actions, kept_actions = self._compact_terminal_actions(
            actions,
            state,
            terminal_action_keys,
        )
        self._save_state(state)

        result = {
            "archived_events": archived_events,
            "kept_events": kept_events,
            "removed_actions": removed_actions,
            "kept_actions": kept_actions,
            "kept_state_keys": len(state.processed_action_keys),
        }
        log(
            "recovery",
            "maintenance completed "
            f"archived_events={archived_events} removed_actions={removed_actions}",
        )
        return result

    def _mark_job_actions(
        self,
        *,
        job_id: str,
        status: str,
        message: str | None,
    ) -> int:
        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)
        updated = 0
        resolved_job_ids: set[str] = set()
        for action in actions:
            if action.job_id != job_id:
                continue
            key = self._action_key(action)
            if key not in state.processed_action_keys:
                continue
            current_status = state.status_by_action_key.get(key, "pending")
            if current_status != "pending":
                continue

            state.status_by_action_key[key] = status
            append_model(
                self.dispatch_events_path,
                RecoveryDispatchEvent(
                    event_type=f"manual_recovery_action_{status}",
                    session_id=action.session_id,
                    job_id=action.job_id,
                    action_type=action.action_type,
                    status=status,
                    decision=self._status_decision(status),
                    failure_category=self._canonical_failure_category(
                        action.failure_category,
                        action.stop_reason,
                    ),
                    is_retryable=self._canonical_is_retryable(
                        action.failure_category,
                        action.recoverable,
                        action.stop_reason,
                    ),
                    reason_code=self._reason_code(message or action.stop_reason),
                    reason_detail=message or action.stop_reason,
                    action_key=key,
                    message=self._status_message(action, status, message),
                    created_at=datetime.now(timezone.utc),
                ),
            )
            updated += 1
            if status == "resolved":
                resolved_job_ids.add(action.job_id)
            log(
                "recovery",
                f"action {status} "
                f"session_id={action.session_id} job_id={action.job_id} action_type={action.action_type}",
            )

        if status == "resolved":
            for resolved_job_id in resolved_job_ids:
                self._emit_requeue_if_job_ready(
                    actions=actions,
                    state=state,
                    job_id=resolved_job_id,
                    reason=message or "manual_recovery_resolved",
                )

        self._save_state(state)
        if updated == 0:
            log("recovery", f"no pending actions to mark {status} job_id={job_id}")
        else:
            log("recovery", f"updated_actions={updated} status={status} job_id={job_id}")
        return updated

    def _mark_action_by_key(
        self,
        *,
        action_key: str,
        status: str,
        message: str | None,
    ) -> int:
        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)
        action: RecorderRecoveryAction | None = None
        latest_legacy_match: tuple[int, RecorderRecoveryAction] | None = None
        for index, candidate in enumerate(actions):
            if self._action_key(candidate) == action_key:
                action = candidate
                break
            if self._legacy_action_key(candidate) != action_key:
                continue
            if latest_legacy_match is None:
                latest_legacy_match = (index, candidate)
                continue
            latest_index, latest_action = latest_legacy_match
            if candidate.created_at > latest_action.created_at or (
                candidate.created_at == latest_action.created_at and index > latest_index
            ):
                latest_legacy_match = (index, candidate)

        if action is None and latest_legacy_match is not None:
            action = latest_legacy_match[1]

        if action is None:
            log("recovery", f"action_key not found action_key={action_key}")
            return 0
        state_key = self._action_key(action)
        if state_key not in state.processed_action_keys:
            log("recovery", f"action_key not dispatched action_key={action_key}")
            return 0
        current_status = state.status_by_action_key.get(state_key, "pending")
        if current_status != "pending":
            log(
                "recovery",
                f"action_key is not pending action_key={action_key} status={current_status}",
            )
            return 0

        state.status_by_action_key[state_key] = status
        append_model(
            self.dispatch_events_path,
            RecoveryDispatchEvent(
                event_type=f"manual_recovery_action_{status}",
                session_id=action.session_id,
                job_id=action.job_id,
                action_type=action.action_type,
                status=status,
                decision=self._status_decision(status),
                failure_category=self._canonical_failure_category(
                    action.failure_category,
                    action.stop_reason,
                ),
                is_retryable=self._canonical_is_retryable(
                    action.failure_category,
                    action.recoverable,
                    action.stop_reason,
                ),
                reason_code=self._reason_code(message or action.stop_reason),
                reason_detail=message or action.stop_reason,
                action_key=state_key,
                message=self._status_message(action, status, message),
                created_at=datetime.now(timezone.utc),
            ),
        )
        if status == "resolved":
            self._emit_requeue_if_job_ready(
                actions=actions,
                state=state,
                job_id=action.job_id,
                reason=message or "manual_recovery_resolved",
            )
        self._save_state(state)
        log(
            "recovery",
            f"action {status} by key "
            f"session_id={action.session_id} job_id={action.job_id} action_type={action.action_type}",
        )
        return 1

    def _mark_job_actions_many(
        self,
        *,
        job_ids: list[str],
        status: str,
        message: str | None,
    ) -> dict[str, Any]:
        normalized_job_ids = [job_id.strip() for job_id in job_ids if job_id.strip()]
        unique_job_ids = list(dict.fromkeys(normalized_job_ids))
        if not unique_job_ids:
            return {"total_updated": 0, "updated_by_job": {}}

        actions = load_models(self.actions_path, RecorderRecoveryAction)
        state = self._load_state()
        self._normalize_state(actions, state)

        target_job_ids = set(unique_job_ids)
        updated_by_job: dict[str, int] = {job_id: 0 for job_id in unique_job_ids}
        resolved_job_ids: set[str] = set()

        for action in actions:
            if action.job_id not in target_job_ids:
                continue
            key = self._action_key(action)
            if key not in state.processed_action_keys:
                continue
            current_status = state.status_by_action_key.get(key, "pending")
            if current_status != "pending":
                continue

            state.status_by_action_key[key] = status
            append_model(
                self.dispatch_events_path,
                RecoveryDispatchEvent(
                    event_type=f"manual_recovery_action_{status}",
                    session_id=action.session_id,
                    job_id=action.job_id,
                    action_type=action.action_type,
                    status=status,
                    decision=self._status_decision(status),
                    failure_category=self._canonical_failure_category(
                        action.failure_category,
                        action.stop_reason,
                    ),
                    is_retryable=self._canonical_is_retryable(
                        action.failure_category,
                        action.recoverable,
                        action.stop_reason,
                    ),
                    reason_code=self._reason_code(message or action.stop_reason),
                    reason_detail=message or action.stop_reason,
                    action_key=key,
                    message=self._status_message(action, status, message),
                    created_at=datetime.now(timezone.utc),
                ),
            )
            updated_by_job[action.job_id] = updated_by_job.get(action.job_id, 0) + 1
            if status == "resolved":
                resolved_job_ids.add(action.job_id)
            log(
                "recovery",
                f"action {status} in batch "
                f"session_id={action.session_id} job_id={action.job_id} action_type={action.action_type}",
            )

        if status == "resolved":
            for resolved_job_id in resolved_job_ids:
                self._emit_requeue_if_job_ready(
                    actions=actions,
                    state=state,
                    job_id=resolved_job_id,
                    reason=message or "manual_recovery_resolved",
                )

        self._save_state(state)
        total_updated = sum(updated_by_job.values())
        log("recovery", f"batch_updated_actions={total_updated} status={status}")
        return {"total_updated": total_updated, "updated_by_job": updated_by_job}

    def _emit_requeue_if_job_ready(
        self,
        *,
        actions: list[RecorderRecoveryAction],
        state: RecoveryStateFile,
        job_id: str,
        reason: str,
    ) -> None:
        if not self._all_actions_resolved_for_job(actions, state, job_id):
            return
        action = self._pick_action_for_job(actions, job_id)
        if action is None:
            return
        append_model(
            self.recorder_events_path,
            RecorderAuditEvent(
                event_type="recording_retry_scheduled",
                session_id=action.session_id,
                job_id=action.job_id,
                source_type=action.source_type,
                decision="retry_scheduled",
                failure_category=self._canonical_failure_category(
                    action.failure_category,
                    action.stop_reason,
                ),
                is_retryable=True,
                reason_code=self._reason_code(reason),
                reason_detail=reason,
                reason=reason,
                created_at=datetime.now(timezone.utc),
            ),
        )
        log(
            "recovery",
            f"recording retry scheduled via recovery job_id={action.job_id}",
        )

    def _all_actions_resolved_for_job(
        self,
        actions: list[RecorderRecoveryAction],
        state: RecoveryStateFile,
        job_id: str,
    ) -> bool:
        effective_actions = self._latest_effective_actions_for_job(actions, job_id)
        if not effective_actions:
            return False
        for action in effective_actions:
            key = self._action_key(action)
            if key not in state.processed_action_keys:
                return False
            if state.status_by_action_key.get(key, "pending") != "resolved":
                return False
        return True

    def _pick_action_for_job(
        self,
        actions: list[RecorderRecoveryAction],
        job_id: str,
    ) -> RecorderRecoveryAction | None:
        effective_actions = self._latest_effective_actions_for_job(actions, job_id)
        if not effective_actions:
            return None
        return max(
            effective_actions,
            key=lambda action: (action.created_at, action.action_type),
        )

    def _latest_effective_actions_for_job(
        self,
        actions: list[RecorderRecoveryAction],
        job_id: str,
    ) -> list[RecorderRecoveryAction]:
        latest_by_action_type: dict[str, tuple[int, RecorderRecoveryAction]] = {}
        for index, action in enumerate(actions):
            if action.job_id != job_id:
                continue
            latest = latest_by_action_type.get(action.action_type)
            if latest is None:
                latest_by_action_type[action.action_type] = (index, action)
                continue
            latest_index, latest_action = latest
            if action.created_at > latest_action.created_at or (
                action.created_at == latest_action.created_at and index > latest_index
            ):
                latest_by_action_type[action.action_type] = (index, action)
        return [item[1] for item in latest_by_action_type.values()]

    def _archive_terminal_events(self, terminal_action_keys: set[str]) -> tuple[int, int]:
        events = load_models(self.dispatch_events_path, RecoveryDispatchEvent)
        if not events:
            return 0, 0

        archive_items: list[RecoveryDispatchEvent] = []
        keep_items: list[RecoveryDispatchEvent] = []
        for event in events:
            has_terminal_status = event.status in {"resolved", "failed"}
            in_terminal_action = (
                event.action_key is not None and event.action_key in terminal_action_keys
            )
            if has_terminal_status or in_terminal_action:
                archive_items.append(event)
            else:
                keep_items.append(event)

        if archive_items:
            self._append_jsonl_models(self.dispatch_archive_path, archive_items)
        self._write_jsonl_models(self.dispatch_events_path, keep_items)
        return len(archive_items), len(keep_items)

    def _compact_terminal_actions(
        self,
        actions: list[RecorderRecoveryAction],
        state: RecoveryStateFile,
        terminal_action_keys: set[str],
    ) -> tuple[int, int]:
        if not actions:
            return 0, 0

        keep_actions: list[RecorderRecoveryAction] = []
        for action in actions:
            key = self._action_key(action)
            if key in terminal_action_keys:
                continue
            keep_actions.append(action)

        self._write_jsonl_models(self.actions_path, keep_actions)
        keep_keys = {self._action_key(action) for action in keep_actions}
        state.processed_action_keys = [
            key for key in state.processed_action_keys if key in keep_keys
        ]
        state.status_by_action_key = {
            key: status
            for key, status in state.status_by_action_key.items()
            if key in keep_keys
        }
        removed = len(actions) - len(keep_actions)
        return removed, len(keep_actions)

    def _action_key(self, action: RecorderRecoveryAction) -> str:
        legacy = self._legacy_action_key(action)
        payload = action.model_dump(mode="json")
        fingerprint = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return f"{legacy}:{fingerprint}"

    def _legacy_action_key(self, action: RecorderRecoveryAction) -> str:
        return (
            f"{action.session_id}:{action.job_id}:"
            f"{action.action_type}:{action.created_at.isoformat()}"
        )

    def _normalize_state(
        self,
        actions: list[RecorderRecoveryAction],
        state: RecoveryStateFile,
    ) -> None:
        for action in actions:
            primary_key = self._action_key(action)
            legacy_key = self._legacy_action_key(action)

            if primary_key not in state.processed_action_keys and legacy_key in state.processed_action_keys:
                migrated = False
                migrated_keys: list[str] = []
                for existing_key in state.processed_action_keys:
                    if not migrated and existing_key == legacy_key:
                        migrated_keys.append(primary_key)
                        migrated = True
                    else:
                        migrated_keys.append(existing_key)
                state.processed_action_keys = list(dict.fromkeys(migrated_keys))

                legacy_status = state.status_by_action_key.pop(legacy_key, None)
                if legacy_status is not None and primary_key not in state.status_by_action_key:
                    state.status_by_action_key[primary_key] = legacy_status

            if primary_key in state.processed_action_keys and primary_key not in state.status_by_action_key:
                state.status_by_action_key[primary_key] = "pending"

    def _action_message(self, action: RecorderRecoveryAction) -> str:
        if action.steps:
            return action.steps[0]
        if action.recovery_hint:
            return action.recovery_hint
        if action.stop_reason:
            return action.stop_reason
        return "manual recovery action pending"

    def _status_message(
        self,
        action: RecorderRecoveryAction,
        status: str,
        message: str | None,
    ) -> str:
        if message:
            return message
        if status == "resolved":
            return "manual recovery resolved by operator"
        if status == "failed":
            if action.stop_reason:
                return action.stop_reason
            if action.recovery_hint:
                return action.recovery_hint
            return "manual recovery failed by operator"
        return self._action_message(action)

    def _status_decision(self, status: str) -> str:
        if status == "resolved":
            return "manual_action_resolved"
        if status == "failed":
            return "manual_action_failed"
        return "manual_action_dispatched"

    def _reason_code(self, reason: str | None) -> str:
        return classify_failure_reason(reason).reason_code

    def _canonical_failure_category(
        self,
        failure_category: str | None,
        reason: str | None,
    ) -> str:
        if failure_category in {
            "http_4xx_non_retryable",
            "http_5xx_retryable",
            "network_timeout_retryable",
            "ffmpeg_process_error_retryable",
            "unknown_unclassified_non_retryable",
        }:
            return failure_category
        return classify_failure_reason(reason).failure_category

    def _canonical_is_retryable(
        self,
        failure_category: str | None,
        recoverable: bool | None,
        reason: str | None,
    ) -> bool:
        category = self._canonical_failure_category(failure_category, reason)
        if recoverable is not None and category != FAILURE_CATEGORY_UNKNOWN_UNCLASSIFIED_NON_RETRYABLE:
            return recoverable
        return category.endswith("_retryable")

    def _load_state(self) -> RecoveryStateFile:
        if not self.state_path.exists():
            return RecoveryStateFile()
        return RecoveryStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: RecoveryStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _write_jsonl_models(self, path: Path, models: list[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for model in models:
                handle.write(json.dumps(model.model_dump(mode="json"), ensure_ascii=False))
                handle.write("\n")

    def _append_jsonl_models(self, path: Path, models: list[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for model in models:
                handle.write(json.dumps(model.model_dump(mode="json"), ensure_ascii=False))
                handle.write("\n")
