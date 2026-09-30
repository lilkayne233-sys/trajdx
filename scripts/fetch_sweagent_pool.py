"""Fetch a SWE-agent trajectory pool from the published Nebius dataset.

``nebius/SWE-agent-trajectories`` holds 80,036 SWE-agent runs across 12 parquet
shards (~89 MB each, 1.1 GB total), and unlike the logs vendored in the upstream
SWE-agent repository these are real task attempts carrying a ``target`` success
label.  This downloads whole shards through the mirror and converts them to
JSONL.

Note the serialization: each record's ``trajectory`` is a flat role/text message
stream (``system`` / ``user`` / ``ai``), which is a third format distinct from
both the ``.traj`` step list and the OpenHands message list.  Check that the
adapters handle it before treating the output as parseable.

Usage::

    python scripts/fetch_sweagent_pool.py --mirror https://hf-mirror.com
    python scripts/fetch_sweagent_pool.py --shards 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DATASET = "nebius/SWE-agent-trajectories"
SHARD = "data/train-{index:05d}-of-00012.parquet"
DEFAULT_OUT = Path("data/raw/sweagent_pool.jsonl")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--shards", type=int, default=1, help="how many shards to fetch")
    ap.add_argument("--cache", type=Path, default=Path("data/cache/sweagent_trajs"))
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
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - environment dependent
        print(f"missing dependency: {exc}. Install the 'data' extra.", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.out.open("w", encoding="utf-8") as out:
        for index in range(args.shards):
            name = SHARD.format(index=index)
            print(f"downloading {name} …", flush=True)
            local = hf_hub_download(
                repo_id=DATASET,
                filename=name,
                repo_type="dataset",
                cache_dir=str(args.cache),
            )
            parquet = pq.ParquetFile(local)
            print(
                f"  {local} ({Path(local).stat().st_size / 1e6:.1f} MB), "
                f"{parquet.metadata.num_rows} rows",
                flush=True,
            )
            if index == 0:
                print(f"  columns: {parquet.schema_arrow.names}")
            for batch in parquet.iter_batches(batch_size=args.batch):
                for row in batch.to_pylist():
                    out.write(json.dumps(row) + "\n")
                    written += 1
            print(f"  written so far: {written}", flush=True)

    print(f"wrote {written} rows -> {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())