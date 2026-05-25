"""OCR prototype: emit MatchStageHint jsonl by running OCR over recording frames.

Research-only; no arl.* imports. Vendors classify_stage_from_text + keyword map
from src/arl/segmenter/stage_text.py (keep in sync). Output mirrors
arl.segmenter.models.MatchStageHint: {session_id, stage, at_seconds}. Consumable
by eval.py.

Two OCR engines are supported via --ocr-engine:
  - paddle (default): PaddleOCR; requires paddlepaddle backend (no wheels for Python 3.14 yet).
  - tesseract: pytesseract; requires tesseract.exe + chi_sim.traineddata on disk.
PRD R4 explicitly allows either engine.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import NamedTuple


STAGE_ORDER = ("champion_select", "loading", "in_game", "post_game")
DEFAULT_SAMPLE_FPS = 0.5
DEFAULT_DEDUP_WINDOW = 5.0


# ---- vendored from src/arl/segmenter/stage_text.py — keep in sync ----
_VENDORED_KEYWORDS: dict[str, tuple[str, ...]] = {
    "champion_select": (
        "champion select", "championselect", "draft", "pick", "ban",
        "banpick", "ban pick", "bp",
        "英雄选择", "选人", "禁选", "禁用", "ban位", "bp阶段",
    ),
    "loading": (
        "loading", "game loading", "connecting", "ready check",
        "加载中", "正在加载", "连接中", "准备就绪", "进入游戏",
    ),
    "in_game": (
        "in game", "minion", "kill", "dragon", "baron", "tower", "scoreboard",
        "对局中", "游戏中", "小兵", "击杀", "人头", "补刀", "推塔",
        "小龙", "大龙", "团战", "经济", "比分", "峡谷",
    ),
    "post_game": (
        "victory", "defeat", "game over", "post game", "mvp",
        "胜利", "失败", "结算", "对局结束", "比赛结束", "游戏结束",
    ),
}


def _normalize_stage_text(text: str) -> str:
    normalized = text.lower().strip()
    for token in ("_", "-", "/", "|"):
        normalized = normalized.replace(token, " ")
    return " ".join(normalized.split())


def _classify_stage_from_text(text: str) -> str | None:
    normalized = _normalize_stage_text(text)
    for stage in STAGE_ORDER:
        for token in _VENDORED_KEYWORDS.get(stage, ()):
            if _normalize_stage_text(token) in normalized:
                return stage
    return None


# ---- end vendored block ----


class Candidate(NamedTuple):
    stage: str
    at_seconds: float


def _dedup(candidates: list[Candidate], window: float) -> list[Candidate]:
    """Per-stage: collapse candidates within `window` seconds to first occurrence."""
    kept: list[Candidate] = []
    for stage in STAGE_ORDER:
        stage_cands = sorted(
            [c for c in candidates if c.stage == stage],
            key=lambda c: c.at_seconds,
        )
        last_kept_t = -1e9
        for c in stage_cands:
            if c.at_seconds - last_kept_t > window:
                kept.append(c)
                last_kept_t = c.at_seconds
    kept.sort(key=lambda c: c.at_seconds)
    return kept


def _parse_roi(roi_str: str | None) -> tuple[int, int, int, int] | None:
    if not roi_str:
        return None
    parts = roi_str.split(",")
    if len(parts) != 4:
        raise SystemExit(f"--roi must be x,y,w,h; got {roi_str!r}")
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError as exc:
        raise SystemExit(f"--roi values must be integers: {roi_str!r}") from exc
    if w <= 0 or h <= 0:
        raise SystemExit(f"--roi w,h must be positive: {roi_str!r}")
    return x, y, w, h


def _extract_text_fragments(ocr_results: object) -> list[str]:
    """Pull text strings out of PaddleOCR's nested return shape.

    PaddleOCR returns roughly [page][line] -> [bbox, (text, score)]; defensive
    against version drift.
    """
    fragments: list[str] = []
    if not isinstance(ocr_results, list):
        return fragments
    for page in ocr_results:
        if not isinstance(page, list):
            continue
        for line in page:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            text_score = line[1]
            if isinstance(text_score, (list, tuple)) and text_score:
                fragments.append(str(text_score[0]))
    return fragments


def _make_ocr_callable(args: argparse.Namespace):
    """Return a function `crop_bgr_ndarray -> joined_text_str` for the chosen engine."""
    engine = args.ocr_engine
    if engine == "paddle":
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]

        ocr = PaddleOCR(lang=args.ocr_lang, show_log=False)

        def call(crop):
            ocr_results = ocr.ocr(crop, cls=False)
            return " ".join(_extract_text_fragments(ocr_results))

        return call

    if engine == "tesseract":
        import cv2  # type: ignore[import-not-found]
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]

        if args.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd
        # Tesseract is fussy about path quoting on Windows; resolve --tessdata-dir
        # to an absolute path and export it via TESSDATA_PREFIX (the documented
        # env-var route) instead of injecting --tessdata-dir into the config
        # string, which fails when the path contains backslashes.
        import os
        if args.tessdata_dir:
            os.environ["TESSDATA_PREFIX"] = str(Path(args.tessdata_dir).resolve())
        config = ""
        # paddle uses "ch"; tesseract uses "chi_sim". Remap the common case so a
        # single --ocr-lang argument works across engines.
        lang = "chi_sim" if args.ocr_lang in ("ch", "zh") else args.ocr_lang

        def call(crop):
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            return pytesseract.image_to_string(pil, lang=lang, config=config)

        return call

    raise SystemExit(f"unknown --ocr-engine: {engine!r} (expected paddle|tesseract)")


def _run(args: argparse.Namespace) -> int:
    import cv2  # type: ignore[import-not-found]

    recording = Path(args.recording)
    output = Path(args.output)
    if not recording.exists():
        print(f"ERROR: recording not found: {recording}", file=sys.stderr)
        return 2

    roi = _parse_roi(args.roi)
    session_id = args.session_id or recording.parent.name

    ocr_call = _make_ocr_callable(args)

    cap = cv2.VideoCapture(str(recording))
    if not cap.isOpened():
        print(f"ERROR: cv2.VideoCapture failed: {recording}", file=sys.stderr)
        return 2
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_skip = max(1, int(round(src_fps / args.sample_fps)))

    candidates: list[Candidate] = []
    frame_index = 0
    sampled = 0
    sample_ms_total = 0.0
    wall_start = time.perf_counter()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % frame_skip == 0:
            t_sec = frame_index / src_fps
            t0 = time.perf_counter()
            if roi is not None:
                x, y, w, h = roi
                crop = frame[y:y + h, x:x + w]
            else:
                crop = frame
            joined = ocr_call(crop)
            stage = _classify_stage_from_text(joined)
            sample_ms_total += (time.perf_counter() - t0) * 1000.0
            sampled += 1
            if stage is not None:
                candidates.append(Candidate(stage, t_sec))
        frame_index += 1

    cap.release()
    wall_total = time.perf_counter() - wall_start
    deduped = _dedup(candidates, args.dedup_window_seconds)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for c in deduped:
            row = {
                "session_id": session_id,
                "stage": c.stage,
                "at_seconds": round(c.at_seconds, 3),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    per_stage_counts = {s: sum(1 for c in deduped if c.stage == s) for s in STAGE_ORDER}
    print(
        f"ocr summary: sampled={sampled} frames "
        f"src_fps={src_fps:.2f} skip={frame_skip} "
        f"ms_per_frame={sample_ms_total / max(sampled, 1):.1f} "
        f"wall_clock_s={wall_total:.1f} "
        f"hits_total={len(deduped)} per_stage={per_stage_counts}",
        file=sys.stderr,
    )
    return 0


def _self_test() -> None:
    assert _classify_stage_from_text("Champion Select") == "champion_select"
    assert _classify_stage_from_text("加载中") == "loading"
    assert _classify_stage_from_text("Victory MVP") == "post_game"
    assert _classify_stage_from_text("小兵 击杀") == "in_game"
    assert _classify_stage_from_text("nothing matches here") is None
    assert _classify_stage_from_text("CHAMPION-SELECT") == "champion_select"
    assert _classify_stage_from_text("post_game") == "post_game"

    cands = [
        Candidate("loading", 50.0),
        Candidate("loading", 51.0),
        Candidate("loading", 60.0),
        Candidate("in_game", 100.0),
    ]
    deduped = _dedup(cands, window=5.0)
    assert len(deduped) == 3, deduped
    by_stage = {s: [c for c in deduped if c.stage == s] for s in STAGE_ORDER}
    assert [c.at_seconds for c in by_stage["loading"]] == [50.0, 60.0], by_stage

    assert _parse_roi(None) is None
    assert _parse_roi("0,0,100,200") == (0, 0, 100, 200)
    try:
        _parse_roi("0,0,100")
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit for malformed --roi")

    nested = [[[[0, 0, 1, 1], ("胜利 MVP", 0.99)], [[0, 0, 1, 1], ("score", 0.7)]]]
    fragments = _extract_text_fragments(nested)
    assert fragments == ["胜利 MVP", "score"], fragments
    assert _extract_text_fragments(None) == []
    assert _extract_text_fragments([[None]]) == []

    print("self-test passed")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="prototype_ocr.py",
        description=(
            "OCR prototype (PaddleOCR) for LoL stage detection. Emits "
            "MatchStageHint jsonl consumable by eval.py. Vendors "
            "classify_stage_from_text from src/arl/segmenter/stage_text.py "
            "to stay arl.*-import-free; keep in sync."
        ),
    )
    parser.add_argument("--recording", help="Path to source .mp4")
    parser.add_argument("--output", help="Path to predicted-hints.jsonl")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--sample-fps", type=float, default=DEFAULT_SAMPLE_FPS)
    parser.add_argument(
        "--roi",
        default=None,
        help="ROI as x,y,w,h (pixels) of the frame to OCR; default whole frame",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=("paddle", "tesseract"),
        default="paddle",
        help="OCR backend (default paddle). Use tesseract on Python versions without paddlepaddle wheels.",
    )
    parser.add_argument(
        "--ocr-lang",
        default="ch",
        help='OCR lang code. PaddleOCR uses "ch"; tesseract uses "chi_sim". '
        'The shortcut "ch" or "zh" is auto-remapped to "chi_sim" for tesseract.',
    )
    parser.add_argument(
        "--tesseract-cmd",
        default=None,
        help='Override path to tesseract.exe (e.g. "C:/Program Files/Tesseract-OCR/tesseract.exe").',
    )
    parser.add_argument(
        "--tessdata-dir",
        default=None,
        help='Path to a tessdata directory containing <lang>.traineddata.',
    )
    parser.add_argument(
        "--dedup-window-seconds", type=float, default=DEFAULT_DEDUP_WINDOW
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if not (args.recording and args.output):
        print(
            "ERROR: --recording and --output are required (or pass --self-test)",
            file=sys.stderr,
        )
        return 2
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
