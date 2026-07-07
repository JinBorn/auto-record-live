"""Retime source SRT cues onto edited output timelines.

Shared by the exporter (subtitle burn-in sidecars) and the quality-report
stage (subtitle coverage measurement) so both consumers map cues through
edit/highlight plans with identical overlap semantics.
"""

from __future__ import annotations

from arl.shared.contracts import EditPlanAsset, HighlightPlanAsset
from arl.subtitles.ass import SrtCue

_MIN_OVERLAP_SECONDS = 0.05


def retime_srt_cues_for_edit_plan(
    source_cues: list[SrtCue],
    edit_plan: EditPlanAsset,
) -> list[SrtCue]:
    retimed: list[SrtCue] = []
    output_cursor = 0.0
    for segment in edit_plan.timeline:
        segment_start = segment.source_start_seconds
        segment_end = segment.source_end_seconds
        for cue in source_cues:
            overlap_start = max(cue.started_at_seconds, segment_start)
            overlap_end = min(cue.ended_at_seconds, segment_end)
            if overlap_end - overlap_start <= _MIN_OVERLAP_SECONDS:
                continue
            retimed.append(
                SrtCue(
                    started_at_seconds=output_cursor
                    + (overlap_start - segment_start),
                    ended_at_seconds=output_cursor
                    + (overlap_end - segment_start),
                    text=cue.text,
                )
            )
        output_cursor += max(0.0, segment_end - segment_start)
    return retimed


def retime_srt_cues_for_highlight_plan(
    source_cues: list[SrtCue],
    highlight_plan: HighlightPlanAsset,
) -> list[SrtCue]:
    retimed: list[SrtCue] = []
    output_cursor = 0.0
    for window in highlight_plan.windows:
        window_start = window.started_at_seconds
        window_end = window.ended_at_seconds
        for cue in source_cues:
            overlap_start = max(cue.started_at_seconds, window_start)
            overlap_end = min(cue.ended_at_seconds, window_end)
            if overlap_end - overlap_start <= _MIN_OVERLAP_SECONDS:
                continue
            retimed.append(
                SrtCue(
                    started_at_seconds=output_cursor
                    + (overlap_start - window_start),
                    ended_at_seconds=output_cursor
                    + (overlap_end - window_start),
                    text=cue.text,
                )
            )
        output_cursor += max(0.0, window_end - window_start)
    return retimed
