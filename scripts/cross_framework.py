#!/usr/bin/env python
"""Put two agent frameworks side by side on the same normalized vocabulary.

The point of the adapter layer is that everything downstream is framework-blind,
so this script is the check on that claim: it runs both corpora through the same
pipeline and prints the same statistics for each.  If the normalization were
lossy or framework-specific, the step-kind mixes and process-shape numbers would
come out visibly incomparable.

Read the caveat before reading the numbers
------------------------------------------
The two corpora are **not** a controlled comparison and the script says so on
every run:

* OpenHands: 300 SWE-bench-style task runs, 150 resolved / 150 unresolved.
* SWE-agent: ~21 runs vendored by the upstream project, mostly *demonstrations*
  (CTF puzzles and smoke tests) plus one real SWE-bench instance.

They differ in task mix, in size, and in age.  Any per-framework difference below
therefore reflects the corpora, not the agents, and the honest use of this script
is adapter validation -- "does the same pipeline read both, and does its step
vocabulary survive contact with real logs" -- rather than a benchmark.

Usage
-----
    python scripts/cross_framework.py
    python scripts/cross_framework.py --openhands data/raw/openhands_sample.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trajdx.adapters import load_file  # noqa: E402
from trajdx.detectors import detect_all  # noqa: E402
from trajdx.metrics import wasted_step_ratio  # noqa: E402

CAVEAT = """\
NOTE: these corpora are not a controlled comparison.  The OpenHands set is 300
SWE-bench-style task runs; the SWE-agent set is ~21 upstream demo/smoke-test logs
plus one real SWE-bench instance.  Differences below reflect the corpora, not the
agents.  Use this for adapter validation, not for ranking frameworks."""


def summarize(label: str, path: Path, only: list[str] | None) -> dict | None:
    if not path.exists():
        print(f"! {label}: {path} not found (run the matching fetch script)", file=sys.stderr)
        return None

    trajectories = load_file(path, framework=only[0] if only else None)
    kinds: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    totals = Counter()
    n_steps = n_source_edits = n_test_runs = n_waste = 0
    unmapped = 0

    for trajectory in trajectories:
        findings = detect_all(trajectory)
        report = wasted_step_ratio(trajectory, findings)
        n_steps += report.total_steps
        n_source_edits += report.source_edits
        n_test_runs += report.test_runs
        n_waste += report.wasted_steps
        for step in trajectory.steps:
            kinds[step.kind.value] += 1
            if step.kind.value == "other":
                unmapped += 1
        for finding in findings:
            categories[finding.category.value] += 1
        totals["runs"] += 1

    runs = totals["runs"] or 1
    return {
        "label": label,
        "path": str(path),
        "runs": totals["runs"],
        "steps": n_steps,
        "mean_steps": n_steps / runs,
        "unmapped": unmapped,
        "kinds": kinds,
        "mean_source_edits": n_source_edits / runs,
        "mean_test_runs": n_test_runs / runs,
        "tests_per_edit": n_test_runs / max(1, n_source_edits),
        "wasted_step_ratio": n_waste / max(1, n_steps),
        "findings_per_run": sum(categories.values()) / runs,
        "categories": categories,
    }


def _row(name: str, left, right, fmt: str = "{}") -> str:
    lv = fmt.format(left) if left is not None else "—"
    rv = fmt.format(right) if right is not None else "—"
    return f"{name:26} {lv:>14} {rv:>14}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--openhands", type=Path, default=Path("data/raw/openhands_sample.jsonl"))
    ap.add_argument("--sweagent", type=Path, default=Path("data/raw/sweagent_sample.jsonl"))
    args = ap.parse_args()

    left = summarize("openhands", args.openhands, ["openhands"])
    right = summarize("sweagent", args.sweagent, ["sweagent"])

    print(CAVEAT)
    print()
    if left is None and right is None:
        return 1

    print(_row("metric", "openhands" if left else None,
               "sweagent" if right else None))
    print("-" * 56)

    def get(side, key):
        return side[key] if side else None

    print(_row("runs", get(left, "runs"), get(right, "runs")))
    print(_row("total steps", get(left, "steps"), get(right, "steps")))
    print(_row("mean steps per run", get(left, "mean_steps"), get(right, "mean_steps"), "{:.1f}"))
    print(_row("unmapped steps (kind=other)", get(left, "unmapped"), get(right, "unmapped")))
    print()
    print(_row("mean source edits", get(left, "mean_source_edits"),
               get(right, "mean_source_edits"), "{:.2f}"))
    print(_row("mean test runs", get(left, "mean_test_runs"),
               get(right, "mean_test_runs"), "{:.2f}"))
    print(_row("tests per source edit", get(left, "tests_per_edit"),
               get(right, "tests_per_edit"), "{:.2f}"))
    print(_row("wasted step ratio", get(left, "wasted_step_ratio"),
               get(right, "wasted_step_ratio"), "{:.2%}"))
    print(_row("findings per run", get(left, "findings_per_run"),
               get(right, "findings_per_run"), "{:.2f}"))
    print()

    labels = sorted({k for side in (left, right) if side for k in side["kinds"]})
    print("step-kind mix (share of all steps)")
    print("-" * 56)
    for kind in labels:
        def share(side):
            if not side or not side["steps"]:
                return None
            return side["kinds"].get(kind, 0) / side["steps"]
        print(_row(f"  {kind}", share(left), share(right), "{:.1%}"))

    categories = sorted({k for side in (left, right) if side for k in side["categories"]})
    if categories:
        print()
        print("findings by category (total count)")
        print("-" * 56)
        for category in categories:
            print(_row(f"  {category}",
                       get(left, "categories").get(category, 0) if left else None,
                       get(right, "categories").get(category, 0) if right else None))

    if left and right:
        print()
        if left["unmapped"] == 0 and right["unmapped"] == 0:
            print("adapter check: both corpora normalized with zero unmapped steps")
        else:
            print(f"adapter check: unmapped steps present "
                  f"(openhands={left['unmapped']}, sweagent={right['unmapped']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())