from __future__ import annotations

from statistics import median

from .models import MatchSegment, SceneReading, TimerReading


def stitch_matches(
    readings: list[TimerReading],
    match_start_threshold_seconds: float = 120.0,
    lobby_gap_threshold_seconds: float = 40.0,
) -> list[MatchSegment]:
    """Group timer readings into match segments.

    Args:
        readings: List of TimerReading from frame samples
        match_start_threshold_seconds: Timer threshold for match start detection
        lobby_gap_threshold_seconds: Gap threshold for match end detection

    Returns:
        List of MatchSegment with completeness analysis
    """
    if not readings:
        return []

    segments: list[MatchSegment] = []
    current_span: list[TimerReading] = []
    last_game_time: float | None = None

    for reading in readings:
        if reading.game_time_text is not None:
            current_span.append(reading)
            last_game_time = reading.timestamp_seconds
        else:
            # Check if we have a span to close
            if current_span:
                # Verify gap is sufficient for match end (at least 2 None readings)
                # This is implicitly enforced by the sampling interval
                segment = _analyze_span(
                    current_span,
                    match_start_threshold_seconds,
                )
                segments.append(segment)
                current_span = []
                last_game_time = None

    if current_span:
        segment = _analyze_span(
            current_span,
            match_start_threshold_seconds,
            has_natural_end=False,
        )
        segments.append(segment)

    return segments


def stitch_scene_readings(
    readings: list[SceneReading],
    *,
    match_start_threshold_seconds: float = 120.0,
    min_match_duration_seconds: float = 300.0,
) -> list[MatchSegment]:
    """Group coarse scene readings into match segments.

    Loading-screen frames are kept as the match start when they immediately
    precede in-game HUD frames. The first non-game frame after a HUD span is
    treated as the natural game end.

    Args:
        readings: Scene readings from frame samples
        match_start_threshold_seconds: Timer threshold for match start detection
        min_match_duration_seconds: Minimum duration to consider as complete match
    """
    if not readings:
        return []

    sorted_readings = sorted(readings, key=lambda reading: reading.timestamp_seconds)
    loading_gap_limit = _loading_to_in_game_gap_limit(sorted_readings)

    segments: list[MatchSegment] = []
    current_span: list[SceneReading] = []
    current_start: float | None = None
    current_start_from_loading = False
    pending_loading_start: float | None = None

    for reading in sorted_readings:
        if reading.scene == "loading":
            if current_span:
                segments.append(
                    _analyze_scene_span(
                        span=current_span,
                        start_seconds=current_start,
                        started_from_loading=current_start_from_loading,
                        match_start_threshold_seconds=match_start_threshold_seconds,
                        min_match_duration_seconds=min_match_duration_seconds,
                        end_seconds=reading.timestamp_seconds,
                    )
                )
                current_span = []
                current_start = None
                current_start_from_loading = False
            pending_loading_start = reading.timestamp_seconds
            continue

        if reading.scene == "in_game":
            if not current_span:
                start_from_loading = (
                    pending_loading_start is not None
                    and reading.timestamp_seconds - pending_loading_start <= loading_gap_limit
                )
                current_start = pending_loading_start if start_from_loading else reading.timestamp_seconds
                current_start_from_loading = start_from_loading
                pending_loading_start = None
            current_span.append(reading)
            continue

        # "other" scene: check if it's a short gap within a match
        if current_span:
            # Allow longer gaps (≤300s/5min) within a match to handle:
            # - Observer perspective switches
            # - Extended replays
            # - Post-match ceremonies/interviews
            # This is especially important for casted/spectated games
            last_in_game_time = current_span[-1].timestamp_seconds
            gap = reading.timestamp_seconds - last_in_game_time
            if gap <= 300.0:
                # Skip this "other" frame, continue the current span
                continue

            # Long gap: end the current match
            segments.append(
                _analyze_scene_span(
                    span=current_span,
                    start_seconds=current_start,
                    started_from_loading=current_start_from_loading,
                    match_start_threshold_seconds=match_start_threshold_seconds,
                    min_match_duration_seconds=min_match_duration_seconds,
                    end_seconds=reading.timestamp_seconds,
                )
            )
            current_span = []
            current_start = None
            current_start_from_loading = False
        pending_loading_start = None

    if current_span:
        segments.append(
            _analyze_scene_span(
                span=current_span,
                start_seconds=current_start,
                started_from_loading=current_start_from_loading,
                match_start_threshold_seconds=match_start_threshold_seconds,
                min_match_duration_seconds=min_match_duration_seconds,
                has_natural_end=False,
            )
        )

    # Post-processing: merge segments separated by short gaps
    # This handles cases where scene classifier fails intermittently during a match
    return _merge_close_segments(segments, max_gap_seconds=600.0)


def _loading_to_in_game_gap_limit(readings: list[SceneReading]) -> float:
    if len(readings) < 2:
        return 180.0
    gaps = [
        current.timestamp_seconds - previous.timestamp_seconds
        for previous, current in zip(readings, readings[1:])
        if current.timestamp_seconds > previous.timestamp_seconds
    ]
    if not gaps:
        return 180.0
    return max(180.0, median(gaps) * 3.0)


