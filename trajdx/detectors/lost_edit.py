"""Lost edits: source changes the agent landed but the final patch forgot.

A rejected edit is visible everywhere -- the tool said so, and ``edit_error``
already flags repeated rejections.  An edit that *succeeded* leaves a much
quieter trail: the tool confirmed the write, and the work simply never shows up
in ``model_patch``, usually because a later ``git checkout`` / ``stash`` /
revert discarded it.  Nothing in the run flags that loss; the report is silently
submitted without the fix the agent had already found.

This detector is a pure artifact reconciliation: the set of pre-existing,
non-scratch files with a *landed* edit is compared against the files the final
patch touches.  Files created this run are excluded (``created_files``): an
agent-written script or scratch fixture is tooling, not the fix, and counting
its absence would reintroduce the false positives that shaped the verification
detectors.  Files under test/scratch paths are excluded for the same reason.

The one deliberate blind spot is recorded rather than hidden: a landed edit
followed by a revert command *is* the loss being flagged, and there is no
syntactic way to distinguish "agent discarded an experiment, then found a
better fix elsewhere" (fine) from "agent reverted and forgot to redo" (the
failure).  Both look identical in the artifact; review decides, which is why
the rule ships as experimental.
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
from trajdx.detectors.verification import created_files, edit_was_rejected
from trajdx.heuristics import is_doc_or_config, is_revert_command, is_test_or_scratch, repo_relative
from trajdx.schema import StepKind, Trajectory


@register_detector
class LostEditDetector(Detector):
    """Flags landed edits to pre-existing source files missing from the patch."""

    name: ClassVar[str] = "lost_edit"
    category: ClassVar[Category] = Category.LOST_EDIT
    phase: ClassVar[Phase] = Phase.EXECUTION
    #: Artifact-level reconciliation with a known blind spot (see module
    #: docstring); experimental until review quantifies how often the blind
    #: spot fires.
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        if not trajectory.model_patch or not trajectory.model_patch.strip():
            return []  # empty patch is termination_anomaly's territory

        patched = {p for p in trajectory.patch_files if p}
        created = created_files(trajectory)

        def in_patch(p: str) -> bool:
            # Container layouts leak a duplicated root component into edit paths
            # (SWE-agent mounts the repo at /{repo_name}, so an absolute edit
            # path normalizes to ``repo/src/x.py`` while the patch says
            # ``src/x.py``).  Compare on path suffixes, which is conservative:
            # a mismatched suffix can only *hide* a lost edit, never invent one.
            if p in patched:
                return True
            return any(p.endswith("/" + q) or q.endswith("/" + p) for q in patched)

        by_path: dict[str, list[int]] = {}
        for idx, step in enumerate(trajectory.steps):
            if step.kind is not StepKind.EDIT or edit_was_rejected(step):
                continue
            files = step.files_touched or (
                (step.args["path"],) if step.args.get("path") else ()
            )
            for f in files:
                p = repo_relative(f)
                if not p or p in created or is_test_or_scratch(p) \
                        or is_doc_or_config(p):
                    continue
                by_path.setdefault(p, []).append(idx)

        lost = {p: idxs for p, idxs in by_path.items() if not in_patch(p)}
        if not lost:
            return []

        steps = sorted({i for idxs in lost.values() for i in idxs})
        evidence = "; ".join(
            f"{p} edited at step {idxs[0]}" for p, idxs in sorted(lost.items())[:3]
        )
        return [
            Finding(
                detector=self.name,
                category=Category.LOST_EDIT,
                phase=Phase.EXECUTION,
                severity=Severity.MEDIUM,
                start=steps[0],
                end=steps[-1],
                wasted_steps=tuple(steps),
                evidence=(
                    f"{len(lost)} edited file(s) absent from the final patch "
                    f"(later discarded?): {evidence}"
                ),
                confidence=0.75,
                detail={
                    "pattern": "edit_missing_from_patch",
                    "lost_files": sorted(lost),
                    "edit_steps": steps,
                    "had_revert_after_edit": any(
                        step.kind is StepKind.SHELL
                        and is_revert_command(str(step.args.get("command") or ""))
                        for step in trajectory.steps[steps[-1]:]
                    ),
                },
            )
        ]
