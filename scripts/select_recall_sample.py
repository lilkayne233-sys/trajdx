#!/usr/bin/env python
"""Draw the recall-audit sample: failed trajectories the tool reported nothing on.

Precision answers "how many alarms were true"; recall asks the other half --
of the failed runs the tool stayed silent on, how many actually contained a
detectable failure cause?  This script selects the audit sample: unresolved
trajectories with zero findings from the v6 main sample, sampled with a fixed
seed.  Sample lines are copied verbatim from the raw file.
"""
import argparse
import json
import random
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trajdx import detect_all
from trajdx.adapters import iter_file


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", default="data/raw/openhands_sample_v6.jsonl")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--out", default="data/raw/recall_sample.jsonl")
    args = ap.parse_args()

    raw_path = Path(args.raw)
    lines = [
        line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    # iter_file yields parsed trajectories in file order, skipping zero-step
    # records; walk both together, advancing past records the adapter skipped.
    trajectories = iter(iter_file(raw_path))
    candidates: list[int] = []
    scanned = 0
    for lineno, line in enumerate(lines):
        record = json.loads(line)
        if not (record.get("trajectory") or []):
            continue
        trajectory = next(trajectories)
        scanned += 1
        # resolved is stored as 0/1 (None = unknown) depending on the adapter
        if trajectory.resolved not in (0, False):
            continue
        if detect_all(trajectory):
            continue
        candidates.append(lineno)

    rng = random.Random(args.seed)
    picked = sorted(rng.sample(candidates, min(args.n, len(candidates))))
    out = Path(args.out)
    out.write_text("\n".join(lines[i] for i in picked) + "\n", encoding="utf-8")
    print(
        f"scanned {scanned} parsed trajectories; {len(candidates)} failed with zero "
        f"findings; sampled {len(picked)} -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
