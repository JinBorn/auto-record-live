"""字幕分类器：为condensed模式分类字幕cue。

根据内容将字幕分类为：
- key_event: 包含关键事件关键词（击杀、团战、大龙等）
- tactical: 包含战术术语（闪现、推线、视野等）
- narration: 有效叙述（不含关键词但有实质内容）
- low_value: 低价值对话（重复、语气词、闲聊等）
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from arl.highlights.models import ClassifiedCue


def classify_cues(
    cues: list[tuple[float, float, str]],
    highlight_keywords: tuple[str, ...],
    tactical_keywords: tuple[str, ...],
    low_value_min_length: int = 3,
    low_value_similarity_threshold: float = 0.8,
    low_value_repeat_window_seconds: float = 30.0,
) -> list[ClassifiedCue]:
    """分类字幕cue列表。

    Args:
        cues: 元组列表 (started_at_seconds, ended_at_seconds, text)
        highlight_keywords: 关键事件关键词集合
        tactical_keywords: 战术术语关键词集合
        low_value_min_length: 最小有效长度
        low_value_similarity_threshold: 重复判定相似度阈值
        low_value_repeat_window_seconds: 重复检测时间窗口

    Returns:
        ClassifiedCue列表，每个包含category和priority
    """
    classified = []

    for i, (start, end, text) in enumerate(cues):
        normalized = _normalize_text(text)

        # 优先级顺序：key_event > tactical > low_value > narration
        if _has_highlight_keyword(normalized, highlight_keywords):
            category = "key_event"
            priority = 1.0
        elif _has_tactical_keyword(normalized, tactical_keywords):
            category = "tactical"
            priority = 0.7
        elif _is_low_value(
            normalized,
            cues,
            i,
            low_value_min_length,
            low_value_similarity_threshold,
            low_value_repeat_window_seconds,
        ):
            category = "low_value"
            priority = 0.0
        else:
            category = "narration"
            priority = 0.4

        classified.append(
            ClassifiedCue(
                started_at_seconds=start,
                ended_at_seconds=end,
                text=text,
                category=category,
                priority=priority,
            )
        )

    return classified


def _normalize_text(text: str) -> str:
    """归一化文本：移除标点、转小写、保留中文和英文字符。"""
    # 移除标点和特殊字符，保留中文、英文、数字
    text = re.sub(r"[^\w\s一-鿿]", " ", text, flags=re.UNICODE)
    # 转小写（仅影响英文）
    text = text.lower()
    # 压缩多余空格
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_highlight_keyword(normalized_text: str, keywords: tuple[str, ...]) -> bool:
    """检查是否包含关键事件关键词。"""
    return any(kw in normalized_text for kw in keywords)


def _has_tactical_keyword(normalized_text: str, keywords: tuple[str, ...]) -> bool:
    """检查是否包含战术术语。"""
    return any(kw in normalized_text for kw in keywords)


def _is_low_value(
    normalized_text: str,
    all_cues: list[tuple[float, float, str]],
    current_index: int,
    min_length: int,
    similarity_threshold: float,
    repeat_window_seconds: float,
) -> bool:
    """判断是否为低价值对话。

    低价值对话满足以下任一条件：
    1. 去除标点后字符数 < min_length
    2. 与时间窗口内其他字幕高度相似（重复）
    3. 90%以上为语气词
    4. 包含闲聊话题词
    """
    # 1. 长度检查
    if len(normalized_text.replace(" ", "")) < min_length:
        return True

    # 2. 重复率检查（在时间窗口内查找相似字幕）
    current_time = all_cues[current_index][0]
    for i, (start, end, text) in enumerate(all_cues):
        if i == current_index:
            continue
        if abs(start - current_time) > repeat_window_seconds:
            continue

        other_normalized = _normalize_text(text)
        similarity = SequenceMatcher(None, normalized_text, other_normalized).ratio()
        if similarity >= similarity_threshold:
            return True

    # 3. 语气词占比检查
    filler_words = {
        "啊",
        "哦",
        "嗯",
        "呃",
        "哎",
        "哇",
        "嘿",
        "卧槽",
        "卧草",
        "我去",
        "我靠",
        "哈哈",
        "呵呵",
        "嘿嘿",
        "uh",
        "um",
        "ah",
        "oh",
        "wow",
        "haha",
        "hehe",
    }
    words = normalized_text.split()
    if words:
        filler_count = sum(1 for w in words if w in filler_words)
        if filler_count / len(words) >= 0.9:
            return True

    # 4. 闲聊话题词检查
    casual_keywords = {
        "吃饭",
        "睡觉",
        "明天",
        "昨天",
        "今天",
        "天气",
        "累了",
        "困了",
        "饿了",
        "渴了",
        "上厕所",
        "喝水",
        "吃东西",
        "休息",
        "eating",
        "sleeping",
        "tomorrow",
        "yesterday",
        "tired",
        "hungry",
        "thirsty",
        "bathroom",
        "rest",
    }
    if any(kw in normalized_text for kw in casual_keywords):
        return True

    return False
