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
