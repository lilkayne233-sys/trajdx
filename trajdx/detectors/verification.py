"""Verification gaps: the agent submitted a patch it never actually checked.

This is the cheapest failure to detect and one of the most predictive, because a
SWE-bench submission is only ever as good as the test run behind it.  The
detector deliberately charges no wasted steps -- skipping verification does not
burn budget, it *invalidates* the budget already spent, which shows up as a
failure rather than as waste.
"""

from __future__ import annotations

from typing import ClassVar

from trajdx.detectors.base import (
    Category,
    Detector,
    Finding,
    Phase,
    Severity,
    Tier,
    register_detector,
)
from trajdx.heuristics import is_doc_or_config, is_test_or_scratch
from trajdx.schema import AgentStep, StepKind, Trajectory

#: An edit tool that refused the change reports it in the observation.  OpenHands'
#: ``str_replace_editor`` starts with ``ERROR:``; SWE-agent rejects an edit that
#: would introduce a syntax error.  Nothing was written, so it is not an edit.
_EDIT_REJECTED = (
    "error:",
    "no replacement was performed",
    "your proposed edit has introduced new syntax error",
    "your edit was not applied",
)


def edit_was_rejected(step: AgentStep) -> bool:
    # startswith only: a successful edit echoes file content, which may well
    # contain the word "error:" somewhere in its first 200 characters.
    obs = (step.observation or "").lstrip().lower()
    return obs.startswith(_EDIT_REJECTED)


def _edit_region(step: AgentStep) -> str | None:
    """Identity of the code an edit rewrote, so repeated rewrites can be merged.

    Two ``str_replace`` calls with the same ``old_str`` on the same file are one
    piece of work being iterated on, not two independent changes.
    """
    old = step.args.get("old_str")
    if not old:
        return None
    return " ".join(str(old).split())[:200]


def source_edit_events(trajectory: Trajectory) -> list[int]:
    """All successful source edit events, without losing repeated-edit timing."""
    out: list[int] = []
    for idx, step in enumerate(trajectory.steps):
        if step.kind is not StepKind.EDIT or edit_was_rejected(step):
            continue
        files = step.files_touched or ((step.args["path"],) if step.args.get("path") else ())
        # Unknown paths are counted conservatively.
        if files and all(is_test_or_scratch(f) or is_doc_or_config(f) for f in files):
            continue
        out.append(idx)
    return out


def source_edits(trajectory: Trajectory) -> list[int]:
    """Distinct source changes for counting, not for verification chronology.

    Rewrites of the same region count once; every successful rewrite remains in
    source_edit_events so a post-test rewrite still invalidates verification.
    """
    out: list[int] = []
    seen_regions: set[tuple[tuple[str, ...], str]] = set()
    for idx in source_edit_events(trajectory):
        step = trajectory.steps[idx]
        files = step.files_touched or ((step.args["path"],) if step.args.get("path") else ())
        region = _edit_region(step)
        if region is not None and files:
            key = (tuple(sorted(files)), region)
            if key in seen_regions:
                continue
            seen_regions.add(key)
        out.append(idx)
    return out


