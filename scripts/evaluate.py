#!/usr/bin/env python
"""Evaluate detector precision against the annotated sample.

Precision is ``valid / (valid + invalid)``; ``uncertain`` labels are reported
separately rather than quietly folded into either side.

The interesting output is the *threshold curve*.  Detector confidence encodes
how strong the underlying evidence is, so sweeping it shows exactly what
precision is buyable and what recall it costs -- which is the honest way to hit
a precision target rather than by tuning until the number looks good.

Usage
-----
    python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl"

    # README-ready table:
    python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl" --markdown

The detectors are RE-RUN on the raw trajectories every time.  Stored labels are
matched to the findings the current code emits; labels whose finding no longer
exists are reported as stale and never scored.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from trajdx.identity import finding_identity


def load_jsonl(pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(pattern)):
        # utf-8-sig: annotation files are written by a mix of tools, and some
        # emit a BOM that plain utf-8 decoding chokes on.
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


DEFAULT_RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "openhands_sample.jsonl"


def code_version() -> str:
    """Short commit hash of the code that produced these numbers (+dirty if modified)."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                              capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "trajdx"], cwd=root,
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"
    if not head:
        return "unknown"
    return head + ("+dirty" if dirty else "")


def live_findings(raw: Path) -> tuple[dict[str, dict[str, Any]], int]:
    """Run the *current* detectors over ``raw``; return findings keyed by finding_id.

    This is the whole point of the script: precision is measured on what the code
    reports today, never on a stored list of what it once reported.
    """
    import warnings

    from trajdx.adapters import load_file
    from trajdx.detectors import detect_all

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        trajectories = load_file(raw)

    live: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for trajectory in trajectories:
        for finding in detect_all(trajectory):
            identity = finding_identity(trajectory, finding)
            fid = identity["finding_id"]
            if fid in live:
                duplicates += 1
                continue
            live[fid] = {
                **identity,
                "finding_id": fid,
                "instance_id": trajectory.instance_id,
                "resolved": trajectory.resolved,
                "detector": finding.detector,
                "confidence": finding.confidence,
                "evidence": finding.evidence,
            }
    return live, duplicates


def join_labels(raw: Path, labels: dict[str, dict[str, Any]], allow_legacy: bool = False):
    """Join stored verdicts onto freshly computed findings.

    A label whose finding no longer exists (the detector changed, so that step is
    no longer flagged) is **stale**: it is counted and reported, never scored.
    """
    live, duplicates = live_findings(raw)
    legacy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in live.values():
        legacy[row["legacy_finding_id"]].append(row)
    joined = []
    stale = Counter()
    legacy_matched = 0
    rejected_legacy = 0
    for fid, label in labels.items():
        row = live.get(fid)
        if row is None and not fid.startswith("v2|"):
            candidates = legacy.get(fid, [])
            if allow_legacy and len(candidates) == 1:
                row = candidates[0]
                legacy_matched += 1
            else:
                rejected_legacy += 1
        if row is not None and label.get("finding_signature", row["finding_signature"]) != row["finding_signature"]:
            row = None
        if row is not None:
            joined.append({**row, "verdict": label["verdict"], "reason": label.get("reason", "")})
        else:
            detector = label.get("detector") or (fid.split("|")[2] if fid.startswith("v2|") else fid.split("|")[1])
            stale[detector] += 1
    info = {
        "live": len(live),
        "labels": len(labels),
        "matched": len(joined),
        "stale": sum(stale.values()),
        "stale_by_detector": dict(stale),
        "unlabelled": len(live) - len(joined),
        "duplicate_ids": duplicates,
        "legacy_matched": legacy_matched,
        "rejected_legacy": rejected_legacy,
    }
    return joined, info


def coverage_line(info: dict[str, Any]) -> str:
    stale = ", ".join(f"{d}={n}" for d, n in sorted(info["stale_by_detector"].items())) or "none"
    return (
        f"labels: {info['labels']} stored, {info['matched']} still match a finding the current "
        f"code emits, {info['stale']} stale and NOT scored (by detector: {stale}); "
        f"current code emits {info['live']} findings, {info['unlabelled']} of them unlabelled; "
        f"legacy matches={info.get('legacy_matched', 0)} (unverified semantics), "
        f"rejected legacy={info.get('rejected_legacy', 0)}, duplicate IDs={info['duplicate_ids']}."
    )


def precision(valid: int, invalid: int) -> float:
    total = valid + invalid
    return valid / total if total else float("nan")


def _bar(value: float, width: int = 20) -> str:
    if value != value:  # NaN
        return ""
    filled = int(round(value * width))
    return "#" * filled + "." * (width - filled)


