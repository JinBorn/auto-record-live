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
from arl.vision.frame_sampler import iter_every_frame_window, iter_frame_window

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
        sample_window: Callable[..., Iterable[tuple[float, Any]]] = iter_frame_window,
        sample_every_frame: Callable[..., Iterable[tuple[float, Any]]] = iter_every_frame_window,
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
        existing_assets = (
            []
            if force_reprocess
            else load_models(self.assets_path, VisionAnalysisAsset)
        )
        outputs: list[VisionAnalysisAsset] = []
        for session_id, recording in latest_recordings.items():
            if session_ids is not None and session_id not in session_ids:
                continue
            asset = self._run_recording(
                recording,
                force_reprocess=force_reprocess,
                existing_assets=existing_assets,
            )
            if asset is not None:
                outputs.append(asset)
        return outputs

    def _run_recording(
        self,
        recording: RecordingAsset,
        *,
        force_reprocess: bool,
        existing_assets: Iterable[VisionAnalysisAsset],
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
            assets=existing_assets,
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
        frame_cap_exhausted = False
        for range_start, range_end, detector_names in merged_requests:
            for detector_name in detector_names:
                detector = detector_by_name.get(detector_name)
                begin_refinement_range = getattr(
                    detector,
                    "begin_refinement_range",
                    None,
                )
                if begin_refinement_range is not None:
                    begin_refinement_range(range_start, range_end)
            for span in resolve_recording_window(
                recording,
                start_seconds=range_start,
                end_seconds=range_end,
            ):
                if self._refinement_range_complete(
                    detector_names,
                    detector_by_name=detector_by_name,
                ):
                    break
                path = Path(span.path)
                if not path.exists():
                    continue
                refinement_interval = self._refinement_interval_seconds(
                    detector_names,
                    detector_by_name=detector_by_name,
                )
                if refinement_interval > 0.0:
                    frames = self.sample_window(
                        path,
                        span.local_start_seconds,
                        span.local_end_seconds,
                        interval_seconds=refinement_interval,
                    )
                else:
                    frames = self.sample_every_frame(
                        path,
                        span.local_start_seconds,
                        span.local_end_seconds,
                    )
                for local_at, frame in frames:
                    if (
                        metrics.refined_decoded_frames
                        >= self.settings.vision_analysis.refinement_max_frames
                    ):
                        metrics.refinement_cap_exhausted = True
                        frame_cap_exhausted = True
                        break
                    metrics.refined_decoded_frames += 1
                    source_at = span.source_start_seconds + (local_at - span.local_start_seconds)
                    for detector_name in detector_names:
                        detector = detector_by_name.get(detector_name)
                        if detector is None or self._detector_refinement_complete(detector):
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
                    if self._refinement_range_complete(
                        detector_names,
                        detector_by_name=detector_by_name,
                    ):
                        break
                if frame_cap_exhausted:
                    break
            if frame_cap_exhausted:
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
        if not normalized or cap <= 0.0:
            return []
        boundaries = sorted(
            {point for start, end, _ in normalized for point in (start, end)}
        )
        atomic_ranges: list[tuple[float, float, set[str]]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            names = {
                detector
                for request_start, request_end, detector in normalized
                if request_start < end - 0.001 and request_end > start + 0.001
            }
            if not names:
                continue
            atomic_ranges.append((start, end, names))

        detector_priority = {"kda": 0, "match_result": 1, "respawn": 2}
        selected: list[tuple[float, float, set[str]]] = []
        remaining = cap
        for start, end, names in sorted(
            atomic_ranges,
            key=lambda item: (
                min(detector_priority.get(name, 1) for name in item[2]),
                item[0],
            ),
        ):
            if remaining <= 0.0:
                break
            allowed_end = min(end, start + remaining)
            if allowed_end > start:
                selected.append((start, allowed_end, names))
                remaining -= allowed_end - start

        merged: list[tuple[float, float, set[str]]] = []
        for start, allowed_end, names in sorted(selected, key=lambda item: item[0]):
            if (
                merged
                and abs(merged[-1][1] - start) <= 0.001
                and merged[-1][2] == names
            ):
                previous_start, _, previous_names = merged[-1]
                merged[-1] = (previous_start, allowed_end, previous_names)
            else:
                merged.append((start, allowed_end, names))
        return merged

    @staticmethod
    def _refinement_interval_seconds(
        detector_names: set[str],
        *,
        detector_by_name: dict[str, VisionDetector],
    ) -> float:
        intervals = [
            max(
                0.0,
                float(
                    getattr(
                        detector_by_name[detector_name],
                        "refinement_interval_seconds",
                        0.0,
                    )
                ),
            )
            for detector_name in detector_names
            if detector_name in detector_by_name
        ]
        if not intervals or any(interval <= 0.0 for interval in intervals):
            return 0.0
        return min(intervals)

    @staticmethod
    def _detector_refinement_complete(detector: VisionDetector) -> bool:
        check = getattr(detector, "refinement_range_complete", None)
        return bool(check()) if check is not None else False

    @classmethod
    def _refinement_range_complete(
        cls,
        detector_names: set[str],
        *,
        detector_by_name: dict[str, VisionDetector],
    ) -> bool:
        detectors = [
            detector_by_name[name]
            for name in detector_names
            if name in detector_by_name
        ]
        return bool(detectors) and all(
            cls._detector_refinement_complete(detector) for detector in detectors
        )

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
        assets: Iterable[VisionAnalysisAsset],
    ) -> VisionAnalysisAsset | None:
        result = None
        for asset in assets:
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
