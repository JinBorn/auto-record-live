from __future__ import annotations

from arl.highlights.models import ClassifiedCue
from arl.highlights.window_optimizer import optimize_windows


def test_optimizer_restores_key_events_removed_by_duration_reduction() -> None:
    cues = [
        ClassifiedCue(
            started_at_seconds=10.0,
            ended_at_seconds=20.0,
            text="ordinary context",
            category="narration",
            priority=0.4,
        ),
        ClassifiedCue(
            started_at_seconds=200.0,
            ended_at_seconds=205.0,
            text="single kill",
            category="key_event",
            priority=1.0,
        ),
        ClassifiedCue(
            started_at_seconds=400.0,
            ended_at_seconds=405.0,
            text="dragon fight",
            category="key_event",
            priority=1.0,
        ),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=30.0,
        match_duration_seconds=600.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
    )

    assert any(
        window.started_at_seconds <= 200.0 <= window.ended_at_seconds
        for window in windows
    )
    assert any(
        window.started_at_seconds <= 400.0 <= window.ended_at_seconds
        for window in windows
    )
    assert {window.reason for window in windows} == {"condensed_key_event"}


def test_optimizer_clamps_windows_to_match_duration() -> None:
    cues = [
        ClassifiedCue(
            started_at_seconds=95.0,
            ended_at_seconds=99.0,
            text="ending fight",
            category="key_event",
            priority=1.0,
        ),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=60.0,
        match_duration_seconds=100.0,
        context_padding_seconds=10.0,
    )

    assert len(windows) == 1
    assert windows[0].started_at_seconds == 75.0
    assert windows[0].ended_at_seconds == 100.0
    assert windows[0].reason == "condensed_key_event"


def test_optimizer_collapses_large_gaps_into_continuous_span() -> None:
    cues = [
        ClassifiedCue(10.0, 12.0, "first kill", "key_event", 1.0),
        ClassifiedCue(350.0, 352.0, "dragon fight", "key_event", 1.0),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=600.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=120.0,
        max_continuous_window_seconds=420.0,
    )

    assert len(windows) == 1
    assert windows[0].started_at_seconds == 0.0
    assert windows[0].ended_at_seconds == 362.0


def test_optimizer_bridges_discontinuous_plan_when_continuous_span_is_too_long() -> None:
    cues = [
        ClassifiedCue(10.0, 12.0, "first kill", "key_event", 1.0),
        ClassifiedCue(700.0, 702.0, "baron fight", "key_event", 1.0),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=900.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=120.0,
        edge_context_seconds=30.0,
        max_continuous_window_seconds=600.0,
    )

    assert windows[0].started_at_seconds == 0.0
    assert windows[-1].ended_at_seconds >= 707.0
    assert _largest_gap(windows) <= 120.0
    assert sum(1 for window in windows if window.reason == "condensed_key_event") == 2


def test_optimizer_preserves_match_edge_context_for_condensed_plan() -> None:
    cues = [
        ClassifiedCue(350.0, 352.0, "dragon fight", "key_event", 1.0),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=600.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=120.0,
        edge_context_seconds=30.0,
        max_continuous_window_seconds=900.0,
    )

    assert windows[0].started_at_seconds == 0.0
    assert windows[-1].ended_at_seconds == 600.0
    assert any(window.started_at_seconds == 340.0 for window in windows)
    assert _largest_gap(windows) <= 120.0


def test_optimizer_trims_full_span_content_to_dense_target_window() -> None:
    cues = [
        ClassifiedCue(10.0, 12.0, "opening kill", "key_event", 1.0),
        ClassifiedCue(250.0, 252.0, "dragon fight", "key_event", 1.0),
        ClassifiedCue(260.0, 262.0, "double kill", "key_event", 1.0),
        ClassifiedCue(270.0, 272.0, "tower dive", "key_event", 1.0),
        ClassifiedCue(590.0, 592.0, "nexus fight", "key_event", 1.0),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=600.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=120.0,
        edge_context_seconds=30.0,
        max_continuous_window_seconds=900.0,
    )

    assert windows[0].started_at_seconds == 0.0
    assert windows[-1].ended_at_seconds == 600.0
    assert _largest_gap(windows) <= 120.0
    for cue in cues:
        assert any(
            window.started_at_seconds <= cue.started_at_seconds <= window.ended_at_seconds
            or window.started_at_seconds <= cue.ended_at_seconds <= window.ended_at_seconds
            for window in windows
        )
    assert not (
        len(windows) == 1
        and windows[0].started_at_seconds == 0.0
        and windows[0].ended_at_seconds == 600.0
    )


def test_optimizer_bridges_large_edge_to_highlight_jumps() -> None:
    cues = [
        ClassifiedCue(700.0, 702.0, "mid game fight", "tactical", 0.7),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=900.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=120.0,
        edge_context_seconds=30.0,
        max_continuous_window_seconds=900.0,
    )

    assert windows[0].started_at_seconds == 0.0
    assert windows[-1].ended_at_seconds == 900.0
    assert _largest_gap(windows) <= 120.0
    assert any(window.reason == "condensed_continuity" for window in windows)


def _largest_gap(windows) -> float:
    ordered = sorted(windows, key=lambda window: window.started_at_seconds)
    return max(
        current.started_at_seconds - previous.ended_at_seconds
        for previous, current in zip(ordered, ordered[1:])
    )
