"""窗口优化器：为condensed模式生成、合并、优化时间窗口。

流程：
Phase 1: 生成初始窗口（基于分类后的cues）
Phase 3: 窗口合并、剔除、padding
Phase 4: 时长控制（按优先级削减/恢复）
Phase 5: 质量检查（关键事件完整性）
"""

from __future__ import annotations

from arl.highlights.models import ClassifiedCue, WindowDraft
from arl.shared.contracts import HighlightClipWindow
from arl.shared.logging import log


def optimize_windows(
    classified_cues: list[ClassifiedCue],
    target_duration_seconds: float,
    match_duration_seconds: float,
    context_padding_seconds: float = 5.0,
    merge_gap_seconds: float = 8.0,
    min_window_duration_seconds: float = 3.0,
    boring_gap_threshold_seconds: float = 45.0,
    edge_context_seconds: float = 0.0,
    start_edge_context_seconds: float | None = None,
    bridge_window_seconds: float | None = None,
    max_continuous_window_seconds: float | None = None,
) -> list[HighlightClipWindow]:
    """优化窗口生成condensed plan。

    Args:
        classified_cues: 已分类的字幕cue列表
        target_duration_seconds: 目标时长（秒）
        match_duration_seconds: 对局总时长（秒）
        context_padding_seconds: 上下文padding
        merge_gap_seconds: 合并gap阈值
        min_window_duration_seconds: 最小窗口时长
        boring_gap_threshold_seconds: 无聊gap阈值

    Returns:
        优化后的HighlightClipWindow列表
    """
    log(
        "highlights",
        f"window_optimizer: starting with {len(classified_cues)} cues, "
        f"target_duration={target_duration_seconds:.1f}s ({target_duration_seconds / 60:.1f}min)"
    )

    # Phase 1: 生成初始窗口
    drafts = _generate_initial_windows(classified_cues, context_padding_seconds)
    log("highlights", f"window_optimizer: Phase 1 generated {len(drafts)} initial windows")

    # Phase 3: 窗口优化
    drafts = _merge_windows(drafts, merge_gap_seconds)
    log("highlights", f"window_optimizer: Phase 3.1 merged to {len(drafts)} windows")

    drafts = _remove_short_windows(drafts, min_window_duration_seconds)
    log("highlights", f"window_optimizer: Phase 3.2 removed short windows, {len(drafts)} remain")

    drafts = _add_context_padding(drafts, context_padding_seconds, match_duration_seconds)
    log("highlights", f"window_optimizer: Phase 3.3 added context padding")

    drafts = _remove_boring_gaps(drafts, boring_gap_threshold_seconds)
    log("highlights", f"window_optimizer: Phase 3.4 removed boring gaps, {len(drafts)} windows")

    # Phase 4: 时长控制
    drafts = _adjust_to_target_duration(
        drafts, target_duration_seconds, boring_gap_threshold_seconds, match_duration_seconds
    )
    drafts = _ensure_key_events_preserved(
        drafts,
        classified_cues,
        context_padding_seconds,
        match_duration_seconds,
    )
    drafts = _clamp_windows_to_match(drafts, match_duration_seconds)
    drafts = _collapse_large_gaps(
        drafts,
        max_gap_seconds=boring_gap_threshold_seconds,
        max_continuous_window_seconds=max_continuous_window_seconds,
        match_duration_seconds=match_duration_seconds,
        target_duration_seconds=target_duration_seconds,
    )
    drafts, relaxed_key_event_preservation = _trim_full_span_content_window(
        drafts,
        classified_cues=classified_cues,
        target_duration_seconds=target_duration_seconds,
        match_duration_seconds=match_duration_seconds,
        edge_context_seconds=edge_context_seconds,
        start_edge_context_seconds=start_edge_context_seconds,
    )
    if not drafts:
        return []
    drafts = _ensure_edge_context_preserved(
        drafts,
        edge_context_seconds=edge_context_seconds,
        start_edge_context_seconds=start_edge_context_seconds,
        match_duration=match_duration_seconds,
    )
    drafts = _clamp_windows_to_match(drafts, match_duration_seconds)
    drafts = _bridge_large_gaps(
        drafts,
        max_gap_seconds=boring_gap_threshold_seconds,
        bridge_window_seconds=(
            edge_context_seconds
            if bridge_window_seconds is None
            else bridge_window_seconds
        ),
        match_duration=match_duration_seconds,
    )
    current_duration = sum(d.ended_at_seconds - d.started_at_seconds for d in drafts)
    log(
        "highlights",
        f"window_optimizer: Phase 4 adjusted to {current_duration:.1f}s "
        f"({current_duration / 60:.1f}min), {len(drafts)} windows"
    )

    # Phase 5: 质量检查
    if not drafts:
        return []
    if not relaxed_key_event_preservation:
        _validate_key_events_preserved(drafts, classified_cues)
    log("highlights", f"window_optimizer: Phase 5 quality check passed")

    # 转换为HighlightClipWindow
    windows = [
        HighlightClipWindow(
            started_at_seconds=d.started_at_seconds,
            ended_at_seconds=d.ended_at_seconds,
            reason=d.reason,
        )
        for d in drafts
    ]

    return windows


