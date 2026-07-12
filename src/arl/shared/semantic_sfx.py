from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from arl.shared.semantic_ids import semantic_reference_id


_RESERVED_CATEGORIES = {
    "kill_coin",
    "multi_kill",
    "transition_whoosh",
    "teaser_impact",
}

_STREAMER_SFX_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mistake", ("失误", "按错", "没按", "空了", "送了", "寄了", "我操坏了", "mistake")),
    ("boom", ("爆炸", "炸了", "秒了", "伤害", "一套", "爆发")),
    ("pew", ("扔", "丢", "射", "飞过去", "技能过去", "钩过去")),
    ("transition_bruh", ("无语", "尴尬", "不是吧", "什么鬼", "啊？", "bruh")),
)

_FIRST_PERSON_MARKERS = ("老子", "本人", " i ", "my ")
_NON_STREAMER_MARKERS = ("队友", "adc", "上单", "打野", "辅助", "对面", "他", "他们")

_SRT_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*\d+\s*\n"
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s*\n"
    r"(?P<text>.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)


@dataclass(frozen=True)
class SemanticSfxCatalogEntry:
    category: str
    description: str = ""


@dataclass(frozen=True)
class SemanticSfxCandidate:
    candidate_id: str
    evidence_id: str
    source_start_seconds: float
    source_end_seconds: float
    anchor_seconds: float
    text: str
    category_hints: tuple[str, ...]

    def prompt_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "evidence_id": self.evidence_id,
            "source_start_seconds": round(self.source_start_seconds, 3),
            "source_end_seconds": round(self.source_end_seconds, 3),
            "anchor_seconds": round(self.anchor_seconds, 3),
            "text": self.text,
            "category_hints": list(self.category_hints),
        }


def load_semantic_sfx_catalog(manifest_path: Path | None) -> list[SemanticSfxCatalogEntry]:
    if manifest_path is None or not manifest_path.is_file():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    raw_tracks = payload.get("tracks") if isinstance(payload, dict) else payload
    if not isinstance(raw_tracks, list):
        return []
    entries: list[SemanticSfxCatalogEntry] = []
    seen: set[str] = set()
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip()
        raw_path = item.get("path")
        if not category or category in _RESERVED_CATEGORIES or category in seen:
            continue
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = manifest_path.parent / path
        if not path.is_file():
            continue
        entries.append(
            SemanticSfxCatalogEntry(
                category=category,
                description=str(item.get("description", "")).strip(),
            )
        )
        seen.add(category)
    return entries


def discover_semantic_sfx_candidates(
    *,
    session_id: str,
    match_index: int,
    cues: list[tuple[float, float, str]],
    allowed_categories: set[str],
    max_candidates: int = 20,
) -> list[SemanticSfxCandidate]:
    candidates: list[SemanticSfxCandidate] = []
    for start, end, raw_text in cues:
        text = re.sub(r"\s+", " ", raw_text).strip()
        if not text or text.startswith("kda_change "):
            continue
        lowered = text.lower()
        first_person = bool(re.search(r"我(?!们)", lowered)) or any(
            marker in f" {lowered} " for marker in _FIRST_PERSON_MARKERS
        )
        non_streamer = any(marker in lowered for marker in _NON_STREAMER_MARKERS)
        raw_hints = tuple(
            category
            for category, keywords in _STREAMER_SFX_HINTS
            if category in allowed_categories
            and any(keyword.lower() in lowered for keyword in keywords)
        )
        hints = tuple(
            category
            for category in raw_hints
            if not (non_streamer and not first_person)
            and (category not in {"boom", "pew"} or first_person)
        )
        if not hints:
            continue
        evidence_id = semantic_reference_id(
            "subtitle",
            session_id,
            match_index,
            start,
            end,
            text,
        )
        candidate_id = semantic_reference_id(
            "sfx",
            session_id,
            match_index,
            start,
            end,
            evidence_id,
        )
        candidates.append(
            SemanticSfxCandidate(
                candidate_id=candidate_id,
                evidence_id=evidence_id,
                source_start_seconds=max(0.0, start),
                source_end_seconds=max(start, end),
                anchor_seconds=max(0.0, (start + end) / 2.0),
                text=text,
                category_hints=hints,
            )
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def discover_semantic_sfx_candidates_from_srt(
    path: Path,
    *,
    session_id: str,
    match_index: int,
    allowed_categories: set[str],
    max_candidates: int = 20,
) -> list[SemanticSfxCandidate]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    cues = [
        (
            _parse_srt_timestamp(match.group("start")),
            _parse_srt_timestamp(match.group("end")),
            re.sub(r"\s+", " ", match.group("text")).strip(),
        )
        for match in _SRT_BLOCK_RE.finditer(content.replace("\r\n", "\n"))
    ]
    return discover_semantic_sfx_candidates(
        session_id=session_id,
        match_index=match_index,
        cues=cues,
        allowed_categories=allowed_categories,
        max_candidates=max_candidates,
    )


def _parse_srt_timestamp(value: str) -> float:
    hours, minutes, seconds_ms = value.split(":")
    seconds, milliseconds = seconds_ms.split(",")
    return (
        int(hours) * 3600.0
        + int(minutes) * 60.0
        + int(seconds)
        + int(milliseconds) / 1000.0
    )
