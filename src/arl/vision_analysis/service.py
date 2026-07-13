from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arl.config import Settings
from arl.media.recording_resolver import recording_duration_seconds, resolve_recording_window
from arl.shared.contracts import RecordingAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log
from arl.vision.frame_sampler import sample_every_frame_window, sample_frame_window

from .detectors import RefinementRequest, VisionDetector
from .builtin_detectors import build_builtin_detectors
from .layouts import LOL_ZH_1080P, VisionLayoutProfile
from .models import (
    VisionAnalysisAsset,
    VisionAnalysisMetrics,
    VisionAnalysisShadowReport,
    VisionDetectorHealth,
    VisionShadowProposal,
)
from .store import VisionAnalysisStateStore


class VisionAnalysisService:
    def __init__(
        self,
        settings: Settings,
        *,
        detectors: Iterable[VisionDetector] | None = None,
        layout: VisionLayoutProfile = LOL_ZH_1080P,
        sample_window: Callable[..., list[tuple[float, Any]]] = sample_frame_window,
        sample_every_frame: Callable[..., list[tuple[float, Any]]] = sample_every_frame_window,
    ) -> None:
        self.settings = settings
        resolved_detectors = (
            build_builtin_detectors(settings) if detectors is None else list(detectors)
        )
        self.detectors = sorted(resolved_detectors, key=lambda item: item.name)
        self.layout = layout
        self.sample_window = sample_window
        self.sample_every_frame = sample_every_frame
        self.assets_path = settings.storage.temp_dir / "vision-analysis-assets.jsonl"
        self.recordings_path = settings.storage.temp_dir / "recording-assets.jsonl"
        self.shadow_reports_path = (
            settings.storage.temp_dir / "vision-analysis-shadow-reports.jsonl"
        )
        self.state_store = VisionAnalysisStateStore(
            settings.storage.temp_dir / "vision-analysis-state.json"
        )

    def run(
        self,
        *,
        session_ids: set[str] | None = None,
        force_reprocess: bool = False,
    ) -> list[VisionAnalysisAsset]:
        if not self.settings.vision_analysis.enabled:
            log("vision-analysis", "skipped reason=disabled")
            return []
        latest_recordings: dict[str, RecordingAsset] = {}
        for recording in load_models(self.recordings_path, RecordingAsset):
            latest_recordings[recording.session_id] = recording
        outputs: list[VisionAnalysisAsset] = []
        for session_id, recording in latest_recordings.items():
            if session_ids is not None and session_id not in session_ids:
                continue
            asset = self._run_recording(recording, force_reprocess=force_reprocess)
            if asset is not None:
                outputs.append(asset)
        return outputs

    def _run_recording(
        self,
        recording: RecordingAsset,
        *,
        force_reprocess: bool,
    ) -> VisionAnalysisAsset | None:
        for detector in self.detectors:
            reset = getattr(detector, "reset", None)
            if reset is not None:
                reset()
        started = time.perf_counter()
        duration = recording_duration_seconds(recording)
        input_fingerprint = self._input_fingerprint(recording, duration)
        config_fingerprint = self._config_fingerprint()
        existing = self._latest_compatible(
            recording.session_id,
            input_fingerprint=input_fingerprint,
            config_fingerprint=config_fingerprint,
        )
        if existing is not None and not force_reprocess:
            cached = existing.model_copy(deep=True)
            cached.metrics.cache_hit = True
            cached.metrics.cache_reason = "compatible_asset"
            log("vision-analysis", f"cache hit session_id={recording.session_id}")
            return cached

        metrics = VisionAnalysisMetrics(
            refinement_cap_seconds=round(
                duration * self.settings.vision_analysis.refinement_max_source_fraction,
                3,
            )
        )
        health = {
            detector.name: VisionDetectorHealth(detector=detector.name)
            for detector in self.detectors
        }
        readings = []
        events = []
        requests: list[RefinementRequest] = []
        status = "ok"
        min_interval = min(
            (max(0.1, detector.coarse_interval_seconds) for detector in self.detectors),
            default=self.settings.vision_analysis.coarse_interval_seconds,
        )
        spans = resolve_recording_window(recording, start_seconds=0.0, end_seconds=duration)
        for span in spans:
            path = Path(span.path)
            if not path.exists():
                status = "degraded"
                continue
            try:
                frames = self.sample_window(
                    path,
                    span.local_start_seconds,
                    span.local_end_seconds,
                    interval_seconds=min_interval,
                )
            except RuntimeError as exc:
                status = "degraded"
                log("vision-analysis", f"coarse sample failed path={path} detail={exc}")
                continue
            for local_at, frame in frames:
                metrics.coarse_decoded_frames += 1
                source_at = span.source_start_seconds + (local_at - span.local_start_seconds)
                if not self.layout.supports(frame):
                    status = "degraded"
                    for item in health.values():
                        item.status = "degraded"
                        item.detail = "unsupported_frame_geometry"
                    continue
                for detector in self.detectors:
                    if not self._is_due(source_at, detector.coarse_interval_seconds):
                        continue
                    item = health[detector.name]
                    item.invocations += 1
                    try:
                        output = detector.analyze(frame, source_at, provenance="coarse")
                    except Exception as exc:  # detector isolation boundary
                        item.status = "degraded"
                        item.detail = f"{type(exc).__name__}: {exc}"
                        status = "degraded"
                        continue
                    item.accepted_readings += len(output.readings)
                    readings.extend(output.readings)
                    events.extend(output.events)
                    requests.extend(output.refinement_requests)

        merged_requests = self._merge_refinement_requests(requests, duration=duration)
        metrics.refinement_candidate_count = len(requests)
        metrics.refinement_range_count = len(merged_requests)
        metrics.refinement_source_seconds = round(
            sum(end - start for start, end, _ in merged_requests), 3
        )
        if metrics.refinement_source_seconds >= metrics.refinement_cap_seconds > 0:
            metrics.refinement_cap_exhausted = True
        detector_by_name = {detector.name: detector for detector in self.detectors}
        for range_start, range_end, detector_names in merged_requests:
            for span in resolve_recording_window(
                recording,
                start_seconds=range_start,
                end_seconds=range_end,
            ):
                path = Path(span.path)
                if not path.exists():
                    continue
                frames = self.sample_every_frame(
                    path,
                    span.local_start_seconds,
                    span.local_end_seconds,
                )
                for local_at, frame in frames:
                    metrics.refined_decoded_frames += 1
                    if metrics.refined_decoded_frames > self.settings.vision_analysis.refinement_max_frames:
                        metrics.refinement_cap_exhausted = True
                        break
                    source_at = span.source_start_seconds + (local_at - span.local_start_seconds)
                    for detector_name in detector_names:
                        detector = detector_by_name.get(detector_name)
                        if detector is None:
                            continue
                        item = health[detector_name]
                        item.invocations += 1
                        try:
                            output = detector.analyze(frame, source_at, provenance="refined")
                        except Exception as exc:
                            item.status = "degraded"
                            item.detail = f"{type(exc).__name__}: {exc}"
                            status = "degraded"
                            continue
                        item.accepted_readings += len(output.readings)
                        readings.extend(output.readings)
                        events.extend(output.events)
                if metrics.refinement_cap_exhausted:
                    break
            if metrics.refinement_cap_exhausted:
                break

        for detector in self.detectors:
            finalize = getattr(detector, "finalize", None)
            if finalize is None:
                continue
            item = health[detector.name]
            try:
                output = finalize()
            except Exception as exc:
                item.status = "degraded"
                item.detail = f"finalize {type(exc).__name__}: {exc}"
                status = "degraded"
                continue
            item.accepted_readings += len(output.readings)
            readings.extend(output.readings)
            events.extend(output.events)

        metrics.wall_time_seconds = round(time.perf_counter() - started, 3)
        asset = VisionAnalysisAsset(
            session_id=recording.session_id,
            recording_path=recording.path,
            source_duration_seconds=duration,
            input_fingerprint=input_fingerprint,
            config_fingerprint=config_fingerprint,
            schema_version=self.settings.vision_analysis.schema_version,
            layout_profile=self.layout.name,
            status=status,
            detector_health=list(health.values()),
            readings=readings,
            events=events,
            metrics=metrics,
            created_at=datetime.now(timezone.utc),
        )
        append_model(self.assets_path, asset)
        if self.settings.vision_analysis.new_signals_shadow_mode:
            append_model(self.shadow_reports_path, self._build_shadow_report(asset))
        state = self.state_store.load()
        state.processed_fingerprint_by_session[recording.session_id] = (
            f"{input_fingerprint}:{config_fingerprint}"
        )
        self.state_store.save(state)
        log(
            "vision-analysis",
            f"completed session_id={recording.session_id} status={status} "
            f"coarse_frames={metrics.coarse_decoded_frames} "
            f"refined_frames={metrics.refined_decoded_frames}",
        )
        return asset

    @staticmethod
    def _build_shadow_report(asset: VisionAnalysisAsset) -> VisionAnalysisShadowReport:
        proposals: list[VisionShadowProposal] = []
        for event in asset.events:
            if event.kind == "death_respawn_state":
                proposed_respawn = float(
                    event.attributes.get("proposed_respawn_at", event.ended_at_seconds)
                )
                proposals.append(
                    VisionShadowProposal(
                        kind="death_wait_trim_candidate",
                        started_at_seconds=event.observed_at_seconds + 3.0,
                        ended_at_seconds=max(
                            event.observed_at_seconds + 3.0,
                            proposed_respawn - 3.0,
                        ),
                        attributes={"preserve_reaction_seconds": 3.0},
                        evidence_event_ids=[event.event_id],
                    )
                )
            elif event.kind == "match_result":
                proposals.append(
                    VisionShadowProposal(
                        kind="match_end_candidate",
                        started_at_seconds=event.observed_at_seconds,
                        ended_at_seconds=event.ended_at_seconds,
                        attributes={"result": event.attributes.get("result")},
                        evidence_event_ids=[event.event_id],
                    )
                )
        new_signal_events = [
            item
            for item in asset.events
            if item.kind in {"death_respawn_state", "match_result"}
        ]
        return VisionAnalysisShadowReport(
            session_id=asset.session_id,
            input_fingerprint=asset.input_fingerprint,
            proposals=proposals,
            accepted_event_count=len(new_signal_events),
            rejected_reason=None if new_signal_events else "no_confirmed_new_signal_events",
            created_at=datetime.now(timezone.utc),
        )

    def _merge_refinement_requests(
        self,
        requests: list[RefinementRequest],
        *,
        duration: float,
    ) -> list[tuple[float, float, set[str]]]:
        cap = max(0.0, duration * self.settings.vision_analysis.refinement_max_source_fraction)
        normalized = sorted(
            (
                max(0.0, request.started_at_seconds),
                min(duration, request.ended_at_seconds),
                request.detector,
            )
            for request in requests
            if request.ended_at_seconds > request.started_at_seconds
        )
        merged: list[tuple[float, float, set[str]]] = []
        used = 0.0
        for start, end, detector in normalized:
            if merged and start <= merged[-1][1] + 0.001:
                previous_start, previous_end, names = merged[-1]
                candidate_end = max(previous_end, end)
                extra = candidate_end - previous_end
                if used + extra > cap:
                    candidate_end = previous_end + max(0.0, cap - used)
                    extra = candidate_end - previous_end
                names.add(detector)
                merged[-1] = (previous_start, candidate_end, names)
                used += extra
            elif used < cap:
                allowed_end = min(end, start + (cap - used))
                if allowed_end > start:
                    merged.append((start, allowed_end, {detector}))
                    used += allowed_end - start
            if used >= cap:
                break
        return merged

    @staticmethod
    def _is_due(at_seconds: float, interval_seconds: float) -> bool:
        interval = max(0.1, interval_seconds)
        nearest = round(at_seconds / interval) * interval
        return abs(at_seconds - nearest) <= 0.05

    def _latest_compatible(
        self,
        session_id: str,
        *,
        input_fingerprint: str,
        config_fingerprint: str,
    ) -> VisionAnalysisAsset | None:
        result = None
        for asset in load_models(self.assets_path, VisionAnalysisAsset):
            if (
                asset.session_id == session_id
                and asset.input_fingerprint == input_fingerprint
                and asset.config_fingerprint == config_fingerprint
                and asset.schema_version == self.settings.vision_analysis.schema_version
            ):
                result = asset
        return result

    def _config_fingerprint(self) -> str:
        payload = {
            "schema": self.settings.vision_analysis.schema_version,
            "layout": self.layout.name,
            "coarse_interval": self.settings.vision_analysis.coarse_interval_seconds,
            "refinement_fraction": self.settings.vision_analysis.refinement_max_source_fraction,
            "refinement_frames": self.settings.vision_analysis.refinement_max_frames,
            "detectors": [
                (item.name, item.version, item.coarse_interval_seconds)
                for item in self.detectors
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _input_fingerprint(recording: RecordingAsset, duration: float) -> str:
        paths = [Path(span.path) for span in resolve_recording_window(recording, start_seconds=0.0, end_seconds=duration)]
        payload = []
        for path in paths:
            try:
                stat = path.stat()
                payload.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns))
            except OSError:
                payload.append((str(path), None, None))
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
