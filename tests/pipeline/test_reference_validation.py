from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import (
    DouyinSettings,
    EditingSettings,
    ExportSettings,
    HighlightSettings,
    OrchestratorSettings,
    Settings,
    StorageSettings,
)
from arl.copywriter.models import PublishingPackage
from arl.copywriter.service import CopywriterService
from arl.editing.service import EditingPlannerService
from arl.exporter.service import ExporterService
from arl.highlights.service import HighlightPlannerService
from arl.orchestrator.models import (
    OrchestratorStateFile,
    RecordingJobRecord,
    RecordingJobStatus,
    SessionRecord,
    SessionStatus,
)
from arl.shared.contracts import (
    AudioBed,
    EditPlanAsset,
    ExportAsset,
    MatchBoundary,
    RecordingAsset,
    SourceType,
    SoundEffectHit,
    SubtitleAsset,
    TimelineSegment,
    TimelineVideoTransform,
)
from arl.shared.jsonl_store import append_model, load_models


class ReferenceValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.temp_root = root / "tmp"
        self.settings = Settings(
            douyin=DouyinSettings(event_log_path=self.temp_root / "windows-agent-events.jsonl"),
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
                agent_event_log_path=self.temp_root / "windows-agent-events.jsonl",
                recorder_event_log_path=self.temp_root / "recorder-events.jsonl",
                audit_log_path=self.temp_root / "orchestrator-events.jsonl",
            ),
            highlights=HighlightSettings(
                enabled=True,
                mode="highlight",
                min_boundary_duration_seconds=60.0,
                min_reduction_seconds=0.0,
                min_retained_seconds=0.0,
                min_retained_fraction=0.1,
            ),
            editing=EditingSettings(
                enabled=True,
                teaser_max_segments=2,
                teaser_max_total_seconds=45.0,
                teaser_min_segment_seconds=3.0,
            ),
            export=ExportSettings(
                enable_ffmpeg=True,
                ffmpeg_max_retries=0,
                ffmpeg_timeout_seconds=10,
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reference_contract_flows_from_highlight_to_edit_plan_and_copy(self) -> None:
        session_id = "session-reference-contract"
        self._seed_boundary(session_id, duration=720.0)
        subtitle_path = self._write_subtitle(
            session_id,
            "\n".join(
                [
                    "1",
                    "00:00:20,000 --> 00:00:24,000",
                    "ordinary lane setup before the hook",
                    "",
                    "2",
                    "00:05:00,000 --> 00:05:04,000",
                    "AP Blitzcrank kill fight tower dive",
                    "",
                ]
            ),
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        export_path = self.export_root / "douyin" / f"{session_id}_match01.mp4"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text("fake export", encoding="utf-8")
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )

        for _ in range(2):
            HighlightPlannerService(self.settings).run()
            EditingPlannerService(self.settings).run()
            CopywriterService(
                self.settings,
                cover_renderer=lambda *args, **kwargs: False,
            ).run()

        edit_plans = load_models(self.temp_root / "edit-plans.jsonl", EditPlanAsset)
        self.assertEqual(len(edit_plans), 1)
        plan = edit_plans[0]
        self.assertEqual([segment.role for segment in plan.timeline][-1], "main")
        self.assertEqual(
            sum(1 for segment in plan.timeline if segment.role == "main"),
            1,
        )
        self.assertTrue(any(segment.role == "teaser" for segment in plan.timeline))
        self.assertTrue(
            any(segment.reason == "highlight_keyword" for segment in plan.timeline)
        )
        self.assertTrue(
            all(segment.role != "insert" for segment in plan.timeline)
        )
        self.assertTrue(all(segment.source_path is None for segment in plan.timeline))

        packages = load_models(
            self.temp_root / "publishing-packages.jsonl",
            PublishingPackage,
        )
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIn("AP Blitzcrank", package.recommended_title)
        self.assertIn("AP Blitzcrank", " ".join(package.cover_lines))
        self.assertEqual(
            package.evidence[0],
            "05:00 AP Blitzcrank kill fight tower dive",
        )

    def test_combined_edit_package_export_command_uses_ass_audio_and_zoom(self) -> None:
        session_id = "session-reference-export"
        self._seed_export_inputs(session_id=session_id, duration=120.0)
        settings = self.settings.model_copy(deep=True)
        settings.export.use_edit_plans = True
        settings.export.burn_subtitles = True
        settings.export.use_ass_subtitles = True
        bgm_path = self.temp_root / "audio" / "bgm.mp3"
        sfx_path = self.temp_root / "audio" / "wow.wav"
        bgm_path.parent.mkdir(parents=True, exist_ok=True)
        bgm_path.write_text("fake bgm", encoding="utf-8")
        sfx_path.write_text("fake sfx", encoding="utf-8")
        append_model(
            self.temp_root / "edit-plans.jsonl",
            EditPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                timeline=[
                    TimelineSegment(
                        role="teaser",
                        source_start_seconds=90.0,
                        source_end_seconds=105.0,
                        transform=TimelineVideoTransform(
                            kind="punch_in",
                            scale=1.25,
                            x_anchor=0.4,
                            y_anchor=0.35,
                        ),
                        reason="highlight_keyword",
                    ),
                    TimelineSegment(
                        role="main",
                        source_start_seconds=0.0,
                        source_end_seconds=120.0,
                        reason="full_validated_match",
                    ),
                ],
                audio_beds=[
                    AudioBed(
                        source_path=str(bgm_path),
                        timeline_start_seconds=0.0,
                        timeline_end_seconds=None,
                        gain_db=-24.0,
                        loop=True,
                    )
                ],
                sound_effects=[
                    SoundEffectHit(
                        source_path=str(sfx_path),
                        at_seconds=15.0,
                        gain_db=-12.0,
                        reason="highlight_keyword",
                    )
                ],
                created_at=self._now(),
            ),
        )

        with patch(
            "arl.exporter.service.shutil.which",
            side_effect=self._which_ffmpeg_and_ffprobe,
        ), patch(
            "arl.exporter.service.subprocess.run",
            side_effect=self._fake_successful_export_run,
        ) as mocked_run:
            ExporterService(settings).run()

        command = [
            list(call.args[0])
            for call in mocked_run.call_args_list
            if call.args[0][0].endswith("ffmpeg")
        ][0]
        self.assertIn(str(bgm_path), command)
        self.assertIn(str(sfx_path), command)
        self.assertIn("-filter_complex", command)
        filter_complex = command[command.index("-filter_complex") + 1]
        self.assertIn("subtitles=", filter_complex)
        self.assertIn(".ass", filter_complex)
        self.assertIn(
            "trim=start=90.000:end=105.000,setpts=PTS-STARTPTS,"
            "scale=iw*1.250:ih*1.250,"
            "crop=iw/1.250:ih/1.250:x=(iw-iw/1.250)*0.400:y=(ih-ih/1.250)*0.350[v0]",
            filter_complex,
        )
        self.assertIn(
            "atrim=start=90.000:end=105.000,asetpts=PTS-STARTPTS[a0]",
            filter_complex,
        )
        self.assertIn(
            "trim=start=0.000:end=120.000,setpts=PTS-STARTPTS[v1]",
            filter_complex,
        )
        self.assertIn("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][basea]", filter_complex)
        self.assertIn("[1:a]atrim=start=0.000:duration=135.000", filter_complex)
        self.assertIn("volume=0.063096[bgm0]", filter_complex)
        self.assertIn(
            "[2:a]asetpts=PTS-STARTPTS,volume=0.251189,adelay=15000|15000[sfx0]",
            filter_complex,
        )
        self.assertIn(
            "[basea][bgm0][sfx0]amix=inputs=3:duration=first:dropout_transition=0[a]",
            filter_complex,
        )
        self.assertNotIn("select=", filter_complex)
        self.assertTrue(
            (self.processed_root / session_id / "match-01.ass").exists()
        )

    def _seed_boundary(self, session_id: str, *, duration: float) -> None:
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=duration,
                confidence=0.95,
                is_complete=True,
            ),
        )

    def _seed_export_inputs(self, *, session_id: str, duration: float) -> None:
        self._seed_boundary(session_id, duration=duration)
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:01,000\nreal subtitle\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        recording_path = self.raw_root / session_id / "recording-source.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("fake recording", encoding="utf-8")
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=self._now(),
                ended_at=self._now(),
            ),
        )
        self.settings.orchestrator.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.orchestrator.state_file.write_text(
            OrchestratorStateFile(
                sessions=[
                    SessionRecord(
                        session_id=session_id,
                        streamer_name="reference-streamer",
                        room_url="https://live.example/reference",
                        platform="douyin",
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url="https://media.example/reference.m3u8",
                        status=SessionStatus.STOPPED,
                        started_at=self._now(),
                        ended_at=self._now(),
                    )
                ],
                recording_jobs=[
                    RecordingJobRecord(
                        job_id=f"job-{session_id}",
                        session_id=session_id,
                        platform="douyin",
                        source_type=SourceType.DIRECT_STREAM,
                        stream_url="https://media.example/reference.m3u8",
                        status=RecordingJobStatus.STOPPED,
                        created_at=self._now(),
                        ended_at=self._now(),
                    )
                ],
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

    def _write_subtitle(self, session_id: str, content: str) -> Path:
        path = self.processed_root / session_id / "match-01.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _fake_successful_export_run(self, command, **kwargs):
        if command[0].endswith("ffprobe"):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "width": 1920,
                                "height": 1080,
                            }
                        ],
                        "format": {"duration": "120.0", "size": "12345"},
                    }
                ),
            )
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("fake exported video", encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0)

    @staticmethod
    def _which_ffmpeg_and_ffprobe(binary: str) -> str | None:
        if binary in {"ffmpeg", "ffprobe"}:
            return f"/usr/bin/{binary}"
        return None

    @staticmethod
    def _now() -> datetime:
        return datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
