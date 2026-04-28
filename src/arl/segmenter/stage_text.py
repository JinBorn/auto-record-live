from __future__ import annotations

import json
from pathlib import Path

from arl.shared.contracts import MatchStage
from arl.shared.logging import log

StageKeywordMap = dict[MatchStage, tuple[str, ...]]

_DEFAULT_STAGE_KEYWORDS: StageKeywordMap = {
    MatchStage.CHAMPION_SELECT: (
        "champion select",
        "championselect",
        "draft",
        "pick",
        "ban",
        "banpick",
        "ban pick",
        "bp",
        "英雄选择",
        "选人",
        "禁选",
        "禁用",
        "ban位",
        "bp阶段",
    ),
    MatchStage.LOADING: (
        "loading",
        "game loading",
        "connecting",
        "ready check",
        "加载中",
        "正在加载",
        "连接中",
        "准备就绪",
        "进入游戏",
    ),
    MatchStage.IN_GAME: (
        "in game",
        "minion",
        "kill",
        "dragon",
        "baron",
        "tower",
        "scoreboard",
        "对局中",
        "游戏中",
        "小兵",
        "击杀",
        "人头",
        "补刀",
        "推塔",
        "小龙",
        "大龙",
        "团战",
        "经济",
        "比分",
        "峡谷",
    ),
    MatchStage.POST_GAME: (
        "victory",
        "defeat",
        "game over",
        "post game",
        "mvp",
        "胜利",
        "失败",
        "结算",
        "对局结束",
        "比赛结束",
        "游戏结束",
    ),
}

_CLASSIFY_ORDER = (
    MatchStage.CHAMPION_SELECT,
    MatchStage.LOADING,
    MatchStage.IN_GAME,
    MatchStage.POST_GAME,
)


def _normalize_stage_text(text: str) -> str:
    normalized = text.lower().strip()
    for token in ("_", "-", "/", "|"):
        normalized = normalized.replace(token, " ")
    return " ".join(normalized.split())


def default_stage_keywords() -> StageKeywordMap:
    return {
        stage: tuple(keywords)
        for stage, keywords in _DEFAULT_STAGE_KEYWORDS.items()
    }


def load_stage_keywords(path: Path | None, component: str = "segmenter") -> StageKeywordMap:
    keywords = default_stage_keywords()
    if path is None:
        return keywords
    if not path.exists():
        log(
            component,
            f"stage-keywords override path missing path={path}; use built-in defaults",
        )
        return keywords

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        log(
            component,
            f"stage-keywords override read failed path={path} reason={exc}; use built-in defaults",
        )
        return keywords
    except json.JSONDecodeError as exc:
        log(
            component,
            f"stage-keywords override invalid json path={path} reason={exc}; use built-in defaults",
        )
        return keywords
    if not isinstance(payload, dict):
        log(
            component,
            f"stage-keywords override root must be object path={path}; use built-in defaults",
        )
        return keywords

    overridden_stages: list[str] = []
    for stage in _CLASSIFY_ORDER:
        raw_keywords = payload.get(stage.value)
        if raw_keywords is None:
            continue
        if not isinstance(raw_keywords, list):
            log(
                component,
                (
                    "stage-keywords override invalid stage entry "
                    f"stage={stage.value} expected=list path={path}; keep default stage keywords"
                ),
            )
            continue
        normalized = tuple(
            normalized_keyword
            for item in raw_keywords
            for normalized_keyword in [_normalize_stage_text(str(item))]
            if normalized_keyword
        )
        if normalized:
            keywords[stage] = normalized
            overridden_stages.append(stage.value)
            continue
        log(
            component,
            (
                "stage-keywords override empty stage list "
                f"stage={stage.value} path={path}; keep default stage keywords"
            ),
        )
    if overridden_stages:
        log(
            component,
            "stage-keywords override loaded "
            f"path={path} stages={','.join(overridden_stages)}",
        )
    return keywords


def classify_stage_from_text(
    text: str,
    stage_keywords: StageKeywordMap | None = None,
) -> MatchStage | None:
    normalized = _normalize_stage_text(text)
    keywords = stage_keywords or _DEFAULT_STAGE_KEYWORDS

    for stage in _CLASSIFY_ORDER:
        stage_tokens = keywords.get(stage, ())
        if any(_normalize_stage_text(token) in normalized for token in stage_tokens):
            return stage
    return None
