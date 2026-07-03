from __future__ import annotations

from arl.highlights.models import ClassifiedCue
from arl.highlights.window_optimizer import bridge_highlight_windows, optimize_windows
from arl.shared.contracts import HighlightClipWindow


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
    assert _total_duration(windows) <= 180.0
    assert any(
        window.started_at_seconds <= 660.0
        and window.ended_at_seconds >= 700.0
        for window in windows
    )
    assert sum(1 for window in windows if window.reason == "condensed_key_event") == 2


def test_optimizer_adds_lead_in_bridge_before_next_segment() -> None:
    cues = [
        ClassifiedCue(10.0, 12.0, "first jungle path", "tactical", 0.7),
        ClassifiedCue(100.0, 102.0, "death timer fight", "key_event", 1.0),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=140.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=45.0,
        edge_context_seconds=30.0,
        max_continuous_window_seconds=90.0,
    )

    assert _total_duration(windows) <= 120.0
    assert any(
        window.started_at_seconds <= 72.5
        and window.ended_at_seconds >= 100.0
        and window.reason == "condensed_key_event"
        for window in windows
    )


def test_final_bridge_helper_repairs_gaps_after_late_trimming() -> None:
    windows = [
        HighlightClipWindow(
            started_at_seconds=0.0,
            ended_at_seconds=30.0,
            reason="condensed_match_context",
        ),
        HighlightClipWindow(
            started_at_seconds=92.0,
            ended_at_seconds=120.0,
            reason="condensed_key_event",
        ),
    ]

    bridged = bridge_highlight_windows(
        windows,
        max_gap_seconds=45.0,
        bridge_window_seconds=30.0,
        match_duration=140.0,
    )

    assert _largest_gap(bridged) < 45.0
    assert any(
        window.started_at_seconds <= 69.5
        and window.ended_at_seconds >= 92.0
        and window.reason == "condensed_key_event"
        for window in bridged
    )


def test_final_bridge_helper_uses_short_bridge_window_when_configured() -> None:
    windows = [
        HighlightClipWindow(
            started_at_seconds=0.0,
            ended_at_seconds=30.0,
            reason="condensed_match_context",
        ),
        HighlightClipWindow(
            started_at_seconds=92.0,
            ended_at_seconds=120.0,
            reason="condensed_key_event",
        ),
    ]

    bridged = bridge_highlight_windows(
        windows,
        max_gap_seconds=45.0,
        bridge_window_seconds=5.0,
        match_duration=140.0,
    )

    continuity_duration = sum(
        window.ended_at_seconds - window.started_at_seconds
        for window in bridged
        if window.reason == "condensed_continuity"
    )
    assert _largest_gap(bridged) <= 45.0
    assert continuity_duration <= 10.0


def test_final_bridge_helper_splits_huge_opening_jump() -> None:
    windows = [
        HighlightClipWindow(
            started_at_seconds=0.0,
            ended_at_seconds=38.0,
            reason="condensed_match_context",
        ),
        HighlightClipWindow(
            started_at_seconds=996.0,
            ended_at_seconds=1549.0,
            reason="condensed_key_event",
        ),
        HighlightClipWindow(
            started_at_seconds=1597.0,
            ended_at_seconds=1650.0,
            reason="condensed_match_context",
        ),
    ]

    bridged = bridge_highlight_windows(
        windows,
        max_gap_seconds=45.0,
        bridge_window_seconds=30.0,
        match_duration=1650.0,
    )

    assert _largest_gap(bridged) <= 45.0
    assert _total_duration(bridged) <= 780.0
    assert any(
        window.started_at_seconds < 996.0
        and window.ended_at_seconds >= 996.0
        and window.reason == "condensed_key_event"
        for window in bridged
    )


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
    assert any(
        window.started_at_seconds <= 340.0
        and window.ended_at_seconds >= 352.0
        and window.reason == "condensed_key_event"
        for window in windows
    )
    assert _total_duration(windows) <= 180.0


def test_optimizer_can_use_short_start_edge_context() -> None:
    cues = [
        ClassifiedCue(80.0, 82.0, "first real lane action", "tactical", 0.7),
    ]

    windows = optimize_windows(
        classified_cues=cues,
        target_duration_seconds=120.0,
        match_duration_seconds=300.0,
        context_padding_seconds=5.0,
        merge_gap_seconds=0.0,
        boring_gap_threshold_seconds=45.0,
        edge_context_seconds=10.0,
        start_edge_context_seconds=1.0,
        bridge_window_seconds=3.0,
        max_continuous_window_seconds=300.0,
    )

    assert windows[0].started_at_seconds == 0.0
    assert windows[0].ended_at_seconds == 1.0
    assert windows[-1].started_at_seconds <= 290.0
    assert windows[-1].ended_at_seconds == 300.0


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
    assert _total_duration(windows) <= 180.0
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
    assert _total_duration(windows) <= 180.0
    assert any(
        window.started_at_seconds <= 665.0
        and window.ended_at_seconds >= 700.0
        for window in windows
    )


def _largest_gap(windows) -> float:
    ordered = sorted(windows, key=lambda window: window.started_at_seconds)
    return max(
        current.started_at_seconds - previous.ended_at_seconds
        for previous, current in zip(ordered, ordered[1:])
    )


def _total_duration(windows) -> float:
    return sum(
        window.ended_at_seconds - window.started_at_seconds for window in windows
    )
