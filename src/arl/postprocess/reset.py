from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel, Field, ValidationError

from arl.config import Settings
from arl.copywriter.models import CopywriterStateFile
from arl.exporter.models import ExporterStateFile
from arl.segmenter.models import (
    MatchStageHint,
    MatchStageSignal,
    SegmenterStateFile,
    StageSignalIngestStateFile,
)
from arl.shared.contracts import CopyAsset, ExportAsset, MatchBoundary, SubtitleAsset
from arl.shared.logging import log
from arl.subtitles.models import SubtitleStateFile

TModel = TypeVar("TModel", bound=BaseModel)
TState = TypeVar("TState", bound=BaseModel)


class PostProcessResetResult(BaseModel):
    session_ids: list[str]
    removed_rows_by_file: dict[str, int] = Field(default_factory=dict)
    removed_state_keys_by_file: dict[str, int] = Field(default_factory=dict)
    deleted_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)


class PostProcessResetService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.temp_dir = settings.storage.temp_dir
        self._generated_roots = [
            settings.storage.processed_dir,
            settings.storage.export_dir,
        ]

    def run(
        self,
        *,
        session_ids: set[str],
        delete_files: bool = True,
    ) -> PostProcessResetResult:
        normalized_session_ids = {item.strip() for item in session_ids if item.strip()}
        if not normalized_session_ids:
            raise ValueError("at least one session id is required")

        result = PostProcessResetResult(session_ids=sorted(normalized_session_ids))
        log(
            "postprocess",
            "reset starting "
            f"session_ids={','.join(result.session_ids)} delete_files={int(delete_files)}",
        )

        self._rewrite_manifests(
            session_ids=normalized_session_ids,
            result=result,
            delete_files=delete_files,
        )
        self._rewrite_states(session_ids=normalized_session_ids, result=result)

        log(
            "postprocess",
            "reset completed "
            f"session_ids={','.join(result.session_ids)} "
            f"deleted_files={len(result.deleted_files)} "
            f"skipped_files={len(result.skipped_files)}",
        )
        return result

    def _rewrite_manifests(
        self,
        *,
        session_ids: set[str],
        result: PostProcessResetResult,
        delete_files: bool,
    ) -> None:
        self._rewrite_jsonl(
            self.temp_dir / "match-stage-hints.jsonl",
            MatchStageHint,
            lambda item: item.session_id in session_ids,
            result.removed_rows_by_file,
        )
        self._rewrite_jsonl(
            self.temp_dir / "match-stage-signals.jsonl",
            MatchStageSignal,
            lambda item: item.session_id in session_ids and item.source == "subtitles_srt",
            result.removed_rows_by_file,
        )
        self._rewrite_jsonl(
            self.temp_dir / "match-boundaries.jsonl",
            MatchBoundary,
            lambda item: item.session_id in session_ids,
            result.removed_rows_by_file,
        )
        self._rewrite_artifact_manifest(
            self.temp_dir / "subtitle-assets.jsonl",
            SubtitleAsset,
            lambda item: item.session_id in session_ids,
            lambda item: item.path,
            result=result,
            delete_files=delete_files,
        )
        self._rewrite_artifact_manifest(
            self.temp_dir / "export-assets.jsonl",
            ExportAsset,
            lambda item: item.session_id in session_ids,
            lambda item: item.path,
            result=result,
            delete_files=delete_files,
        )
        self._rewrite_artifact_manifest(
            self.temp_dir / "copy-assets.jsonl",
            CopyAsset,
            lambda item: item.session_id in session_ids,
            lambda item: item.path,
            result=result,
            delete_files=delete_files,
        )

    def _rewrite_states(
        self,
        *,
        session_ids: set[str],
        result: PostProcessResetResult,
    ) -> None:
        self._rewrite_state(
            self.temp_dir / "segmenter-state.json",
            SegmenterStateFile,
            lambda state: self._remove_session_prefixed_keys(
                state.processed_asset_keys,
                session_ids,
            ),
            result.removed_state_keys_by_file,
        )
        self._rewrite_state(
            self.temp_dir / "subtitles-state.json",
            SubtitleStateFile,
            lambda state: self._remove_session_prefixed_keys(
                state.processed_match_keys,
                session_ids,
            ),
            result.removed_state_keys_by_file,
        )
        self._rewrite_state(
            self.temp_dir / "exporter-state.json",
            ExporterStateFile,
            lambda state: self._remove_session_prefixed_keys(
                state.processed_match_keys,
                session_ids,
            ),
            result.removed_state_keys_by_file,
        )
        self._rewrite_state(
            self.temp_dir / "copywriter-state.json",
            CopywriterStateFile,
            lambda state: self._remove_session_prefixed_keys(
                state.processed_match_keys,
                session_ids,
            ),
            result.removed_state_keys_by_file,
        )
        self._rewrite_state(
            self.temp_dir / "stage-signal-ingest-state.json",
            StageSignalIngestStateFile,
            lambda state: self._remove_stage_signal_state_keys(state, session_ids),
            result.removed_state_keys_by_file,
        )

    def _rewrite_artifact_manifest(
        self,
        path: Path,
        model_type: type[TModel],
        should_remove: Callable[[TModel], bool],
        artifact_path: Callable[[TModel], str],
        *,
        result: PostProcessResetResult,
        delete_files: bool,
    ) -> None:
        removed_paths: list[str] = []

        def remove_and_collect(item: TModel) -> bool:
            remove = should_remove(item)
            if remove:
                removed_paths.append(artifact_path(item))
            return remove

        self._rewrite_jsonl(
            path,
            model_type,
            remove_and_collect,
            result.removed_rows_by_file,
        )
        if not delete_files:
            return
        for raw_path in removed_paths:
            self._delete_generated_file(raw_path, result)

    def _rewrite_jsonl(
        self,
        path: Path,
        model_type: type[TModel],
        should_remove: Callable[[TModel], bool],
        counter: dict[str, int],
    ) -> None:
        if not path.exists():
            return

        kept_lines: list[str] = []
        removed = 0
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                item = model_type.model_validate(json.loads(raw_line))
            except (json.JSONDecodeError, ValidationError):
                kept_lines.append(raw_line)
                continue
            if should_remove(item):
                removed += 1
                continue
            kept_lines.append(json.dumps(item.model_dump(mode="json"), ensure_ascii=False))

        if removed <= 0:
            return
        self._write_jsonl_lines(path, kept_lines)
        counter[path.name] = removed

    def _rewrite_state(
        self,
        path: Path,
        state_type: type[TState],
        remove_keys: Callable[[TState], int],
        counter: dict[str, int],
    ) -> None:
        if not path.exists():
            return
        state = state_type.model_validate_json(path.read_text(encoding="utf-8"))
        removed = remove_keys(state)
        if removed <= 0:
            return
        path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        counter[path.name] = removed

    def _delete_generated_file(
        self,
        raw_path: str,
        result: PostProcessResetResult,
    ) -> None:
        path = Path(raw_path)
        resolved = path.expanduser().resolve(strict=False)
        if not self._is_under_generated_roots(resolved):
            result.skipped_files.append(f"{raw_path}:outside_generated_roots")
            return
        if not resolved.exists():
            result.skipped_files.append(f"{raw_path}:missing")
            return
        if not resolved.is_file():
            result.skipped_files.append(f"{raw_path}:not_file")
            return
        resolved.unlink()
        result.deleted_files.append(str(resolved))
        self._remove_empty_generated_parent(resolved.parent)

    def _remove_empty_generated_parent(self, path: Path) -> None:
        if not self._is_under_generated_roots(path):
            return
        try:
            path.rmdir()
        except OSError:
            return

    def _is_under_generated_roots(self, path: Path) -> bool:
        for root in self._generated_roots:
            try:
                path.relative_to(root.expanduser().resolve(strict=False))
            except ValueError:
                continue
            return True
        return False

    def _remove_stage_signal_state_keys(
        self,
        state: StageSignalIngestStateFile,
        session_ids: set[str],
    ) -> int:
        removed = self._remove_session_prefixed_keys(
            state.processed_subtitle_keys,
            session_ids,
        )
        kept_fingerprints: dict[str, list[str]] = {}
        removed_fingerprint_keys = 0
        for key, fingerprints in state.emitted_signal_fingerprints_by_subtitle_key.items():
            if self._key_session_id(key) in session_ids:
                removed_fingerprint_keys += 1
                continue
            kept_fingerprints[key] = fingerprints
        state.emitted_signal_fingerprints_by_subtitle_key = kept_fingerprints
        return removed + removed_fingerprint_keys

    def _remove_session_prefixed_keys(
        self,
        keys: list[str],
        session_ids: set[str],
    ) -> int:
        kept = [key for key in keys if self._key_session_id(key) not in session_ids]
        removed = len(keys) - len(kept)
        if removed > 0:
            keys[:] = kept
        return removed

    @staticmethod
    def _key_session_id(key: str) -> str:
        return key.split(":", 1)[0]

    @staticmethod
    def _write_jsonl_lines(path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines)
        if text:
            text += "\n"
        path.write_text(text, encoding="utf-8")