def bridge_highlight_windows(
    windows: list[HighlightClipWindow],
    *,
    max_gap_seconds: float = 45.0,
    bridge_window_seconds: float = 3.0,
    match_duration: float,
) -> list[HighlightClipWindow]:
    drafts = [
        WindowDraft(
            started_at_seconds=window.started_at_seconds,
            ended_at_seconds=window.ended_at_seconds,
            reason=window.reason,
            priority=_priority_for_reason(window.reason),
        )
        for window in windows
    ]
    bridged = _bridge_large_gaps(
        drafts,
        max_gap_seconds=max_gap_seconds,
        bridge_window_seconds=bridge_window_seconds,
        match_duration=match_duration,
    )
    return [
        HighlightClipWindow(
            started_at_seconds=draft.started_at_seconds,
            ended_at_seconds=draft.ended_at_seconds,
            reason=draft.reason,
        )
        for draft in bridged
    ]


def _priority_for_reason(reason: str) -> float:
    priority_map = {
        "highlight_keyword": 1.0,
        "condensed_key_event": 1.0,
        "condensed_tactical": 0.7,
        "condensed_context": 0.4,
        "condensed_match_context": 0.3,
        "match_start_context": 0.3,
        "match_end_context": 0.3,
        "condensed_continuity": 0.2,
    }
    return priority_map.get(reason, 0.2)


def _trim_full_span_content_window(
    drafts: list[WindowDraft],
    *,
    classified_cues: list[ClassifiedCue],
    target_duration_seconds: float,
    match_duration_seconds: float,
    edge_context_seconds: float,
    start_edge_context_seconds: float | None,
) -> tuple[list[WindowDraft], bool]:
    if len(drafts) != 1 or match_duration_seconds <= 0.0:
        return drafts, False

    draft = drafts[0]
    if not _draft_nearly_covers_match(draft, match_duration_seconds):
        return drafts, False

    start_edge = (
        edge_context_seconds
        if start_edge_context_seconds is None
        else start_edge_context_seconds
    )
    edge_budget = min(
        max(start_edge, 0.0) + max(edge_context_seconds, 0.0),
        match_duration_seconds,
    )
    content_target = min(
        target_duration_seconds,
        max(0.0, match_duration_seconds - edge_budget),
    )
    if content_target <= 0.0 or content_target >= match_duration_seconds - 1.0:
        return drafts, False

    cue_candidates = [
        cue for cue in classified_cues if cue.category != "low_value"
    ]
    if not cue_candidates:
        return drafts, False

    selected = _best_continuous_content_window(
        cue_candidates,
        window_duration=content_target,
        match_duration=match_duration_seconds,
    )
    if selected is None:
        return drafts, False

    log(
        "highlights",
        "window_optimizer: trimmed full-span condensed content "
        f"from {match_duration_seconds:.1f}s to {content_target:.1f}s",
    )
    return [selected], True


def _draft_nearly_covers_match(draft: WindowDraft, match_duration: float) -> bool:
    return draft.started_at_seconds <= 1.0 and draft.ended_at_seconds >= match_duration - 1.0


