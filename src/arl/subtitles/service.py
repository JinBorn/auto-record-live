from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.segmenter.signals_from_subtitles import StageSignalFromSubtitlesService
from arl.shared.contracts import MatchBoundary, RecordingAsset, SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log
from arl.subtitles.models import SubtitleAuditEvent, SubtitleStateFile


@dataclass(frozen=True)
class TranscribeOutcome:
    entries: list[tuple[float, float, str]]
    language: str | None = None
    language_probability: float | None = None
    reason: str | None = None
    reason_detail: str | None = None


class SubtitleService:
    _TRANSCRIBE_SUFFIXES = {
        ".aac",
        ".flac",
        ".m4a",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".opus",
        ".ts",
        ".wav",
        ".webm",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries_path = settings.storage.temp_dir / "match-boundaries.jsonl"
        self.recording_assets_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.audit_path = settings.storage.temp_dir / "subtitles-events.jsonl"
        self.state_path = settings.storage.temp_dir / "subtitles-state.json"
        self._whisper_model: Any | None = None
        self._whisper_model_initialized = False

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
    ) -> None:
        log("subtitles", "starting")
        log(
            "subtitles",
            f"provider={self.settings.subtitles.provider} model={self.settings.subtitles.model_size}",
        )
        if not self.settings.subtitles.enabled:
            log("subtitles", "disabled")
            return

        boundaries = load_models(self.boundaries_path, MatchBoundary)
        filtered_boundaries = self._filter_boundaries(
            boundaries,
            session_ids=session_ids,
            match_indices=match_indices,
        )
        if session_ids is not None or match_indices is not None:
            log(
                "subtitles",
                (
                    "filters summary "
                    f"total_boundaries={len(boundaries)} "
                    f"matched_boundaries={len(filtered_boundaries)}"
                ),
            )
        if (
            not filtered_boundaries
            and (session_ids is not None or match_indices is not None)
        ):
            session_filter = ",".join(sorted(session_ids)) if session_ids is not None else "-"
            match_index_filter = (
                ",".join(str(item) for item in sorted(match_indices))
                if match_indices is not None
                else "-"
            )
            log(
                "subtitles",
                (
                    "no boundaries matched filters "
                    f"session_ids={session_filter} match_indices={match_index_filter}"
                ),
            )

        recording_assets = load_models(self.recording_assets_path, RecordingAsset)
        latest_recording_path_by_session: dict[str, str] = {}
        for recording_asset in recording_assets:
            latest_recording_path_by_session[recording_asset.session_id] = recording_asset.path
        state = self._load_state()

        processed = 0
        for boundary in filtered_boundaries:
            key = self._key(boundary.session_id, boundary.match_index)
            if key in state.processed_match_keys:
                continue

            recording_path = latest_recording_path_by_session.get(boundary.session_id)
            subtitle_path, outcome = self._write_subtitle(boundary, recording_path)
            subtitle_asset = SubtitleAsset(
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                path=str(subtitle_path),
                format="srt",
            )
            append_model(self.assets_path, subtitle_asset)
            self._append_subtitle_audit(boundary, outcome)
            state.processed_match_keys.append(key)
            processed += 1
            log(
                "subtitles",
                (
                    "subtitle asset written "
                    f"session_id={subtitle_asset.session_id} "
                    f"match_index={subtitle_asset.match_index}"
                ),
            )

        self._save_state(state)
        log("subtitles", f"processed_matches={processed}")
        try:
            StageSignalFromSubtitlesService(self.settings).run(
                session_ids=session_ids,
                match_indices=match_indices,
            )
        except Exception as exc:
            log("subtitles", f"stage-signal ingest skipped reason={exc}")

    def _filter_boundaries(
        self,
        boundaries: list[MatchBoundary],
        *,
        session_ids: set[str] | None,
        match_indices: set[int] | None,
    ) -> list[MatchBoundary]:
        if session_ids is None and match_indices is None:
            return boundaries

        filtered: list[MatchBoundary] = []
        for boundary in boundaries:
            if session_ids is not None and boundary.session_id not in session_ids:
                continue
            if match_indices is not None and boundary.match_index not in match_indices:
                continue
            filtered.append(boundary)
        return filtered

    def _write_subtitle(
        self,
        boundary: MatchBoundary,
        recording_path: str | None,
    ) -> tuple[Path, TranscribeOutcome]:
        output_dir = self.settings.storage.processed_dir / boundary.session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = output_dir / f"match-{boundary.match_index:02d}.srt"
        outcome = self._transcribe_boundary(boundary, recording_path)
        if outcome.entries:
            subtitle_path.write_text(self._build_srt(outcome.entries), encoding="utf-8")
        else:
            subtitle_path.write_text(self._placeholder_srt(), encoding="utf-8")
        return subtitle_path, outcome

    def _transcribe_boundary(
        self,
        boundary: MatchBoundary,
        recording_path: str | None,
    ) -> TranscribeOutcome:
        if self.settings.subtitles.provider != "faster-whisper":
            return TranscribeOutcome(
                entries=[],
                reason="model_unavailable",
                reason_detail=f"unsupported_provider:{self.settings.subtitles.provider}",
            )
        if recording_path is None:
            log(
                "subtitles",
                (
                    "fallback to placeholder "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=missing_recording_asset"
                ),
            )
            return TranscribeOutcome(
                entries=[],
                reason="missing_recording",
                reason_detail=f"no_recording_asset_for_session={boundary.session_id}",
            )

        source_path = Path(recording_path)
        if source_path.suffix.lower() not in self._TRANSCRIBE_SUFFIXES:
            reason_detail = f"unsupported_suffix:{source_path.suffix.lower()}"
            log(
                "subtitles",
                (
                    "fallback to placeholder "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"reason={reason_detail}"
                ),
            )
            return TranscribeOutcome(
                entries=[],
                reason="unsupported_suffix",
                reason_detail=reason_detail,
            )

        model = self._load_whisper_model()
        if model is None:
            return TranscribeOutcome(
                entries=[],
                reason="model_unavailable",
                reason_detail="load_whisper_model_returned_none",
            )

        try:
            segments, info = model.transcribe(
                str(source_path),
                language=self.settings.subtitles.language or None,
            )
        except Exception as exc:
            log(
                "subtitles",
                (
                    "transcribe failed "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"reason={exc}"
                ),
            )
            return TranscribeOutcome(
                entries=[],
                reason="transcribe_failed",
                reason_detail=f"transcribe_exc:{exc.__class__.__name__}:{exc}",
            )

        language = getattr(info, "language", None)
        language_probability = getattr(info, "language_probability", None)
        if language_probability is not None:
            language_probability = float(language_probability)
        threshold = self.settings.subtitles.min_language_probability
        if (
            self.settings.subtitles.language
            and language_probability is not None
            and language_probability < threshold
        ):
            log(
                "subtitles",
                "low language confidence "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"language={language or 'unknown'} "
                f"probability={language_probability} threshold={threshold}",
            )
            return TranscribeOutcome(
                entries=[],
                language=str(language) if language is not None else None,
                language_probability=language_probability,
                reason="low_language_confidence",
                reason_detail=(
                    f"language={language or 'unknown'} "
                    f"probability={language_probability} threshold={threshold}"
                ),
            )

        boundary_start = boundary.started_at_seconds
        boundary_end = boundary.ended_at_seconds
        entries: list[tuple[float, float, str]] = []
        for segment in segments:
            raw_text = str(getattr(segment, "text", "")).strip()
            if not raw_text:
                continue
            seg_start = float(getattr(segment, "start", 0.0))
            seg_end = float(getattr(segment, "end", seg_start))
            if seg_end <= boundary_start or seg_start >= boundary_end:
                continue
            rel_start = max(seg_start, boundary_start) - boundary_start
            rel_end = min(seg_end, boundary_end) - boundary_start
            if rel_end <= rel_start:
                continue
            entries.append((rel_start, rel_end, raw_text))
        return TranscribeOutcome(
            entries=entries,
            language=str(language) if language is not None else None,
            language_probability=language_probability,
        )

    def _load_whisper_model(self) -> Any | None:
        if self._whisper_model_initialized:
            return self._whisper_model
        self._whisper_model_initialized = True

        cache_dir = self.settings.subtitles.model_cache_dir.resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_dir))

        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except Exception as exc:
            log("subtitles", f"faster-whisper unavailable reason={exc}")
            return None

        try:
            self._whisper_model = WhisperModel(self.settings.subtitles.model_size)
        except Exception as exc:
            log("subtitles", f"failed to initialize faster-whisper model reason={exc}")
            self._whisper_model = None
        return self._whisper_model

    def _build_srt(self, entries: list[tuple[float, float, str]]) -> str:
        rows: list[str] = []
        for index, (start, end, text) in enumerate(entries, start=1):
            rows.append(str(index))
            rows.append(
                f"{self._format_srt_timestamp(start)} --> {self._format_srt_timestamp(end)}"
            )
            rows.append(text)
            rows.append("")
        return "\n".join(rows).rstrip() + "\n"

    def _placeholder_srt(self) -> str:
        return (
            "1\n"
            "00:00:00,000 --> 00:00:03,000\n"
            "Placeholder subtitle generated by local pipeline.\n"
        )

    def _format_srt_timestamp(self, seconds: float) -> str:
        millis = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(millis, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, ms = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def _load_state(self) -> SubtitleStateFile:
        if not self.state_path.exists():
            return SubtitleStateFile()
        return SubtitleStateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: SubtitleStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _append_subtitle_audit(
        self,
        boundary: MatchBoundary,
        outcome: TranscribeOutcome,
    ) -> None:
        if outcome.entries:
            event = SubtitleAuditEvent(
                event_type="subtitle_transcribe_succeeded",
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                language=outcome.language,
                language_probability=outcome.language_probability,
                created_at=datetime.now(timezone.utc),
            )
        else:
            event = SubtitleAuditEvent(
                event_type="subtitle_fallback_placeholder",
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                language=outcome.language,
                language_probability=outcome.language_probability,
                reason=outcome.reason or "model_unavailable",
                reason_detail=outcome.reason_detail,
                created_at=datetime.now(timezone.utc),
            )
        append_model(self.audit_path, event)

    def _key(self, session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"
