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
from trajdx.schema import Trajectory

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n": self.n,
            "mean_steps": round(self.mean_steps, 2),
            "mean_wasted_steps": round(self.mean_wasted, 2),
            "mean_wasted_step_ratio": round(self.mean_ratio, 4),
            "category_rate": {k: round(v, 4) for k, v in self.category_rate.items()},
            "mean_waste_by_category": {k: round(v, 2) for k, v in self.category_waste.items()},
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