def _best_continuous_content_window(
    cues: list[ClassifiedCue],
    *,
    window_duration: float,
    match_duration: float,
) -> WindowDraft | None:
    if window_duration <= 0.0 or match_duration <= 0.0:
        return None

    window_duration = min(window_duration, match_duration)
    latest_start = max(0.0, match_duration - window_duration)
    best_start = 0.0
    best_score = -1.0
    for cue in cues:
        center = (cue.started_at_seconds + cue.ended_at_seconds) / 2.0
        start = min(max(0.0, center - window_duration / 2.0), latest_start)
        end = start + window_duration
        score = _score_window(cues, start, end)
        # Later windows are usually more decisive when density ties.
        score += start * 0.000001
        if score > best_score:
            best_score = score
            best_start = start

    if best_score <= 0.0:
        return None

    best_end = min(match_duration, best_start + window_duration)
    covered = [
        cue for cue in cues if _cue_overlaps_window(cue, best_start, best_end)
    ]
    if not covered:
        return None

    highest = max(covered, key=lambda cue: cue.priority)
    return WindowDraft(
        started_at_seconds=best_start,
        ended_at_seconds=best_end,
        reason=_reason_for_category(highest.category),
        priority=highest.priority,
    )


def _score_window(cues: list[ClassifiedCue], start: float, end: float) -> float:
    score = 0.0
    for cue in cues:
        if _cue_overlaps_window(cue, start, end):
            score += cue.priority
    return score


def _cue_overlaps_window(cue: ClassifiedCue, start: float, end: float) -> bool:
    return min(end, cue.ended_at_seconds) > max(start, cue.started_at_seconds)


def _reason_for_category(category: str) -> str:
    reason_map = {
        "key_event": "condensed_key_event",
        "tactical": "condensed_tactical",
        "narration": "condensed_context",
    }
    return reason_map.get(category, "condensed_context")


def _generate_edge_context_windows(
    edge_context_seconds: float,
    match_duration: float,
    start_edge_context_seconds: float | None = None,
) -> list[WindowDraft]:
    if match_duration <= 0.0:
        return []

    start_edge = (
        edge_context_seconds
        if start_edge_context_seconds is None
        else start_edge_context_seconds
    )
    start_edge = min(max(start_edge, 0.0), match_duration)
    end_edge = min(max(edge_context_seconds, 0.0), match_duration)
    if start_edge <= 0.0 and end_edge <= 0.0:
        return []

    windows: list[WindowDraft] = []
    if start_edge > 0.0:
        windows.append(
            WindowDraft(
                started_at_seconds=0.0,
                ended_at_seconds=min(start_edge, match_duration),
                reason="condensed_match_context",
                priority=1.0,
            )
        )
    if end_edge > 0.0:
        windows.append(
            WindowDraft(
                started_at_seconds=max(0.0, match_duration - end_edge),
                ended_at_seconds=match_duration,
                reason="condensed_match_context",
                priority=1.0,
            )
        )
    return windows


def _ensure_edge_context_preserved(
    drafts: list[WindowDraft],
    *,
    edge_context_seconds: float,
    start_edge_context_seconds: float | None = None,
    match_duration: float,
) -> list[WindowDraft]:
    if match_duration <= 0.0:
        return drafts

    edge_windows = _generate_edge_context_windows(
        edge_context_seconds,
        match_duration,
        start_edge_context_seconds=start_edge_context_seconds,
    )
    if not edge_windows:
        return drafts

    needed: list[WindowDraft] = []
    start_window = next(
        (window for window in edge_windows if window.started_at_seconds <= 0.001),
        None,
    )
    end_window = next(
        (
            window
            for window in edge_windows
            if window.ended_at_seconds >= match_duration - 0.001
        ),
        None,
    )
    if start_window is not None and not any(
        draft.started_at_seconds <= 0.001 for draft in drafts
    ):
        needed.append(start_window)
    if end_window is not None and not any(
        draft.ended_at_seconds >= match_duration - 0.001 for draft in drafts
    ):
        needed.append(end_window)

    if not needed:
        return drafts

    return _merge_windows(drafts + needed, merge_gap=0.0)


