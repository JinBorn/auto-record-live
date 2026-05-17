"""Stage-hint evaluator: compare prototype predictions against human ground truth."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple


STAGE_ORDER = ("champion_select", "loading", "in_game", "post_game")
STAGE_LABEL_SHORT = {
    "champion_select": "CS",
    "loading": "LD",
    "in_game": "IG",
    "post_game": "PG",
}
DEFAULT_TOLERANCE_SECONDS = 10.0


class Hint(NamedTuple):
    stage: str
    at_seconds: float


def _load_hints(path: Path) -> list[Hint]:
    if not path.exists():
        raise SystemExit(f"hints file not found: {path}")
    hints: list[Hint] = []
    with path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"{path}:{lineno}: row must be a JSON object")
            stage = row.get("stage")
            at = row.get("at_seconds")
            if stage not in STAGE_ORDER:
                raise SystemExit(
                    f"{path}:{lineno}: stage must be one of {STAGE_ORDER}, got {stage!r}"
                )
            if not isinstance(at, (int, float)):
                raise SystemExit(
                    f"{path}:{lineno}: at_seconds must be a number, got {at!r}"
                )
            hints.append(Hint(stage=stage, at_seconds=float(at)))
    return hints


def _greedy_match_per_stage(
    truth: list[Hint],
    pred: list[Hint],
    tolerance: float,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {
        s: {"tp": 0, "fp": 0, "fn": 0} for s in STAGE_ORDER
    }
    for stage in STAGE_ORDER:
        stage_truth = sorted([h.at_seconds for h in truth if h.stage == stage])
        stage_pred = sorted([h.at_seconds for h in pred if h.stage == stage])
        matched: set[int] = set()
        for p in stage_pred:
            best_idx = -1
            best_delta = tolerance + 1.0
            for idx, t in enumerate(stage_truth):
                if idx in matched:
                    continue
                delta = abs(p - t)
                if delta < best_delta:
                    best_delta = delta
                    best_idx = idx
            if best_idx >= 0 and best_delta <= tolerance:
                matched.add(best_idx)
                counts[stage]["tp"] += 1
            else:
                counts[stage]["fp"] += 1
        counts[stage]["fn"] = len(stage_truth) - len(matched)
    return counts


def _confusion_matrix(
    truth: list[Hint],
    pred: list[Hint],
    tolerance: float,
) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {
        p: {a: 0 for a in STAGE_ORDER} for p in STAGE_ORDER
    }
    for p in pred:
        best_actual: str | None = None
        best_delta = tolerance + 1.0
        for t in truth:
            delta = abs(p.at_seconds - t.at_seconds)
            if delta <= tolerance and delta < best_delta:
                best_delta = delta
                best_actual = t.stage
        if best_actual is not None:
            matrix[p.stage][best_actual] += 1
    return matrix


def _precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0
    return precision, recall, f1


def _format_report(
    counts: dict[str, dict[str, int]],
    matrix: dict[str, dict[str, int]],
) -> str:
    lines: list[str] = []
    lines.append(
        f"{'stage':<20}{'precision':>11}{'recall':>9}{'f1':>7}{'TP':>5}{'FP':>5}{'FN':>5}"
    )
    overall = {"tp": 0, "fp": 0, "fn": 0}
    for stage in STAGE_ORDER:
        c = counts[stage]
        p, r, f1 = _precision_recall_f1(c["tp"], c["fp"], c["fn"])
        lines.append(
            f"{stage:<20}{p:>11.3f}{r:>9.3f}{f1:>7.3f}{c['tp']:>5}{c['fp']:>5}{c['fn']:>5}"
        )
        overall["tp"] += c["tp"]
        overall["fp"] += c["fp"]
        overall["fn"] += c["fn"]
    p, r, f1 = _precision_recall_f1(overall["tp"], overall["fp"], overall["fn"])
    lines.append(
        f"{'overall':<20}{p:>11.3f}{r:>9.3f}{f1:>7.3f}"
        f"{overall['tp']:>5}{overall['fp']:>5}{overall['fn']:>5}"
    )
    lines.append("")
    lines.append("confusion matrix (predicted rows x actual cols, FPs without nearby actual are off-matrix):")
    header = " " * 16
    for actual in STAGE_ORDER:
        header += f"{STAGE_LABEL_SHORT[actual]:>5}"
    lines.append(header)
    for predicted in STAGE_ORDER:
        row = f"{STAGE_LABEL_SHORT[predicted]:<16}"
        for actual in STAGE_ORDER:
            row += f"{matrix[predicted][actual]:>5}"
        lines.append(row)
    return "\n".join(lines)


def _self_test() -> None:
    truth = [
        Hint("champion_select", 0.0),
        Hint("loading", 50.0),
        Hint("in_game", 80.0),
        Hint("post_game", 1800.0),
        Hint("champion_select", 1900.0),
        Hint("loading", 1950.0),
        Hint("in_game", 1980.0),
        Hint("post_game", 3700.0),
    ]
    pred = [
        Hint("champion_select", 1.5),
        Hint("in_game", 81.0),
        Hint("champion_select", 1000.0),
        Hint("loading", 1902.0),
        Hint("loading", 1950.0),
        Hint("in_game", 1985.0),
        Hint("post_game", 3705.0),
    ]
    counts = _greedy_match_per_stage(truth, pred, tolerance=10.0)
    assert counts["champion_select"] == {"tp": 1, "fp": 1, "fn": 1}, counts
    assert counts["loading"] == {"tp": 1, "fp": 1, "fn": 1}, counts
    assert counts["in_game"] == {"tp": 2, "fp": 0, "fn": 0}, counts
    assert counts["post_game"] == {"tp": 1, "fp": 0, "fn": 1}, counts

    p, r, f1 = _precision_recall_f1(1, 1, 1)
    assert abs(p - 0.5) < 1e-9 and abs(r - 0.5) < 1e-9 and abs(f1 - 0.5) < 1e-9

    matrix = _confusion_matrix(truth, pred, tolerance=10.0)
    assert matrix["champion_select"]["champion_select"] == 1, matrix
    assert matrix["loading"]["champion_select"] == 1, matrix
    assert matrix["loading"]["loading"] == 1, matrix
    assert matrix["in_game"]["in_game"] == 2, matrix
    assert matrix["post_game"]["post_game"] == 1, matrix

    report = _format_report(counts, matrix)
    assert "stage" in report and "confusion matrix" in report

    print("self-test passed")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eval.py",
        description=(
            "Evaluate stage-hint prototype predictions against human ground truth. "
            "Inputs are jsonl files matching arl.segmenter.models.MatchStageHint "
            "shape: {session_id, stage, at_seconds}. Stages: "
            "champion_select | loading | in_game | post_game. "
            "Standalone research code; does not import arl.*."
        ),
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        help="Path to ground-truth jsonl (one MatchStageHint per line; `#` lines skipped).",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        help="Path to prediction jsonl (one MatchStageHint per line; `#` lines skipped).",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE_SECONDS,
        help=f"Matching tolerance in seconds (default {DEFAULT_TOLERANCE_SECONDS}).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run internal self-test on hardcoded synthetic data and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if args.ground_truth is None or args.predictions is None:
        print(
            "ERROR: --ground-truth and --predictions are required (or pass --self-test)",
            file=sys.stderr,
        )
        return 2
    truth = _load_hints(args.ground_truth)
    pred = _load_hints(args.predictions)
    counts = _greedy_match_per_stage(truth, pred, args.tolerance)
    matrix = _confusion_matrix(truth, pred, args.tolerance)
    print(_format_report(counts, matrix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
