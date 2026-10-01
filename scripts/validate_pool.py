#!/usr/bin/env python
"""Bounded, stratified parsing validation. No network and no precision claims.

Scan at most --max-scan JSONL records, reservoir-sample by outcome/length, and
validate one record at a time. --code-root can compare an archived code version
against exactly the same --input sample; it never loads a corpus into memory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys


def sample_pool(source: Path, destination: Path, max_scan: int, per_stratum: int, seed: int):
    rng = random.Random(seed)
    buckets = defaultdict(list)
    seen = Counter()
    scanned = 0
    with source.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            scanned += 1
            outcome = row.get("resolved", row.get("target"))
            outcome = "resolved" if outcome in (True, 1) else "unresolved" if outcome in (False, 0) else "unknown"
            length = len(row.get("trajectory") or row.get("history") or [])
            band = "short" if length < 40 else "medium" if length < 120 else "long"
            key = f"{outcome}/{band}"
            seen[key] += 1
            bucket = buckets[key]
            if len(bucket) < per_stratum:
                bucket.append(line.strip())
            else:
                position = rng.randrange(seen[key])
                if position < per_stratum:
                    bucket[position] = line.strip()
            if scanned >= max_scan:
                break
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for key in sorted(buckets):
            for row in buckets[key]:
                handle.write(row + "\n")
    return {"scanned": scanned, "seen_strata": dict(seen), "sampled_strata": {k: len(v) for k, v in buckets.items()}, "seed": seed}


def validate(source: Path):
    from trajdx.adapters import detect_adapter
    from trajdx.detectors import detect_all
    from trajdx.metrics import wasted_step_ratio
    try:
        from trajdx.identity import finding_identity
    except ImportError:
        finding_identity = None
    totals = Counter()
    kinds = Counter()
    detectors = Counter()
    outcomes = Counter()
    errors = []
    ids = set()
    repos = set()
    per_run = []
    sha = hashlib.sha256()
    with source.open(encoding="utf-8-sig") as handle:
        for index, line in enumerate(handle, 1):
            if not line.strip():
                continue
            sha.update(line.encode("utf-8"))
            totals["records"] += 1
            try:
                row = json.loads(line)
                trajectory = detect_adapter(row).parse(row)
                totals["parsed"] += 1
                totals["empty_trajectories"] += not bool(trajectory.steps)
                repos.add(trajectory.repo or row.get("repo") or trajectory.instance_id.rsplit("-", 1)[0])
                outcomes[str(trajectory.resolved)] += 1
                findings = detect_all(trajectory)
                waste = wasted_step_ratio(trajectory, findings)
                totals["steps"] += trajectory.n_steps
                totals["wasted_steps"] += waste.wasted_steps
                totals["findings"] += len(findings)
                for step in trajectory.steps:
                    kinds[step.kind.value] += 1
                    totals["failed_steps"] += step.failed
                    totals["error_kind_but_not_failed"] += bool(step.error_kind) and not step.failed
                    totals["nonzero_exits"] += step.exit_code not in (None, 0)
                    totals["error_kinds"] += bool(step.error_kind)
                    if step.kind.value in ("shell", "edit"):
                        totals["empty_observations"] += not bool((step.observation or "").strip())
                for finding in findings:
                    detectors[finding.detector] += 1
                    fid = finding_identity(trajectory, finding)["finding_id"] if finding_identity else f"{trajectory.instance_id}|{finding.detector}|{finding.start}"
                    totals["duplicate_finding_ids"] += fid in ids
                    ids.add(fid)
                per_run.append({"record": index, "instance_id": trajectory.instance_id, "steps": trajectory.n_steps, "findings": len(findings), "wasted_steps": waste.wasted_steps, "by_detector": dict(Counter(f.detector for f in findings))})
            except Exception as exc:
                totals["parse_or_detection_errors"] += 1
                errors.append({"record": index, "type": type(exc).__name__, "message": str(exc)[:500]})
    return {"source": str(source), "sample_sha256": sha.hexdigest(), "totals": dict(totals), "step_kinds": dict(kinds), "detectors": dict(detectors), "outcomes": dict(outcomes), "distinct_repos": len(repos), "errors": errors, "per_run": per_run, "caveat": "Bounded stratified sample, not a random sample of the full corpus; no human labels, so neither precision nor recall is measured. Length bands use serialized message count, not normalized steps."}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--sample-out", type=Path)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--max-scan", type=int, default=1200)
    ap.add_argument("--per-stratum", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if args.max_scan <= 0 or args.per_stratum <= 0:
        ap.error("max-scan and per-stratum must be positive")
    sys.path.insert(0, str(args.code_root.resolve()))
    sampling = None
    source = args.input
    if args.sample_out:
        if args.sample_out.resolve() == args.input.resolve():
            ap.error("sample-out must not overwrite input")
        sampling = sample_pool(source, args.sample_out, args.max_scan, args.per_stratum, args.seed)
        source = args.sample_out
    report = validate(source)
    report["sampling"] = sampling
    report["code_root"] = str(args.code_root.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("per_run", "errors")}, ensure_ascii=False, indent=2))
    return 1 if report["errors"] or report["totals"].get("empty_trajectories") else 0


if __name__ == "__main__":
    raise SystemExit(main())
