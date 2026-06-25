from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence


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
            image = ImageEnhance.Brightness(image).enhance(0.72)
            draw = ImageDraw.Draw(image)
            _draw_cover_text(draw, image.size, cover_lines, ImageFont)
            image.save(output_path, quality=92)
        except Exception:
            return False

    return output_path.exists() and output_path.stat().st_size > 0


def _draw_cover_text(draw: object, image_size: tuple[int, int], cover_lines: Sequence[str], image_font: object) -> None:
    width, height = image_size
    max_text_width = int(width * 0.78)
    font_size = 112 if len(cover_lines) <= 3 else 92
    fonts = [_load_font(image_font, font_size)]
    line_metrics: list[tuple[str, object, int, int]] = []
    for raw_line in cover_lines[:4]:
        line = raw_line.strip()
        if not line:
            continue
        font = fonts[0]
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=5)
        while bbox[2] - bbox[0] > max_text_width and font_size > 48:
            font_size -= 6
            font = _load_font(image_font, font_size)
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=5)
        line_metrics.append((line, font, bbox[2] - bbox[0], bbox[3] - bbox[1]))

    if not line_metrics:
        return

    gap = 18
    total_height = sum(item[3] for item in line_metrics) + gap * (len(line_metrics) - 1)
    y = max(80, int(height * 0.50) - total_height // 2)
    x = int(width * 0.12)
    for line, font, _, line_height in line_metrics:
        try:
            draw.text(
                (x, y),
                line,
                font=font,
                fill=(255, 238, 0),
                stroke_width=6,
                stroke_fill=(18, 18, 18),
            )
        except UnicodeEncodeError:
            return
        y += line_height + gap


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
