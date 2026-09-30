#!/usr/bin/env python
"""Download a stratified sample of real OpenHands trajectories.

The full dataset row is ~330 KB, so pulling all 67k trajectories is wasteful for
a diagnostic study.  We instead page through the HuggingFace *datasets-server*
row API, which streams JSON rows without materializing the 2 GB parquet, and
stop as soon as we have enough resolved / unresolved examples.

Usage
-----
    python scripts/fetch_trajectories.py --per-class 150
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATASET = "nebius/SWE-rebench-openhands-trajectories"
CONFIG = "default"
SPLIT = "train"
ROWS_ENDPOINT = "https://datasets-server.huggingface.co/rows"
PAGE_SIZE = 100

# Fields kept per trajectory.  `tools` is identical for every row of a run, so
# it is hoisted out into its own file instead of being duplicated 300 times.
KEEP = (
    "trajectory_id",
    "instance_id",
    "repo",
    "resolved",
    "exit_status",
    "model_patch",
    "gen_tests_correct",
    "pred_passes_gen_tests",
)


def fetch_page(offset: int, length: int, *, retries: int = 5) -> dict:
    qs = urllib.parse.urlencode(
        {
            "dataset": DATASET,
            "config": CONFIG,
            "split": SPLIT,
            "offset": offset,
            "length": length,
        }
    )
    url = f"{ROWS_ENDPOINT}?{qs}"
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            wait = 2 * (attempt + 1)
            print(f"  ! offset={offset} attempt {attempt + 1} failed ({exc}); retry in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"could not fetch offset={offset} after {retries} tries") from last_exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/raw/openhands_sample.jsonl"))
    ap.add_argument("--tools-out", type=Path, default=Path("data/raw/openhands_tools.json"))
    ap.add_argument("--per-class", type=int, default=150,
                    help="how many resolved and how many unresolved trajectories to keep")
    ap.add_argument("--max-scan", type=int, default=3000,
                    help="give up after scanning this many rows")
    ap.add_argument("--start-offset", type=int, default=0)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    kept: list[dict] = []
    counts = {True: 0, False: 0}
    tools_blob: list | None = None
    tools_sha: str | None = None
    seen_ids: set[str] = set()

    offset = args.start_offset
    scanned = 0
    while scanned < args.max_scan:
        if counts[True] >= args.per_class and counts[False] >= args.per_class:
            break
        try:
            payload = fetch_page(offset, PAGE_SIZE)
        except RuntimeError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            break

        rows = payload.get("rows", [])
        if not rows:
            print("  reached end of split", file=sys.stderr)
            break

        for entry in rows:
            row = entry.get("row", {})
            scanned += 1

            if tools_blob is None and row.get("tools"):
                tools_blob = row["tools"]
                tools_sha = hashlib.sha256(
                    json.dumps(tools_blob, sort_keys=True).encode("utf-8")
                ).hexdigest()[:16]

            resolved = bool(row.get("resolved"))
            tid = row.get("trajectory_id") or f"{row.get('instance_id')}#{entry.get('row_idx')}"
            if tid in seen_ids:
                continue
            if counts[resolved] >= args.per_class:
                continue

            record = {k: row.get(k) for k in KEEP if k in row}
            record["trajectory"] = row.get("trajectory") or []
            record["n_messages"] = len(record["trajectory"])
            kept.append(record)
            seen_ids.add(tid)
            counts[resolved] += 1

        print(f"  scanned={scanned:5d}  kept={len(kept):4d} "
              f"(resolved={counts[True]}, unresolved={counts[False]})")
        offset += len(rows)
        time.sleep(0.3)

    if not kept:
        print("nothing fetched", file=sys.stderr)
        return 1

    with args.out.open("w", encoding="utf-8") as fh:
        for record in kept:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    if tools_blob is not None:
        args.tools_out.parent.mkdir(parents=True, exist_ok=True)
        args.tools_out.write_text(
            json.dumps({"sha256_16": tools_sha, "tools": tools_blob}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"\nwrote {len(kept)} trajectories -> {args.out}")
    print(f"  resolved={counts[True]}  unresolved={counts[False]}  scanned={scanned}")
    if tools_sha:
        print(f"  tool schema sha256[:16]={tools_sha} -> {args.tools_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
