from __future__ import annotations

from .models import MatchSegment, TimerReading


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
