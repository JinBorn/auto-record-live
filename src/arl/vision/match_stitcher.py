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
    min_match_duration_seconds: float = 360.0,
    timer_readings: list[TimerReading] | None = None,
) -> list[MatchSegment]:
    """Group coarse scene readings into match segments.

    Loading-screen frames are kept as the match start when they immediately
    precede in-game HUD frames. The first non-game frame after a HUD span is
    treated as the natural game end.

    Args:
        readings: Scene readings from frame samples
        match_start_threshold_seconds: Timer threshold for match start detection
        min_match_duration_seconds: Minimum duration to consider as complete match
        timer_readings: Optional timer OCR readings for cross-validation.
            When provided they are used to detect game starts that were
            missed by the scene classifier (e.g. loading screens shorter
            than the sample interval).
    """
    if not readings:
        return []

    sorted_readings = sorted(readings, key=lambda reading: reading.timestamp_seconds)
    loading_gap_limit = _loading_to_in_game_gap_limit(sorted_readings)

    # Build a fast lookup: timestamp → parsed game-time seconds.
    timer_by_ts: dict[float, float] = {}
    if timer_readings:
        for tr in timer_readings:
            if tr.game_time_text:
                gt = _parse_timer(tr.game_time_text)
                if gt > 0:
                    timer_by_ts[tr.timestamp_seconds] = gt

    segments: list[MatchSegment] = []
    current_span: list[SceneReading] = []
    current_start: float | None = None
    current_start_from_loading = False
    pending_loading_start: float | None = None
    pending_other_start: float | None = None

    for reading in sorted_readings:
        if reading.scene == "loading":
            if current_span:
                # Guard against death/respawn screens misclassified as
                # "loading": when a loading frame arrives immediately after
                # the last in-game frame with no intervening "other" frames
                # (pending_other_start is None) the gap is just one sample
                # interval (~20 s).  Real game boundaries always have at
                # least a post-game lobby phase (several minutes of "other"
                # frames) between the last in-game and the next loading
                # screen.  Allow up to 90 s to cover long death timers.
                last_in_game = current_span[-1].timestamp_seconds
                direct_gap = reading.timestamp_seconds - last_in_game
                if pending_other_start is None and direct_gap <= 90.0:
                    # Likely death/respawn — skip to avoid false split.
                    continue
                end_seconds = (
                    pending_other_start
                    if pending_other_start is not None
                    else reading.timestamp_seconds
                )
                segments.append(
                    _analyze_scene_span(
                        span=current_span,
                        start_seconds=current_start,
                        started_from_loading=current_start_from_loading,
                        match_start_threshold_seconds=match_start_threshold_seconds,
                        min_match_duration_seconds=min_match_duration_seconds,
                        end_seconds=end_seconds,
                    )
                )
                current_span = []
                current_start = None
                current_start_from_loading = False
                pending_other_start = None
            pending_loading_start = reading.timestamp_seconds
            continue

        if reading.scene == "in_game":
            if current_span and pending_other_start is not None:
                last_in_game_time = current_span[-1].timestamp_seconds
                gap = reading.timestamp_seconds - last_in_game_time
                if gap > 300.0:
                    segments.append(
                        _analyze_scene_span(
                            span=current_span,
                            start_seconds=current_start,
                            started_from_loading=current_start_from_loading,
                            match_start_threshold_seconds=match_start_threshold_seconds,
                            min_match_duration_seconds=min_match_duration_seconds,
                            end_seconds=pending_other_start,
                        )
                    )
                    current_span = []
                    current_start = None
                    current_start_from_loading = False
                pending_other_start = None

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
            if pending_other_start is None:
                pending_other_start = reading.timestamp_seconds

            # Allow longer gaps (up to 300s/5min) within a match to handle:
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
                    end_seconds=pending_other_start,
                )
            )
            current_span = []
            current_start = None
            current_start_from_loading = False
            pending_other_start = None
        pending_loading_start = None

    if current_span:
        if pending_other_start is not None:
            segments.append(
                _analyze_scene_span(
                    span=current_span,
                    start_seconds=current_start,
                    started_from_loading=current_start_from_loading,
                    match_start_threshold_seconds=match_start_threshold_seconds,
                    min_match_duration_seconds=min_match_duration_seconds,
                    end_seconds=pending_other_start,
                )
            )
        else:
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

    # Post-processing: validate starts with timer data when available.
    # This catches two cases:
    #  1) Death/respawn screens misclassified as "loading" create
    #     false started_from_loading starts (timer will show mid-game
    #     time → downgrade).
    #  2) Loading screens shorter than the sample interval are missed
    #     entirely, so later games have started_from_loading=False
    #     even though they start from a real loading screen.  Timer
    #     will show a low game time → upgrade.
    if timer_by_ts:
        segments = _validate_segment_starts_with_timer(
            segments,
            timer_by_ts,
            match_start_threshold_seconds=match_start_threshold_seconds,
        )

    # Merge segments separated by short gaps (handles intermittent
    # classifier failures during a match).  Must run *after* timer
    # validation so that correctly-detected game boundaries are not
    # merged away.
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


