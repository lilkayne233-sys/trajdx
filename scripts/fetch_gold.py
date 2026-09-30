#!/usr/bin/env python
"""Fetch the ground-truth patch for each instance, to enable localization_failure.

``localization_failure`` compares the agent's edits against the files the real fix
touched, so it cannot run without the task's gold patch.  Trajectory dumps do not
carry it -- only the task dataset does.  This script joins the two:

    nebius/SWE-rebench-openhands-trajectories  (the trajectories we diagnose)
    nebius/SWE-rebench                        (the tasks, with `patch`)

Two sources, because neither is reliable on its own
---------------------------------------------------
``parquet`` (default)
    Download the task shards once and filter them locally with pyarrow.  One
    connection per shard instead of 300 API calls, so a flaky link costs a retry
    rather than 300 of them.  Requires ``pyarrow``.

``api``
    Query the HuggingFace datasets-server ``/filter`` endpoint per instance.  Needs
    no extra dependency and transfers less, but the endpoint rate-limits, drops
    connections, and answers 500 while it rebuilds a dataset index.

Either way, reachability is the real problem in some networks, so an alternative
HuggingFace endpoint can be used::

    python scripts/fetch_gold.py --mirror https://hf-mirror.com

The sidecar stores **file paths, not patch bodies**.  That is all the detector
needs, it keeps the artifact a few kilobytes, and it avoids redistributing task
patches.  A content hash of each patch is kept so the provenance is checkable.

Writes are incremental and a re-run skips what is already on disk: a fetch that
dies at instance 250 must not throw away the first 249.

Usage
-----
    python scripts/fetch_gold.py --mirror https://hf-mirror.com
    python scripts/fetch_gold.py --source api
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trajdx.schema import patch_files  # noqa: E402

DATASET = "nebius/SWE-rebench"
CONFIG = "default"
SPLITS = ("test", "filtered")
ENDPOINT = "https://datasets-server.huggingface.co/filter"
USER_AGENT = "trajdx/0.1 (+gold-sidecar fetch)"


# --------------------------------------------------------------------------
# Instance list
# --------------------------------------------------------------------------


def wanted_instances(path: Path) -> list[str]:
    instance_ids: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        instance_id = json.loads(line)["instance_id"]
        if instance_id not in seen:
            seen.add(instance_id)
            instance_ids.append(instance_id)
    return instance_ids


def _row(instance_id: str, repo: str | None, patch: str, split: str) -> dict | None:
    files = patch_files(patch)
    if not files:
        return None
    return {
        "instance_id": instance_id,
        "repo": repo,
        "split": split,
        "files": files,
        "patch_chars": len(patch),
        "patch_sha256_16": hashlib.sha256(patch.encode("utf-8", "replace")).hexdigest()[:16],
    }


# --------------------------------------------------------------------------
# Source: parquet shards
# --------------------------------------------------------------------------


def fetch_via_parquet(wanted: set[str], mirror: str, cache: Path, sinks: dict) -> dict:
    """Download the task shards and filter them locally."""
    # huggingface_hub reads HF_ENDPOINT at import time, so it must be set first.
    if mirror:
        os.environ["HF_ENDPOINT"] = mirror
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        print("huggingface_hub is not installed; use --source api instead", file=sys.stderr)
        return {}
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("pyarrow is not installed (`pip install pyarrow`); use --source api instead",
              file=sys.stderr)
        return {}

    api = HfApi()
    all_files = api.list_repo_files(DATASET, repo_type="dataset")
    shards = sorted(f for f in all_files if f.endswith(".parquet"))
    print(f"  {len(shards)} parquet shard(s): {', '.join(Path(s).name for s in shards)}")

    resolved: dict[str, dict] = {}
    for shard in shards:
        print(f"  downloading {Path(shard).name} …", flush=True)
        local = hf_hub_download(
            repo_id=DATASET,
            filename=shard,
            repo_type="dataset",
            local_dir=str(cache / "swe-rebench"),
        )
        table = pq.read_table(local, columns=["instance_id", "repo", "patch"])
        ids = table.column("instance_id").to_pylist()
        repos = table.column("repo").to_pylist()
        patches = table.column("patch").to_pylist()
        split = Path(shard).name.split("-")[0]
        new = 0
        for instance_id, repo, patch in zip(ids, repos, patches):
            if instance_id not in wanted or instance_id in resolved:
                continue
            row = _row(instance_id, repo, patch or "", split)
            if row:
                resolved[instance_id] = row
                _emit(row, sinks)
                new += 1
        print(f"    matched {new} new instance(s); total {len(resolved)}")
        if len(resolved) >= len(wanted):
            break
    return resolved


# --------------------------------------------------------------------------
# Source: datasets-server filter API
# --------------------------------------------------------------------------


def _query(instance_id: str, split: str, *, retries: int = 4) -> dict | None:
    url = ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "dataset": DATASET,
            "config": CONFIG,
            "split": split,
            "where": '"instance_id"=\'%s\'' % instance_id,
            "limit": 1,
        }
    )
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = payload.get("rows") or []
            return rows[0]["row"] if rows else None
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            # 500 with "index is loading" is a transient server-side warm-up.
            if exc.code == 500 and "index is loading" in body and attempt < retries - 1:
                time.sleep(20 * (attempt + 1))
                continue
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  ! {instance_id}/{split}: {exc} {body[:120]}", file=sys.stderr)
            return None
        except Exception as exc:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  ! {instance_id}/{split}: {type(exc).__name__} {exc}", file=sys.stderr)
            return None
    return None


def fetch_one(instance_id: str) -> dict | None:
    for split in SPLITS:
        row = _query(instance_id, split)
        if row is None:
            continue
        return _row(instance_id, row.get("repo"), row.get("patch") or "", split)
    return None


def fetch_via_api(pending: list[str], workers: int, sinks: dict) -> int:
    resolved = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, (instance_id, result) in enumerate(
            zip(pending, pool.map(fetch_one, pending)), 1
        ):
            if result:
                resolved += 1
                _emit(result, sinks)
            if index % 25 == 0 or index == len(pending):
                print(f"  {index:4d}/{len(pending)}  resolved={resolved}", flush=True)
    return resolved


# --------------------------------------------------------------------------
# Incremental sink
# --------------------------------------------------------------------------


def _emit(row: dict, sinks: dict) -> None:
    with sinks["lock"]:
        sinks["fh"].write(json.dumps(row, ensure_ascii=False) + "\n")
        sinks["fh"].flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trajectories", type=Path,
                    default=Path("data/raw/openhands_sample.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("data/gold/swe-rebench-gold.jsonl"))
    ap.add_argument("--source", choices=("parquet", "api"), default="parquet")
    ap.add_argument("--mirror", default="",
                    help="alternative HuggingFace endpoint, e.g. https://hf-mirror.com")
    ap.add_argument("--cache", type=Path, default=Path("data/cache"))
    ap.add_argument("--limit", type=int, default=0, help="only the first N instances")
    ap.add_argument("--workers", type=int, default=4, help="api source only")
    args = ap.parse_args()

    if not args.trajectories.exists():
        print(f"missing {args.trajectories}; run scripts/fetch_trajectories.py first",
              file=sys.stderr)
        return 1

    instance_ids = wanted_instances(args.trajectories)
    if args.limit:
        instance_ids = instance_ids[: args.limit]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["instance_id"])

    pending = [iid for iid in instance_ids if iid not in done]
    print(f"source={args.source}  instances={len(instance_ids)}  "
          f"already resolved={len(done)}  to resolve={len(pending)}")
    if not pending:
        print(f"\ngold coverage: {len(done)}/{len(instance_ids)} (100%) -> {args.out}")
        return 0

    with args.out.open("a", encoding="utf-8") as fh:
        sinks = {"fh": fh, "lock": threading.Lock()}
        if args.source == "parquet":
            fetch_via_parquet(set(pending), args.mirror, args.cache, sinks)
        else:
            fetch_via_api(pending, args.workers, sinks)

    total = len({json.loads(l)["instance_id"]
                 for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()})
    print(f"\ngold coverage: {total}/{len(instance_ids)} "
          f"({total / len(instance_ids):.0%}) -> {args.out}")
    if total < len(instance_ids):
        print(f"  {len(instance_ids) - total} unresolved; re-run to retry only those")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())