@register_detector
class VerificationGapDetector(Detector):
    name: ClassVar[str] = "verification_gap"
    category: ClassVar[Category] = Category.VERIFICATION_GAP
    phase: ClassVar[Phase] = Phase.VERIFICATION
    #: 53.8% valid (7/13) on the held-out sample.  The rejections are consistent:
    #: the "later edits" the rule saw were scratch scripts that were then run, so
    #: the run *was* verified even though no library source changed.
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    def __init__(self, stale_grace: int = 3) -> None:
        # Retained for API compatibility.  Even an immediately post-test edit
        # changes the tested tree; distance from the test cannot make it valid.
        self.stale_grace = stale_grace

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        if trajectory.n_steps == 0:
            return []

        test_steps = [i for i, s in enumerate(trajectory.steps) if s.is_test_run]
        edit_steps = source_edits(trajectory)
        edit_events = source_edit_events(trajectory)
        submit_steps = [i for i, s in enumerate(trajectory.steps) if s.kind is StepKind.SUBMIT]
        end = submit_steps[-1] if submit_steps else trajectory.n_steps - 1

        findings: list[Finding] = []

        # --- never verified at all -----------------------------------------
        if not test_steps and edit_steps:
            findings.append(
                Finding(
                    detector=self.name,
                    category=Category.VERIFICATION_GAP,
                    phase=Phase.VERIFICATION,
                    severity=Severity.HIGH,
                    start=edit_steps[0],
                    end=end,
                    wasted_steps=(),
                    evidence=(
                        f"submitted after {len(edit_steps)} edit(s) without ever "
                        f"executing the test suite"
                    ),
                    confidence=0.9,
                    detail={"pattern": "never_verified", "n_edits": len(edit_steps)},
                )
            )
            return findings

        # --- verified, but not after the final change ------------------------
        if test_steps and edit_events:
            last_edit, last_test = edit_events[-1], test_steps[-1]
            if last_edit > last_test and last_test < end:
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.VERIFICATION_GAP,
                        phase=Phase.VERIFICATION,
                        severity=Severity.MEDIUM,
                        start=last_test,
                        end=end,
                        wasted_steps=(),
                        evidence=(
                            f"last test ran at step {last_test} but the final edit was "
                            f"step {last_edit}; {end - last_test} later steps went "
                            f"unverified"
                        ),
                        confidence=0.8,
                        detail={
                            "pattern": "stale_verification",
                            "last_test": last_test,
                            "last_edit": last_edit,
                        },
                    )
                )

        return findings


@register_detector
class WeakVerificationDetector(Detector):
    """Editing library source while testing almost nothing.

    On 300 OpenHands runs this is the strongest corrected process-level signal for
    failure: trajectories with 3 or more *source* edits and fewer than 0.75 test
    executions per edit failed 73% of the time, against a 50% base rate.

    The edit count deliberately excludes tests and scratch scripts.  An earlier
    version of this rule counted every file write, and it reported an 81% failure
    rate from a seemingly much stronger signal -- which turned out to be an
    artefact.  Agents constantly write a ``reproduce_issue.py`` and immediately
    run it, which is verification, not churn; across this dataset **67% of all
    edits touch scratch or test files**.  Counting them made heavy testers look
    like reckless editors.  LLM pre-annotation caught this, which is exactly what
    the annotation loop exists for.

    Note the deliberate asymmetry with the rest of the toolkit: this detector
    charges **no wasted steps**.  Skipping verification does not burn budget, it
    leaves the budget unspent on the one thing that could have caught the bug.
    That is a coverage gap, not waste, and conflating the two would corrupt the
    Wasted Step Ratio.
    """

    name: ClassVar[str] = "weak_verification"
    category: ClassVar[Category] = Category.VERIFICATION_GAP
    phase: ClassVar[Phase] = Phase.VERIFICATION
    #: 18.2% valid (2/11) on the held-out sample, despite a strong *population*
    #: signal (see the docstring).  A rule can separate the groups on average and
    #: still be wrong about most individual runs, and this one is.
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    def __init__(
        self,
        min_edits: int = 3,
        low_intensity: float = 0.75,
        critical_edits: int = 5,
        critical_tests: int = 0,
    ) -> None:
        self.min_edits = min_edits
        self.low_intensity = low_intensity
        self.critical_edits = critical_edits
        self.critical_tests = critical_tests

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        edit_steps = source_edits(trajectory)
        test_steps = [i for i, s in enumerate(trajectory.steps) if s.is_test_run]
        n_edits, n_tests = len(edit_steps), len(test_steps)

        if n_edits < self.min_edits:
            return []

        intensity = n_tests / n_edits
        critical = n_edits >= self.critical_edits and n_tests <= self.critical_tests

        if not critical and intensity >= self.low_intensity:
            return []

        severity = Severity.HIGH if critical else Severity.MEDIUM
        confidence = 0.85 if critical else 0.7

        return [
            Finding(
                detector=self.name,
                category=Category.VERIFICATION_GAP,
                phase=Phase.VERIFICATION,
                severity=severity,
                start=edit_steps[0],
                end=trajectory.n_steps - 1,
                wasted_steps=(),
                evidence=(
                    f"{n_edits} edits against only {n_tests} test run(s) "
                    f"({intensity:.2f} tests per edit)"
                ),
                confidence=confidence,
                detail={
                    "pattern": "low_test_intensity",
                    "n_edits": n_edits,
                    "n_tests": n_tests,
                    "tests_per_edit": round(intensity, 3),
                    "critical": critical,
                },
            )
        ]
