"""内容密度分析器：为condensed模式计算内容密度并映射目标时长。

综合评估：
- highlight_event_density: 关键事件频率
- narration_density: 有效字幕覆盖率
- visual_activity: 视觉活跃度（可选）
- content_density_score: 加权评分
- target_duration: 目标时长（分钟）
"""

from __future__ import annotations

from pathlib import Path

from arl.highlights.models import ClassifiedCue, ContentDensityResult
from arl.highlights.visual_analyzer import analyze_visual_activity
from arl.shared.logging import log


def analyze_content_density(
    classified_cues: list[ClassifiedCue],
    match_duration_seconds: float,
    video_path: Path | None = None,
    weight_highlight_events: float = 0.5,
    weight_narration: float = 0.25,
    weight_visual: float = 0.15,
    weight_baseline: float = 0.1,
    use_visual_analysis: bool = True,
    visual_sample_interval: float = 10.0,
    high_density_threshold: float = 0.8,
    low_density_threshold: float = 0.5,
    high_density_duration_range: tuple[int, int] = (16, 20),
    mid_density_duration_range: tuple[int, int] = (10, 16),
    low_density_duration_range: tuple[int, int] = (7, 11),
) -> ContentDensityResult:
    """分析内容密度并计算目标时长。

    Args:
        classified_cues: 已分类的字幕cue列表
        match_duration_seconds: 对局总时长（秒）
        video_path: 视频文件路径（用于视觉分析）
        weight_highlight_events: 关键事件权重
        weight_narration: 字幕密度权重
        weight_visual: 视觉活跃度权重
        weight_baseline: 基线活跃度权重
        use_visual_analysis: 是否启用视觉分析
        visual_sample_interval: 视觉分析采样间隔
        high_density_threshold: 高密度阈值
        low_density_threshold: 低密度阈值
        high_density_duration_range: 高密度时长范围（分钟）
        mid_density_duration_range: 中密度时长范围（分钟）
        low_density_duration_range: 低密度时长范围（分钟）

    Returns:
        ContentDensityResult包含各项指标和目标时长
    """
    # 1. 计算关键事件密度
    highlight_event_density = _compute_highlight_event_density(
        classified_cues, match_duration_seconds
    )

    # 2. 计算字幕密度
    narration_density = _compute_narration_density(
        classified_cues, match_duration_seconds
    )

    # 3. 计算视觉活跃度（可选）
    visual_activity = 0.0
    if use_visual_analysis and video_path is not None and video_path.exists():
        visual_activity = analyze_visual_activity(
            video_path, sample_interval_seconds=visual_sample_interval
        )
    elif use_visual_analysis and (video_path is None or not video_path.exists()):
        log(
            "highlights",
            f"content_analyzer: visual analysis enabled but video unavailable, "
            f"degrading to visual_activity=0.0",
        )

    # 4. 计算内容密度评分
    content_density_score = _compute_content_density_score(
        highlight_event_density,
        narration_density,
        visual_activity,
        weight_highlight_events,
        weight_narration,
        weight_visual,
        weight_baseline,
    )

    # 5. 映射目标时长
    target_duration_seconds = _map_target_duration(
        content_density_score,
        high_density_threshold,
        low_density_threshold,
        high_density_duration_range,
        mid_density_duration_range,
        low_density_duration_range,
    )

    log(
        "highlights",
        f"content_analyzer: match_duration={match_duration_seconds:.1f}s "
        f"highlight_event_density={highlight_event_density:.3f} "
        f"narration_density={narration_density:.3f} "
        f"visual_activity={visual_activity:.3f} "
        f"→ content_density_score={content_density_score:.3f} "
        f"→ target_duration={target_duration_seconds:.1f}s "
        f"({target_duration_seconds / 60:.1f}min)"
    )

    return ContentDensityResult(
        highlight_event_density=highlight_event_density,
        narration_density=narration_density,
        visual_activity=visual_activity,
        content_density_score=content_density_score,
        target_duration_seconds=target_duration_seconds,
    )


def _compute_highlight_event_density(
    classified_cues: list[ClassifiedCue], match_duration_seconds: float
) -> float:
    """计算关键事件密度：events per minute。

    Returns:
        归一化密度 [0, 1]，假设5 events/min为满分
    """
    if match_duration_seconds <= 0:
        return 0.0

    key_event_count = sum(1 for c in classified_cues if c.category == "key_event")
    match_duration_minutes = match_duration_seconds / 60.0
    events_per_minute = key_event_count / match_duration_minutes

    # 归一化：假设5 events/min为高密度对局（满分1.0）
    normalized = min(events_per_minute / 5.0, 1.0)
    return float(normalized)


def _compute_narration_density(
    classified_cues: list[ClassifiedCue], match_duration_seconds: float
) -> float:
    """计算字幕密度：有效字幕覆盖率。

    Returns:
        覆盖率 [0, 1]
    """
    if match_duration_seconds <= 0:
        return 0.0

    # 统计非low_value字幕的总时长
    effective_subtitle_duration = 0.0
    for cue in classified_cues:
        if cue.category != "low_value":
            effective_subtitle_duration += cue.ended_at_seconds - cue.started_at_seconds

    coverage = effective_subtitle_duration / match_duration_seconds
    return float(min(coverage, 1.0))


def _compute_content_density_score(
    highlight_event_density: float,
    narration_density: float,
    visual_activity: float,
    weight_highlight_events: float,
    weight_narration: float,
    weight_visual: float,
    weight_baseline: float,
) -> float:
    """加权计算内容密度评分。

    Returns:
        评分 [0, 1]
    """
    score = (
        weight_highlight_events * highlight_event_density
        + weight_narration * narration_density
        + weight_visual * visual_activity
        + weight_baseline * 1.0  # 基线活跃度始终为1.0
    )
    return float(min(score, 1.0))


def _map_target_duration(
    score: float,
    high_threshold: float,
    low_threshold: float,
    high_range: tuple[int, int],
    mid_range: tuple[int, int],
    low_range: tuple[int, int],
) -> float:
    """根据评分映射目标时长。

    Args:
        score: 内容密度评分 [0, 1]
        high_threshold: 高密度阈值
        low_threshold: 低密度阈值
        high_range: 高密度时长范围（分钟）
        mid_range: 中密度时长范围（分钟）
        low_range: 低密度时长范围（分钟）

    Returns:
        目标时长（秒）
    """
    if score > high_threshold:
        # 高密度对局：20-25分钟
        min_min, max_min = high_range
        # 线性插值
        ratio = (score - high_threshold) / (1.0 - high_threshold)
        target_minutes = min_min + ratio * (max_min - min_min)
    elif score > low_threshold:
        # 中密度对局：12-18分钟
        min_min, max_min = mid_range
        ratio = (score - low_threshold) / (high_threshold - low_threshold)
        target_minutes = min_min + ratio * (max_min - min_min)
    else:
        # 低密度对局：6-10分钟
        min_min, max_min = low_range
        ratio = score / low_threshold if low_threshold > 0 else 0.0
        target_minutes = min_min + ratio * (max_min - min_min)

    return float(target_minutes * 60.0)
