from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from arl.shared.contracts import MediaSpan


SAMPLE_RATE = 22_050
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1
MAX_I16 = 32_767
DETECTION_SAMPLE_RATE = 11_025
DETECTION_SAMPLE_SECONDS = 4.0

_TAG_ALIASES = {
    "机器人": "robot",
    "布里茨": "robot",
    "蒸汽机器人": "robot",
    "电刀": "statikk",
    "斯塔缇克": "statikk",
    "斯塔缇克电刃": "statikk",
    "法强": "ap",
    "ap": "ap",
    "教学": "tutorial",
    "讲解": "tutorial",
    "解说": "tutorial",
    "套路": "trick",
    "骗": "trick",
    "整活": "trick",
    "节目效果": "trick",
    "搞笑": "funny",
    "幽默": "funny",
    "欢乐": "funny",
    "团战": "hype",
    "开团": "hype",
    "击杀": "hype",
    "双杀": "hype",
    "三杀": "hype",
    "四杀": "hype",
    "五杀": "hype",
    "燃": "hype",
    "高燃": "hype",
    "高潮": "climax",
    "后期": "climax",
    "战斗": "fight",
    "打架": "fight",
    "翻盘": "comeback",
    "逆风": "comeback",
    "发育": "laning",
    "对线": "laning",
    "清线": "laning",
    "补刀": "laning",
    "前期": "early",
    "前中期": "early",
    "通用": "any",
    "全部": "any",
    "任意": "any",
    "战术": "tactical",
    "运营": "tactical",
    "决策": "tactical",
    "轻松": "chill",
    "舒缓": "chill",
    "俏皮": "playful",
    "可爱": "playful",
}


@dataclass(frozen=True)
class SourceMusicSpan:
    start_seconds: float
    end_seconds: float
    confidence: float = 0.0


@dataclass(frozen=True)
class SourceMusicDetection:
    has_music: bool
    confidence: float
    reason: str
    music_spans: tuple[SourceMusicSpan, ...] = ()
    coverage_ratio: float = 0.0


@dataclass(frozen=True)
class BgmLibraryTrack:
    path: Path
    tags: tuple[str, ...] = ()
    mood: str | None = None
    energy: int | None = None
    phase: str | None = None


@dataclass(frozen=True)
class BgmLibraryLoadReport:
    manifest_path: Path | None
    tracks: tuple[BgmLibraryTrack, ...] = ()
    total_items: int = 0
    skipped_non_object_count: int = 0
    skipped_missing_path_count: int = 0
    skipped_missing_file_count: int = 0
    missing_manifest: bool = False
    invalid_schema: bool = False
    parse_error: str | None = None


@dataclass(frozen=True)
class SfxLibraryTrack:
    path: Path
    category: str
    gain_db: float | None = None


@dataclass(frozen=True)
class SfxLibraryLoadReport:
    manifest_path: Path | None
    tracks: tuple[SfxLibraryTrack, ...] = ()
    total_items: int = 0
    skipped_non_object_count: int = 0
    skipped_missing_path_count: int = 0
    skipped_missing_file_count: int = 0
    skipped_missing_category_count: int = 0
    missing_manifest: bool = False
    invalid_schema: bool = False
    parse_error: str | None = None


@dataclass(frozen=True)
class BgmSelectionContext:
    tags: tuple[str, ...]
    highlight_reasons: tuple[str, ...]
    rendered_duration_seconds: float
    selection_key: str = ""


