"""Template-matching prototype: emit MatchStageHint jsonl from UI screenshot templates.

Research-only; no arl.* imports. Output shape mirrors
arl.segmenter.models.MatchStageHint: {session_id, stage, at_seconds}.
Consumable by eval.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import NamedTuple


STAGE_ORDER = ("champion_select", "loading", "in_game", "post_game")
DEFAULT_SAMPLE_FPS = 1.0
DEFAULT_THRESHOLD = 0.7
DEFAULT_DEDUP_WINDOW = 5.0


class Candidate(NamedTuple):
    stage: str
    at_seconds: float
    score: float


def _dedup(candidates: list[Candidate], window: float) -> list[Candidate]:
    """Per-stage: collapse candidates within `window` seconds to highest-score one."""
    kept: list[Candidate] = []
    for stage in STAGE_ORDER:
        stage_cands = sorted(
            [c for c in candidates if c.stage == stage],
            key=lambda c: c.at_seconds,
        )
        i = 0
        while i < len(stage_cands):
            cluster_start = stage_cands[i].at_seconds
            j = i
            while (
                j < len(stage_cands)
                and stage_cands[j].at_seconds - cluster_start <= window
            ):
                j += 1
            best = max(stage_cands[i:j], key=lambda c: c.score)
            kept.append(best)
            i = j
    kept.sort(key=lambda c: c.at_seconds)
    return kept


def _discover_templates(templates_dir: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for stage in STAGE_ORDER:
        stage_dir = templates_dir / stage
        if not stage_dir.is_dir():
            continue
        pngs = sorted(stage_dir.glob("*.png"))
        if pngs:
            out[stage] = pngs
    return out


def _run(args: argparse.Namespace) -> int:
    import cv2  # type: ignore[import-not-found]

    recording = Path(args.recording)
    templates_dir = Path(args.templates)
    output = Path(args.output)
    if not recording.exists():
        print(f"ERROR: recording not found: {recording}", file=sys.stderr)
        return 2
    if not templates_dir.is_dir():
        print(f"ERROR: templates dir not found: {templates_dir}", file=sys.stderr)
        return 2

    templates_by_stage = _discover_templates(templates_dir)
    if not templates_by_stage:
        print(
            "ERROR: no templates found under "
            f"{templates_dir}/<stage>/*.png (stages: {STAGE_ORDER})",
            file=sys.stderr,
        )
        return 2

    loaded: dict[str, list[tuple[str, "cv2.Mat"]]] = {}
    for stage, paths in templates_by_stage.items():
        loaded[stage] = []
        for p in paths:
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                print(f"WARN: failed to read template {p}", file=sys.stderr)
                continue
            loaded[stage].append((p.name, img))

    cap = cv2.VideoCapture(str(recording))
    if not cap.isOpened():
        print(f"ERROR: cv2.VideoCapture failed: {recording}", file=sys.stderr)
        return 2
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_skip = max(1, int(round(src_fps / args.sample_fps)))
    session_id = args.session_id or recording.parent.name

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
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for stage, items in loaded.items():
                for _name, tmpl in items:
                    if tmpl.shape[0] > gray.shape[0] or tmpl.shape[1] > gray.shape[1]:
                        continue
                    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, _ = cv2.minMaxLoc(res)
                    if max_val >= args.threshold:
                        candidates.append(Candidate(stage, t_sec, float(max_val)))
            sample_ms_total += (time.perf_counter() - t0) * 1000.0
            sampled += 1
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
        f"template-matching summary: sampled={sampled} frames "
        f"src_fps={src_fps:.2f} skip={frame_skip} "
        f"ms_per_frame={sample_ms_total / max(sampled, 1):.1f} "
        f"wall_clock_s={wall_total:.1f} "
        f"hits_total={len(deduped)} per_stage={per_stage_counts}",
        file=sys.stderr,
    )
    return 0


def _self_test() -> None:
    cands = [
        Candidate("champion_select", 10.0, 0.75),
        Candidate("champion_select", 11.5, 0.90),
        Candidate("champion_select", 14.0, 0.80),
        Candidate("loading", 20.0, 0.85),
        Candidate("champion_select", 100.0, 0.72),
    ]
    deduped = _dedup(cands, window=5.0)
    by_stage = {s: [c for c in deduped if c.stage == s] for s in STAGE_ORDER}
    assert len(by_stage["champion_select"]) == 2, by_stage
    assert by_stage["champion_select"][0].score == 0.90, by_stage
    assert by_stage["champion_select"][1].at_seconds == 100.0, by_stage
    assert len(by_stage["loading"]) == 1, by_stage
    assert by_stage["loading"][0].score == 0.85, by_stage

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "champion_select").mkdir()
        (root / "champion_select" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (root / "loading").mkdir()
        (root / "noise").mkdir()
        (root / "noise" / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        discovered = _discover_templates(root)
        assert "champion_select" in discovered and len(discovered["champion_select"]) == 1, discovered
        assert "loading" not in discovered, discovered
        assert "noise" not in discovered, discovered

    print("self-test passed")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="prototype_template_matching.py",
        description=(
            "Template-matching prototype for LoL stage detection. Emits "
            "MatchStageHint jsonl consumable by eval.py. Research-only; "
            "no arl.* imports."
        ),
    )
    parser.add_argument("--recording", help="Path to source .mp4")
    parser.add_argument("--templates", help="Dir containing <stage>/*.png templates")
    parser.add_argument("--output", help="Path to predicted-hints.jsonl")
    parser.add_argument(
        "--session-id",
        default=None,
        help="Override session_id; defaults to recording parent dir name",
    )
    parser.add_argument("--sample-fps", type=float, default=DEFAULT_SAMPLE_FPS)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
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
    if not (args.recording and args.templates and args.output):
        print(
            "ERROR: --recording, --templates, --output are required (or pass --self-test)",
            file=sys.stderr,
        )
        return 2
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
