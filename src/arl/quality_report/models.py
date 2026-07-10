from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MediaProbeResult(BaseModel):
    duration_seconds: float | None = None
    bitrate_kbps: float | None = None
    width: int | None = None
    height: int | None = None
    probe_error: str | None = None


class QualityWarning(BaseModel):
    code: str
    message: str
    value: Any = None
    threshold: Any = None


class NoSubtitleGap(BaseModel):
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class BgmBedMetric(BaseModel):
    source_path: str
    timeline_start_seconds: float
    timeline_end_seconds: float | None = None
    reason: str


class SfxHitMetric(BaseModel):
    source_path: str
    at_seconds: float
    reason: str
    source_timestamp_seconds: float | None = None
    nearest_kda_delta_seconds: float | None = None


class ZoomSegmentMetric(BaseModel):
    role: str
    source_start_seconds: float
    source_end_seconds: float
    output_start_seconds: float
    duration_seconds: float
    target: str | None = None


class CopyMetric(BaseModel):
    title: str | None = None
    cover_lines: list[str] = Field(default_factory=list)
    title_equals_raw_leading_subtitle: bool = False


class TargetDurationMetric(BaseModel):
    target_duration_seconds: float | None = None
    content_density_score: float | None = None
    highlight_event_density: float | None = None
    narration_density: float | None = None
    visual_activity: float | None = None


class QualityReportRow(BaseModel):
    session_id: str
    match_index: int
    export_path: str
    subtitle_path: str | None = None
    report_json_path: str | None = None
    report_markdown_path: str | None = None
    export_duration_seconds: float | None = None
    container_bitrate_kbps: float | None = None
    width: int | None = None
    height: int | None = None
    condensed_target: TargetDurationMetric = Field(default_factory=TargetDurationMetric)
    condensed_target_range_seconds: tuple[float, float]
    plan_duration_seconds: float | None = None
    main_duration_seconds: float | None = None
    duration_budget_seconds: float | None = None
    budget_exception_reason: str | None = None
    boundary_duration_seconds: float | None = None
    max_source_gap_seconds: float = 0.0
    subtitle_active_ratio: float = 0.0
    subtitle_covered_seconds: float = 0.0
    no_subtitle_gap_count: int = 0
    long_no_subtitle_gap_min_seconds: float = 0.0
    max_no_subtitle_gap_seconds: float = 0.0
    longest_no_subtitle_gaps: list[NoSubtitleGap] = Field(default_factory=list)
    kda_event_count: int = 0
    kda_uncovered_count: int = 0
    teaser_segment_count: int = 0
    teaser_total_seconds: float = 0.0
    bgm_beds: list[BgmBedMetric] = Field(default_factory=list)
    sfx_hits: list[SfxHitMetric] = Field(default_factory=list)
    zoom_segments: list[ZoomSegmentMetric] = Field(default_factory=list)
    copywriter: CopyMetric = Field(default_factory=CopyMetric)
    warnings: list[QualityWarning] = Field(default_factory=list)


class QualityReportResult(BaseModel):
    generated_at: datetime
    strict: bool
    exit_code: int
    rows: list[QualityReportRow]
    markdown: str