def _bridge_large_gaps(
    drafts: list[WindowDraft],
    *,
    max_gap_seconds: float,
    bridge_window_seconds: float,
    match_duration: float,
) -> list[WindowDraft]:
    """Insert continuity snippets plus a lead-in before the next kept segment."""
    if (
        len(drafts) <= 1
        or max_gap_seconds <= 0.0
        or bridge_window_seconds <= 0.0
        or match_duration <= 0.0
    ):
        return drafts

    bridge_duration = min(
        max(bridge_window_seconds, 2.0),
        max_gap_seconds / 2.0,
    )
    progression_duration = min(5.0, bridge_duration)
    ordered = sorted(drafts, key=lambda draft: draft.started_at_seconds)
    bridged: list[WindowDraft] = []
    inserted = 0

    for current in ordered:
        if not bridged:
            bridged.append(current)
            continue

        previous = bridged[-1]
        gap = current.started_at_seconds - previous.ended_at_seconds
        if gap <= max_gap_seconds:
            bridged.append(current)
            continue

        bridge_end = current.started_at_seconds
        bridge_start = max(previous.ended_at_seconds, bridge_end - bridge_duration)
        cursor_end = previous.ended_at_seconds

        while bridge_start - cursor_end > max_gap_seconds:
            progress_start = min(
                cursor_end + max_gap_seconds,
                bridge_start - progression_duration,
            )
            progress_start = max(cursor_end, progress_start)
            progress_end = min(bridge_start, progress_start + progression_duration)
            if progress_end <= progress_start:
                break
            bridged.append(
                WindowDraft(
                    started_at_seconds=max(0.0, progress_start),
                    ended_at_seconds=min(match_duration, progress_end),
                    reason="condensed_continuity",
                    priority=0.2,
                )
            )
            cursor_end = progress_end
            inserted += 1

        if bridge_end > bridge_start:
            bridged.append(
                WindowDraft(
                    started_at_seconds=max(0.0, bridge_start),
                    ended_at_seconds=min(match_duration, bridge_end),
                    reason="condensed_continuity",
                    priority=0.2,
                )
            )
            inserted += 1

        bridged.append(current)

    if inserted:
        log(
            "highlights",
            "window_optimizer: inserted continuity bridge windows "
            f"count={inserted} max_gap={max_gap_seconds:.1f}s",
        )

    return _merge_windows(bridged, merge_gap=0.0)


def _collapse_large_gaps(
    drafts: list[WindowDraft],
    *,
    max_gap_seconds: float,
    max_continuous_window_seconds: float | None,
    match_duration_seconds: float,
    target_duration_seconds: float,
) -> list[WindowDraft]:
    """Prefer one continuous condensed span over abrupt source-time jumps."""
    if len(drafts) <= 1 or max_continuous_window_seconds is None:
        return drafts

    ordered = sorted(drafts, key=lambda draft: draft.started_at_seconds)
    largest_gap = max(
        current.started_at_seconds - previous.ended_at_seconds
        for previous, current in zip(ordered, ordered[1:])
    )
    if largest_gap <= max_gap_seconds:
        return ordered

    start = ordered[0].started_at_seconds
    end = ordered[-1].ended_at_seconds
    span = end - start
    key_event_count = sum(1 for draft in ordered if draft.reason == "condensed_key_event")
    if (
        match_duration_seconds > 0.0
        and start <= 1.0
        and end >= match_duration_seconds - 1.0
        and key_event_count > 0
    ):
        log(
            "highlights",
            "window_optimizer: preserving near-full-span key-event windows "
            f"for bridge pass largest_gap={largest_gap:.1f}s",
        )
        return ordered

    if (
        key_event_count >= 3
        and target_duration_seconds > 0.0
        and span > target_duration_seconds * 1.5
    ):
        log(
            "highlights",
            "window_optimizer: preserving dispersed key-event windows "
            f"for bridge pass key_events={key_event_count} span={span:.1f}s "
            f"target={target_duration_seconds:.1f}s",
        )
        return ordered

    if span > max_continuous_window_seconds:
        log(
            "highlights",
            "window_optimizer: preserving discontinuous windows for bridge pass "
            f"largest_gap={largest_gap:.1f}s continuous_span={span:.1f}s "
            f"max_continuous={max_continuous_window_seconds:.1f}s",
        )
        return ordered

    highest = max(ordered, key=lambda draft: draft.priority)
    log(
        "highlights",
        "window_optimizer: collapsed discontinuous windows into continuous span "
        f"largest_gap={largest_gap:.1f}s span={span:.1f}s",
    )
    return [
        WindowDraft(
            started_at_seconds=start,
            ended_at_seconds=end,
            reason=highest.reason,
            priority=highest.priority,
        )
    ]


