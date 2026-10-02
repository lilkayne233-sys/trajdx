"""Detector protocol and the :class:`Finding` record.

A detector is a pure function over a normalized trajectory: no model calls, no
network, no randomness.  That is the entire point of the tool -- it can label
67k trajectories in the time it takes to read them off disk.

Findings are deliberately *evidence-carrying* rather than boolean flags.  A
finding says which steps were wasted and why, so the CLI can replay exactly the
region that went wrong and a human annotator can agree or disagree with it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Iterable, Sequence

from trajdx.schema import Trajectory


class Phase(str, Enum):
    """Coarse stage of the task lifecycle a failure belongs to.

    Kept aligned with the empirical taxonomy in arXiv:2509.13941 (*An Empirical
    Study on Failures in Automated Issue Solving*), which splits failures into a
    planning / execution / verification triad, so results are directly
    comparable with the manual-analysis literature.
    """

    PLANNING = "planning"
    EXECUTION = "execution"
    VERIFICATION = "verification"


class Category(str, Enum):
    """Fine-grained failure category produced by a detector."""

    LOCALIZATION_FAILURE = "localization_failure"
    EDIT_ERROR = "edit_error"
    VERIFICATION_GAP = "verification_gap"
    TERMINATION_ANOMALY = "termination_anomaly"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Tier(str, Enum):
    """Reporting tier, assigned from *measured* precision on labelled data.

    The rule set was validated by annotating a sample of its findings, and the
    rules did not all survive equally well.  Rather than tune thresholds until
    one aggregate number looked good, rules are split by what the annotation
    actually showed:

    ``CORE``
        The claim held up under review at or above the project's precision bar
        (88%).  These are on by default.
    ``EXPERIMENTAL``
        Still emitted by ``trajdx export`` and available via ``--tier all``, but
        hidden from the default output, because a user acting on these claims
        would too often be acting on a false positive.

    The default is ``EXPERIMENTAL``: a new rule has to earn its way into the
    default set with evidence, not by being written.
    """

    CORE = "core"
    EXPERIMENTAL = "experimental"


@dataclass
class Finding:
    """One rule-based diagnosis attached to a span of steps."""

    detector: str
    category: Category
    phase: Phase
    severity: Severity
    start: int                       # first step of the offending span
    end: int                         # last step (inclusive)
    wasted_steps: tuple[int, ...]    # exactly the steps considered wasted
    evidence: str                    # one-line, human readable justification
    confidence: float = 1.0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def n_wasted(self) -> int:
        return len(self.wasted_steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "category": self.category.value,
            "phase": self.phase.value,
            "severity": self.severity.value,
            "start": self.start,
            "end": self.end,
            "wasted_steps": list(self.wasted_steps),
            "evidence": self.evidence,
            "confidence": self.confidence,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"[{self.severity.value:6}] {self.category.value:22} "
            f"steps {self.start}-{self.end} ({self.n_wasted} wasted) :: {self.evidence}"
        )


class Detector(ABC):
    """Base class for all rules."""

    name: ClassVar[str] = "detector"
    category: ClassVar[Category] = Category.TERMINATION_ANOMALY
    phase: ClassVar[Phase] = Phase.EXECUTION
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    @abstractmethod
    def detect(self, trajectory: Trajectory) -> list[Finding]:
        """Return every instance of this failure mode in ``trajectory``."""

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _indexed(trajectory: Trajectory) -> list[tuple[int, Any]]:
        return list(enumerate(trajectory.steps))


# --------------------------------------------------------------------------
# Shared sequence helpers
# --------------------------------------------------------------------------


def dense_groups(positions: list[int], threshold: int, window: int) -> list[list[int]]:
    """Split sorted step positions into maximal runs that are dense within ``window``.

    A group qualifies when ``threshold`` occurrences fall inside a span of at
    most ``window`` steps.  Density (rather than plain frequency) is what makes
    this a *loop* detector: the same command issued fifty steps apart is normal
    re-verification, whereas the same command issued five steps apart is a loop.
    """
    out: list[list[int]] = []
    i = 0
    while i < len(positions):
        j = i
        while j + 1 < len(positions) and positions[j + 1] - positions[i] < window:
            j += 1
        span = positions[i : j + 1]
        if len(span) >= threshold:
            out.append(span)
            i = j + 1
        else:
            i += 1
    return out


#: ``name -> class``, populated by :func:`register_detector`.
REGISTRY: dict[str, type[Detector]] = {}


def register_detector(cls: type[Detector]) -> type[Detector]:
    REGISTRY[cls.name] = cls
    return cls


def filter_findings(
    findings: Sequence[Finding],
    min_confidence: float = 0.0,
    categories: Iterable[str] | None = None,
    min_severity: Severity | None = None,
) -> list[Finding]:
    """Apply reporting thresholds.

    Detector confidence maps to how strong the underlying evidence is, so the
    threshold is the knob that trades recall for precision when the labelled
    evaluation says a rule is too permissive.
    """
    severity_rank = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}
    floor = severity_rank.get(min_severity, -1) if min_severity else -1
    wanted = set(categories) if categories else None

    return [
        f
        for f in findings
        if f.confidence >= min_confidence
        and (wanted is None or f.category.value in wanted)
        and severity_rank.get(f.severity, 0) >= floor
    ]


def detect_all(
    trajectory: Trajectory,
    only: Iterable[str] | None = None,
    tier: str | Tier | None = None,
    **kwargs: Any,
) -> list[Finding]:
    """Run every registered detector over ``trajectory``.

    ``only`` restricts execution to a subset of detector names, ``tier`` to a
    single reporting tier (``None`` runs everything), and extra keyword
    arguments are forwarded to each detector's constructor so thresholds can be
    swept from the CLI without touching code.
    """
    wanted = set(only) if only else None
    wanted_tier = Tier(tier) if tier is not None else None
    findings: list[Finding] = []
    for name, cls in REGISTRY.items():
        if wanted is not None and name not in wanted:
            continue
        if wanted_tier is not None and cls.tier is not wanted_tier:
            continue
        try:
            detector = cls(**kwargs)
        except TypeError:
            detector = cls()
        findings.extend(detector.detect(trajectory))
    findings.sort(key=lambda f: (f.start, f.category.value))
    return findings
