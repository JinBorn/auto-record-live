from __future__ import annotations

from dataclasses import dataclass
import inspect
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
    device: str | None = None
    compute_type: str | None = None
    fallback_device: str | None = None
    reason: str | None = None
    reason_detail: str | None = None


@dataclass(frozen=True)
class WhisperModelConfig:
    device: str
    compute_type: str


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
        self._whisper_models: dict[WhisperModelConfig, Any | None] = {}
        self._whisper_model_initialized_configs: set[WhisperModelConfig] = set()
        self._current_whisper_model_config: WhisperModelConfig | None = None
        self._disabled_whisper_configs: set[WhisperModelConfig] = set()
        self._cuda_disabled_for_batch = False

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

        last_failure: TranscribeOutcome | None = None
        attempted_devices: list[str] = []
        for model_config in self._whisper_model_candidates():
            if model_config.device == "cuda" and self._cuda_disabled_for_batch:
                continue
            if model_config in self._disabled_whisper_configs:
                continue
            attempted_devices.append(model_config.device)
            model = self._load_whisper_model_for_config(model_config)
            if model is None:
                last_failure = TranscribeOutcome(
                    entries=[],
                    device=model_config.device,
                    compute_type=model_config.compute_type,
                    reason="model_unavailable",
                    reason_detail=(
                        "load_whisper_model_returned_none "
                        f"device={model_config.device} compute_type={model_config.compute_type}"
                    ),
                )
                if model_config.device == "cuda":
                    self._cuda_disabled_for_batch = True
                    continue
                return last_failure

            outcome = self._transcribe_with_model(
                model,
                model_config,
                boundary,
                source_path,
            )
            if model_config.device == "cpu" and "cuda" in attempted_devices:
                outcome = TranscribeOutcome(
                    entries=outcome.entries,
                    language=outcome.language,
                    language_probability=outcome.language_probability,
                    device=outcome.device,
                    compute_type=outcome.compute_type,
                    fallback_device="cpu",
                    reason=outcome.reason,
                    reason_detail=outcome.reason_detail,
                )
            if outcome.reason is None or outcome.reason == "low_language_confidence":
                return outcome
            last_failure = outcome
            if model_config.device == "cuda" and self._should_retry_cpu_after_cuda_failure():
                self._cuda_disabled_for_batch = True
                continue
            return outcome

        if last_failure is not None:
            if last_failure.device == "cuda" and self._should_retry_cpu_after_cuda_failure():
                return TranscribeOutcome(
                    entries=[],
                    reason="model_unavailable",
                    reason_detail=(
                        "all_whisper_candidates_unavailable "
                        f"attempted_devices={','.join(attempted_devices) or '-'}"
                    ),
                )
            return last_failure
        return TranscribeOutcome(
            entries=[],
            reason="model_unavailable",
            reason_detail="no_whisper_model_candidates",
        )

    def _transcribe_with_model(
        self,
        model: Any,
        model_config: WhisperModelConfig,
        boundary: MatchBoundary,
        source_path: Path,
    ) -> TranscribeOutcome:
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
                    f"device={model_config.device} compute_type={model_config.compute_type} "
                    f"reason={exc}"
                ),
            )
            self._disable_whisper_model_config(model_config)
            return TranscribeOutcome(
                entries=[],
                device=model_config.device,
                compute_type=model_config.compute_type,
                fallback_device=self._fallback_device_for(model_config),
                reason="transcribe_failed",
                reason_detail=(
                    f"transcribe_exc:{exc.__class__.__name__}:{exc} "
                    f"device={model_config.device} compute_type={model_config.compute_type}"
                ),
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
                device=model_config.device,
                compute_type=model_config.compute_type,
                reason="low_language_confidence",
                reason_detail=(
                    f"language={language or 'unknown'} "
                    f"probability={language_probability} threshold={threshold} "
                    f"device={model_config.device} compute_type={model_config.compute_type}"
                ),
            )

        boundary_start = boundary.started_at_seconds
        boundary_end = boundary.ended_at_seconds
        entries: list[tuple[float, float, str]] = []
        try:
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
        except Exception as exc:
            log(
                "subtitles",
                (
                    "transcribe failed "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"device={model_config.device} compute_type={model_config.compute_type} "
                    f"reason={exc}"
                ),
            )
            self._disable_whisper_model_config(model_config)
            return TranscribeOutcome(
                entries=[],
                language=str(language) if language is not None else None,
                language_probability=language_probability,
                device=model_config.device,
                compute_type=model_config.compute_type,
                fallback_device=self._fallback_device_for(model_config),
                reason="transcribe_failed",
                reason_detail=(
                    f"transcribe_exc:{exc.__class__.__name__}:{exc} "
                    f"device={model_config.device} compute_type={model_config.compute_type}"
                ),
            )
        if not entries:
            return TranscribeOutcome(
                entries=[],
                language=str(language) if language is not None else None,
                language_probability=language_probability,
                device=model_config.device,
                compute_type=model_config.compute_type,
                reason="no_transcript_segments",
                reason_detail=(
                    "no_segments_with_text_inside_boundary "
                    f"device={model_config.device} compute_type={model_config.compute_type}"
                ),
            )
        return TranscribeOutcome(
            entries=entries,
            language=str(language) if language is not None else None,
            language_probability=language_probability,
            device=model_config.device,
            compute_type=model_config.compute_type,
        )

    def _whisper_model_candidates(self) -> list[WhisperModelConfig]:
        device = self.settings.subtitles.device
        compute_type = self.settings.subtitles.compute_type
        cpu_compute_type = self.settings.subtitles.cpu_compute_type
        cuda_compute_type = "float16" if compute_type == "auto" else compute_type
        resolved_cpu_compute_type = cpu_compute_type if compute_type == "auto" else compute_type

        if device == "cpu":
            return [WhisperModelConfig("cpu", resolved_cpu_compute_type)]
        if device == "cuda":
            return [WhisperModelConfig("cuda", cuda_compute_type)]
        return [
            WhisperModelConfig("cuda", cuda_compute_type),
            WhisperModelConfig("cpu", resolved_cpu_compute_type),
        ]

    def _should_retry_cpu_after_cuda_failure(self) -> bool:
        return self.settings.subtitles.device == "auto"

    def _fallback_device_for(self, model_config: WhisperModelConfig) -> str | None:
        if model_config.device == "cuda" and self._should_retry_cpu_after_cuda_failure():
            return "cpu"
        return None

    def _disable_whisper_model_config(self, model_config: WhisperModelConfig) -> None:
        self._disabled_whisper_configs.add(model_config)
        self._whisper_models[model_config] = None
        if model_config.device == "cuda":
            self._cuda_disabled_for_batch = True
        if self._current_whisper_model_config == model_config:
            self._whisper_model = None

    def _load_whisper_model_for_config(self, model_config: WhisperModelConfig) -> Any | None:
        signature = inspect.signature(self._load_whisper_model)
        accepts_config = any(
            parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            }
            for parameter in signature.parameters.values()
        )
        if not accepts_config:
            return self._load_whisper_model()
        return self._load_whisper_model(model_config)

    def _load_whisper_model(
        self,
        model_config: WhisperModelConfig | None = None,
    ) -> Any | None:
        if model_config is None:
            model_config = self._current_whisper_model_config or self._whisper_model_candidates()[0]
        self._current_whisper_model_config = model_config

        if model_config in self._disabled_whisper_configs:
            return None
        if (
            self._whisper_model_initialized
            and not self._whisper_model_initialized_configs
            and self._whisper_model is not None
        ):
            self._whisper_model_initialized_configs.add(model_config)
            self._whisper_models[model_config] = self._whisper_model
            return self._whisper_model
        if model_config in self._whisper_model_initialized_configs:
            return self._whisper_models.get(model_config)
        self._whisper_model_initialized_configs.add(model_config)

        cache_dir = self.settings.subtitles.model_cache_dir.resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_dir))

        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except Exception as exc:
            log("subtitles", f"faster-whisper unavailable reason={exc}")
            return None

        try:
            model = WhisperModel(
                self.settings.subtitles.model_size,
                device=model_config.device,
                compute_type=model_config.compute_type,
            )
        except Exception as exc:
            log(
                "subtitles",
                (
                    "failed to initialize faster-whisper model "
                    f"device={model_config.device} compute_type={model_config.compute_type} "
                    f"reason={exc}"
                ),
            )
            model = None
            if model_config.device == "cuda":
                self._cuda_disabled_for_batch = True
        self._whisper_models[model_config] = model
        self._whisper_model = model
        self._whisper_model_initialized = True
        return model

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
                device=outcome.device,
                compute_type=outcome.compute_type,
                fallback_device=outcome.fallback_device,
                created_at=datetime.now(timezone.utc),
            )
        else:
            event = SubtitleAuditEvent(
                event_type="subtitle_fallback_placeholder",
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                language=outcome.language,
                language_probability=outcome.language_probability,
                device=outcome.device,
                compute_type=outcome.compute_type,
                fallback_device=outcome.fallback_device,
                reason=outcome.reason or "model_unavailable",
                reason_detail=outcome.reason_detail,
                created_at=datetime.now(timezone.utc),
            )
        append_model(self.audit_path, event)

    def _key(self, session_id: str, match_index: int) -> str:
        return f"{session_id}:{match_index}"