def _validate_segment_starts_with_timer(
    segments: list[MatchSegment],
    timer_by_ts: dict[float, float],
    *,
    match_start_threshold_seconds: float = 120.0,
) -> list[MatchSegment]:
    """Validate segment start completeness using game-timer data.

    - Downgrade: a complete segment whose first in-game timer shows
      mid-game time (> threshold) → likely a death-screen false split
      misclassified as loading.
    - Upgrade: an incomplete_no_start segment whose first in-game timer
      shows a low value (≤ threshold) → a real game start whose loading
      screen was missed by the sampler.
    """
    if not segments or not timer_by_ts:
        return segments

    sorted_timestamps = sorted(timer_by_ts)

    def _first_timer_in_range(start: float, end: float) -> float | None:
        for ts in sorted_timestamps:
            if start <= ts <= end:
                return timer_by_ts[ts]
            if ts > end:
                break
        return None

    for seg in segments:
        first_gt = _first_timer_in_range(seg.start_seconds, seg.end_seconds)
        if first_gt is None:
            continue

        if seg.is_complete and first_gt > match_start_threshold_seconds:
            # Timer shows mid-game → the "loading" that started this
            # segment was likely a death/respawn misclassification.
            seg.is_complete = False
            seg.confidence = max(0.25, seg.confidence - 0.5)
            seg.reason = "incomplete_no_start"
        elif not seg.is_complete and seg.reason == "incomplete_no_start":
            if first_gt <= match_start_threshold_seconds:
                # Timer shows early-game → a real start whose loading
                # screen was not sampled.  reason == "incomplete_no_start"
                # already implies a natural end was detected.
                seg.is_complete = True
                seg.confidence = min(0.95, seg.confidence + 0.45)
                seg.reason = "complete"

    return segments


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

        # Merge only likely classifier fragments. A complete segment is treated
        # as a real match boundary, and a later loading-started segment is a
        # hard boundary between games — *unless* timer validation has since
        # downgraded the next segment (mid-game timer at its start), which
        # means the "loading" was a death-screen misclassification and the
        # two fragments belong to the same game.
        next_is_timer_downgraded = (
            next_seg.reason == "incomplete_no_start"
            and _segment_started_from_loading(next_seg)
        )
        if (
            gap <= max_gap_seconds
            and not current.is_complete
            and not next_seg.is_complete
            and not _segment_started_from_loading(next_seg)
        ) or (
            gap <= max_gap_seconds
            and next_is_timer_downgraded
        ):
            # Merge: extend current segment to include next
            merged_trace = current.timer_trace + next_seg.timer_trace

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


def _segment_started_from_loading(segment: MatchSegment) -> bool:
    if not segment.timer_trace:
        return False
    first_in_game_time = segment.timer_trace[0][0]
    return segment.start_seconds < first_in_game_time


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


def validate_scene_segments_with_timer(
    segments: list[MatchSegment],
    timer_readings: list[TimerReading],
    *,
    match_start_threshold_seconds: float = 120.0,
) -> list[MatchSegment]:
    """Cross-validate scene-based segments with timer OCR readings.

    Scene stitching can produce false ``is_complete`` segments when a
    death/respawn overlay is misclassified as a loading screen (resulting in
    ``started_from_loading=True`` for what is really a mid-game continuation).
    Timer readings provide ground-truth game time that can catch these cases.

    For each segment currently marked complete, the first valid timer reading
    inside the segment is checked.  If the game time exceeds the start
    threshold the segment is downgraded to ``incomplete_no_start``.
    """
    if not segments or not timer_readings:
        return segments

    # Build sorted lookup: timer readings with a valid game-time parse.
    valid_timers = [
        (r.timestamp_seconds, _parse_timer(r.game_time_text or ""))
        for r in timer_readings
        if r.game_time_text
    ]
    valid_timers.sort(key=lambda t: t[0])

    if not valid_timers:
        return segments

    def _first_timer_in_range(
        start: float, end: float
    ) -> float | None:
        """Return the first valid game-time (seconds) within [start, end]."""
        for ts, game_time in valid_timers:
            if start <= ts <= end:
                return game_time
        return None

    for seg in segments:
        if not seg.is_complete:
            continue
        # Only re-evaluate segments whose completeness came from
        # started_from_loading (loading screen detected before in-game).
        # The heuristic long-segment path also sets is_complete=True but
        # we validate those too when they look suspicious.
        first_timer = _first_timer_in_range(seg.start_seconds, seg.end_seconds)
        if first_timer is None:
            continue
        if first_timer > match_start_threshold_seconds:
            seg.is_complete = False
            seg.confidence = max(0.25, seg.confidence - 0.5)
            seg.reason = "incomplete_no_start"

    return segments
