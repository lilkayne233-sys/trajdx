"""Process-waste quantification.

The headline number is the **Wasted Step Ratio**::

    WSR = |steps charged as wasted| / |steps taken|

Two design decisions matter more than the formula.

*Waste is attributed, not summed.*  A step often triggers several detectors --
a repeating failing ``pip install`` is both an execution loop and an environment
stall.  Counting it twice would let the ratio exceed 1.0 and would make the
per-category breakdown meaningless, so each wasted step is assigned to exactly
one category, by descending severity.

*Only unproductive repetition is charged.*  Exploring the repository, running
the suite, or reproducing the bug all cost steps and are all necessary.  A step
earns the "wasted" label only when a detector can point at concrete evidence
that nothing was learned from it -- it was the second, third or fourth identical
attempt, not the first.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from trajdx.detectors.base import Category, Finding, Phase, Severity
from trajdx.heuristics import is_test_or_scratch
from trajdx.schema import StepKind, Trajectory

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


# --------------------------------------------------------------------------
# Process-shape metrics
# --------------------------------------------------------------------------


def source_edits(trajectory: Trajectory) -> int:
    """Edits that touched library source, excluding tests and scratch scripts.

    A raw edit count is dominated by noise: 67% of all edits in the shipped
    corpus land on test or scratch files, so "how much did the agent change"
    is only meaningful once those are subtracted.
    """
    return sum(
        1
        for step in trajectory.steps
        if step.kind is StepKind.EDIT
        and not is_test_or_scratch(step.args.get("path"))
    )


def test_runs(trajectory: Trajectory) -> int:
    """Shell steps that actually executed the test suite."""
    return sum(1 for step in trajectory.steps if step.is_test_run)


def tests_per_source_edit(trajectory: Trajectory) -> float:
    """Verification intensity: how often the agent checked its own work.

    This is the strongest actionable signal in the corpus (AUC 0.66 against the
    resolved label, inverted), and it is the metric the CLI now leads with.
    Successful runs test more per edit than failing ones.
    """
    return test_runs(trajectory) / max(1, source_edits(trajectory))


def test_run_ratio(trajectory: Trajectory) -> float:
    """Share of the whole run spent executing tests rather than editing."""
    return test_runs(trajectory) / max(1, trajectory.n_steps)


def novel_observation_ratio(trajectory: Trajectory) -> float:
    """Share of steps that produced a never-before-seen observation.

    Computed straight off the observation identity hashes, with no detector in
    the loop.  A run stuck in a loop keeps re-deriving observations it already
    had, so its novelty share collapses; a healthy run -- even a long one --
    keeps learning something per step.  Steps with no observation at all are
    excluded: a blank output carries no information by construction.

    This is the replacement the Wasted Step Ratio never was: WSR aggregates
    detector output (and inherits every detector's blind spots), while this
    metric reads the one signal every step carries anyway.
    """
    seen: set[str] = set()
    novel = 0
    total = 0
    for step in trajectory.steps:
        key = step.observation_key
        if not key or key == "empty":
            continue
        total += 1
        if key not in seen:
            novel += 1
            seen.add(key)
    return novel / total if total else 0.0


@dataclass
class WasteReport:
    """Per-trajectory waste accounting."""

    instance_id: str
    framework: str
    resolved: bool | None
    total_steps: int
    wasted_steps: int
    ratio: float
    n_findings: int
    by_category: dict[str, int] = field(default_factory=dict)
    by_phase: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    # --- process shape, which carries the signal waste does not -------------
    source_edits: int = 0
    test_runs: int = 0
    tests_per_source_edit: float = 0.0
    test_run_ratio: float = 0.0
    novel_observation_ratio: float = 0.0

    def to_dict(self, with_findings: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "instance_id": self.instance_id,
            "framework": self.framework,
            "resolved": self.resolved,
            "total_steps": self.total_steps,
            "wasted_steps": self.wasted_steps,
            "wasted_step_ratio": round(self.ratio, 4),
            "n_findings": self.n_findings,
            "by_category": self.by_category,
            "by_phase": self.by_phase,
            "by_severity": self.by_severity,
            "source_edits": self.source_edits,
            "test_runs": self.test_runs,
            "tests_per_source_edit": round(self.tests_per_source_edit, 4),
            "test_run_ratio": round(self.test_run_ratio, 4),
            "novel_observation_ratio": round(self.novel_observation_ratio, 4),
        }
        if with_findings:
            out["findings"] = [f.to_dict() for f in self.findings]
        return out


def attribute_waste(findings: Sequence[Finding]) -> dict[int, str]:
    """Map each wasted step to exactly one category, highest severity winning."""
    attribution: dict[int, str] = {}
    for finding in sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3)):
        for step in finding.wasted_steps:
            attribution.setdefault(step, finding.category.value)
    return attribution


def wasted_step_ratio(
    trajectory: Trajectory,
    findings: Sequence[Finding],
) -> WasteReport:
    """Compute the full waste accounting for one diagnosed trajectory."""
    attribution = attribute_waste(findings)

    by_category = Counter(attribution.values())
    by_phase: Counter[str] = Counter()
    category_phase = {c.value: c for c in Category}
    phase_of = {
        "execution_loop": Phase.EXECUTION,
        "environment_stuck": Phase.EXECUTION,
        "termination_anomaly": Phase.EXECUTION,
        "edit_error": Phase.EXECUTION,
        "localization_failure": Phase.PLANNING,
        "blind_search": Phase.PLANNING,
        "verification_gap": Phase.VERIFICATION,
    }
    for category, count in by_category.items():
        phase = phase_of.get(category)
        by_phase[(phase or Phase.EXECUTION).value] += count

    by_severity = Counter(f.severity.value for f in findings)
    total = trajectory.n_steps

    return WasteReport(
        instance_id=trajectory.instance_id,
        framework=trajectory.framework,
        resolved=trajectory.resolved,
        total_steps=total,
        wasted_steps=len(attribution),
        ratio=(len(attribution) / total) if total else 0.0,
        n_findings=len(findings),
        by_category=dict(by_category),
        by_phase=dict(by_phase),
        by_severity=dict(by_severity),
        findings=list(findings),
        source_edits=source_edits(trajectory),
        test_runs=test_runs(trajectory),
        tests_per_source_edit=tests_per_source_edit(trajectory),
        test_run_ratio=test_run_ratio(trajectory),
        novel_observation_ratio=novel_observation_ratio(trajectory),
    )


# --------------------------------------------------------------------------
# Aggregate analysis
# --------------------------------------------------------------------------


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


@dataclass
class OutcomeStats:
    """Aggregate behaviour of one outcome group (resolved / unresolved)."""

    label: str
    n: int
    mean_steps: float
    mean_wasted: float
    mean_ratio: float
    category_rate: dict[str, float]      # fraction of runs showing >=1 finding
    category_waste: dict[str, float]     # mean wasted steps attributed

    # --- process shape, the part that actually separates outcomes ----------
    mean_source_edits: float = 0.0
    mean_test_runs: float = 0.0
    mean_tests_per_source_edit: float = 0.0
    mean_test_run_ratio: float = 0.0
    mean_novel_observation_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n": self.n,
            "mean_steps": round(self.mean_steps, 2),
            "mean_wasted_steps": round(self.mean_wasted, 2),
            "mean_wasted_step_ratio": round(self.mean_ratio, 4),
            "category_rate": {k: round(v, 4) for k, v in self.category_rate.items()},
            "mean_waste_by_category": {k: round(v, 2) for k, v in self.category_waste.items()},
            "mean_source_edits": round(self.mean_source_edits, 2),
            "mean_test_runs": round(self.mean_test_runs, 2),
            "mean_tests_per_source_edit": round(self.mean_tests_per_source_edit, 2),
            "mean_test_run_ratio": round(self.mean_test_run_ratio, 4),
            "mean_novel_observation_ratio": round(self.mean_novel_observation_ratio, 4),
        }


@dataclass
class AggregateReport:
    """Corpus-level view, and the resolved/unresolved contrast that validates it."""

    n_trajectories: int
    overall: OutcomeStats
    by_outcome: dict[str, OutcomeStats] = field(default_factory=dict)
    detector_precision_sample: dict[str, int] = field(default_factory=dict)

    def category_lift(self, positive: str = "unresolved", negative: str = "resolved") -> dict[str, float]:
        """How much more often a category fires on failing runs than on passing ones.

        A detector whose category fires just as often on resolved runs is not
        describing failure at all, so this contrast is the cheapest available
        sanity check on the whole rule set.
        """
        pos = self.by_outcome.get(positive)
        neg = self.by_outcome.get(negative)
        if not pos or not neg:
            return {}
        keys = set(pos.category_rate) | set(neg.category_rate)
        return {
            k: pos.category_rate.get(k, 0.0) - neg.category_rate.get(k, 0.0)
            for k in sorted(keys)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_trajectories": self.n_trajectories,
            "overall": self.overall.to_dict(),
            "by_outcome": {k: v.to_dict() for k, v in self.by_outcome.items()},
            "category_lift_unresolved_minus_resolved": {
                k: round(v, 4) for k, v in self.category_lift().items()
            },
        }


def _outcome_stats(label: str, reports: Sequence[WasteReport]) -> OutcomeStats:
    if not reports:
        return OutcomeStats(label, 0, 0.0, 0.0, 0.0, {}, {})

    n = len(reports)
    category_hits: Counter[str] = Counter()
    category_waste: Counter[str] = Counter()
    for report in reports:
        seen = {f.category.value for f in report.findings}
        for category in seen:
            category_hits[category] += 1
        for category, count in report.by_category.items():
            category_waste[category] += count

    return OutcomeStats(
        label=label,
        n=n,
        mean_steps=_mean(r.total_steps for r in reports),
        mean_wasted=_mean(r.wasted_steps for r in reports),
        mean_ratio=_mean(r.ratio for r in reports),
        category_rate={k: v / n for k, v in category_hits.items()},
        category_waste={k: v / n for k, v in category_waste.items()},
        mean_source_edits=_mean(r.source_edits for r in reports),
        mean_test_runs=_mean(r.test_runs for r in reports),
        mean_tests_per_source_edit=_mean(r.tests_per_source_edit for r in reports),
        mean_test_run_ratio=_mean(r.test_run_ratio for r in reports),
        mean_novel_observation_ratio=_mean(r.novel_observation_ratio for r in reports),
    )


def aggregate(reports: Sequence[WasteReport]) -> AggregateReport:
    """Summarize a corpus of per-trajectory reports."""
    grouped: dict[str, list[WasteReport]] = defaultdict(list)
    for report in reports:
        if report.resolved is None:
            grouped["unknown"].append(report)
        else:
            grouped["resolved" if report.resolved else "unresolved"].append(report)

    return AggregateReport(
        n_trajectories=len(reports),
        overall=_outcome_stats("all", reports),
        by_outcome={label: _outcome_stats(label, group) for label, group in grouped.items()},
    )
