from __future__ import annotations

import argparse
import gc
import json
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from arl.config import load_settings
from arl.highlights.service import HighlightPlannerService
from arl.media.recording_resolver import recording_duration_seconds
from arl.shared.contracts import MatchBoundary, RecordingAsset
from arl.shared.jsonl_store import load_models
from arl.vision import detector as detector_module
from arl.vision import frame_sampler, kda_ocr
from arl.vision.detector import VisionMatchDetector


def _latest_recording(temp_dir: Path, session_id: str) -> RecordingAsset:
    latest = None
    for recording in load_models(temp_dir / "recording-assets.jsonl", RecordingAsset):
        if recording.session_id == session_id:
            latest = recording
    if latest is None:
        raise SystemExit(f"recording asset not found: {session_id}")
    return latest


def _boundaries(temp_dir: Path, session_id: str) -> list[MatchBoundary]:
    return sorted(
        (
            boundary
            for boundary in load_models(temp_dir / "match-boundaries.jsonl", MatchBoundary)
            if boundary.session_id == session_id
        ),
        key=lambda item: item.match_index,
    )


def benchmark(session_id: str) -> dict[str, object]:
    settings = load_settings()
    source_temp_dir = settings.storage.temp_dir
    recording = _latest_recording(source_temp_dir, session_id)
    recording_path = Path(recording.path)
    if recording_path.suffix.lower() == ".json":
        raise SystemExit("legacy timer detector does not support segmented manifests")
    boundaries = _boundaries(source_temp_dir, session_id)

    counts = {
        "timer_coarse_frames": 0,
        "timer_refined_frames": 0,
        "timer_ocr_calls": 0,
        "kda_coarse_frames": 0,
        "kda_refined_frames": 0,
        "kda_ocr_calls": 0,
    }

    original_sample_frames = detector_module.iter_frame_window
    original_timer_window = detector_module.sample_frame_window
    original_read_timer = detector_module.read_timer

    def counted_sample_frames(*args, **kwargs):
        for item in original_sample_frames(*args, **kwargs):
            counts["timer_coarse_frames"] += 1
            yield item

    def counted_timer_window(*args, **kwargs):
        frames = original_timer_window(*args, **kwargs)
        counts["timer_refined_frames"] += len(frames)
        return frames

    def counted_read_timer(*args, **kwargs):
        counts["timer_ocr_calls"] += 1
        return original_read_timer(*args, **kwargs)

    timer_started = time.perf_counter()
    with (
        patch.object(detector_module, "iter_frame_window", counted_sample_frames),
        patch.object(detector_module, "sample_frame_window", counted_timer_window),
        patch.object(detector_module, "read_timer", counted_read_timer),
    ):
        detected_segments = VisionMatchDetector(settings.vision).detect(recording_path)
    timer_wall = time.perf_counter() - timer_started
    gc.collect()

    original_kda_window = frame_sampler.iter_frame_window
    original_every_frame = frame_sampler.iter_every_frame_window
    original_read_kda = kda_ocr.read_kda

    def counted_kda_window(*args, **kwargs):
        for item in original_kda_window(*args, **kwargs):
            counts["kda_coarse_frames"] += 1
            yield item

    def counted_every_frame(*args, **kwargs):
        for item in original_every_frame(*args, **kwargs):
            counts["kda_refined_frames"] += 1
            yield item

    def counted_read_kda(*args, **kwargs):
        counts["kda_ocr_calls"] += 1
        return original_read_kda(*args, **kwargs)

    kda_events_by_match: dict[str, list[dict[str, object]]] = {}
    with TemporaryDirectory() as isolated_temp:
        settings.storage.temp_dir = Path(isolated_temp)
        planner = HighlightPlannerService(settings)
        kda_started = time.perf_counter()
        with (
            patch.object(frame_sampler, "iter_frame_window", counted_kda_window),
            patch.object(frame_sampler, "iter_every_frame_window", counted_every_frame),
            patch.object(kda_ocr, "read_kda", counted_read_kda),
        ):
            for boundary in boundaries:
                cues = planner._detect_kda_event_cues(
                    recording=recording,
                    boundary=boundary,
                    duration=boundary.ended_at_seconds - boundary.started_at_seconds,
                )
                kda_events_by_match[str(boundary.match_index)] = [
                    {
                        "started_at_seconds": cue.started_at_seconds,
                        "ended_at_seconds": cue.ended_at_seconds,
                        "text": cue.text,
                    }
                    for cue in cues
                ]
        kda_wall = time.perf_counter() - kda_started

    return {
        "session_id": session_id,
        "recording_path": str(recording_path),
        "source_duration_seconds": recording_duration_seconds(recording),
        "boundary_count": len(boundaries),
        "legacy_timer": {
            "wall_time_seconds": round(timer_wall, 3),
            "detected_segments": [asdict(segment) for segment in detected_segments],
            "coarse_frames": counts["timer_coarse_frames"],
            "refined_frames": counts["timer_refined_frames"],
            "ocr_calls": counts["timer_ocr_calls"],
        },
        "legacy_kda": {
            "wall_time_seconds": round(kda_wall, 3),
            "events_by_match": kda_events_by_match,
            "event_count": sum(len(items) for items in kda_events_by_match.values()),
            "coarse_frames": counts["kda_coarse_frames"],
            "refined_frames": counts["kda_refined_frames"],
            "ocr_calls": counts["kda_ocr_calls"],
        },
        "combined_wall_time_seconds": round(timer_wall + kda_wall, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = benchmark(args.session_id)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
