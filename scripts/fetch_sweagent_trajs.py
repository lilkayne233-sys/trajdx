#!/usr/bin/env python
"""Fetch real SWE-agent ``.traj`` logs, so the sweagent adapter runs on real data.

The adapter shipped with only synthetic fixtures behind it: nothing in the
repository ever parsed a genuine SWE-agent log, which means "supports SWE-agent"
rested on the author's reading of the format rather than on evidence.  This script
closes that gap from the one source that is small enough to be practical -- the
upstream SWE-agent repository, which vendors a set of real run logs:

* ``tests/test_data/trajectories/`` -- real runs, including SWE-bench instances
* ``trajectories/demonstrations/``  -- demonstration runs (CTF tasks)

They are downloaded verbatim and concatenated into one JSONL, one run per line,
which is the format ``trajdx.adapters.load_file`` already reads.

Usage
-----
    python scripts/fetch_sweagent_trajs.py
    python scripts/fetch_sweagent_trajs.py --validate
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = "SWE-agent/SWE-agent"
BRANCH = "main"
TREE_URL = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
USER_AGENT = "trajdx/0.1 (+sweagent traj fetch)"

#: Path prefixes worth pulling: real runs first, demonstrations second.
PREFIXES = ("tests/test_data/trajectories/", "trajectories/demonstrations/")


def _get(url: str, *, retries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url}: {last}")


def list_trajs() -> list[str]:
    payload = json.loads(_get(TREE_URL).decode("utf-8"))
    paths = [
        entry["path"]
        for entry in payload.get("tree", [])
        if entry.get("type") == "blob" and entry["path"].endswith(".traj")
    ]
    return sorted(p for p in paths if p.startswith(PREFIXES))


def validate(path: Path) -> dict[str, Any]:
    """Parse every fetched run through the adapter and summarize what it saw."""
    from trajdx.adapters import load_file
    from trajdx.detectors import detect_all
    from trajdx.metrics import wasted_step_ratio

    trajectories = load_file(path)
    kinds: dict[str, int] = {}
    matched_gold_ids: list[str] = []
    n_steps = 0
    for trajectory in trajectories:
        n_steps += trajectory.n_steps
        for step in trajectory.steps:
            kinds[step.kind.value] = kinds.get(step.kind.value, 0) + 1
        # `unmapped` would show up as StepKind.OTHER; name the ids so a format
        # change upstream is traceable rather than just a smaller number.
        if any(step.kind.value == "other" for step in trajectory.steps):
            matched_gold_ids.append(trajectory.instance_id)
    return {
        "runs": len(trajectories),
        "steps": n_steps,
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "runs_with_unmapped_steps": matched_gold_ids,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/raw/sweagent_sample.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="fetch at most N runs")
    ap.add_argument("--validate", action="store_true",
                    help="parse what was fetched and print a step-kind breakdown")
    args = ap.parse_args()

    socket.setdefaulttimeout(120)

    try:
        paths = list_trajs()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.limit:
        paths = paths[: args.limit]
    print(f"found {len(paths)} .traj files in {REPO}")

    records: list[dict[str, Any]] = []
    failed: list[str] = []
    for path in paths:
        try:
            payload = json.loads(_get(RAW_URL + path).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            failed.append(path)
            print(f"  ! {path}: {type(exc).__name__} {exc}", file=sys.stderr)
            continue
        payload.setdefault("instance_id", Path(path).stem)
        payload["_source_path"] = path
        records.append(payload)

    if not records:
        print("nothing fetched", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nwrote {len(records)} runs -> {args.out}")
    if failed:
        print(f"  {len(failed)} failed: {', '.join(failed[:3])} …")

    if args.validate:
        summary = validate(args.out)
        print(f"\nvalidation: {summary['runs']} runs, {summary['steps']} steps")
        print("  step kinds:", summary["kinds"])
        unmapped = summary["runs_with_unmapped_steps"]
        if unmapped:
            print(f"  runs containing unmapped (kind=other) steps: {len(unmapped)}")
            print(f"    e.g. {', '.join(unmapped[:5])}")
        else:
            print("  no unmapped steps: every action matched the shared vocabulary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())