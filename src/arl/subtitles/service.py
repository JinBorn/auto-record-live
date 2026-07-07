from __future__ import annotations

from dataclasses import dataclass
import inspect
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.media.recording_resolver import (
    recording_duration_seconds,
    resolve_recording_window,
)
from arl.segmenter.signals_from_subtitles import StageSignalFromSubtitlesService
from arl.shared.contracts import MatchBoundary, MediaSpan, RecordingAsset, SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log
from arl.subtitles.models import SubtitleAuditEvent, SubtitleStateFile
from arl.subtitles.normalization import SubtitleTextNormalizer


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
    model_size: str
    device: str
    compute_type: str


@dataclass(frozen=True)
class TranscriptionInput:
    path: Path
    boundary_start_seconds: float
    boundary_end_seconds: float
    clip_timestamps: list[float] | None
    preprocessed: bool = False


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
        self.asr_audio_dir = settings.storage.temp_dir / "asr-audio"
        self._whisper_model: Any | None = None
        self._whisper_model_initialized = False
        self._whisper_models: dict[WhisperModelConfig, Any | None] = {}
        self._whisper_model_initialized_configs: set[WhisperModelConfig] = set()
        self._current_whisper_model_config: WhisperModelConfig | None = None
        self._disabled_whisper_configs: set[WhisperModelConfig] = set()
        self._cuda_disabled_for_batch = False
        self._text_normalizer = SubtitleTextNormalizer(
            settings.subtitles,
            warn=lambda message: log("subtitles", message),
        )
        self._initial_prompt_cache: str | None = None
        self._initial_prompt_loaded = False

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        match_indices: set[int] | None = None,
        force_reprocess: bool = False,
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
        latest_recording_by_session: dict[str, RecordingAsset] = {}
        latest_recording_duration_by_session: dict[str, float] = {}
        for recording_asset in recording_assets:
            latest_recording_by_session[recording_asset.session_id] = recording_asset
            latest_recording_duration_by_session[recording_asset.session_id] = (
                recording_duration_seconds(recording_asset)
            )
        subtitle_assets = load_models(self.assets_path, SubtitleAsset)
        existing_output_keys = {
            self._key(asset.session_id, asset.match_index)
            for asset in subtitle_assets
            if Path(asset.path).exists()
        }
        state = self._load_state()
        processed_keys = set(state.processed_match_keys)

        processed = 0
        for boundary in filtered_boundaries:
            key = self._key(boundary.session_id, boundary.match_index)
            if not boundary.is_complete:
                if key not in processed_keys:
                    log(
                        "subtitles",
                        "skip incomplete match boundary "
                        f"session_id={boundary.session_id} "
                        f"match_index={boundary.match_index} "
                        f"reason={boundary.reason or 'unknown'}",
                    )
                    state.processed_match_keys.append(key)
                    processed_keys.add(key)
                continue
            if key in processed_keys and key in existing_output_keys and not force_reprocess:
                continue
            if key in processed_keys and not force_reprocess:
                log(
                    "subtitles",
                    "reprocessing missing subtitle output "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )
            elif key in processed_keys:
                log(
                    "subtitles",
                    "force reprocessing subtitle output "
                    f"session_id={boundary.session_id} match_index={boundary.match_index}",
                )

            recording_asset = latest_recording_by_session.get(boundary.session_id)
            recording_duration = latest_recording_duration_by_session.get(boundary.session_id)
            subtitle_path, outcome = self._write_subtitle(
                boundary,
                recording_asset,
                recording_duration,
            )
            subtitle_asset = SubtitleAsset(
                session_id=boundary.session_id,
                match_index=boundary.match_index,
                path=str(subtitle_path),
                format="srt",
            )
            append_model(self.assets_path, subtitle_asset)
            self._append_subtitle_audit(boundary, outcome)
            if key not in processed_keys:
                state.processed_match_keys.append(key)
                processed_keys.add(key)
            existing_output_keys.add(key)
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
                force_reprocess=force_reprocess,
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
        recording_asset: RecordingAsset | None,
        recording_duration_seconds: float | None = None,
    ) -> tuple[Path, TranscribeOutcome]:
        output_dir = self.settings.storage.processed_dir / boundary.session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = output_dir / f"match-{boundary.match_index:02d}.srt"
        outcome = self._transcribe_boundary(
            boundary,
            recording_asset,
            recording_duration_seconds=recording_duration_seconds,
        )
        if outcome.entries:
            subtitle_path.write_text(self._build_srt(outcome.entries), encoding="utf-8")
        else:
            subtitle_path.write_text(self._placeholder_srt(), encoding="utf-8")
        return subtitle_path, outcome

    def _transcribe_boundary(
        self,
        boundary: MatchBoundary,
        recording_asset: RecordingAsset | None,
        *,
        recording_duration_seconds: float | None = None,
    ) -> TranscribeOutcome:
        if self.settings.subtitles.provider != "faster-whisper":
            return TranscribeOutcome(
                entries=[],
                reason="model_unavailable",
                reason_detail=f"unsupported_provider:{self.settings.subtitles.provider}",
            )
        if recording_asset is None:
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

        if self._should_skip_full_recording_fallback_asr(
            boundary,
            recording_duration_seconds,
        ):
            threshold = self._fallback_asr_threshold_seconds()
            reason_detail = (
                "low_confidence_full_recording "
                f"duration={recording_duration_seconds:.3f} threshold={threshold:.3f}"
            )
            log(
                "subtitles",
                (
                    "fallback to placeholder "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=low_confidence_full_recording"
                ),
            )
            return TranscribeOutcome(
                entries=[],
                reason="low_confidence_full_recording",
                reason_detail=reason_detail,
            )
        spans = resolve_recording_window(
            recording_asset,
            start_seconds=boundary.started_at_seconds,
            end_seconds=boundary.ended_at_seconds,
        )
        span_error = self._validate_transcription_spans(spans)
        if span_error is not None:
            reason, reason_detail = span_error
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
                reason=reason,
                reason_detail=reason_detail,
            )

        transcription_input = self._prepare_transcription_input(boundary, spans)
        if transcription_input is None:
            reason_detail = "chunk_audio_preprocess_unavailable"
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
                reason="missing_recording",
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
                        f"model={model_config.model_size} "
                        f"device={model_config.device} compute_type={model_config.compute_type}"
                    ),
                )
                if model_config.device == "cuda":
                    self._cuda_disabled_for_batch = True
                    continue
                continue

            outcome = self._transcribe_with_model(
                model,
                model_config,
                boundary,
                transcription_input,
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
            if self._should_try_next_whisper_candidate(outcome):
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
        transcription_input: TranscriptionInput,
    ) -> TranscribeOutcome:
        try:
            transcribe_kwargs: dict[str, Any] = {
                "language": self.settings.subtitles.language or None,
                "word_timestamps": True,
                "beam_size": self.settings.subtitles.beam_size,
                "vad_filter": self.settings.subtitles.vad_filter,
            }
            initial_prompt = self._initial_prompt()
            if initial_prompt:
                transcribe_kwargs["initial_prompt"] = initial_prompt
            if self.settings.subtitles.vad_filter:
                transcribe_kwargs["vad_parameters"] = {
                    "min_silence_duration_ms": (
                        self.settings.subtitles.vad_min_silence_duration_ms
                    ),
                    "speech_pad_ms": self.settings.subtitles.vad_speech_pad_ms,
                }
            if transcription_input.clip_timestamps is not None:
                transcribe_kwargs["clip_timestamps"] = transcription_input.clip_timestamps
            segments, info = model.transcribe(
                str(transcription_input.path),
                **transcribe_kwargs,
            )
        except Exception as exc:
            log(
                "subtitles",
                (
                    "transcribe failed "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"model={model_config.model_size} "
                    f"device={model_config.device} compute_type={model_config.compute_type} "
                    f"reason={exc}"
                ),
            )
            if self._should_disable_whisper_config_after_transcribe_error(
                model_config,
                exc,
            ):
                self._disable_whisper_model_config(model_config)
            return TranscribeOutcome(
                entries=[],
                device=model_config.device,
                compute_type=model_config.compute_type,
                fallback_device=self._fallback_device_for(model_config),
                reason="transcribe_failed",
                reason_detail=(
                    f"transcribe_exc:{exc.__class__.__name__}:{exc} "
                    f"model={model_config.model_size} "
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
                f"model={model_config.model_size} "
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
                    f"model={model_config.model_size} "
                    f"device={model_config.device} compute_type={model_config.compute_type}"
                ),
            )

        entries: list[tuple[float, float, str]] = []
        try:
            for segment in segments:
                raw_text = str(getattr(segment, "text", "")).strip()
                if not raw_text:
                    continue
                entry = self._entry_from_segment(
                    segment,
                    raw_text,
                    transcription_input.boundary_start_seconds,
                    transcription_input.boundary_end_seconds,
                )
                if entry is None:
                    continue
                start, end, text = entry
                entries.append((start, end, self._text_normalizer.normalize(text)))
        except Exception as exc:
            log(
                "subtitles",
                (
                    "transcribe failed "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"model={model_config.model_size} "
                    f"device={model_config.device} compute_type={model_config.compute_type} "
                    f"reason={exc}"
                ),
            )
            if self._should_disable_whisper_config_after_transcribe_error(
                model_config,
                exc,
            ):
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
                    f"model={model_config.model_size} "
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
                    f"model={model_config.model_size} "
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

    def _entry_from_segment(
        self,
        segment: Any,
        raw_text: str,
        boundary_start: float,
        boundary_end: float,
    ) -> tuple[float, float, str] | None:
        has_word_timestamps, word_entry = self._entry_from_word_timestamps(
            segment,
            raw_text,
            boundary_start,
            boundary_end,
        )
        if has_word_timestamps:
            return word_entry

        seg_start = self._optional_seconds(getattr(segment, "start", None))
        if seg_start is None:
            seg_start = 0.0
        seg_end = self._optional_seconds(getattr(segment, "end", None))
        if seg_end is None:
            seg_end = seg_start
        if seg_end <= boundary_start or seg_start >= boundary_end:
            return None

        rel_start = max(seg_start, boundary_start) - boundary_start
        rel_end = min(seg_end, boundary_end) - boundary_start
        if rel_end <= rel_start:
            return None
        return rel_start, rel_end, raw_text

    def _entry_from_word_timestamps(
        self,
        segment: Any,
        raw_text: str,
        boundary_start: float,
        boundary_end: float,
    ) -> tuple[bool, tuple[float, float, str] | None]:
        words = getattr(segment, "words", None)
        if not words:
            return False, None

        has_timed_words = False
        first_start: float | None = None
        last_end: float | None = None
        text_parts: list[str] = []
        for word in words:
            word_start = self._optional_seconds(getattr(word, "start", None))
            word_end = self._optional_seconds(getattr(word, "end", None))
            if word_start is None or word_end is None or word_end <= word_start:
                continue

            has_timed_words = True
            if word_end <= boundary_start or word_start >= boundary_end:
                continue

            clamped_start = max(word_start, boundary_start)
            clamped_end = min(word_end, boundary_end)
            if clamped_end <= clamped_start:
                continue

            first_start = (
                clamped_start if first_start is None else min(first_start, clamped_start)
            )
            last_end = clamped_end if last_end is None else max(last_end, clamped_end)
            word_text = str(getattr(word, "word", ""))
            if word_text:
                text_parts.append(word_text)

        if not has_timed_words:
            return False, None
        if first_start is None or last_end is None:
            return True, None

        text = "".join(text_parts).strip() or raw_text
        rel_start = first_start - boundary_start
        rel_end = last_end - boundary_start
        if rel_end <= rel_start or not text:
            return True, None
        return True, (rel_start, rel_end, text)

    @staticmethod
    def _optional_seconds(value: object) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _validate_transcription_spans(
        self,
        spans: list[MediaSpan],
    ) -> tuple[str, str] | None:
        if not spans:
            return "missing_recording", "recording_window_unavailable"
        for span in spans:
            span_path = Path(span.path)
            if not span_path.exists():
                return "missing_recording", f"recording_path_not_found:{span_path}"
            suffix = span_path.suffix.lower()
            if suffix not in self._TRANSCRIBE_SUFFIXES:
                return "unsupported_suffix", f"unsupported_suffix:{suffix}"
        return None

    def _prepare_transcription_input(
        self,
        boundary: MatchBoundary,
        spans: list[MediaSpan],
    ) -> TranscriptionInput | None:
        if len(spans) > 1:
            return self._prepare_chunked_transcription_input(boundary, spans)

        span = spans[0]
        source_path = Path(span.path)
        original_input = TranscriptionInput(
            path=source_path,
            boundary_start_seconds=span.local_start_seconds,
            boundary_end_seconds=span.local_end_seconds,
            clip_timestamps=[
                span.local_start_seconds,
                span.local_end_seconds,
            ],
        )
        if not self.settings.subtitles.preprocess_audio:
            return original_input

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            log(
                "subtitles",
                (
                    "audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=missing_ffmpeg"
                ),
            )
            return original_input

        duration = span.local_end_seconds - span.local_start_seconds
        if duration <= 0:
            log(
                "subtitles",
                (
                    "audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=invalid_boundary_duration"
                ),
            )
            return original_input

        output_path = (
            self.asr_audio_dir
            / boundary.session_id
            / f"match-{boundary.match_index:02d}.wav"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{span.local_start_seconds:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            self.settings.subtitles.preprocess_audio_filter,
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.settings.subtitles.preprocess_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log(
                "subtitles",
                (
                    "audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"reason={self._format_preprocess_failure(exc)}"
                ),
            )
            return original_input

        if not output_path.exists() or output_path.stat().st_size <= 0:
            log(
                "subtitles",
                (
                    "audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=output_missing_or_empty"
                ),
            )
            return original_input

        log(
            "subtitles",
            (
                "audio preprocess written "
                f"session_id={boundary.session_id} match_index={boundary.match_index}"
            ),
        )
        return TranscriptionInput(
            path=output_path,
            boundary_start_seconds=0.0,
            boundary_end_seconds=duration,
            clip_timestamps=None,
            preprocessed=True,
        )

    def _prepare_chunked_transcription_input(
        self,
        boundary: MatchBoundary,
        spans: list[MediaSpan],
    ) -> TranscriptionInput | None:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            log(
                "subtitles",
                (
                    "chunk audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=missing_ffmpeg"
                ),
            )
            return None

        duration = sum(
            max(0.0, span.local_end_seconds - span.local_start_seconds)
            for span in spans
        )
        if duration <= 0:
            log(
                "subtitles",
                (
                    "chunk audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=invalid_span_duration"
                ),
            )
            return None

        output_path = (
            self.asr_audio_dir
            / boundary.session_id
            / f"match-{boundary.match_index:02d}.wav"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-nostdin",
        ]
        for span in spans:
            command.extend(["-i", span.path])
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for index, span in enumerate(spans):
            label = f"a{index}"
            filter_parts.append(
                f"[{index}:a]"
                f"atrim=start={span.local_start_seconds:.3f}:"
                f"end={span.local_end_seconds:.3f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
            concat_inputs.append(f"[{label}]")
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(spans)}:v=0:a=1[aconcat]"
        )
        filter_parts.append(
            f"[aconcat]{self.settings.subtitles.preprocess_audio_filter}[aout]"
        )
        command.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[aout]",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.settings.subtitles.preprocess_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log(
                "subtitles",
                (
                    "chunk audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    f"reason={self._format_preprocess_failure(exc)}"
                ),
            )
            return None

        if not output_path.exists() or output_path.stat().st_size <= 0:
            log(
                "subtitles",
                (
                    "chunk audio preprocess skipped "
                    f"session_id={boundary.session_id} match_index={boundary.match_index} "
                    "reason=output_missing_or_empty"
                ),
            )
            return None

        log(
            "subtitles",
            (
                "chunk audio preprocess written "
                f"session_id={boundary.session_id} match_index={boundary.match_index} "
                f"spans={len(spans)}"
            ),
        )
        return TranscriptionInput(
            path=output_path,
            boundary_start_seconds=0.0,
            boundary_end_seconds=duration,
            clip_timestamps=None,
            preprocessed=True,
        )

    def _format_preprocess_failure(self, exc: Exception) -> str:
        if isinstance(exc, subprocess.TimeoutExpired):
            return f"timeout:{exc.timeout}"
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = (exc.stderr or "").strip().splitlines()
            detail = stderr[-1] if stderr else str(exc)
            return f"exit_status:{exc.returncode}:{detail[-240:]}"
        return f"{exc.__class__.__name__}:{str(exc)[-240:]}"

    def _whisper_model_candidates(self) -> list[WhisperModelConfig]:
        device = self.settings.subtitles.device
        compute_type = self.settings.subtitles.compute_type
        cpu_compute_type = self.settings.subtitles.cpu_compute_type
        configured_cuda_compute_type = self.settings.subtitles.cuda_compute_type
        cuda_compute_type = (
            "float16"
            if configured_cuda_compute_type == "auto"
            else configured_cuda_compute_type
        )
        if compute_type != "auto":
            cuda_compute_type = compute_type
        resolved_cpu_compute_type = cpu_compute_type if compute_type == "auto" else compute_type

        device_candidates: list[tuple[str, str]]
        if device == "cpu":
            device_candidates = [("cpu", resolved_cpu_compute_type)]
        elif device == "cuda":
            device_candidates = [("cuda", cuda_compute_type)]
        else:
            device_candidates = [
                ("cuda", cuda_compute_type),
                ("cpu", resolved_cpu_compute_type),
            ]

        candidates: list[WhisperModelConfig] = []
        seen: set[WhisperModelConfig] = set()
        for model_size in self._whisper_model_size_candidates():
            for candidate_device, candidate_compute_type in device_candidates:
                candidate = WhisperModelConfig(
                    model_size,
                    candidate_device,
                    candidate_compute_type,
                )
                if candidate in seen:
                    continue
                candidates.append(candidate)
                seen.add(candidate)
        return candidates

    def _whisper_model_size_candidates(self) -> list[str]:
        configured = self.settings.subtitles.model_size.strip() or "small"
        candidates = [configured]
        if configured != "small":
            candidates.extend(["medium", "small"])
        return list(dict.fromkeys(candidates))

    def _initial_prompt(self) -> str | None:
        if self._initial_prompt_loaded:
            return self._initial_prompt_cache
        self._initial_prompt_loaded = True
        path = self.settings.subtitles.initial_prompt_path
        if path is None or not path.exists():
            self._initial_prompt_cache = None
            return None
        try:
            prompt = path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            log("subtitles", f"initial prompt skipped path={path} reason={exc}")
            self._initial_prompt_cache = None
            return None
        max_chars = self.settings.subtitles.initial_prompt_max_chars
        if max_chars <= 0:
            self._initial_prompt_cache = None
            return None
        self._initial_prompt_cache = prompt[:max_chars] if prompt else None
        return self._initial_prompt_cache

    def _should_retry_cpu_after_cuda_failure(self) -> bool:
        return self.settings.subtitles.device == "auto"

    def _fallback_device_for(self, model_config: WhisperModelConfig) -> str | None:
        if model_config.device == "cuda" and self._should_retry_cpu_after_cuda_failure():
            return "cpu"
        return None

    def _should_skip_full_recording_fallback_asr(
        self,
        boundary: MatchBoundary,
        recording_duration: float | None,
    ) -> bool:
        if recording_duration is None:
            return False
        if boundary.confidence > 0.5:
            return False
        if boundary.match_index != 1:
            return False
        if abs(boundary.started_at_seconds) > 0.001:
            return False
        if abs(boundary.ended_at_seconds - recording_duration) > 2.0:
            return False
        return recording_duration > self._fallback_asr_threshold_seconds()

    def _fallback_asr_threshold_seconds(self) -> float:
        return max(60.0, float(self.settings.recording.segment_minutes) * 60.0)

    def _should_disable_whisper_config_after_transcribe_error(
        self,
        model_config: WhisperModelConfig,
        exc: Exception,
    ) -> bool:
        if isinstance(exc, FileNotFoundError):
            return False
        message = str(exc).lower()
        media_specific_markers = {
            "invalid data found",
            "moov atom not found",
            "no such file or directory",
            "recording_path_not_found",
            "could not open",
            "failed to open",
            "permission denied",
        }
        if any(marker in message for marker in media_specific_markers):
            return False
        if model_config.device == "cuda":
            return True
        return False

    def _should_try_next_whisper_candidate(self, outcome: TranscribeOutcome) -> bool:
        if outcome.reason != "transcribe_failed":
            return False
        reason_detail = (outcome.reason_detail or "").lower()
        media_specific_markers = {
            "filenotfounderror",
            "invalid data found",
            "moov atom not found",
            "no such file or directory",
            "recording_path_not_found",
            "could not open",
            "failed to open",
            "permission denied",
        }
        return not any(marker in reason_detail for marker in media_specific_markers)

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
                model_config.model_size,
                device=model_config.device,
                compute_type=model_config.compute_type,
            )
        except Exception as exc:
            log(
                "subtitles",
                (
                    "failed to initialize faster-whisper model "
                    f"model={model_config.model_size} "
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
