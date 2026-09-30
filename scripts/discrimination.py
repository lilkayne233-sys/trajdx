#!/usr/bin/env python
"""Does the waste metric actually predict failure?

Detector *presence* turns out to be a weak signal: repetitive behaviour shows up
in successful runs too.  The question that decides whether the Wasted Step Ratio
is worth reporting is whether its *magnitude* separates passing runs from
failing ones, which is what this script measures with a rank AUC.

A predictor with AUC 0.5 is noise; 1.0 is a perfect oracle.  Anything at or above
roughly 0.7 is worth putting on a slide.

Usage
-----
    python scripts/discrimination.py --data data/raw/openhands_sample.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trajdx.adapters import load_file  # noqa: E402
from trajdx.detectors import detect_all  # noqa: E402
from trajdx.metrics import WasteReport, wasted_step_ratio  # noqa: E402
from trajdx.schema import Trajectory  # noqa: E402


def auc(pairs: Iterable[tuple[float, bool]]) -> float:
    """Rank AUC via the Mann-Whitney U statistic, with ties counted as 0.5."""
    pairs = list(pairs)
    pos = [s for s, label in pairs if label]
    neg = [s for s, label in pairs if not label]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data/raw/openhands_sample.jsonl"))
    ap.add_argument("--framework", default=None)
    args = ap.parse_args()

    trajectories = load_file(args.data, framework=args.framework)
    rows: list[tuple[Trajectory, WasteReport]] = []
    for trajectory in trajectories:
        if trajectory.resolved is None:
            continue
        rows.append((trajectory, wasted_step_ratio(trajectory, detect_all(trajectory))))

    if not rows:
        print("no labelled trajectories found", file=sys.stderr)
        return 1

    # Positive class = the run failed.
    def failed(trajectory: Trajectory) -> bool:
        return not trajectory.resolved

    predictors: list[tuple[str, Callable[[Trajectory, WasteReport], float]]] = [
        ("total_steps", lambda t, r: r.total_steps),
        ("wasted_steps", lambda t, r: r.wasted_steps),
        ("wasted_step_ratio", lambda t, r: r.ratio),
        ("n_findings", lambda t, r: r.n_findings),
    ]

    print(f"n = {len(rows)}  (failed = {sum(1 for t, _ in rows if failed(t))})\n")
    print(f"{'predictor':36} {'AUC':>7}")
    print("-" * 44)
    for name, fn in predictors:
        print(f"{name:36} {auc((fn(t, r), failed(t)) for t, r in rows):7.3f}")

    categories = sorted({c for _, r in rows for c in r.by_category})
    for category in categories:
        value = auc(
            (r.by_category.get(category, 0), failed(t)) for t, r in rows
        )
        print(f"{'wasted_steps:' + category:36} {value:7.3f}")

    print("\nconcentration of failures at the high end of the waste distribution")
    print(f"{'top quantile by WSR':36} {'n':>5} {'failed':>8}")
    print("-" * 44)
    ordered = sorted(rows, key=lambda pair: -pair[1].ratio)
    base_rate = sum(1 for t, _ in rows if failed(t)) / len(rows)
    for quantile in (0.10, 0.25, 0.50, 0.75, 1.00):
        k = max(1, int(len(ordered) * quantile))
        subset = ordered[:k]
        rate = sum(1 for t, _ in subset if failed(t)) / k
        print(f"top {quantile:>5.0%}{'':29} {k:5d} {rate:8.1%}")
    print(f"\nbase failure rate: {base_rate:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
