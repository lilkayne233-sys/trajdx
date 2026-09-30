#!/usr/bin/env python
"""Profile detectors against the resolved/unresolved split.

Two questions this answers, both of which decide whether a rule is worth
keeping:

1. **Discrimination** -- does the failure mode appear more often on runs that
   actually failed?  A rule that fires equally on resolved runs is not
   describing failure, whatever its internal logic looks like.
2. **Base rate** -- is the rule so permissive that it fires on almost every
   trajectory?  A rule with a 95% base rate carries almost no information even
   if its lift is positive.

Usage
-----
    python scripts/detector_profile.py --data data/raw/openhands_sample.jsonl
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trajdx.adapters import load_file  # noqa: E402
from trajdx.detectors import detect_all  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data/raw/openhands_sample.jsonl"))
    ap.add_argument("--framework", default=None)
    args = ap.parse_args()

    t0 = time.perf_counter()
    trajectories = load_file(args.data, framework=args.framework)
    load_s = time.perf_counter() - t0
    print(f"loaded {len(trajectories)} trajectories in {load_s:.2f}s "
          f"({load_s / len(trajectories) * 1000:.1f} ms each)\n")

    # (detector, pattern) -> outcome -> number of trajectories with >= 1 hit
    hits: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    waste: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    n_findings: Counter[str] = Counter()

    t0 = time.perf_counter()
    for trajectory in trajectories:
        outcome = "unknown" if trajectory.resolved is None else (
            "resolved" if trajectory.resolved else "unresolved"
        )
        totals[outcome] += 1
        findings = detect_all(trajectory)
        seen: set[tuple[str, str]] = set()
        for finding in findings:
            key = (finding.detector, str(finding.detail.get("pattern", "-")))
            n_findings[key] += 1
            if key not in seen:
                hits[key][outcome] += 1
                seen.add(key)
            waste[key][outcome] += finding.n_wasted

    detect_s = time.perf_counter() - t0
    print(f"detected in {detect_s:.2f}s ({detect_s / len(trajectories) * 1000:.1f} ms each)\n")

    n_res, n_unres = totals["resolved"], totals["unresolved"]
    header = (f"{'detector':22} {'pattern':22} {'res%':>7} {'unres%':>7} "
              f"{'lift':>7} {'find':>6} {'waste/unres':>12}")
    print(header)
    print("-" * len(header))

    rows = []
    for key, counter in hits.items():
        res_rate = counter["resolved"] / n_res if n_res else 0.0
        unres_rate = counter["unresolved"] / n_unres if n_unres else 0.0
        rows.append((unres_rate - res_rate, key, res_rate, unres_rate))
    for lift, (detector, pattern), res_rate, unres_rate in sorted(rows, reverse=True):
        w = waste[(detector, pattern)]["unresolved"] / n_unres if n_unres else 0.0
        print(f"{detector:22} {pattern:22} {res_rate:7.1%} {unres_rate:7.1%} "
              f"{lift:+7.3f} {n_findings[(detector, pattern)]:6d} {w:12.2f}")

    print(f"\ntotals: resolved={n_res} unresolved={n_unres}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