def _generate_initial_windows(
    classified_cues: list[ClassifiedCue], context_padding: float
) -> list[WindowDraft]:
    """Phase 1: 为重要cue生成初始窗口。"""
    drafts = []

    for cue in classified_cues:
        if cue.category == "low_value":
            continue

        # 为每个重要cue生成窗口（cue时间 ± padding）
        start = max(0.0, cue.started_at_seconds - context_padding)
        end = cue.ended_at_seconds + context_padding

        drafts.append(
            WindowDraft(
                started_at_seconds=start,
                ended_at_seconds=end,
                reason=_reason_for_category(cue.category),
                priority=cue.priority,
            )
        )

    return drafts


def _merge_windows(drafts: list[WindowDraft], merge_gap: float) -> list[WindowDraft]:
    """Phase 3.1: 合并相邻窗口（gap < merge_gap）。"""
    if not drafts:
        return []

    # 按开始时间排序
    sorted_drafts = sorted(drafts, key=lambda d: d.started_at_seconds)
    merged = [sorted_drafts[0]]

    for current in sorted_drafts[1:]:
        last = merged[-1]
        gap = current.started_at_seconds - last.ended_at_seconds

        if gap <= merge_gap:
            # 合并：取较高优先级的reason和priority
            if current.priority > last.priority:
                reason = current.reason
                priority = current.priority
            else:
                reason = last.reason
                priority = last.priority

            merged[-1] = WindowDraft(
                started_at_seconds=last.started_at_seconds,
                ended_at_seconds=current.ended_at_seconds,
                reason=reason,
                priority=priority,
            )
        else:
            merged.append(current)

    return merged


def _remove_short_windows(
    drafts: list[WindowDraft], min_duration: float
) -> list[WindowDraft]:
    """Phase 3.2: 剔除过短窗口。"""
    return [
        d
        for d in drafts
        if (d.ended_at_seconds - d.started_at_seconds) >= min_duration
    ]


def _add_context_padding(
    drafts: list[WindowDraft], padding: float, match_duration: float
) -> list[WindowDraft]:
    """Phase 3.3: 为关键事件添加额外padding。"""
    padded = []
    for d in drafts:
        if d.reason == "condensed_key_event":
            # 关键事件额外扩展
            start = max(0.0, d.started_at_seconds - padding)
            end = min(match_duration, d.ended_at_seconds + padding)
            padded.append(
                WindowDraft(
                    started_at_seconds=start,
                    ended_at_seconds=end,
                    reason=d.reason,
                    priority=d.priority,
                )
            )
        else:
            padded.append(d)

    # 再次合并（padding可能导致重叠）
    return _merge_windows(padded, merge_gap=0.0)


def _remove_boring_gaps(
    drafts: list[WindowDraft], boring_gap_threshold: float
) -> list[WindowDraft]:
    """Phase 3.4: 剔除boring gaps（相邻窗口gap过大时，视为两段独立内容）。

    注意：这里不是删除窗口，而是标记gap位置，实际上保留所有窗口。
    真正的gap处理在exporter拼接时体现。
    """
    # 这里保持窗口不变，boring gap的处理通过窗口之间的自然间隔体现
    # Exporter会按照windows列表裁切并拼接，自动跳过gap
    return drafts


