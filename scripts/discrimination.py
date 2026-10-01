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
        # Process shape.  These are the metrics that actually separate outcomes;
        # they live here rather than in the waste accounting because they describe
        # how a run was conducted, not how much of it was wasted.
        ("source_edits", lambda t, r: r.source_edits),
        ("test_runs", lambda t, r: r.test_runs),
        ("tests_per_source_edit", lambda t, r: r.tests_per_source_edit),
        ("test_run_ratio", lambda t, r: r.test_run_ratio),
        # Observation novelty needs no detector at all -- it reads the identity
        # hashes every step carries.  It is the detector-free answer to the
        # question WSR tried to answer through detector output.
        ("novel_observation_ratio", lambda t, r: r.novel_observation_ratio),
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

    # ------------------------------------------------------------ group means
    # The README quotes resolved/unresolved means next to each AUC; printing them
    # here is what makes those numbers reproducible rather than asserted.
    print("\nprocess shape by outcome (mean per run)")
    header = f"{'metric':28} {'resolved':>10} {'unresolved':>12}"
    print(header)
    print("-" * len(header))
    resolved = [r for t, r in rows if t.resolved]
    unresolved = [r for t, r in rows if not t.resolved]

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else float("nan")

    for name, field, decimals in [
        ("total_steps", lambda r: r.total_steps, 2),
        ("source_edits", lambda r: r.source_edits, 2),
        ("test_runs", lambda r: r.test_runs, 2),
        ("tests_per_source_edit", lambda r: r.tests_per_source_edit, 2),
        ("test_run_ratio", lambda r: r.test_run_ratio, 4),
        ("novel_observation_ratio", lambda r: r.novel_observation_ratio, 4),
        ("wasted_step_ratio", lambda r: r.ratio, 4),
    ]:
        print(
            f"{name:28} {mean([field(r) for r in resolved]):10.{decimals}f} "
            f"{mean([field(r) for r in unresolved]):12.{decimals}f}"
        )

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
