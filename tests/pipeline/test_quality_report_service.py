from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.config import QualityReportSettings, Settings, StorageSettings
from arl.copywriter.models import PublishingPackage
from arl.highlights.models import ClassifiedCue
from arl.quality_report.models import MediaProbeResult
from arl.quality_report.service import QualityReportService
from arl.shared.contracts import (
    AudioBed,
    CopyAsset,
    EditPlanAsset,
    ExportAsset,
    MatchBoundary,
    SoundEffectHit,
    SubtitleAsset,
    TimelineSegment,
    TimelineVideoTransform,
)
from arl.shared.jsonl_store import append_model


class QualityReportServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.temp_root = root / "tmp"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=root / "raw",
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            quality_report=QualityReportSettings(
                subtitle_active_ratio_min=0.2,
                max_source_gap_seconds=45.0,
                teaser_min_segments=1,
                teaser_max_segments=3,
                sfx_max_hits=6,
                zoom_min_segments=1,
                zoom_max_segments=4,
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_report_computes_metrics_and_writes_json_and_markdown(self) -> None:
        session_id = "session-quality-report"
        export_path, subtitle_path = self._seed_publish_assets(session_id)
        kda_events = [
            ClassifiedCue(
                started_at_seconds=60.0,
                ended_at_seconds=90.0,
                text=(
                    "kda_change kills=0->1 deaths=0->0 "
                    "previous_at=50.000 current_at=70.000"
                ),
                category="key_event",
                priority=1.0,
            )
        ]

        service = QualityReportService(
            self.settings,
            media_probe=lambda path: MediaProbeResult(
                duration_seconds=40.0,
                bitrate_kbps=8150.0,
                width=1920,
                height=1080,
            ),
            kda_event_provider=lambda boundary: kda_events,
        )
        result = service.run(session_ids={session_id}, match_indices={2})

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual(row.export_path, str(export_path))
        self.assertEqual(row.subtitle_path, str(subtitle_path))
        self.assertEqual(row.export_duration_seconds, 40.0)
        self.assertEqual(row.container_bitrate_kbps, 8150.0)
        self.assertEqual((row.width, row.height), (1920, 1080))
        self.assertEqual(row.plan_duration_seconds, 60.0)
        self.assertEqual(row.max_source_gap_seconds, 40.0)
        self.assertAlmostEqual(row.subtitle_active_ratio, 0.25)
        self.assertEqual(row.no_subtitle_gap_count, 2)
        self.assertEqual(row.max_no_subtitle_gap_seconds, 20.0)
        self.assertEqual(row.kda_uncovered_count, 0)
        self.assertEqual(row.kda_event_count, 1)
        self.assertEqual(row.teaser_segment_count, 1)
        self.assertEqual(row.teaser_total_seconds, 10.0)
        self.assertEqual(len(row.bgm_beds), 1)
        self.assertEqual(row.bgm_beds[0].timeline_start_seconds, 20.0)
        self.assertEqual(len(row.sfx_hits), 2)
        self.assertEqual(row.sfx_hits[0].source_timestamp_seconds, 70.0)
        self.assertEqual(row.sfx_hits[0].nearest_kda_delta_seconds, 0.0)
        self.assertEqual(len(row.zoom_segments), 1)
        self.assertEqual(row.zoom_segments[0].duration_seconds, 10.0)
        self.assertEqual(row.copywriter.title, "custom title")
        self.assertEqual(row.copywriter.cover_lines, ["line one", "line two"])
        self.assertFalse(row.copywriter.title_equals_raw_leading_subtitle)
        self.assertEqual(row.warnings, [])

        json_path = self.processed_root / session_id / "reports" / "match-02-quality-report.json"
        markdown_path = self.processed_root / session_id / "reports" / "match-02-quality-report.md"
        self.assertTrue(json_path.exists())
        self.assertTrue(markdown_path.exists())
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["session_id"], session_id)
        self.assertIn("Quality Report", markdown_path.read_text(encoding="utf-8"))

    def test_strict_returns_nonzero_when_threshold_warnings_exist(self) -> None:
        session_id = "session-quality-report-strict"
        self._seed_publish_assets(session_id)
        strict_settings = self.settings.model_copy(deep=True)
        strict_settings.quality_report = QualityReportSettings(
            subtitle_active_ratio_min=0.9,
            max_source_gap_seconds=5.0,
            teaser_min_segments=2,
            teaser_max_segments=3,
            sfx_max_hits=1,
            zoom_min_segments=2,
            zoom_max_segments=4,
        )

        result = QualityReportService(
            strict_settings,
            media_probe=lambda path: MediaProbeResult(
                duration_seconds=40.0,
                bitrate_kbps=4000.0,
                width=1280,
                height=720,
            ),
            kda_event_provider=lambda boundary: [],
        ).run(session_ids={session_id}, match_indices={2}, strict=True)

        codes = {warning.code for warning in result.rows[0].warnings}
        self.assertEqual(result.exit_code, 1)
        self.assertIn("subtitle_active_ratio_below_min", codes)
        self.assertIn("max_source_gap_above_limit", codes)
        self.assertIn("teaser_segment_count_out_of_range", codes)
        self.assertIn("sfx_hit_count_above_limit", codes)
        self.assertIn("zoom_segment_count_out_of_range", codes)

    def _seed_publish_assets(self, session_id: str) -> tuple[Path, Path]:
        now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        subtitle_path = self.processed_root / session_id / "match-02.srt"
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text(
            "\n".join(
                [
                    "1",
                    "00:00:00,000 --> 00:00:10,000",
                    "opening line",
                    "",
                    "2",
                    "00:00:30,000 --> 00:00:35,000",
                    "unused source line",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        export_path = self.export_root / "bilibili" / f"{session_id}_match02.mp4"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_bytes(b"fake mp4")
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=2,
                started_at_seconds=0.0,
                ended_at_seconds=120.0,
                confidence=0.95,
                is_complete=True,
            ),
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=2,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=2,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=now,
            ),
        )
        append_model(
            self.temp_root / "edit-plans.jsonl",
            EditPlanAsset(
                session_id=session_id,
                match_index=2,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                timeline=[
                    TimelineSegment(
                        role="teaser",
                        source_start_seconds=100.0,
                        source_end_seconds=110.0,
                        transform=TimelineVideoTransform(
                            kind="punch_in",
                            scale=1.2,
                            x_anchor=0.0,
                            y_anchor=1.0,
                            target="chat",
                        ),
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=20.0,
                        reason="condensed_context",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=60.0,
                        source_end_seconds=90.0,
                        reason="condensed_key_event",
                    ),
                ],
                audio_beds=[
                    AudioBed(
                        source_path="data/bgm/track.mp3",
                        timeline_start_seconds=20.0,
                        timeline_end_seconds=None,
                        reason="background_music_library",
                    )
                ],
                sound_effects=[
                    SoundEffectHit(
                        source_path="data/sfx/hit.wav",
                        at_seconds=40.0,
                        reason="condensed_key_event",
                    ),
                    SoundEffectHit(
                        source_path="data/sfx/hit.wav",
                        at_seconds=45.0,
                        reason="condensed_key_event",
                    ),
                ],
                created_at=now,
            ),
        )
        append_model(
            self.temp_root / "copy-assets.jsonl",
            CopyAsset(
                session_id=session_id,
                match_index=2,
                path=str(self.processed_root / session_id / "match-02-copy.json"),
                title="fallback title",
                description="description",
                tags=[],
                subtitle_path=str(subtitle_path),
                export_path=str(export_path),
                created_at=now,
            ),
        )
        append_model(
            self.temp_root / "publishing-packages.jsonl",
            PublishingPackage(
                session_id=session_id,
                match_index=2,
                source_subtitle_path=str(subtitle_path),
                source_export_path=str(export_path),
                transcript_excerpt=["opening line"],
                evidence=[],
                title_candidates=["custom title"],
                recommended_title="custom title",
                summary="summary",
                cover_lines=["line one", "line two"],
                tags=[],
                status="generated",
                created_at=now,
            ),
        )
        return export_path, subtitle_path


class QualityReportMetricUnitTest(unittest.TestCase):
    def test_kda_coverage_merges_zoom_split_adjacent_spans(self) -> None:
        # Close-up zoom splits one key-event span at the kill timestamp into
        # adjacent pieces; the merged span must still count as covering the
        # KDA event interval.
        windows = QualityReportService._merge_adjacent_windows(
            [(100.0, 120.0), (120.0, 126.0), (126.0, 150.0), (200.0, 210.0)]
        )
        self.assertEqual(windows, [(100.0, 150.0), (200.0, 210.0)])
        events = [
            ClassifiedCue(
                started_at_seconds=110.0,
                ended_at_seconds=130.0,
                text="kda_change kills=1->2 deaths=0->0 previous_at=110.000 current_at=123.000",
                category="key_event",
                priority=1.0,
            )
        ]
        self.assertEqual(
            QualityReportService._kda_uncovered_count(events, windows),
            0,
        )
        # Without merging, the same adjacent pieces would false-positive.
        self.assertEqual(
            QualityReportService._kda_uncovered_count(
                events,
                [(100.0, 120.0), (120.0, 126.0), (126.0, 150.0)],
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
