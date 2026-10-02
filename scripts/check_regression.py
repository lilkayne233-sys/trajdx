#!/usr/bin/env python
"""Fail the build if a core detector stops meeting the precision bar.

The tier system only means something if it is enforced.  A rule is promoted to
``core`` when it clears the project's precision target on annotated data, and it
should lose that status -- loudly -- the moment it no longer does.  Without a
check like this, a detector tweak that quietly ruins the precision of a
default-on rule would ship, and the README would keep asserting the old number
until someone happened to re-read it.

Two things are errors here:

* a ``core`` detector whose measured precision falls below the target;
* a ``core`` detector with **no** validated sample in the round, because a tier
  is a claim about measured precision and "unmeasured" cannot support it.  A tier
  is a claim about measured precision, and "unmeasured" cannot support it.

Usage
-----
    python scripts/check_regression.py
    python scripts/check_regression.py --target 0.90
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import evaluate  # noqa: E402  (sibling script, reused rather than duplicated)

from trajdx.detectors import REGISTRY, Tier  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=evaluate.DEFAULT_RAW)
    ap.add_argument("--labels", default="data/labels/labelled_v3_*.jsonl")
    ap.add_argument("--target", type=float, default=0.88)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--min-coverage", type=float, default=0.8)
    args = ap.parse_args()

    if not args.raw.exists():
        print(f"raw trajectories not found: {args.raw}", file=sys.stderr)
        return 1
    labels = {row["finding_id"]: row for row in evaluate.load_jsonl(args.labels)}
    joined, info = evaluate.join_labels(args.raw, labels)
    if not joined:
        print("no stored label matches the current detector output", file=sys.stderr)
        return 1
    print(f"code {evaluate.code_version()}: {evaluate.coverage_line(info)}\n")

    by_detector: dict[str, Counter[str]] = defaultdict(Counter)
    for row in joined:
        by_detector[row["detector"]][row["verdict"]] += 1

    print(f"joined {len(joined)} findings  (target {args.target:.0%})\n")
    print(f"{'core detector':24} {'n':>4} {'valid':>6} {'invalid':>8} {'prec':>8}  verdict")
    print("-" * 72)

    problems: list[str] = []
    live, duplicates = evaluate.live_findings(args.raw)
    if duplicates:
        problems.append(f"duplicate finding identities: {duplicates}")
    for name, cls in sorted(REGISTRY.items()):
        if cls.tier is not Tier.CORE:
            continue
        counter = by_detector.get(name, Counter())
        valid, invalid = counter["valid"], counter["invalid"]
        n = valid + invalid
        if n == 0:
            problems.append(
                f"{name}: core tier but no validated sample in this round"
            )
            print(f"{name:24} {0:4d} {0:6d} {0:8d} {'—':>8}  NO SAMPLE")
            continue
        precision = valid / n
        live_n = sum(row["detector"] == name for row in live.values())
        coverage = n / live_n if live_n else 0.0
        if n < args.min_samples:
            problems.append(f"{name}: only {n} labels, need {args.min_samples}")
        if coverage < args.min_coverage:
            problems.append(f"{name}: labelled coverage {coverage:.1%} < {args.min_coverage:.1%}")
        ok = precision >= args.target
        if not ok:
            problems.append(
                f"{name}: precision {precision:.1%} on {n} findings < {args.target:.0%}"
            )
        print(
            f"{name:24} {n:4d} {valid:6d} {invalid:8d} {precision:8.1%}  "
            f"{'ok' if ok else 'BELOW TARGET'}"
        )

    unresolved = sorted(set(by_detector) - {n for n, c in REGISTRY.items() if c.tier is Tier.CORE})
    if unresolved:
        print(f"\nnon-core detectors present in the sample: {', '.join(unresolved)}")

    print()
    if problems:
        print("FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nEither fix the detector, or demote it to Tier.EXPERIMENTAL -- a rule that no"
            "\nlonger clears the bar must not ship as default-on."
        )
        return 1

    print("OK: every core detector clears the precision target on this sample.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())