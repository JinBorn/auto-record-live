from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from arl.config import Settings, StorageSettings, SubtitleSettings
from arl.segmenter.models import MatchStageSignal
from arl.shared.contracts import MatchBoundary, RecordingAsset, SourceType
from arl.shared.jsonl_store import append_model, load_models
from arl.subtitles.ass import AssSubtitleStyle, convert_srt_to_ass
from arl.subtitles.service import SubtitleService, TranscribeOutcome


class AssSubtitleConversionTest(unittest.TestCase):
    def test_convert_srt_to_ass_emits_reference_style_sections(self) -> None:
        ass_text = convert_srt_to_ass(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            AssSubtitleStyle(
                font_name="Microsoft YaHei",
                font_size=40,
                margin_v=18,
                outline=3,
            ),
        )

        self.assertIn("[Script Info]", ass_text)
        self.assertIn("PlayResX: 1280", ass_text)
        self.assertIn("PlayResY: 720", ass_text)
        self.assertIn("[V4+ Styles]", ass_text)
        self.assertIn("[Events]", ass_text)
        self.assertIn(
            "Style: Default,Microsoft YaHei,40,"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,3,0,2,20,20,18,1",
            ass_text,
        )

    def test_convert_srt_to_ass_preserves_timing_and_text(self) -> None:
        ass_text = convert_srt_to_ass(
            "1\n"
            "00:00:01,250 --> 00:00:03.500\n"
            "<i>清线快</i> 伤害高\n"
            "第二行 {AP}\n\n"
            "2\n"
            "01:02:03,004 --> 01:02:04,006\n"
            "结尾\n"
        )

        dialogue_lines = [
            line for line in ass_text.splitlines() if line.startswith("Dialogue:")
        ]
        self.assertEqual(
            dialogue_lines[0],
            "Dialogue: 0,0:00:01.25,0:00:03.50,"
            "Default,,0,0,0,,清线快 伤害高\\N第二行 \\{AP\\}",
        )
        self.assertEqual(
            dialogue_lines[1],
            "Dialogue: 0,1:02:03.00,1:02:04.01,Default,,0,0,0,,结尾",
        )


class _Segment:
    def __init__(
        self,
        start: float,
        end: float,
        text: str,
        *,
        words: list["_Word"] | None = None,
    ) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.words = words or []


class _Word:
    def __init__(self, start: float, end: float, word: str) -> None:
        self.start = start
        self.end = end
        self.word = word


class _Info:
    def __init__(self, language: str, language_probability: float) -> None:
        self.language = language
        self.language_probability = language_probability


class _WhisperModelStub:
    def __init__(
        self,
        *,
        language_probability: float,
        language: str = "zh",
        raises: Exception | None = None,
        lazy_raises: Exception | None = None,
        segments: list[_Segment] | None = None,
    ) -> None:
        self.language_probability = language_probability
        self.language = language
        self.raises = raises
        self.lazy_raises = lazy_raises
        self.segments = segments
        self.transcribe_calls = 0
        self.transcribe_kwargs: list[dict[str, object]] = []

    def transcribe(
        self,
        path: str,
        language: str | None,
        *,
        word_timestamps: bool = False,
        clip_timestamps: list[float] | None = None,
    ):
        self.transcribe_calls += 1
        self.transcribe_kwargs.append(
            {
                "path": path,
                "language": language,
                "word_timestamps": word_timestamps,
                "clip_timestamps": clip_timestamps,
            }
        )
        if self.raises is not None:
            raise self.raises
        if self.lazy_raises is not None:
            return (
                self._raise_during_segment_iteration(),
                _Info(self.language, self.language_probability),
            )
        return (
            self.segments or [_Segment(0.0, 1.5, "真实字幕行。")],
            _Info(self.language, self.language_probability),
        )

    def _raise_during_segment_iteration(self):
        raise self.lazy_raises
        yield


class _WhisperModelFactory:
    def __init__(
        self,
        *,
        fail_init_devices: set[str] | None = None,
        lazy_fail_devices: set[str] | None = None,
    ) -> None:
        self.fail_init_devices = fail_init_devices or set()
        self.lazy_fail_devices = lazy_fail_devices or set()
        self.calls: list[dict[str, str]] = []

    def __call__(self, model_size: str, *, device: str, compute_type: str):
        self.calls.append(
            {
                "model_size": model_size,
                "device": device,
                "compute_type": compute_type,
            }
        )
        if device in self.fail_init_devices:
            raise RuntimeError(f"{device} init failed")
        return _WhisperModelStub(
            language_probability=0.95,
            language="zh",
            lazy_raises=(
                RuntimeError("Library cublas64_12.dll is not found")
                if device in self.lazy_fail_devices
                else None
            ),
        )


