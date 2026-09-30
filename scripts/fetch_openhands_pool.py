"""Fetch an OpenHands trajectory pool from the published SWE-rebench dataset.

``nebius/SWE-rebench-openhands-trajectories`` ships all 67,074 trajectories in a
single 2 GB parquet file.  Pulling that whole file to work with a few thousand
rows is wasteful, so this reads only the requested row groups over HTTP range
requests and writes JSONL -- the shape ``trajdx.adapters.load_file`` already
consumes.

The output is a *pool*, not a curated sample: it is neither balanced across
``resolved`` nor deduplicated.  Sampling from it is a separate step.

Usage::

    python scripts/fetch_openhands_pool.py --mirror https://hf-mirror.com
    python scripts/fetch_openhands_pool.py --rows 8192 --out data/raw/big.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DATASET = "datasets/nebius/SWE-rebench-openhands-trajectories"
DEFAULT_OUT = Path("data/raw/openhands_pool.jsonl")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--rows",
        type=int,
        default=4096,
        help="how many rows to fetch; rounded up to whole row groups (default 4096)",
    )
    ap.add_argument(
        "--mirror",
        default=None,
        help="alternative HuggingFace endpoint, e.g. https://hf-mirror.com",
    )
    ap.add_argument("--batch", type=int, default=64, help="rows per write batch")
    args = ap.parse_args(argv)

    if args.mirror:
        os.environ["HF_ENDPOINT"] = args.mirror

    try:
        import pyarrow.parquet as pq
        from huggingface_hub import HfFileSystem
    except ImportError as exc:  # pragma: no cover - environment dependent
        print(f"missing dependency: {exc}. Install the 'data' extra.", file=sys.stderr)
        return 2

    fs = HfFileSystem()
    with fs.open(f"{DATASET}/trajectories.parquet", "rb") as handle:
        parquet = pq.ParquetFile(handle)
        total = parquet.metadata.num_rows
        groups = parquet.metadata.num_row_groups
        print(f"source: {total} rows in {groups} row groups")
        print(f"columns: {parquet.schema_arrow.names}")

        wanted: list[int] = []
        taken = 0
        for index in range(groups):
            wanted.append(index)
            taken += parquet.metadata.row_group(index).num_rows
            if taken >= args.rows:
                break
        print(f"reading {len(wanted)} row group(s) for ~{taken} rows")

        args.out.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with args.out.open("w", encoding="utf-8") as out:
            for batch in parquet.iter_batches(
                batch_size=args.batch, row_groups=wanted
            ):
                for row in batch.to_pylist():
                    out.write(json.dumps(row) + "\n")
                    written += 1
                print(f"  written {written}", flush=True)

    print(f"wrote {written} rows -> {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())