def markdown_table(by_detector: dict[str, Counter]) -> str:
    """Render the per-detector precision table as markdown.

    Every registered detector is listed, including the ones that produced no
    findings in this round (n=0, precision "—").  Dropping the silent rules would
    hide the difference between "validated" and "never fired", which is exactly
    the distinction the table exists to make.  Rows are ordered by precision so
    the ordering below is a *result*, not a hand-maintained list.
    """
    from trajdx.detectors import REGISTRY, Tier

    rows: list[tuple[str, str, str, int, float]] = []
    for name, cls in sorted(REGISTRY.items()):
        counter = by_detector.get(name, Counter())
        valid, invalid = counter["valid"], counter["invalid"]
        n = valid + invalid
        if n:
            rows.append((name, cls.tier.value, f"{valid / n:.1%}", n, valid / n))
        else:
            rows.append((name, cls.tier.value, "—", 0, -1.0))
    rows.sort(key=lambda row: (-row[4], row[0]))

    total_valid = sum(c["valid"] for c in by_detector.values())
    total_invalid = sum(c["invalid"] for c in by_detector.values())

    lines = ["| Detector | Tier | Precision | n |", "|---|---|---|---|"]
    for name, tier, prec, n, _ in rows:
        lines.append(f"| `{name}` | {tier} | {prec} | {n} |")
    overall = precision(total_valid, total_invalid)
    lines.append(
        f"| **overall** | | **{overall:.1%}** | **{total_valid + total_invalid}** |"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW,
                    help="raw trajectories; the detectors are re-run on these every time")
    ap.add_argument("--labels", default="data/labels/labelled_v3_*.jsonl")
    ap.add_argument("--target", type=float, default=0.88,
                    help="precision target used to pick a recommended threshold")
    ap.add_argument("--markdown", action="store_true",
                    help="print only the per-detector table as markdown, for the README")
    ap.add_argument("--allow-legacy", action="store_true", help="historical comparison only: reuse unversioned labels when unambiguous")
    args = ap.parse_args()

    if not args.raw.exists():
        print(f"raw trajectories not found: {args.raw}\n"
              "The detectors are re-run on every evaluation, so the raw file is required "
              "(gitignored; regenerate with scripts/fetch_trajectories.py).", file=sys.stderr)
        return 1

    labels = {row["finding_id"]: row for row in load_jsonl(args.labels)}
    joined, join_info = join_labels(args.raw, labels, allow_legacy=args.allow_legacy)

    if not joined:
        print("no stored label matches a finding the current code emits\n"
              + coverage_line(join_info), file=sys.stderr)
        return 1

    by_detector: dict[str, Counter[str]] = defaultdict(Counter)
    for row in joined:
        by_detector[row["detector"]][row["verdict"]] += 1

    if args.markdown:
        print(markdown_table(by_detector))
        print()
        print(f"_{coverage_line(join_info)}_")
        return 0

    valid = sum(1 for r in joined if r["verdict"] == "valid")
    invalid = sum(1 for r in joined if r["verdict"] == "invalid")
    uncertain = sum(1 for r in joined if r["verdict"] == "uncertain")

    print(f"code      : {code_version()}")
    print(coverage_line(join_info))
    print(f"joined    : {len(joined)}")
    print(f"verdicts  : valid={valid} invalid={invalid} uncertain={uncertain}")
    print(f"PRECISION : {precision(valid, invalid):.1%}  (valid / (valid + invalid))\n")

    # ------------------------------------------------------------ per detector
    print("precision by detector")
    header = f"{'detector':22} {'n':>4} {'valid':>6} {'invalid':>8} {'prec':>7}  {'':20}"
    print(header)
    print("-" * len(header))
    for detector, counter in sorted(by_detector.items()):
        v, i = counter["valid"], counter["invalid"]
        p = precision(v, i)
        print(f"{detector:22} {v + i:4d} {v:6d} {i:8d} {p:7.1%}  {_bar(p)}")

    # ---------------------------------------------------------- per confidence
    print("\nprecision by confidence band")
    bands = [(0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    header = f"{'band':14} {'n':>4} {'valid':>6} {'invalid':>8} {'prec':>7}"
    print(header)
    print("-" * len(header))
    for lo, hi in bands:
        rows = [r for r in joined if lo <= r["confidence"] < hi]
        v = sum(1 for r in rows if r["verdict"] == "valid")
        i = sum(1 for r in rows if r["verdict"] == "invalid")
        if v + i:
            print(f"{lo:.2f}-{hi:.2f}    {v + i:4d} {v:6d} {i:8d} {precision(v, i):7.1%}")

    # --------------------------------------------------------- threshold curve
    print("\nthreshold curve (detector confidence cutoff)")
    header = (f"{'min_conf':>9} {'kept':>6} {'valid':>6} {'invalid':>8} "
              f"{'prec':>7} {'retained':>8}")
    print(header)
    print("-" * (len(header) + 22))
    best_threshold = None
    for threshold in [0.0, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]:
        rows = [r for r in joined if r["confidence"] >= threshold]
        v = sum(1 for r in rows if r["verdict"] == "valid")
        i = sum(1 for r in rows if r["verdict"] == "invalid")
        if v + i == 0:
            continue
        p = precision(v, i)
        recall = v / valid if valid else 0.0
        flag = ""
        if best_threshold is None and p >= args.target:
            best_threshold = threshold
            flag = "  <-- meets target"
        print(f"{threshold:9.2f} {v + i:6d} {v:6d} {i:8d} {p:7.1%} {recall:8.1%}  {_bar(p)}{flag}")

    print()
    if best_threshold is not None:
        print(f"lowest threshold meeting the {args.target:.0%} precision target: "
              f"{best_threshold:.2f}")
    else:
        print(f"no confidence threshold reaches {args.target:.0%} precision on this sample.")

    # -------------------------------------------------------- frequent mistakes
    print("\nmost common reasons findings were rejected")
    reasons = Counter(r["reason"] for r in joined if r["verdict"] == "invalid")
    for reason, count in reasons.most_common(10):
        print(f"  {count:3d}  {reason[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
