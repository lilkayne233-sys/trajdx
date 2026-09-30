"""Termination anomalies: runs that end without producing a real patch.

These are the highest-precision signals in the tool because they check the
*artifact* rather than the process: an empty diff or a diff that only touches
scratch files cannot possibly resolve a SWE-bench instance, no matter how
sensible the intervening steps looked.
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
from trajdx.heuristics import is_test_or_scratch
from trajdx.schema import StepKind, Trajectory


@register_detector
class TerminationAnomalyDetector(Detector):
    name: ClassVar[str] = "termination_anomaly"
    category: ClassVar[Category] = Category.TERMINATION_ANOMALY
    phase: ClassVar[Phase] = Phase.EXECUTION
    #: Verified at 96% precision on 26 annotated findings -- the strongest rule in
    #: the set, because it checks the artifact (the diff, the exit status) rather
    #: than inferring intent from the process.
    tier: ClassVar[Tier] = Tier.CORE

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._empty_patch(trajectory))
        capped = self._iteration_cap(trajectory)
        findings.extend(capped)
        findings.extend(self._patch_ignores_source(trajectory))
        findings.extend(self._no_submit(trajectory, capped=bool(capped)))
        return findings

    # ----------------------------------------------------------------- empty
    def _empty_patch(self, trajectory: Trajectory) -> list[Finding]:
        """The run ended with an empty diff, so every step was spent for nothing."""
        patch = trajectory.model_patch
        if patch is None or patch.strip():
            return []
        if trajectory.n_steps == 0:
            return []
        return [
            Finding(
                detector=self.name,
                category=Category.TERMINATION_ANOMALY,
                phase=Phase.EXECUTION,
                severity=Severity.HIGH,
                start=0,
                end=trajectory.n_steps - 1,
                wasted_steps=tuple(range(trajectory.n_steps)),
                evidence=(
                    f"run finished with an empty patch after {trajectory.n_steps} steps"
                ),
                confidence=0.98,
                detail={"pattern": "empty_patch"},
            )
        ]

    # --------------------------------------------------------------- cut off
    def _iteration_cap(self, trajectory: Trajectory) -> list[Finding]:
        status = (trajectory.exit_status or "").lower()
        if "maximum iteration" not in status and "max iteration" not in status:
            return []
        return [
            Finding(
                detector=self.name,
                category=Category.TERMINATION_ANOMALY,
                phase=Phase.EXECUTION,
                severity=Severity.HIGH,
                start=max(0, trajectory.n_steps - 1),
                end=max(0, trajectory.n_steps - 1),
                wasted_steps=(),
                evidence=f"run was killed by the step budget: {trajectory.exit_status}",
                confidence=0.95,
                detail={"pattern": "iteration_cap", "exit_status": trajectory.exit_status},
            )
        ]

    # ------------------------------------------------- patch misses the code
    def _patch_ignores_source(self, trajectory: Trajectory) -> list[Finding]:
        """The only files touched are tests or throwaway repro scripts."""
        patched = trajectory.patch_files
        if not patched:
            return []
        if not all(is_test_or_scratch(p) for p in patched):
            return []
        edit_steps = [
            idx for idx, s in enumerate(trajectory.steps) if s.kind is StepKind.EDIT
        ]
        return [
            Finding(
                detector=self.name,
                category=Category.TERMINATION_ANOMALY,
                phase=Phase.EXECUTION,
                severity=Severity.HIGH,
                start=edit_steps[0] if edit_steps else 0,
                end=edit_steps[-1] if edit_steps else trajectory.n_steps - 1,
                wasted_steps=tuple(edit_steps[1:]) if len(edit_steps) > 1 else (),
                evidence=(
                    f"patch touches only test/scratch files and no library source: "
                    f"{patched}"
                ),
                confidence=0.9,
                detail={"pattern": "patch_ignores_source", "patched_files": list(patched)},
            )
        ]

    # ------------------------------------------------------------- no submit
    def _no_submit(self, trajectory: Trajectory, *, capped: bool = False) -> list[Finding]:
        """The run ended without submitting its work.

        Suppressed when :meth:`_iteration_cap` already fired.  A run cut off by
        the step budget by definition never submitted, so reporting both is one
        anomaly counted twice: on 300 OpenHands runs, 37 of the 39 trajectories
        carrying either pattern carried *both*, and not one carried `no_submit`
        alone.  As a separate finding it added no signal there, only a second
        entry in the precision table.
        """
        if trajectory.n_steps == 0:
            return []
        if capped:
            return []
        if any(s.kind is StepKind.SUBMIT for s in trajectory.steps):
            return []
        return [
            Finding(
                detector=self.name,
                category=Category.TERMINATION_ANOMALY,
                phase=Phase.EXECUTION,
                severity=Severity.MEDIUM,
                start=trajectory.n_steps - 1,
                end=trajectory.n_steps - 1,
                wasted_steps=(),
                evidence="trajectory contains no submit/finish action",
                confidence=0.85,
                detail={"pattern": "no_submit"},
            )
        ]