def _adjust_to_target_duration(
    drafts: list[WindowDraft],
    target_duration: float,
    boring_gap_threshold: float,
    match_duration: float,
) -> list[WindowDraft]:
    """Phase 4: 时长控制（按优先级削减/恢复）。"""
    current_duration = sum(d.ended_at_seconds - d.started_at_seconds for d in drafts)

    if current_duration <= target_duration:
        # 低于目标时长，尝试恢复部分gap内容
        return _restore_gaps_if_needed(drafts, target_duration, current_duration)

    # 超出目标时长，按优先级削减
    return _reduce_by_priority(drafts, target_duration)


def _restore_gaps_if_needed(
    drafts: list[WindowDraft], target_duration: float, current_duration: float
) -> list[WindowDraft]:
    """Phase 4.1: 放宽boring_gap_threshold恢复部分内容。

    简化策略：如果当前时长低于目标下限，保持不变（避免过度填充无聊内容）。
    """
    # MVP实现：不主动恢复gap，保持conservative策略
    return drafts


def _reduce_by_priority(
    drafts: list[WindowDraft], target_duration: float
) -> list[WindowDraft]:
    """Phase 4.2: 按优先级削减窗口。"""
    # 按优先级排序（低优先级在前）
    sorted_drafts = sorted(drafts, key=lambda d: d.priority)

    retained = []
    retained_duration = 0.0

    # 从高优先级开始保留
    for d in reversed(sorted_drafts):
        duration = d.ended_at_seconds - d.started_at_seconds
        if retained_duration + duration <= target_duration:
            retained.append(d)
            retained_duration += duration

    # 恢复时间顺序
    retained.sort(key=lambda d: d.started_at_seconds)
    return retained


def _ensure_key_events_preserved(
    drafts: list[WindowDraft],
    classified_cues: list[ClassifiedCue],
    context_padding: float,
    match_duration: float,
) -> list[WindowDraft]:
    key_event_cues = [cue for cue in classified_cues if cue.category == "key_event"]
    if not key_event_cues:
        return drafts

    restored: list[WindowDraft] = []
    for cue in key_event_cues:
        if _cue_is_covered(cue, drafts):
            continue
        restored.append(
            WindowDraft(
                started_at_seconds=max(0.0, cue.started_at_seconds - context_padding),
                ended_at_seconds=min(match_duration, cue.ended_at_seconds + context_padding),
                reason="condensed_key_event",
                priority=cue.priority,
            )
        )

    if not restored:
        return drafts

    log(
        "highlights",
        f"window_optimizer: restored {len(restored)} missing key-event windows",
    )
    return _merge_windows(drafts + restored, merge_gap=0.0)


def _clamp_windows_to_match(
    drafts: list[WindowDraft], match_duration: float
) -> list[WindowDraft]:
    clamped: list[WindowDraft] = []
    for draft in drafts:
        start = max(0.0, min(match_duration, draft.started_at_seconds))
        end = max(0.0, min(match_duration, draft.ended_at_seconds))
        if end <= start:
            continue
        clamped.append(
            WindowDraft(
                started_at_seconds=start,
                ended_at_seconds=end,
                reason=draft.reason,
                priority=draft.priority,
            )
        )
    return clamped


def _cue_is_covered(cue: ClassifiedCue, drafts: list[WindowDraft]) -> bool:
    return any(
        d.started_at_seconds <= cue.started_at_seconds <= d.ended_at_seconds
        or d.started_at_seconds <= cue.ended_at_seconds <= d.ended_at_seconds
        for d in drafts
    )


def _validate_key_events_preserved(
    drafts: list[WindowDraft], classified_cues: list[ClassifiedCue]
) -> None:
    """Phase 5: 质量检查 - 确保关键事件被保留。"""
    key_event_cues = [c for c in classified_cues if c.category == "key_event"]
    if not key_event_cues:
        return

    missing_events = []
    for cue in key_event_cues:
        # 检查cue是否在任何window内
        covered = any(
            d.started_at_seconds <= cue.started_at_seconds <= d.ended_at_seconds
            or d.started_at_seconds <= cue.ended_at_seconds <= d.ended_at_seconds
            for d in drafts
        )
        if not covered:
            missing_events.append(cue.text[:30])

    if missing_events:
        log(
            "highlights",
            f"window_optimizer: WARNING - {len(missing_events)} key events not covered: "
            f"{missing_events[:3]}...",
        )