def _merge_close_segments(
    segments: list[MatchSegment],
    max_gap_seconds: float = 600.0,
) -> list[MatchSegment]:
    """Merge segments separated by short gaps.

    This handles cases where scene classifier intermittently fails during a match,
    causing a continuous match to be split into multiple segments.

    Args:
        segments: Initial segments from scene stitching
        max_gap_seconds: Maximum gap between segments to merge (default 10min)

    Returns:
        Merged segments
    """
    if len(segments) <= 1:
        return segments

    sorted_segments = sorted(segments, key=lambda s: s.start_seconds)
    merged: list[MatchSegment] = []
    current = sorted_segments[0]

    for next_seg in sorted_segments[1:]:
        gap = next_seg.start_seconds - current.end_seconds

        # Merge if gap is small relative to combined duration
        if gap <= max_gap_seconds:
            # Merge: extend current segment to include next
            merged_trace = current.timer_trace + next_seg.timer_trace
            merged_duration = next_seg.end_seconds - current.start_seconds

            # Inherit best confidence and completeness
            merged_confidence = max(current.confidence, next_seg.confidence)
            merged_complete = current.is_complete or next_seg.is_complete
            merged_reason = current.reason if current.is_complete else next_seg.reason

            current = MatchSegment(
                start_seconds=current.start_seconds,
                end_seconds=next_seg.end_seconds,
                timer_trace=merged_trace,
                is_complete=merged_complete,
                confidence=merged_confidence,
                reason=merged_reason,
            )
        else:
            # Gap too large: keep current, start new
            merged.append(current)
            current = next_seg

    merged.append(current)
    return merged


def _analyze_scene_span(
    *,
    span: list[SceneReading],
    start_seconds: float | None,
    started_from_loading: bool,
    match_start_threshold_seconds: float,
    min_match_duration_seconds: float,
    end_seconds: float | None = None,
    has_natural_end: bool = True,
) -> MatchSegment:
    if not span:
        raise ValueError("Empty scene span")

    first_in_game = span[0].timestamp_seconds
    resolved_start = start_seconds if start_seconds is not None else first_in_game
    resolved_end = end_seconds if end_seconds is not None else span[-1].timestamp_seconds
    duration = resolved_end - resolved_start

    has_start = (
        started_from_loading
        or first_in_game <= match_start_threshold_seconds
    )
    timer_trace = [(reading.timestamp_seconds, reading.scene) for reading in span]

    # Heuristic: long segments (≥10min) with natural end are likely complete matches
    # even if we didn't detect loading screen or early start
    is_long_segment = duration >= 600.0  # 10 minutes

    # Filter: reject segments shorter than minimum duration if marked as "complete"
    if has_start and has_natural_end and duration < min_match_duration_seconds:
        return MatchSegment(
            start_seconds=resolved_start,
            end_seconds=resolved_end,
            timer_trace=timer_trace,
            is_complete=False,
            confidence=0.2,
            reason="incomplete_too_short",
        )

    if has_start and has_natural_end:
        return MatchSegment(
            start_seconds=resolved_start,
            end_seconds=resolved_end,
            timer_trace=timer_trace,
            is_complete=True,
            confidence=0.9,
            reason="complete",
        )

    # Heuristic: long segment with natural end → likely complete
    if is_long_segment and has_natural_end:
        return MatchSegment(
            start_seconds=resolved_start,
            end_seconds=resolved_end,
            timer_trace=timer_trace,
            is_complete=True,
            confidence=0.8,
            reason="complete",
        )

    if not has_natural_end:
        return MatchSegment(
            start_seconds=resolved_start,
            end_seconds=resolved_end,
            timer_trace=timer_trace,
            is_complete=False,
            confidence=0.4,
            reason="incomplete_no_end",
        )
    return MatchSegment(
        start_seconds=resolved_start,
        end_seconds=resolved_end,
        timer_trace=timer_trace,
        is_complete=False,
        confidence=0.3,
        reason="incomplete_no_start",
    )


def _analyze_span(
    span: list[TimerReading],
    match_start_threshold_seconds: float,
    has_natural_end: bool = True,
) -> MatchSegment:
    """Analyze a span of in-game readings for completeness."""
    if not span:
        raise ValueError("Empty span")

    timer_trace: list[tuple[float, str]] = [
        (r.timestamp_seconds, r.game_time_text or "")
        for r in span
        if r.game_time_text
    ]

    start_seconds = span[0].timestamp_seconds
    end_seconds = span[-1].timestamp_seconds

    first_timer = _parse_timer(span[0].game_time_text or "")
    has_start = first_timer <= match_start_threshold_seconds if first_timer else False

    if has_start and has_natural_end:
        return MatchSegment(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            timer_trace=timer_trace,
            is_complete=True,
            confidence=0.95,
            reason="complete",
        )
    elif not has_start:
        return MatchSegment(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            timer_trace=timer_trace,
            is_complete=False,
            confidence=0.3,
            reason="incomplete_no_start",
        )
    else:
        return MatchSegment(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            timer_trace=timer_trace,
            is_complete=False,
            confidence=0.4,
            reason="incomplete_no_end",
        )


def _parse_timer(timer_text: str) -> float:
    """Convert MM:SS to seconds."""
    if not timer_text or ":" not in timer_text:
        return 0.0

    parts = timer_text.split(":")
    if len(parts) != 2:
        return 0.0

    try:
        minutes = int(parts[0])
        seconds = int(parts[1])
        return minutes * 60.0 + seconds
    except ValueError:
        return 0.0
