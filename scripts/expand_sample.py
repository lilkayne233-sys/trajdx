#!/usr/bin/env python
"""Draw an outcome-stratified expansion sample from a trajectory pool.

The evaluation sample (300 trajectories) was drawn from the head of the
SWE-rebench OpenHands corpus via the datasets-server API.  To validate rules
that fired rarely on that sample, more trajectories are needed.  This draws them from the local
pool file (``scripts/fetch_openhands_pool.py`` output), excluding every
trajectory already present in existing samples, stratified by ``resolved`` so
the expansion keeps the same outcome balance as the original sample.

Two passes over the pool: the first records (offset, stratum) per record, the
second copies only the selected lines, so the ~1 GB pool is never held in
memory.

Usage
-----
    python scripts/expand_sample.py --n 400 --seed 20261001
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


def identity(record: dict) -> str:
    tid = record.get("trajectory_id")
    if tid:
        return str(tid)
    # Pool rows always carry trajectory_id; fall back to the instance id plus
    # message count rather than collapsing multiple runs of one instance.
    return f"{record.get('instance_id')}#{len(record.get('trajectory') or [])}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pool", type=Path, default=Path("data/raw/openhands_pool.jsonl"))
    ap.add_argument("--exclude", type=Path, action="append", default=[],
                    help="existing sample file(s); may be given multiple times")
    ap.add_argument("--out", type=Path, default=Path("data/raw/openhands_sample_v4_add.jsonl"))
    ap.add_argument("--n", type=int, default=400, help="total to draw, split evenly by outcome")
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--outcome-key", default="resolved",
                    help="record field (truthy) defining the two strata; the SWE-agent "
                         "pool carries its outcome as 'target' instead")
    ap.add_argument("--max-per-instance", type=int, default=0,
                    help="cap how many trajectories of the same instance_id may be drawn "
                         "(0 = uncapped).  Pools that repeat each instance many times "
                         "would otherwise produce a clustered, near-duplicate sample.")
    args = ap.parse_args()

    existing: set[str] = set()
    for path in args.exclude:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    existing.add(identity(json.loads(line)))
    print(f"excluding {len(existing)} trajectories already in existing samples")

    # Pass 1: reservoir-sample per stratum; offsets let pass 2 re-read the rows.
    quota = args.n // 2
    reservoir: dict[bool, list[tuple[int, str]]] = {True: [], False: []}
    seen: set[str] = set()
    per_instance: dict[str, int] = {}
    scanned = 0
    with args.pool.open(encoding="utf-8") as fh:
        for offset, line in enumerate(fh):
            if not line.strip():
                continue
            scanned += 1
            record = json.loads(line)
            key = identity(record)
            if key in existing or key in seen:
                continue
            if args.max_per_instance and per_instance.get(record.get("instance_id"), 0) \
                    >= args.max_per_instance:
                continue
            seen.add(key)
            per_instance[record.get("instance_id")] = \
                per_instance.get(record.get("instance_id"), 0) + 1
            stratum = bool(record.get(args.outcome_key))
            bucket = reservoir[stratum]
            pick = random.Random(args.seed + offset).random()
            if len(bucket) < quota:
                bucket.append((offset, key, pick))
                bucket.sort(key=lambda t: t[2])
            elif pick < bucket[-1][2]:
                bucket[-1] = (offset, key, pick)
                bucket.sort(key=lambda t: t[2])
    counts = {s: len(v) for s, v in reservoir.items()}
    print(f"scanned {scanned} pool records, {len(seen)} new; "
          f"reservoir {args.outcome_key}=true {counts[True]} / false {counts[False]}")

    wanted = {offset: key for stratum in reservoir.values() for offset, key, _ in stratum}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.pool.open(encoding="utf-8") as fh, args.out.open("w", encoding="utf-8") as out:
        for offset, line in enumerate(fh):
            if offset in wanted:
                out.write(line)
                written += 1

    sha = hashlib.sha256(args.out.read_bytes()).hexdigest()[:16]
    print(f"wrote {written} trajectories -> {args.out} (sha256[:16]={sha})")
    if written < args.n:
        print(f"note: pool exhausted before the full --n {args.n}; drew {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
