"""Localization failure: the agent edits files the real fix never touches.

Ground-truth evidence only -- the agent's edits do not intersect the files the
real fix (gold patch) touches.  This is the strong signal, and the reason
:class:`LocalizationFailureDetector` stays silent without gold data instead of
guessing: an unverifiable localization claim is exactly the kind of false
positive that discredits rule-based labelling.
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
from trajdx.heuristics import repo_relative
from trajdx.schema import StepKind

@register_detector
class LocalizationFailureDetector(Detector):
    """Compares the agent's patch against the gold patch's file set.

    Requires ``trajectory.meta['gold_files']``.  Without it the detector yields
    nothing rather than falling back to a guess -- an unverifiable localization
    claim is exactly the kind of false positive that discredits rule-based
    labelling.
    """

    name: ClassVar[str] = "localization_failure"
    category: ClassVar[Category] = Category.LOCALIZATION_FAILURE
    phase: ClassVar[Phase] = Phase.PLANNING

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        gold = trajectory.meta.get("gold_files") or ()
        if not gold:
            return []

        # Both sides are normalized to repo-relative form.  Gold diffs come from a
        # task dataset and agent edits come out of a container path, so comparing
        # them raw produces "no overlap" for every instance -- a false positive on
        # every run.  `repo_relative` is the same normalizer the adapters use.
        gold_set = {repo_relative(g) for g in gold}
        gold_set.discard("")
        patched = {repo_relative(p) for p in trajectory.patch_files}
        patched.discard("")
        edited = {
            repo_relative(f)
            for step in trajectory.steps
            if step.kind is StepKind.EDIT
            for f in step.files_touched
        }
        edited.discard("")

        if patched & gold_set:
            return []

        edited_steps = [
            idx
            for idx, step in enumerate(trajectory.steps)
            if step.kind is StepKind.EDIT
        ]
        touched = sorted(patched or edited)
        return [
            Finding(
                detector=self.name,
                category=Category.LOCALIZATION_FAILURE,
                phase=Phase.PLANNING,
                severity=Severity.HIGH,
                start=edited_steps[0] if edited_steps else 0,
                end=edited_steps[-1] if edited_steps else trajectory.n_steps - 1,
                wasted_steps=tuple(edited_steps),
                evidence=(
                    f"agent edited {touched or 'nothing'} but the gold patch touches "
                    f"{sorted(gold_set)}"
                ),
                confidence=0.95,
                detail={
                    "pattern": "gold_miss",
                    "gold_files": sorted(gold_set),
                    "patched_files": sorted(patched),
                },
            )
        ]