class _IntermittentFileFailureModel:
    def __init__(self) -> None:
        self.transcribe_calls = 0

    def transcribe(
        self,
        path: str,
        language: str | None,
        *,
        word_timestamps: bool = False,
        clip_timestamps: list[float] | None = None,
    ):
        self.transcribe_calls += 1
        if self.transcribe_calls == 1:
            raise FileNotFoundError("temporary media file missing")
        return (
            [_Segment(0.0, 1.5, "real subtitle line")],
            _Info("zh", 0.95),
        )


class _EnvIsolation:
    def __enter__(self) -> "_EnvIsolation":
        self._snapshot = {k: v for k, v in os.environ.items() if k.startswith("ARL_")}
        for key in list(os.environ):
            if key.startswith("ARL_"):
                del os.environ[key]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for key in list(os.environ):
            if key.startswith("ARL_"):
                del os.environ[key]
        os.environ.update(self._snapshot)


class _FakeSubtitleService(SubtitleService):
    def __init__(
        self,
        settings: Settings,
        entries: list[tuple[float, float, str]],
    ) -> None:
        super().__init__(settings)
        self._entries = entries

    def _transcribe_boundary(
        self,
        boundary: MatchBoundary,
        recording_path: str | None,
        *,
        recording_duration_seconds: float | None = None,
    ) -> TranscribeOutcome:
        return TranscribeOutcome(entries=self._entries)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


class SubtitleServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.temp_root = root / "tmp"
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.export_root = root / "exports"
        self.boundaries_path = self.temp_root / "match-boundaries.jsonl"
        self.recording_assets_path = self.temp_root / "recording-assets.jsonl"
        self.subtitle_assets_path = self.temp_root / "subtitle-assets.jsonl"

        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=self.raw_root,
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            subtitles=SubtitleSettings(enabled=True, provider="faster-whisper"),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_subtitle_service_falls_back_to_placeholder_for_unknown_provider(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.provider = "placeholder"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-fallback",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )

        service = SubtitleService(settings)
        service.run()

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(len(subtitle_assets), 1)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("Placeholder subtitle generated by local pipeline.", subtitle_text)

        service.run()
        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(len(subtitle_assets), 1)

    def test_subtitle_service_skips_incomplete_match_boundary(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-incomplete",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=900.0,
                confidence=0.95,
                is_complete=False,
                reason="incomplete_no_end",
            ),
        )
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: self.fail("Whisper should not load")

        service.run()

        self.assertFalse(self.subtitle_assets_path.exists())
        state = json.loads(
            (self.temp_root / "subtitles-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["processed_match_keys"], ["session-subtitle-incomplete:1"])

    def test_subtitle_service_writes_transcribed_srt_when_entries_available(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-transcribe",
                match_index=2,
                started_at_seconds=10.0,
                ended_at_seconds=40.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / "session-subtitle-transcribe" / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-subtitle-transcribe",
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )

        service = _FakeSubtitleService(
            self.settings,
            entries=[
                (0.0, 1.25, "First subtitle line."),
                (1.25, 3.5, "Second subtitle line."),
            ],
        )
        service.run()

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(len(subtitle_assets), 1)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:01,250", subtitle_text)
        self.assertIn("00:00:01,250 --> 00:00:03,500", subtitle_text)
        self.assertIn("First subtitle line.", subtitle_text)
        self.assertIn("Second subtitle line.", subtitle_text)
        self.assertNotIn("Placeholder subtitle generated by local pipeline.", subtitle_text)

    def _seed_single_media_boundary(
        self,
        *,
        session_id: str = "session-subtitle-quality",
    ) -> Path:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        return recording_path

    def test_transcribe_uses_boundary_clip_timestamps(self) -> None:
        session_id = "session-subtitle-clipped-asr"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=40.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        model = _WhisperModelStub(
            language_probability=0.95,
            segments=[_Segment(10.0, 11.0, "clipped subtitle")],
        )
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: model

        service.run()

        self.assertEqual(model.transcribe_calls, 1)
        self.assertEqual(model.transcribe_kwargs[0]["clip_timestamps"], [10.0, 40.0])
        self.assertTrue(model.transcribe_kwargs[0]["word_timestamps"])

    def test_low_language_probability_falls_back_to_placeholder(self) -> None:
        self._seed_single_media_boundary()
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.3,
            language="ko",
        )

        service.run()

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("Placeholder subtitle generated by local pipeline.", subtitle_text)
        self.assertNotIn("真实字幕行", subtitle_text)

    def test_high_language_probability_emits_real_srt(self) -> None:
        self._seed_single_media_boundary()
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.95,
            language="zh",
        )

        service.run()

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("真实字幕行。", subtitle_text)
        self.assertNotIn("Placeholder subtitle generated by local pipeline.", subtitle_text)

    def test_word_timestamps_delay_subtitle_until_first_spoken_word(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-late-speech")
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.95,
            language="zh",
            segments=[
                _Segment(
                    0.0,
                    6.0,
                    "late speech starts now",
                    words=[
                        _Word(3.2, 3.8, "late"),
                        _Word(3.8, 4.4, " speech"),
                        _Word(4.4, 5.0, " starts"),
                        _Word(5.0, 5.4, " now"),
                    ],
                )
            ],
        )

        service.run()

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("00:00:03,200 --> 00:00:05,400", subtitle_text)
        self.assertNotIn("00:00:00,000 --> 00:00:06,000", subtitle_text)
        self.assertIn("late speech starts now", subtitle_text)

    def test_successful_asr_with_no_boundary_segments_has_explicit_reason(self) -> None:
        session_id = "session-subtitle-empty-asr"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=30.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.95,
            language="zh",
        )

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "no_transcript_segments")

    def test_threshold_disabled_when_language_setting_empty(self) -> None:
        self._seed_single_media_boundary()
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.language = ""
        service = SubtitleService(settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.1,
            language="ko",
        )

        service.run()

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("真实字幕行。", subtitle_text)

    def test_env_overrides_threshold(self) -> None:
        from arl.config import load_settings

        with _EnvIsolation(), patch("arl.config._load_dotenv"):
            os.environ["ARL_WHISPER_MIN_LANGUAGE_PROBABILITY"] = "0.7"
            settings = load_settings()

        self.assertEqual(settings.subtitles.min_language_probability, 0.7)

    def _run_with_fake_faster_whisper(
        self,
        service: SubtitleService,
        factory: _WhisperModelFactory,
    ) -> None:
        fake_module = types.SimpleNamespace(WhisperModel=factory)
        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            service.run()

    def test_auto_device_falls_back_to_cpu_when_cuda_init_fails(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-cuda-init-fallback")
        service = SubtitleService(self.settings)
        factory = _WhisperModelFactory(fail_init_devices={"cuda"})

        self._run_with_fake_faster_whisper(service, factory)

        self.assertEqual(
            [(call["device"], call["compute_type"]) for call in factory.calls],
            [("cuda", "float16"), ("cpu", "int8")],
        )
        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[0]["device"], "cpu")
        self.assertEqual(audit_rows[0]["compute_type"], "int8")
        self.assertEqual(audit_rows[0]["fallback_device"], "cpu")

    def test_auto_device_retries_cpu_when_cuda_lazy_iteration_fails(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-cuda-lazy-fallback")
        service = SubtitleService(self.settings)
        factory = _WhisperModelFactory(lazy_fail_devices={"cuda"})

        self._run_with_fake_faster_whisper(service, factory)

        self.assertEqual(
            [(call["device"], call["compute_type"]) for call in factory.calls],
            [("cuda", "float16"), ("cpu", "int8")],
        )
        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[0]["device"], "cpu")
        self.assertEqual(audit_rows[0]["fallback_device"], "cpu")

    def test_auto_device_can_use_cuda_compute_type_with_cpu_fallback(self) -> None:
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.compute_type = "auto"
        settings.subtitles.cuda_compute_type = "int8_float16"
        settings.subtitles.cpu_compute_type = "int8"

        service = SubtitleService(settings)

        self.assertEqual(
            [
                (candidate.device, candidate.compute_type)
                for candidate in service._whisper_model_candidates()
            ],
            [("cuda", "int8_float16"), ("cpu", "int8")],
        )

    def test_explicit_cuda_does_not_fallback_to_cpu(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-cuda-only")
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.device = "cuda"
        service = SubtitleService(settings)
        factory = _WhisperModelFactory(fail_init_devices={"cuda"})

        self._run_with_fake_faster_whisper(service, factory)

        self.assertEqual(
            [(call["device"], call["compute_type"]) for call in factory.calls],
            [("cuda", "float16")],
        )
        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "model_unavailable")
        self.assertEqual(audit_rows[0]["device"], "cuda")
        self.assertIsNone(audit_rows[0]["fallback_device"])

    def test_explicit_cpu_uses_cpu_compute_type_only(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-cpu-only")
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.device = "cpu"
        settings.subtitles.cpu_compute_type = "int8"
        service = SubtitleService(settings)
        factory = _WhisperModelFactory()

        self._run_with_fake_faster_whisper(service, factory)

        self.assertEqual(
            [(call["device"], call["compute_type"]) for call in factory.calls],
            [("cpu", "int8")],
        )
        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[0]["device"], "cpu")

    def test_preprocessed_audio_is_used_for_transcription(self) -> None:
        session_id = "session-subtitle-preprocessed"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=40.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.device = "cpu"
        settings.subtitles.preprocess_audio = True
        model = _WhisperModelStub(
            language_probability=0.95,
            language="zh",
            segments=[_Segment(2.0, 4.0, "preprocessed speech")],
        )
        service = SubtitleService(settings)
        service._load_whisper_model = lambda: model

        def _fake_ffmpeg(command, **kwargs):
            Path(command[-1]).write_bytes(b"wav")
            return types.SimpleNamespace(returncode=0)

        with patch("arl.subtitles.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.subtitles.service.subprocess.run",
            side_effect=_fake_ffmpeg,
        ) as mocked_run:
            service.run()

        mocked_run.assert_called_once()
        transcribe_kwargs = model.transcribe_kwargs[0]
        self.assertTrue(str(transcribe_kwargs["path"]).endswith("match-01.wav"))
        self.assertIsNone(transcribe_kwargs["clip_timestamps"])
        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("00:00:02,000 --> 00:00:04,000", subtitle_text)
        self.assertIn("preprocessed speech", subtitle_text)

    def test_preprocess_failure_falls_back_to_original_media(self) -> None:
        session_id = "session-subtitle-preprocess-fallback"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=40.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.device = "cpu"
        settings.subtitles.preprocess_audio = True
        model = _WhisperModelStub(
            language_probability=0.95,
            language="zh",
            segments=[_Segment(10.0, 12.0, "original speech")],
        )
        service = SubtitleService(settings)
        service._load_whisper_model = lambda: model

        with patch("arl.subtitles.service.shutil.which", return_value="ffmpeg"), patch(
            "arl.subtitles.service.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                1,
                ["ffmpeg"],
                stderr="filter failed",
            ),
        ):
            service.run()

        transcribe_kwargs = model.transcribe_kwargs[0]
        self.assertEqual(transcribe_kwargs["path"], str(recording_path))
        self.assertEqual(transcribe_kwargs["clip_timestamps"], [10.0, 40.0])
        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:02,000", subtitle_text)
        self.assertIn("original speech", subtitle_text)

    def test_success_emits_succeeded_audit_with_language_fields(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-audit-success")
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.95,
            language="zh",
        )

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[0]["language"], "zh")
        self.assertEqual(audit_rows[0]["language_probability"], 0.95)
        self.assertIsNone(audit_rows[0].get("reason"))

    def test_missing_recording_emits_fallback_reason_missing_recording(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-audit-missing",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.9,
            ),
        )

        SubtitleService(self.settings).run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "missing_recording")
        self.assertIn("session-subtitle-audit-missing", audit_rows[0]["reason_detail"])

    def test_unsupported_suffix_emits_fallback_reason_unsupported_suffix(self) -> None:
        session_id = "session-subtitle-audit-suffix"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.9,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.txt"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )

        SubtitleService(self.settings).run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "unsupported_suffix")
        self.assertEqual(audit_rows[0]["reason_detail"], "unsupported_suffix:.txt")

    def test_model_unavailable_emits_fallback_reason_model_unavailable(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-audit-model")
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: None

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "model_unavailable")

    def test_transcribe_exception_emits_fallback_reason_transcribe_failed(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-audit-exc")
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.95,
            raises=RuntimeError("CUDA error"),
        )

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "transcribe_failed")
        self.assertIn("RuntimeError:CUDA error", audit_rows[0]["reason_detail"])

    def test_lazy_segment_exception_emits_fallback_reason_transcribe_failed(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-audit-lazy-exc")
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.95,
            lazy_raises=RuntimeError("Library cublas64_12.dll is not found"),
        )

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "transcribe_failed")
        self.assertIn("RuntimeError:Library cublas64_12.dll", audit_rows[0]["reason_detail"])

    def test_runtime_failure_disables_whisper_for_remaining_batch(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-runtime-a")
        self._seed_single_media_boundary(session_id="session-subtitle-runtime-b")
        service = SubtitleService(self.settings)
        factory = _WhisperModelFactory(lazy_fail_devices={"cuda"})

        self._run_with_fake_faster_whisper(service, factory)

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(len(audit_rows), 2)
        self.assertEqual(
            [(call["device"], call["compute_type"]) for call in factory.calls],
            [("cuda", "float16"), ("cpu", "int8")],
        )
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[0]["device"], "cpu")
        self.assertEqual(audit_rows[0]["fallback_device"], "cpu")
        self.assertEqual(audit_rows[1]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[1]["device"], "cpu")

    def test_file_transcribe_failure_does_not_disable_cpu_for_remaining_batch(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-file-a")
        self._seed_single_media_boundary(session_id="session-subtitle-file-b")
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.device = "cpu"
        model = _IntermittentFileFailureModel()
        service = SubtitleService(settings)
        service._load_whisper_model = lambda: model

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(len(audit_rows), 2)
        self.assertEqual(model.transcribe_calls, 2)
        self.assertEqual(audit_rows[0]["reason"], "transcribe_failed")
        self.assertEqual(audit_rows[0]["device"], "cpu")
        self.assertEqual(audit_rows[1]["event_type"], "subtitle_transcribe_succeeded")
        self.assertEqual(audit_rows[1]["device"], "cpu")

    def test_missing_recording_path_short_circuits_before_loading_whisper(self) -> None:
        session_id = "session-subtitle-missing-path"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.9,
            ),
        )
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(self.raw_root / session_id / "missing.mp4"),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: self.fail("Whisper should not load")

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["reason"], "missing_recording")
        self.assertIn("recording_path_not_found", audit_rows[0]["reason_detail"])

    def test_long_low_confidence_full_boundary_skips_asr(self) -> None:
        session_id = "session-subtitle-long-fallback"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=3600.0,
                confidence=0.5,
            ),
        )
        recording_path = self.raw_root / session_id / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc),
            ),
        )
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: self.fail("Whisper should not load")

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["reason"], "low_confidence_full_recording")
        self.assertIn("duration=3600.000", audit_rows[0]["reason_detail"])
        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        subtitle_text = Path(subtitle_assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("Placeholder subtitle generated by local pipeline.", subtitle_text)

    def test_low_language_probability_emits_fallback_reason_low_language_confidence(self) -> None:
        self._seed_single_media_boundary(session_id="session-subtitle-audit-low-language")
        service = SubtitleService(self.settings)
        service._load_whisper_model = lambda: _WhisperModelStub(
            language_probability=0.3,
            language="ko",
        )

        service.run()

        audit_rows = _read_jsonl(self.temp_root / "subtitles-events.jsonl")
        self.assertEqual(audit_rows[0]["event_type"], "subtitle_fallback_placeholder")
        self.assertEqual(audit_rows[0]["reason"], "low_language_confidence")
        self.assertEqual(audit_rows[0]["language"], "ko")
        self.assertEqual(audit_rows[0]["language_probability"], 0.3)

    def test_subtitle_service_auto_extracts_stage_signals_from_generated_srt(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-signal-001",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=600.0,
                confidence=0.95,
            ),
        )
        recording_path = self.raw_root / "session-subtitle-signal-001" / "recording.mp4"
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_text("dummy media placeholder", encoding="utf-8")
        append_model(
            self.recording_assets_path,
            RecordingAsset(
                session_id="session-subtitle-signal-001",
                source_type=SourceType.BROWSER_CAPTURE,
                path=str(recording_path),
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc),
            ),
        )
        service = _FakeSubtitleService(
            self.settings,
            entries=[
                (0.0, 10.0, "Champion select draft begins."),
                (15.0, 25.0, "Game loading now."),
                (40.0, 80.0, "In game scoreboard update."),
                (300.0, 320.0, "Victory game over."),
                (350.0, 360.0, "Another in game cue should not duplicate stage."),
            ],
        )

        service.run()
        signals_path = self.temp_root / "match-stage-signals.jsonl"
        signals = load_models(signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 4)
        self.assertEqual([signal.source for signal in signals], ["subtitles_srt"] * 4)
        self.assertEqual([signal.at_seconds for signal in signals], [0.0, 15.0, 40.0, 300.0])

        service.run()
        signals = load_models(signals_path, MatchStageSignal)
        self.assertEqual(len(signals), 4)

    def test_subtitle_service_filters_by_session_ids_and_match_indices(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-filter-a",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-filter-a",
                match_index=2,
                started_at_seconds=30.0,
                ended_at_seconds=60.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-filter-b",
                match_index=2,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-filter-c",
                match_index=2,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.provider = "placeholder"
        service = SubtitleService(settings)

        output = StringIO()
        with redirect_stdout(output):
            service.run(
                session_ids={"session-subtitle-filter-a", "session-subtitle-filter-b"},
                match_indices={2},
            )

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(len(subtitle_assets), 2)
        self.assertEqual(
            sorted((row["session_id"], row["match_index"]) for row in subtitle_assets),
            [("session-subtitle-filter-a", 2), ("session-subtitle-filter-b", 2)],
        )
        self.assertIn("filters summary total_boundaries=4 matched_boundaries=2", output.getvalue())

    def test_subtitle_service_logs_no_match_when_filters_match_nothing(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-no-match",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.provider = "placeholder"
        service = SubtitleService(settings)

        output = StringIO()
        with redirect_stdout(output):
            service.run(
                session_ids={"session-not-exists"},
                match_indices={9},
            )
        self.assertIn("filters summary total_boundaries=1 matched_boundaries=0", output.getvalue())
        self.assertIn("no boundaries matched filters", output.getvalue())
        self.assertIn("processed_matches=0", output.getvalue())
        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(subtitle_assets, [])

    def test_subtitle_service_scopes_auto_stage_signal_ingest_with_same_filters(self) -> None:
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-scope-a",
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-scope-a",
                match_index=2,
                started_at_seconds=30.0,
                ended_at_seconds=60.0,
                confidence=0.8,
            ),
        )
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id="session-subtitle-scope-b",
                match_index=2,
                started_at_seconds=0.0,
                ended_at_seconds=30.0,
                confidence=0.8,
            ),
        )
        settings = self.settings.model_copy(deep=True)
        settings.subtitles.provider = "placeholder"
        service = SubtitleService(settings)

        output = StringIO()
        with redirect_stdout(output):
            service.run(
                session_ids={"session-subtitle-scope-a"},
                match_indices={2},
            )
        logs = output.getvalue()
        self.assertIn("filters summary total_boundaries=3 matched_boundaries=1", logs)
        self.assertIn(
            "stage-signals-from-subtitles filter summary total_assets=1 matched_assets=1",
            logs,
        )

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(
            subtitle_assets,
            [
                {
                    "session_id": "session-subtitle-scope-a",
                    "match_index": 2,
                    "path": str(
                        self.processed_root / "session-subtitle-scope-a" / "match-02.srt"
                    ),
                    "format": "srt",
                }
            ],
        )

    def test_subtitle_force_reprocess_rewrites_existing_asset_and_reingests_signals(
        self,
    ) -> None:
        session_id = "session-subtitle-force"
        append_model(
            self.boundaries_path,
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=600.0,
                confidence=0.9,
            ),
        )
        settings = self.settings.model_copy(deep=True)
        first_service = _FakeSubtitleService(
            settings,
            entries=[(10.0, 20.0, "In game first signal.")],
        )
        first_service.run()

        second_service = _FakeSubtitleService(
            settings,
            entries=[
                (10.0, 20.0, "In game first signal."),
                (300.0, 320.0, "Victory game over."),
            ],
        )
        second_service.run(
            session_ids={session_id},
            match_indices={1},
            force_reprocess=True,
        )

        subtitle_assets = _read_jsonl(self.subtitle_assets_path)
        self.assertEqual(len(subtitle_assets), 2)
        subtitle_text = Path(subtitle_assets[-1]["path"]).read_text(encoding="utf-8")
        self.assertIn("Victory game over.", subtitle_text)

        signals = load_models(self.temp_root / "match-stage-signals.jsonl", MatchStageSignal)
        self.assertEqual(
            [(signal.text, signal.at_seconds) for signal in signals],
            [
                ("In game first signal.", 10.0),
                ("Victory game over.", 300.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