def ensure_default_editing_audio_assets(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    playful_bgm = root / "bgm-playful.wav"
    climax_bgm = root / "bgm-climax.wav"
    coin_sfx = root / "coin.wav"

    if not playful_bgm.exists():
        _write_bgm_loop(
            playful_bgm,
            notes=[261.63, 329.63, 392.00, 523.25, 440.00, 392.00, 329.63, 293.66],
            note_seconds=0.22,
            amplitude=0.18,
        )
    if not climax_bgm.exists():
        _write_bgm_loop(
            climax_bgm,
            notes=[329.63, 392.00, 493.88, 659.25, 587.33, 493.88, 440.00, 392.00],
            note_seconds=0.16,
            amplitude=0.20,
        )
    if not coin_sfx.exists():
        _write_coin_sfx(coin_sfx)

    return {
        "playful_bgm": playful_bgm,
        "climax_bgm": climax_bgm,
        "coin_sfx": coin_sfx,
    }


def load_bgm_library(manifest_path: Path | None) -> list[BgmLibraryTrack]:
    return list(load_bgm_library_report(manifest_path).tracks)


def load_bgm_library_report(manifest_path: Path | None) -> BgmLibraryLoadReport:
    if manifest_path is None or not manifest_path.is_file():
        return BgmLibraryLoadReport(
            manifest_path=manifest_path,
            missing_manifest=manifest_path is not None,
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return BgmLibraryLoadReport(
            manifest_path=manifest_path,
            parse_error=exc.__class__.__name__,
        )
    raw_tracks = payload.get("tracks") if isinstance(payload, dict) else payload
    if not isinstance(raw_tracks, list):
        return BgmLibraryLoadReport(
            manifest_path=manifest_path,
            invalid_schema=True,
        )

    tracks: list[BgmLibraryTrack] = []
    skipped_non_object_count = 0
    skipped_missing_path_count = 0
    skipped_missing_file_count = 0
    base_dir = manifest_path.parent
    for item in raw_tracks:
        if not isinstance(item, dict):
            skipped_non_object_count += 1
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            skipped_missing_path_count += 1
            continue
        track_path = Path(raw_path)
        if not track_path.is_absolute():
            track_path = base_dir / track_path
        if not track_path.is_file():
            skipped_missing_file_count += 1
            continue
        tags = tuple(
            _normalize_tag(tag)
            for tag in item.get("tags", [])
            if isinstance(tag, str) and _normalize_tag(tag)
        )
        mood = item.get("mood")
        phase = item.get("phase")
        energy = item.get("energy")
        tracks.append(
            BgmLibraryTrack(
                path=track_path,
                tags=tags,
                mood=_normalize_tag(mood) if isinstance(mood, str) else None,
                phase=_normalize_tag(phase) if isinstance(phase, str) else None,
                energy=energy if isinstance(energy, int) else None,
            )
        )
    return BgmLibraryLoadReport(
        manifest_path=manifest_path,
        tracks=tuple(tracks),
        total_items=len(raw_tracks),
        skipped_non_object_count=skipped_non_object_count,
        skipped_missing_path_count=skipped_missing_path_count,
        skipped_missing_file_count=skipped_missing_file_count,
    )


def load_sfx_library_report(manifest_path: Path | None) -> SfxLibraryLoadReport:
    if manifest_path is None or not manifest_path.is_file():
        return SfxLibraryLoadReport(
            manifest_path=manifest_path,
            missing_manifest=manifest_path is not None,
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return SfxLibraryLoadReport(
            manifest_path=manifest_path,
            parse_error=exc.__class__.__name__,
        )
    raw_tracks = payload.get("tracks") if isinstance(payload, dict) else payload
    if not isinstance(raw_tracks, list):
        return SfxLibraryLoadReport(
            manifest_path=manifest_path,
            invalid_schema=True,
        )

    tracks: list[SfxLibraryTrack] = []
    skipped_non_object_count = 0
    skipped_missing_path_count = 0
    skipped_missing_file_count = 0
    skipped_missing_category_count = 0
    base_dir = manifest_path.parent
    for item in raw_tracks:
        if not isinstance(item, dict):
            skipped_non_object_count += 1
            continue
        raw_category = item.get("category")
        if not isinstance(raw_category, str) or not raw_category.strip():
            skipped_missing_category_count += 1
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            skipped_missing_path_count += 1
            continue
        track_path = Path(raw_path)
        if not track_path.is_absolute():
            track_path = base_dir / track_path
        if not track_path.is_file():
            skipped_missing_file_count += 1
            continue
        raw_gain = item.get("gain_db")
        gain_db = float(raw_gain) if isinstance(raw_gain, (int, float)) else None
        tracks.append(
            SfxLibraryTrack(
                path=track_path,
                category=raw_category.strip(),
                gain_db=gain_db,
            )
        )
    return SfxLibraryLoadReport(
        manifest_path=manifest_path,
        tracks=tuple(tracks),
        total_items=len(raw_tracks),
        skipped_non_object_count=skipped_non_object_count,
        skipped_missing_path_count=skipped_missing_path_count,
        skipped_missing_file_count=skipped_missing_file_count,
        skipped_missing_category_count=skipped_missing_category_count,
    )


def select_bgm_tracks(
    tracks: list[BgmLibraryTrack],
    context: BgmSelectionContext,
    requested_phases: tuple[str, ...] | None = None,
) -> list[BgmLibraryTrack]:
    if not tracks:
        return []
    phases = requested_phases
    if phases is None:
        phases = (
            ("early", "climax")
            if context.rendered_duration_seconds >= 120.0
            else ("early",)
        )
    selected: list[BgmLibraryTrack] = []
    available = list(tracks)
    for phase in phases:
        candidate = _best_bgm_track(available, context, preferred_phase=phase)
        if candidate is None:
            continue
        selected.append(candidate)
        available = [track for track in available if track.path != candidate.path]
        if not available:
            break
    return selected


def infer_bgm_context_tags(
    *,
    transcript_text: str,
    highlight_reasons: list[str],
    streamer_name: str | None,
) -> tuple[str, ...]:
    text = transcript_text.lower()
    tags: list[str] = []
    if streamer_name:
        streamer = _normalize_tag(streamer_name)
        if streamer:
            tags.append(streamer)

    keyword_tags = [
        (("机器人", "robot", "blitz", "布里茨"), "robot"),
        (("电刀", "statikk"), "statikk"),
        (("ap", "法强"), "ap"),
        (("教学", "讲解"), "tutorial"),
        (("套路", "骗", "整活"), "trick"),
        (("搞笑", "哈哈", "笑死"), "funny"),
        ((
            "\u88c5\u6ca1\u94b1",
            "\u6ca1\u94b1",
            "\u6709\u94b1",
            "\u7c89\u4e1d",
            "\u8ba4\u51fa",
            "\u8ba4\u51fa\u6765",
        ), "funny"),
        (("\u7092\u80a1", "\u80a1\u7968", "\u4e8f\u94b1"), "chill"),
        (("团战", "开团", "击杀", "双杀", "三杀", "四杀", "五杀"), "hype"),
        (("翻盘", "逆风"), "comeback"),
        (("发育", "清线", "补刀"), "laning"),
    ]
    for keywords, tag in keyword_tags:
        if any(keyword in text for keyword in keywords):
            tags.append(tag)

    reason_tags = {
        "highlight_keyword": "hype",
        "condensed_key_event": "hype",
        "condensed_tactical": "tactical",
        "condensed_context": "chill",
        "condensed_match_context": "chill",
    }
    for reason in highlight_reasons:
        tag = reason_tags.get(reason)
        if tag is not None:
            tags.append(tag)
    return tuple(dict.fromkeys(tags))


def _best_bgm_track(
    tracks: list[BgmLibraryTrack],
    context: BgmSelectionContext,
    *,
    preferred_phase: str,
) -> BgmLibraryTrack | None:
    tracks = _preferred_phase_tracks(
        tracks,
        context,
        preferred_phase=preferred_phase,
    )
    scored = [
        (_bgm_track_score(track, context, preferred_phase=preferred_phase), track)
        for track in tracks
    ]
    scored = [(score, track) for score, track in scored if score > 0]
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    candidates = [
        (score, track)
        for score, track in scored
        if score == best_score
    ]
    candidates.sort(key=lambda item: str(item[1].path))
    if len(candidates) == 1 or not context.selection_key:
        return candidates[0][1]
    index = _stable_bgm_candidate_index(
        context.selection_key,
        preferred_phase=preferred_phase,
        candidate_count=len(candidates),
    )
    return candidates[index][1]


def _preferred_phase_tracks(
    tracks: list[BgmLibraryTrack],
    context: BgmSelectionContext,
    *,
    preferred_phase: str,
) -> list[BgmLibraryTrack]:
    preferred = [
        track
        for track in tracks
        if _track_matches_preferred_phase(track, preferred_phase=preferred_phase)
    ]
    if not preferred:
        return tracks
    if any(
        _bgm_track_score(track, context, preferred_phase=preferred_phase) > 0
        for track in preferred
    ):
        return preferred
    return tracks


def _track_matches_preferred_phase(
    track: BgmLibraryTrack,
    *,
    preferred_phase: str,
) -> bool:
    phase = track.phase
    mood = track.mood
    energy = track.energy
    if preferred_phase in {"early", "laning"}:
        if phase in {"early", "laning", "playful"}:
            return True
        if phase in {"climax", "hype", "fight"}:
            return False
        if mood in {"playful", "chill", "tutorial"} and (energy is None or energy <= 3):
            return True
        return phase in {None, "any", "all"} and (energy is None or energy <= 3)
    if preferred_phase == "momentum":
        if phase in {"momentum", "mid", "tactical", "fight"}:
            return True
        if phase in {"climax", "hype"} and (energy is None or energy >= 5):
            return False
        if mood in {"tactical", "fight", "trick"} and (
            energy is None or 2 <= energy <= 4
        ):
            return True
        return phase in {None, "any", "all"} and (
            energy is None or 2 <= energy <= 4
        )
    if phase in {"climax", "hype", "fight"}:
        return True
    if energy is not None and energy >= 4:
        return True
    return phase in {None, "any", "all"} and mood in {"hype", "trick"}


def _stable_bgm_candidate_index(
    selection_key: str,
    *,
    preferred_phase: str,
    candidate_count: int,
) -> int:
    if candidate_count <= 1:
        return 0
    key_without_match = selection_key
    match_index_offset = 0
    match = re.match(r"^(?P<session>[^:]+):(?P<match_index>\d+):(?P<context>.*)$", selection_key)
    if match is not None:
        key_without_match = f"{match.group('session')}:{match.group('context')}"
        match_index_offset = int(match.group("match_index"))
    digest = hashlib.blake2b(
        f"{key_without_match}:{preferred_phase}".encode("utf-8"),
        digest_size=4,
    ).digest()
    return (int.from_bytes(digest, "big") + match_index_offset) % candidate_count


def _bgm_track_score(
    track: BgmLibraryTrack,
    context: BgmSelectionContext,
    *,
    preferred_phase: str,
) -> int:
    context_tags = set(context.tags)
    track_tags = set(track.tags)
    score = len(context_tags & track_tags) * 8
    if track.mood is not None and track.mood in context_tags:
        score += 5
    if track.phase == preferred_phase:
        score += 6
    elif track.phase in {None, "any", "all"}:
        score += 2
    elif preferred_phase in {"early", "laning"} and track.phase in {
        "early",
        "playful",
        "laning",
    }:
        score += 4
    elif preferred_phase == "momentum" and track.phase in {
        "mid",
        "tactical",
        "fight",
    }:
        score += 4
    elif preferred_phase == "climax" and track.phase in {"hype", "fight"}:
        score += 4

    if preferred_phase == "climax" or "hype" in context_tags:
        desired_energy = 4
    elif preferred_phase == "momentum":
        desired_energy = 3
    else:
        desired_energy = 2
    if track.energy is not None:
        score += max(0, 4 - abs(track.energy - desired_energy))
    if not context_tags and track.phase in {None, "any", "all", preferred_phase}:
        score += 1
    return score


def _normalize_tag(value: str | None) -> str:
    if value is None:
        return ""
    normalized = value.strip().lower().replace(" ", "_")
    return _TAG_ALIASES.get(normalized, normalized)


def detect_source_background_music(
    source_path: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    ffmpeg_path: str = "ffmpeg",
) -> SourceMusicDetection:
    if not source_path.is_file():
        return SourceMusicDetection(False, 0.0, "missing_source")
    if end_seconds <= start_seconds:
        return SourceMusicDetection(False, 0.0, "invalid_window")
    if shutil.which(ffmpeg_path) is None and not Path(ffmpeg_path).is_file():
        return SourceMusicDetection(False, 0.0, "missing_ffmpeg")

    scores: list[float] = []
    confident_spans: list[SourceMusicSpan] = []
    for sample_start in _detection_sample_starts(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    ):
        pcm = _extract_pcm_window(
            source_path,
            sample_start_seconds=sample_start,
            ffmpeg_path=ffmpeg_path,
        )
        if not pcm:
            continue
        score = _music_likeness_score(pcm)
        scores.append(score)
        if score >= 0.72:
            confident_spans.append(
                SourceMusicSpan(
                    start_seconds=round(sample_start, 3),
                    end_seconds=round(
                        min(end_seconds, sample_start + DETECTION_SAMPLE_SECONDS),
                        3,
                    ),
                    confidence=round(score, 3),
                )
            )

    if not scores:
        return SourceMusicDetection(False, 0.0, "no_audio_samples")
    confident_scores = [score for score in scores if score >= 0.72]
    confidence = round(sum(scores) / len(scores), 3)
    has_music = len(confident_scores) >= 2 and len(confident_scores) >= len(scores) * 0.6
    music_spans = _merge_source_music_spans(confident_spans)
    return SourceMusicDetection(
        has_music,
        confidence,
        "persistent_music_like_audio" if has_music else "no_persistent_music_bed",
        music_spans=music_spans,
        coverage_ratio=_source_music_coverage_ratio(
            music_spans,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        ),
    )


def detect_source_background_music_spans(
    spans: list[MediaSpan],
    *,
    start_seconds: float,
    end_seconds: float,
    ffmpeg_path: str = "ffmpeg",
) -> SourceMusicDetection:
    if not spans:
        return SourceMusicDetection(False, 0.0, "missing_source")
    if end_seconds <= start_seconds:
        return SourceMusicDetection(False, 0.0, "invalid_window")
    if shutil.which(ffmpeg_path) is None and not Path(ffmpeg_path).is_file():
        return SourceMusicDetection(False, 0.0, "missing_ffmpeg")
    for span in spans:
        if not Path(span.path).is_file():
            return SourceMusicDetection(False, 0.0, "missing_source")

    scores: list[float] = []
    confident_spans: list[SourceMusicSpan] = []
    for sample_start in _detection_sample_starts(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    ):
        span = _span_for_source_second(spans, sample_start)
        if span is None:
            continue
        local_sample_start = span.local_start_seconds + (
            sample_start - span.source_start_seconds
        )
        pcm = _extract_pcm_window(
            Path(span.path),
            sample_start_seconds=local_sample_start,
            ffmpeg_path=ffmpeg_path,
        )
        if not pcm:
            continue
        score = _music_likeness_score(pcm)
        scores.append(score)
        if score >= 0.72:
            confident_spans.append(
                SourceMusicSpan(
                    start_seconds=round(sample_start, 3),
                    end_seconds=round(
                        min(
                            span.source_end_seconds,
                            sample_start + DETECTION_SAMPLE_SECONDS,
                        ),
                        3,
                    ),
                    confidence=round(score, 3),
                )
            )

    if not scores:
        return SourceMusicDetection(False, 0.0, "no_audio_samples")
    confident_scores = [score for score in scores if score >= 0.72]
    confidence = round(sum(scores) / len(scores), 3)
    has_music = len(confident_scores) >= 2 and len(confident_scores) >= len(scores) * 0.6
    music_spans = _merge_source_music_spans(confident_spans)
    return SourceMusicDetection(
        has_music,
        confidence,
        "persistent_music_like_audio" if has_music else "no_persistent_music_bed",
        music_spans=music_spans,
        coverage_ratio=_source_music_coverage_ratio(
            music_spans,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        ),
    )


def _merge_source_music_spans(
    spans: list[SourceMusicSpan],
) -> tuple[SourceMusicSpan, ...]:
    if not spans:
        return ()
    merged: list[SourceMusicSpan] = []
    for span in sorted(spans, key=lambda item: (item.start_seconds, item.end_seconds)):
        if span.end_seconds <= span.start_seconds:
            continue
        if not merged:
            merged.append(span)
            continue
        previous = merged[-1]
        if span.start_seconds <= previous.end_seconds + 0.001:
            merged[-1] = SourceMusicSpan(
                start_seconds=previous.start_seconds,
                end_seconds=max(previous.end_seconds, span.end_seconds),
                confidence=max(previous.confidence, span.confidence),
            )
        else:
            merged.append(span)
    return tuple(merged)


def _source_music_coverage_ratio(
    spans: tuple[SourceMusicSpan, ...],
    *,
    start_seconds: float,
    end_seconds: float,
) -> float:
    duration = max(0.0, end_seconds - start_seconds)
    if duration <= 0.0:
        return 0.0
    covered = 0.0
    for span in spans:
        overlap = min(end_seconds, span.end_seconds) - max(
            start_seconds,
            span.start_seconds,
        )
        if overlap > 0.0:
            covered += overlap
    return round(min(1.0, covered / duration), 3)


def _span_for_source_second(
    spans: list[MediaSpan],
    sample_start_seconds: float,
) -> MediaSpan | None:
    for span in spans:
        if (
            span.source_start_seconds <= sample_start_seconds < span.source_end_seconds
        ):
            return span
    return None


def _detection_sample_starts(
    *,
    start_seconds: float,
    end_seconds: float,
) -> list[float]:
    duration = max(0.0, end_seconds - start_seconds)
    max_start = max(start_seconds, end_seconds - DETECTION_SAMPLE_SECONDS)
    candidates = [
        start_seconds + min(10.0, duration * 0.1),
        start_seconds + duration * 0.33,
        start_seconds + duration * 0.66,
        max_start,
    ]
    starts: list[float] = []
    for candidate in candidates:
        clamped = round(max(start_seconds, min(max_start, candidate)), 3)
        if all(abs(clamped - existing) > 1.0 for existing in starts):
            starts.append(clamped)
    return starts


def _extract_pcm_window(
    source_path: Path,
    *,
    sample_start_seconds: float,
    ffmpeg_path: str,
) -> bytes:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{sample_start_seconds:.3f}",
        "-t",
        f"{DETECTION_SAMPLE_SECONDS:.3f}",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(DETECTION_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return b""
    if completed.returncode != 0:
        return b""
    return completed.stdout


def _music_likeness_score(pcm: bytes) -> float:
    samples = _pcm_i16_samples(pcm)
    if len(samples) < DETECTION_SAMPLE_RATE:
        return 0.0

    frame_size = max(1, int(DETECTION_SAMPLE_RATE * 0.2))
    frame_dbs: list[float] = []
    zero_crossing_rates: list[float] = []
    for index in range(0, len(samples) - frame_size + 1, frame_size):
        frame = samples[index : index + frame_size]
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        if rms <= 0.0:
            frame_dbs.append(-120.0)
        else:
            frame_dbs.append(20.0 * math.log10(rms / MAX_I16))
        crossings = sum(
            1
            for current, previous in zip(frame[1:], frame)
            if (current >= 0 > previous) or (current < 0 <= previous)
        )
        zero_crossing_rates.append(crossings / max(1, len(frame) - 1))

    active_dbs = [value for value in frame_dbs if value >= -45.0]
    if not active_dbs:
        return 0.0

    active_ratio = len(active_dbs) / len(frame_dbs)
    median_db = _percentile(active_dbs, 0.5)
    dynamic_range_db = _percentile(active_dbs, 0.9) - _percentile(active_dbs, 0.1)
    mean_zcr = sum(zero_crossing_rates) / len(zero_crossing_rates)

    score = 0.0
    if active_ratio >= 0.82:
        score += 0.35
    elif active_ratio >= 0.7:
        score += 0.2
    if median_db >= -38.0:
        score += 0.25
    elif median_db >= -42.0:
        score += 0.12
    if dynamic_range_db <= 18.0:
        score += 0.25
    elif dynamic_range_db <= 24.0:
        score += 0.12
    if 0.015 <= mean_zcr <= 0.28:
        score += 0.15
    return min(1.0, score)


def _pcm_i16_samples(pcm: bytes) -> list[int]:
    usable_length = len(pcm) - (len(pcm) % SAMPLE_WIDTH_BYTES)
    return [
        int.from_bytes(
            pcm[index : index + SAMPLE_WIDTH_BYTES],
            byteorder="little",
            signed=True,
        )
        for index in range(0, usable_length, SAMPLE_WIDTH_BYTES)
    ]


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _write_bgm_loop(
    path: Path,
    *,
    notes: list[float],
    note_seconds: float,
    amplitude: float,
) -> None:
    total_seconds = max(1.0, len(notes) * note_seconds)
    total_samples = int(total_seconds * SAMPLE_RATE)
    frames = bytearray()
    for sample_index in range(total_samples):
        seconds = sample_index / SAMPLE_RATE
        note_index = min(len(notes) - 1, int(seconds / note_seconds))
        note_seconds_offset = seconds - note_index * note_seconds
        frequency = notes[note_index]
        envelope = _percussive_envelope(note_seconds_offset, note_seconds)
        value = amplitude * envelope * (
            math.sin(math.tau * frequency * seconds)
            + 0.35 * math.sin(math.tau * frequency * 2.0 * seconds)
        )
        frames.extend(_i16_bytes(value / 1.35))
    _write_wav(path, frames)


def _write_coin_sfx(path: Path) -> None:
    notes = [1318.51, 1760.00, 2637.02]
    note_seconds = 0.075
    tail_seconds = 0.12
    total_seconds = len(notes) * note_seconds + tail_seconds
    total_samples = int(total_seconds * SAMPLE_RATE)
    frames = bytearray()
    for sample_index in range(total_samples):
        seconds = sample_index / SAMPLE_RATE
        note_index = min(len(notes) - 1, int(seconds / note_seconds))
        note_offset = seconds - note_index * note_seconds
        note_remaining = max(0.0, note_seconds - note_offset)
        frequency = notes[note_index]
        decay = math.exp(-9.0 * seconds)
        note_gate = min(1.0, note_remaining / 0.025)
        shimmer = 0.55 * math.sin(math.tau * frequency * seconds)
        shimmer += 0.25 * math.sin(math.tau * frequency * 2.01 * seconds)
        shimmer += 0.10 * math.sin(math.tau * frequency * 3.98 * seconds)
        frames.extend(_i16_bytes(0.42 * decay * note_gate * shimmer))
    _write_wav(path, frames)


def _percussive_envelope(offset_seconds: float, duration_seconds: float) -> float:
    attack = min(1.0, offset_seconds / max(0.001, duration_seconds * 0.12))
    release = min(
        1.0,
        max(0.0, duration_seconds - offset_seconds) / max(0.001, duration_seconds * 0.24),
    )
    return attack * release


def _write_wav(path: Path, frames: bytearray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(SAMPLE_WIDTH_BYTES)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(bytes(frames))


def _i16_bytes(value: float) -> bytes:
    clamped = max(-1.0, min(1.0, value))
    sample = int(clamped * MAX_I16)
    return sample.to_bytes(SAMPLE_WIDTH_BYTES, byteorder="little", signed=True)
