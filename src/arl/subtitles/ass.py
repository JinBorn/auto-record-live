from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from html import unescape
from pathlib import Path


_SRT_HTML_TAG_RE = re.compile(
    r"</?(?:b|i|u|s|font|span|c|ruby|rt|rp)"
    r"(?:\.[A-Za-z0-9_-]+)?(?:\s+[^<>]*)?>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SrtCue:
    started_at_seconds: float
    ended_at_seconds: float
    text: str


@dataclass(frozen=True)
class AssSubtitleStyle:
    font_name: str = "SimHei"
    font_size: int = 32
    margin_v: int = 110
    outline: int = 2
    max_chars_per_line: int = 18
    max_lines: int = 2
    play_res_x: int = 1280
    play_res_y: int = 720
    margin_l: int = 20
    margin_r: int = 20


def parse_srt_cues(srt_text: str) -> list[SrtCue]:
    lines = srt_text.splitlines()
    cues: list[SrtCue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if index == 0:
            line = line.removeprefix("\ufeff")
        if "-->" not in line:
            index += 1
            continue

        start_raw, end_raw = [item.strip() for item in line.split("-->", 1)]
        start_seconds = _parse_srt_timestamp(start_raw)
        end_seconds = _parse_srt_timestamp(end_raw)
        if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
            index += 1
            continue

        index += 1
        text_rows: list[str] = []
        while index < len(lines) and lines[index].strip():
            row = _clean_srt_text_row(lines[index])
            if row:
                text_rows.append(row)
            index += 1

        text = "\n".join(text_rows).strip()
        if text:
            cues.append(SrtCue(start_seconds, end_seconds, text))
        index += 1
    return cues


def convert_srt_to_ass(
    srt_text: str,
    style: AssSubtitleStyle | None = None,
) -> str:
    return _build_ass_document(parse_srt_cues(srt_text), style or AssSubtitleStyle())


def write_ass_from_srt(
    srt_path: Path,
    ass_path: Path,
    style: AssSubtitleStyle | None = None,
) -> Path:
    srt_text = srt_path.read_text(encoding="utf-8")
    cues = parse_srt_cues(srt_text)
    if not cues:
        raise ValueError("no valid SRT cues")

    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(
        _build_ass_document(cues, style or AssSubtitleStyle()),
        encoding="utf-8",
    )
    return ass_path


def _build_ass_document(cues: list[SrtCue], style: AssSubtitleStyle) -> str:
    normalized_style = _normalize_style(style)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {normalized_style.play_res_x}",
        f"PlayResY: {normalized_style.play_res_y}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            "Style: Default,"
            f"{_ass_field(normalized_style.font_name)},"
            f"{normalized_style.font_size},"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,"
            f"{normalized_style.outline},0,2,"
            f"{normalized_style.margin_l},"
            f"{normalized_style.margin_r},"
            f"{normalized_style.margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        lines.extend(_dialogue_lines_for_cue(cue, normalized_style))
    return "\n".join(lines).rstrip() + "\n"


def _normalize_style(style: AssSubtitleStyle) -> AssSubtitleStyle:
    return AssSubtitleStyle(
        font_name=style.font_name.strip() or "SimHei",
        font_size=max(1, int(style.font_size)),
        margin_v=max(0, int(style.margin_v)),
        outline=max(0, int(style.outline)),
        max_chars_per_line=max(1, int(style.max_chars_per_line)),
        max_lines=max(1, int(style.max_lines)),
        play_res_x=max(1, int(style.play_res_x)),
        play_res_y=max(1, int(style.play_res_y)),
        margin_l=max(0, int(style.margin_l)),
        margin_r=max(0, int(style.margin_r)),
    )


def _parse_srt_timestamp(raw: str) -> float | None:
    try:
        timestamp = raw.strip().split()[0]
        separator = "," if "," in timestamp else "."
        hhmmss, millis = timestamp.split(separator, 1)
        hours, minutes, seconds = hhmmss.split(":", 2)
        return max(
            0.0,
            int(hours) * 3600
            + int(minutes) * 60
            + int(seconds)
            + int(millis[:3].ljust(3, "0")) / 1000.0,
        )
    except (IndexError, ValueError):
        return None


def _format_ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _clean_srt_text_row(row: str) -> str:
    return unescape(_SRT_HTML_TAG_RE.sub("", row.strip()))


def _dialogue_lines_for_cue(cue: SrtCue, style: AssSubtitleStyle) -> list[str]:
    wrapped_lines = _wrap_subtitle_text(cue.text, style.max_chars_per_line)
    chunks = _chunk_lines(wrapped_lines, style.max_lines)
    if len(chunks) == 1:
        return [
            _format_dialogue_line(
                cue.started_at_seconds,
                cue.ended_at_seconds,
                chunks[0],
            )
        ]

    duration = cue.ended_at_seconds - cue.started_at_seconds
    dialogue_lines: list[str] = []
    for index, chunk in enumerate(chunks):
        start = cue.started_at_seconds + duration * index / len(chunks)
        end = cue.started_at_seconds + duration * (index + 1) / len(chunks)
        dialogue_lines.append(_format_dialogue_line(start, end, chunk))
    return dialogue_lines


def _format_dialogue_line(
    started_at_seconds: float,
    ended_at_seconds: float,
    text_lines: list[str],
) -> str:
    return (
        "Dialogue: 0,"
        f"{_format_ass_timestamp(started_at_seconds)},"
        f"{_format_ass_timestamp(ended_at_seconds)},"
        f"Default,,0,0,0,,{_escape_ass_text_lines(text_lines)}"
    )


def _escape_ass_text_lines(lines: list[str]) -> str:
    escaped_lines = [
        line.replace("{", r"\{").replace("}", r"\}") for line in lines
    ]
    return r"\N".join(escaped_lines)


def _wrap_subtitle_text(text: str, max_chars_per_line: int) -> list[str]:
    rows: list[str] = []
    for raw_line in text.splitlines() or [text]:
        line = raw_line.strip()
        if not line:
            continue
        rows.extend(
            textwrap.wrap(
                line,
                width=max(1, max_chars_per_line),
                break_long_words=True,
                replace_whitespace=False,
                drop_whitespace=True,
            )
            or [line]
        )
    return rows or [""]


def _chunk_lines(lines: list[str], max_lines: int) -> list[list[str]]:
    return [
        lines[index : index + max_lines]
        for index in range(0, len(lines), max_lines)
    ]


def _ass_field(value: str) -> str:
    return value.replace(",", " ").replace("\n", " ").strip()
