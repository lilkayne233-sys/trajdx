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
    python scripts/evaluate.py --findings data/labels/to_label_v2.jsonl \
                              --labels data/labels/labelled_v2_*.jsonl

    # README-ready table for the shipped v3 round:
    python scripts/evaluate.py --findings data/labels/to_label_v3.jsonl \
                              --labels "data/labels/labelled_v3_*.jsonl" --markdown
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
    ap.add_argument("--findings", type=Path, default=Path("data/labels/to_label_v2.jsonl"))
    ap.add_argument("--labels", default="data/labels/labelled_v2_*.jsonl")
    ap.add_argument("--target", type=float, default=0.88,
                    help="precision target used to pick a recommended threshold")
    ap.add_argument("--markdown", action="store_true",
                    help="print only the per-detector table as markdown, for the README")
    args = ap.parse_args()

    findings = {row["finding_id"]: row for row in load_jsonl(str(args.findings))}
    labels = {row["finding_id"]: row for row in load_jsonl(args.labels)}

    joined, missing_label, unknown_id = [], [], []
    for fid, finding in findings.items():
        label = labels.get(fid)
        if label is None:
            missing_label.append(fid)
            continue
        joined.append({**finding, "verdict": label["verdict"], "reason": label.get("reason", "")})
    for fid in labels:
        if fid not in findings:
            unknown_id.append(fid)

    if not joined:
        print("no joined rows; check the findings/labels paths", file=sys.stderr)
        return 1

    by_detector: dict[str, Counter[str]] = defaultdict(Counter)
    for row in joined:
        by_detector[row["detector"]][row["verdict"]] += 1

    if args.markdown:
        print(markdown_table(by_detector))
        return 0

    valid = sum(1 for r in joined if r["verdict"] == "valid")
    invalid = sum(1 for r in joined if r["verdict"] == "invalid")
    uncertain = sum(1 for r in joined if r["verdict"] == "uncertain")

    print(f"findings  : {len(findings)}")
    print(f"labelled  : {len(labels)}  (missing label: {len(missing_label)}, unmatched id: {len(unknown_id)})")
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
              f"{'prec':>7} {'recall':>8}")
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
