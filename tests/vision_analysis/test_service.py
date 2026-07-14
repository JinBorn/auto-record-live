from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from arl.config import Settings
from arl.shared.contracts import (
    RecordingAsset,
    RecordingChunk,
    RecordingChunkManifest,
    SourceType,
)
from arl.shared.jsonl_store import append_model, load_models
from arl.vision_analysis.detectors import DetectorOutput, RefinementRequest
from arl.vision_analysis.models import VisionAnalysisAsset, VisionReading
from arl.vision_analysis.models import VisionEvent
from arl.vision_analysis.service import VisionAnalysisService


FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)


class _Detector:
    version = "1"

    def __init__(
        self,
        name: str,
        interval: float,
        *,
        request: tuple[float, float] | None = None,
        fail: bool = False,
        refinement_interval: float = 0.0,
    ) -> None:
        self.name = name
        self.coarse_interval_seconds = interval
        self.request = request
        self.fail = fail
        self.refinement_interval_seconds = refinement_interval
        self.calls: list[tuple[float, str]] = []

    def analyze(self, frame, at_seconds: float, *, provenance: str) -> DetectorOutput:
        self.calls.append((at_seconds, provenance))
        if self.fail:
            raise RuntimeError("detector failed")
        requests = []
        if provenance == "coarse" and self.request and len(self.calls) == 1:
            requests.append(RefinementRequest(self.name, *self.request))
        return DetectorOutput(
            readings=[
                VisionReading(
                    reading_id=f"{self.name}:{provenance}:{at_seconds:.1f}",
                    detector=self.name,
                    at_seconds=at_seconds,
                    confidence=0.9,
                    provenance=provenance,
                )
            ],
            refinement_requests=requests,
        )


class _CompletingDetector(_Detector):
    def __init__(self, name: str, interval: float, *, request: tuple[float, float]) -> None:
        super().__init__(name, interval, request=request)
        self._complete = False

    def begin_refinement_range(self, start_seconds: float, end_seconds: float) -> None:
        self._complete = False

    def refinement_range_complete(self) -> bool:
        return self._complete

    def analyze(self, frame, at_seconds: float, *, provenance: str) -> DetectorOutput:
        output = super().analyze(frame, at_seconds, provenance=provenance)
        if provenance == "refined":
            self._complete = True
        return output


class VisionAnalysisServiceTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings()
        self.settings.storage.temp_dir = root / "tmp"
        self.settings.vision_analysis.enabled = True
        self.video = root / "recording.mp4"
        self.video.write_bytes(b"video")
        append_model(
            self.settings.storage.temp_dir / "recording-assets.jsonl",
            RecordingAsset(
                session_id="session-a",
                source_type=SourceType.DIRECT_STREAM,
                path=str(self.video),
                started_at=datetime.now(timezone.utc),
            ),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_one_coarse_decode_schedule_serves_multiple_detectors(self) -> None:
        first = _Detector("first", 10.0)
        second = _Detector("second", 20.0)
        sample_calls = []

        def sample_window(path, start, end, *, interval_seconds):
            sample_calls.append((path, start, end, interval_seconds))
            return [(0.0, FRAME), (10.0, FRAME), (20.0, FRAME)]

        service = VisionAnalysisService(
            self.settings,
            detectors=[first, second],
            sample_window=sample_window,
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=20.0):
            assets = service.run()

        self.assertEqual(len(sample_calls), 1)
        self.assertEqual(sample_calls[0][3], 10.0)
        self.assertEqual(len(first.calls), 3)
        self.assertEqual(len(second.calls), 2)
        self.assertEqual(assets[0].metrics.coarse_decoded_frames, 3)

    def test_cache_hit_avoids_video_decode_and_config_change_invalidates(self) -> None:
        detector = _Detector("timer", 10.0)
        calls = 0

        def sample_window(path, start, end, *, interval_seconds):
            nonlocal calls
            calls += 1
            return [(0.0, FRAME)]

        service = VisionAnalysisService(
            self.settings,
            detectors=[detector],
            sample_window=sample_window,
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=20.0):
            first = service.run()
            cached = service.run()
            self.settings.vision_analysis.coarse_interval_seconds = 7.0
            changed = service.run()

        self.assertEqual(calls, 2)
        self.assertFalse(first[0].metrics.cache_hit)
        self.assertTrue(cached[0].metrics.cache_hit)
        self.assertFalse(changed[0].metrics.cache_hit)
        self.assertEqual(
            len(load_models(service.assets_path, VisionAnalysisAsset)),
            2,
        )

    def test_multi_session_cache_loads_asset_manifest_once(self) -> None:
        append_model(
            self.settings.storage.temp_dir / "recording-assets.jsonl",
            RecordingAsset(
                session_id="session-b",
                source_type=SourceType.DIRECT_STREAM,
                path=str(self.video),
                started_at=datetime.now(timezone.utc),
            ),
        )
        detector = _Detector("timer", 10.0)
        service = VisionAnalysisService(
            self.settings,
            detectors=[detector],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=20.0):
            service.run()
            with patch(
                "arl.vision_analysis.service.load_models",
                wraps=load_models,
            ) as mocked_load_models:
                cached = service.run()

        asset_manifest_loads = [
            call
            for call in mocked_load_models.call_args_list
            if call.args[0] == service.assets_path
        ]
        self.assertEqual(len(asset_manifest_loads), 1)
        self.assertEqual(len(cached), 2)
        self.assertTrue(all(asset.metrics.cache_hit for asset in cached))

    def test_detector_failure_is_isolated(self) -> None:
        good = _Detector("good", 10.0)
        bad = _Detector("bad", 10.0, fail=True)
        service = VisionAnalysisService(
            self.settings,
            detectors=[good, bad],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=20.0):
            asset = service.run()[0]

        self.assertEqual(asset.status, "degraded")
        self.assertTrue(any(item.detector == "good" for item in asset.readings))
        health = {item.detector: item for item in asset.detector_health}
        self.assertEqual(health["good"].status, "ok")
        self.assertEqual(health["bad"].status, "degraded")

    def test_refinement_ranges_merge_and_respect_fraction_cap(self) -> None:
        self.settings.vision_analysis.refinement_max_source_fraction = 0.15
        first = _Detector("first", 10.0, request=(10.0, 22.0))
        second = _Detector("second", 10.0, request=(20.0, 40.0))
        refined_ranges = []

        def sample_every(path, start, end):
            refined_ranges.append((start, end))
            return []

        service = VisionAnalysisService(
            self.settings,
            detectors=[first, second],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
            sample_every_frame=sample_every,
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=100.0):
            asset = service.run()[0]

        self.assertEqual(
            refined_ranges,
            [(10.0, 20.0), (20.0, 22.0), (22.0, 25.0)],
        )
        self.assertEqual(asset.metrics.refinement_source_seconds, 15.0)
        self.assertTrue(asset.metrics.refinement_cap_exhausted)

    def test_source_fraction_cap_does_not_skip_allowed_refinement_ranges(self) -> None:
        self.settings.vision_analysis.refinement_max_source_fraction = 0.15
        first = _Detector("first", 10.0, request=(0.0, 5.0))
        second = _Detector("second", 10.0, request=(10.0, 20.0))
        refined_ranges = []

        def sample_every(path, start, end):
            refined_ranges.append((start, end))
            return [(start, FRAME)]

        service = VisionAnalysisService(
            self.settings,
            detectors=[first, second],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
            sample_every_frame=sample_every,
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=100.0):
            asset = service.run()[0]

        self.assertEqual(refined_ranges, [(0.0, 5.0), (10.0, 20.0)])
        self.assertEqual(asset.metrics.refined_decoded_frames, 2)
        self.assertEqual(asset.metrics.refinement_source_seconds, 15.0)
        self.assertTrue(asset.metrics.refinement_cap_exhausted)

    def test_refinement_budget_prioritizes_late_kda_over_early_shadow_range(self) -> None:
        self.settings.vision_analysis.refinement_max_source_fraction = 0.15
        respawn = _Detector("respawn", 10.0, request=(0.0, 20.0))
        kda = _Detector("kda", 10.0, request=(80.0, 90.0))
        refined_ranges = []

        service = VisionAnalysisService(
            self.settings,
            detectors=[respawn, kda],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
            sample_every_frame=lambda path, start, end: refined_ranges.append(
                (start, end)
            )
            or [],
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=100.0):
            asset = service.run()[0]

        self.assertEqual(refined_ranges, [(0.0, 5.0), (80.0, 90.0)])
        self.assertEqual(asset.metrics.refinement_source_seconds, 15.0)

    def test_refinement_frame_cap_never_overcounts_decoded_frames(self) -> None:
        self.settings.vision_analysis.refinement_max_source_fraction = 1.0
        self.settings.vision_analysis.refinement_max_frames = 2
        detector = _Detector("timer", 10.0, request=(0.0, 10.0))

        service = VisionAnalysisService(
            self.settings,
            detectors=[detector],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
            sample_every_frame=lambda *args, **kwargs: [
                (0.0, FRAME),
                (1.0, FRAME),
                (2.0, FRAME),
            ],
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=10.0):
            asset = service.run()[0]

        self.assertEqual(asset.metrics.refined_decoded_frames, 2)
        self.assertTrue(asset.metrics.refinement_cap_exhausted)

    def test_sparse_refinement_uses_detector_interval_without_every_frame_decode(self) -> None:
        detector = _Detector(
            "respawn",
            10.0,
            request=(10.0, 20.0),
            refinement_interval=0.5,
        )
        sample_calls = []

        def sample_window(path, start, end, *, interval_seconds):
            sample_calls.append((start, end, interval_seconds))
            if interval_seconds == 0.5:
                return [(10.0, FRAME), (10.5, FRAME)]
            return [(0.0, FRAME)]

        service = VisionAnalysisService(
            self.settings,
            detectors=[detector],
            sample_window=sample_window,
            sample_every_frame=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("sparse refinement should not decode every frame")
            ),
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=100.0):
            asset = service.run()[0]

        self.assertIn((10.0, 20.0, 0.5), sample_calls)
        self.assertEqual(asset.metrics.refined_decoded_frames, 2)

    def test_completed_refinement_range_stops_decoding_remaining_frames(self) -> None:
        detector = _CompletingDetector("kda", 10.0, request=(0.0, 10.0))
        service = VisionAnalysisService(
            self.settings,
            detectors=[detector],
            sample_window=lambda *args, **kwargs: [(0.0, FRAME)],
            sample_every_frame=lambda *args, **kwargs: [
                (0.0, FRAME),
                (1.0, FRAME),
                (2.0, FRAME),
            ],
        )
        with patch("arl.vision_analysis.service.recording_duration_seconds", return_value=10.0):
            asset = service.run()[0]

        self.assertEqual(asset.metrics.refined_decoded_frames, 1)
        self.assertEqual(
            [provenance for _, provenance in detector.calls].count("refined"),
            1,
        )

    def test_segmented_recording_maps_local_frames_to_source_time(self) -> None:
        root = Path(self.temp.name)
        chunk1 = root / "chunk-1.mp4"
        chunk2 = root / "chunk-2.mp4"
        chunk1.write_bytes(b"one")
        chunk2.write_bytes(b"two")
        manifest_path = root / "recording-chunks.json"
        manifest_path.write_text(
            RecordingChunkManifest(
                session_id="session-a",
                source_type=SourceType.DIRECT_STREAM,
                path=str(manifest_path),
                started_at=datetime.now(timezone.utc),
                chunks=[
                    RecordingChunk(path=str(chunk1), started_at_seconds=0, ended_at_seconds=10, duration_seconds=10, index=0),
                    RecordingChunk(path=str(chunk2), started_at_seconds=10, ended_at_seconds=20, duration_seconds=10, index=1),
                ],
                created_at=datetime.now(timezone.utc),
            ).model_dump_json(),
            encoding="utf-8",
        )
        recordings_path = self.settings.storage.temp_dir / "recording-assets.jsonl"
        recordings_path.unlink()
        append_model(
            recordings_path,
            RecordingAsset(
                session_id="session-a",
                source_type=SourceType.DIRECT_STREAM,
                path=str(manifest_path),
                started_at=datetime.now(timezone.utc),
            ),
        )
        detector = _Detector("timer", 10.0)
        service = VisionAnalysisService(
            self.settings,
            detectors=[detector],
            sample_window=lambda path, start, end, **kwargs: [(start, FRAME)],
        )
        asset = service.run()[0]

        self.assertEqual([item.at_seconds for item in asset.readings], [0.0, 10.0])
        self.assertEqual(asset.metrics.coarse_decoded_frames, 2)

    def test_shadow_report_records_proposed_adjustments_without_mutation(self) -> None:
        asset = VisionAnalysisAsset(
            session_id="session-shadow",
            recording_path=str(self.video),
            source_duration_seconds=600.0,
            input_fingerprint="input",
            config_fingerprint="config",
            schema_version=1,
            layout_profile="lol_zh_1080p_v1",
            status="ok",
            events=[
                VisionEvent(
                    event_id="death-1",
                    kind="death_respawn_state",
                    started_at_seconds=100.0,
                    ended_at_seconds=130.0,
                    observed_at_seconds=100.0,
                    confidence=0.8,
                    attributes={"proposed_respawn_at": 130.0},
                ),
                VisionEvent(
                    event_id="result-1",
                    kind="match_result",
                    started_at_seconds=590.0,
                    ended_at_seconds=592.0,
                    observed_at_seconds=590.0,
                    confidence=0.9,
                    attributes={"result": "victory"},
                ),
            ],
            created_at=datetime.now(timezone.utc),
        )

        report = VisionAnalysisService._build_shadow_report(asset)

        self.assertEqual(report.accepted_event_count, 2)
        self.assertEqual(
            [item.kind for item in report.proposals],
            ["death_wait_trim_candidate", "match_end_candidate"],
        )
        self.assertEqual(asset.events[0].started_at_seconds, 100.0)
