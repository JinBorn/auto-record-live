from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class CoverFrameSeed:
    timestamp_seconds: float
    reason: str
    priority: float = 0.0


@dataclass(frozen=True)
class CoverFrameScore:
    timestamp_seconds: float
    score: float
    reasons: tuple[str, ...]


FrameSampler = Any


def render_cover(
    recording_path: Path,
    output_path: Path,
    cover_lines: Sequence[str],
    *,
    at_seconds: float = 0.0,
) -> bool:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None or not recording_path.exists() or not cover_lines:
        return False

    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
    except Exception:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        frame_path = Path(temp_dir) / "cover-frame.jpg"
        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, at_seconds):.3f}",
            "-i",
            str(recording_path),
            "-frames:v",
            "1",
            str(frame_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if not frame_path.exists() or frame_path.stat().st_size <= 0:
            return False

        try:
            image = Image.open(frame_path).convert("RGB")
            image = ImageOps.fit(image, (1920, 1080))
            image = ImageEnhance.Brightness(image).enhance(0.70)
            image = ImageEnhance.Contrast(image).enhance(1.08)
            draw = ImageDraw.Draw(image)
            _draw_cover_text(draw, image.size, cover_lines, ImageFont)
            image.save(output_path, quality=92)
        except Exception:
            return False

    return output_path.exists() and output_path.stat().st_size > 0


def select_cover_frame_candidates(
    video_path: Path,
    seeds: Sequence[CoverFrameSeed],
    *,
    sampler: FrameSampler | None = None,
    max_candidates: int = 3,
    min_spacing_seconds: float = 5.0,
    window_radius_seconds: float = 2.0,
    interval_seconds: float = 1.0,
) -> list[CoverFrameScore]:
    if max_candidates <= 0 or not seeds or not video_path.exists():
        return []

    sampler = sampler or _sample_cover_frame_window
    scored: list[CoverFrameScore] = []
    for seed in seeds:
        start = max(0.0, seed.timestamp_seconds - window_radius_seconds)
        end = max(start, seed.timestamp_seconds + window_radius_seconds)
        try:
            frames = sampler(
                video_path,
                start,
                end,
                interval_seconds=interval_seconds,
            )
        except Exception:
            continue
        frames = sorted(frames, key=lambda item: item[0])
        previous_frame: Any | None = None
        for timestamp, frame in frames:
            score = score_cover_frame(
                timestamp,
                frame,
                seed=seed,
                previous_frame=previous_frame,
            )
            distance_penalty = min(5.0, abs(timestamp - seed.timestamp_seconds)) * 1.5
            scored.append(
                CoverFrameScore(
                    timestamp_seconds=round(max(0.0, timestamp), 3),
                    score=round(score.score - distance_penalty, 3),
                    reasons=score.reasons,
                )
            )
            previous_frame = frame

    selected: list[CoverFrameScore] = []
    for candidate in sorted(
        scored,
        key=lambda item: (-item.score, item.timestamp_seconds, item.reasons),
    ):
        if len(selected) >= max_candidates:
            break
        if any(
            abs(candidate.timestamp_seconds - existing.timestamp_seconds)
            < min_spacing_seconds
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return selected


def score_cover_frame(
    timestamp_seconds: float,
    frame: Any,
    *,
    seed: CoverFrameSeed,
    previous_frame: Any | None = None,
) -> CoverFrameScore:
    score = float(seed.priority)
    reasons = [f"seed:{seed.reason}"]

    if frame is None or not hasattr(frame, "shape"):
        return CoverFrameScore(round(max(0.0, timestamp_seconds), 3), score, tuple(reasons))

    try:
        import cv2
    except ModuleNotFoundError:
        return CoverFrameScore(round(max(0.0, timestamp_seconds), 3), score, tuple(reasons))

    try:
        gray = frame
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        sharpness_score = min(35.0, sharpness / 20.0)
        if sharpness_score > 0.0:
            score += sharpness_score
            reasons.append("sharp")

        brightness = float(gray.mean() / 255.0)
        brightness_score = max(0.0, 1.0 - abs(brightness - 0.55) / 0.55) * 20.0
        score += brightness_score
        if brightness_score >= 10.0:
            reasons.append("readable_brightness")
    except Exception:
        pass

    scene_score, scene_reason = _scene_score(frame, timestamp_seconds)
    score += scene_score
    if scene_reason:
        reasons.append(scene_reason)

    chat_score = _chat_activity_score(previous_frame, frame)
    if chat_score > 0.0:
        score += chat_score
        reasons.append("chat_activity")

    return CoverFrameScore(
        timestamp_seconds=round(max(0.0, timestamp_seconds), 3),
        score=round(score, 3),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _sample_cover_frame_window(
    video_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    interval_seconds: float,
) -> list[tuple[float, Any]]:
    from arl.vision.frame_sampler import sample_frame_window

    return sample_frame_window(
        video_path,
        start_seconds,
        end_seconds,
        interval_seconds=interval_seconds,
    )


def _scene_score(frame: Any, timestamp_seconds: float) -> tuple[float, str | None]:
    try:
        from arl.vision.scene_classifier import classify_scene

        reading = classify_scene(frame, timestamp_seconds)
    except Exception:
        return 0.0, None

    if reading.scene == "in_game":
        return 20.0 * reading.confidence, "scene:in_game"
    if reading.scene == "loading":
        return -20.0 * reading.confidence, "scene:loading"
    return 0.0, "scene:other"


def _chat_activity_score(previous_frame: Any | None, current_frame: Any) -> float:
    if previous_frame is None:
        return 0.0
    previous_crop = _chat_region_crop(previous_frame)
    current_crop = _chat_region_crop(current_frame)
    if previous_crop is None or current_crop is None:
        return 0.0
    if previous_crop.shape != current_crop.shape:
        return 0.0
    diff_score = _chat_region_diff_score(previous_crop, current_crop)
    if diff_score < 0.02:
        return 0.0
    return min(15.0, diff_score / 0.08 * 15.0)


def _chat_region_crop(frame: Any) -> Any | None:
    try:
        import cv2
    except ModuleNotFoundError:
        return None
    if frame is None or not hasattr(frame, "shape"):
        return None
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        return None
    x1 = 0
    x2 = max(1, int(width * 0.36))
    y1 = max(0, int(height * 0.55))
    y2 = max(y1 + 1, int(height * 0.95))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    if len(crop.shape) == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return crop


def _chat_region_diff_score(previous: Any, current: Any) -> float:
    try:
        import cv2
    except ModuleNotFoundError:
        return 0.0
    diff = cv2.absdiff(previous, current)
    return float(diff.mean() / 255.0)


def _draw_cover_text(draw: object, image_size: tuple[int, int], cover_lines: Sequence[str], image_font: object) -> None:
    width, height = image_size
    lines = [raw_line.strip() for raw_line in cover_lines[:4] if raw_line.strip()]
    if not lines:
        return

    left = int(width * 0.08)
    top = int(height * 0.42)
    bottom = int(height * 0.86)
    max_text_width = int(width * 0.68)
    max_text_height = bottom - top
    line_metrics, stroke_width, gap = _fit_cover_text(
        draw,
        lines,
        image_font,
        max_width=max_text_width,
        max_height=max_text_height,
    )
    if not line_metrics:
        return

    total_height = sum(item[3] for item in line_metrics) + gap * (len(line_metrics) - 1)
    y = max(top, int(height * 0.55) - total_height // 2)
    y = min(y, max(top, bottom - total_height))

    for line, font, _, line_height in line_metrics:
        try:
            draw.text(
                (left, y),
                line,
                font=font,
                fill=(255, 238, 0),
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0),
            )
        except UnicodeEncodeError:
            return
        y += line_height + gap


def _fit_cover_text(
    draw: object,
    lines: Sequence[str],
    image_font: object,
    *,
    max_width: int,
    max_height: int,
) -> tuple[list[tuple[str, object, int, int]], int, int]:
    font_size = 138 if len(lines) <= 2 else 122 if len(lines) == 3 else 108
    while font_size >= 48:
        font = _load_font(image_font, font_size)
        stroke_width = max(6, int(round(font_size * 0.10)))
        gap = max(12, int(round(font_size * 0.14)))
        metrics: list[tuple[str, object, int, int]] = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            metrics.append((line, font, bbox[2] - bbox[0], bbox[3] - bbox[1]))
        total_height = sum(item[3] for item in metrics) + gap * (len(metrics) - 1)
        if all(item[2] <= max_width for item in metrics) and total_height <= max_height:
            return metrics, stroke_width, gap
        font_size -= 6
    return [], 0, 0


def _load_font(image_font: object, size: int) -> object:
    for raw_path in [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]:
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            return image_font.truetype(str(path), size=size)
        except Exception:
            continue
    return image_font.load_default()
