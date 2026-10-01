"""Edit flailing: the agent fighting its own edit tool.

A rejected edit is ordinary debugging -- the tool refused the change, the agent
reads the error and tries again.  What is not ordinary is *repeatedly* failing
to land an edit on the same file: at that point the agent is burning steps
against its own tooling, usually because it keeps proposing a change that
introduces a syntax error or no longer matches the file.  The signal is purely
syntactic -- the tool's own rejection message -- so there is no intent to
guess at, which is why this rule exists despite the corpus having a dedicated
``edit_rejected`` error fingerprint that no detector previously consumed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import ClassVar

from trajdx.detectors.base import (
    Category,
    Detector,
    Finding,
    Phase,
    Severity,
    dense_groups,
    register_detector,
)
from trajdx.detectors.verification import edit_was_rejected
from trajdx.heuristics import repo_relative
from trajdx.schema import StepKind, Trajectory


@register_detector
class EditErrorDetector(Detector):
    """Flags consecutive rejected edits against the same file."""

    name: ClassVar[str] = "edit_error"
    category: ClassVar[Category] = Category.EDIT_ERROR
    phase: ClassVar[Phase] = Phase.EXECUTION

    def __init__(self, min_repeats: int = 3, window: int = 10) -> None:
        self.min_repeats = min_repeats
        self.window = window

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        by_path: dict[str, list[int]] = defaultdict(list)
        for idx, step in enumerate(trajectory.steps):
            if step.kind is not StepKind.EDIT:
                continue
            if not (
                edit_was_rejected(step)
                or (step.error_fp or "").startswith("edit_rejected")
            ):
                continue
            files = step.files_touched or (
                (step.args["path"],) if step.args.get("path") else ()
            )
            if files:
                by_path[repo_relative(files[0])].append(idx)

        findings: list[Finding] = []
        for path, positions in by_path.items():
            for span in dense_groups(positions, self.min_repeats, self.window):
                # A landed edit to the same file inside the span means the
                # agent recovered between rejections; that is progress, and
                # the rejections around it do not form one continuous fight.
                recovered = any(
                    trajectory.steps[k].kind is StepKind.EDIT
                    and not edit_was_rejected(trajectory.steps[k])
                    and path
                    in {
                        repo_relative(f)
                        for f in (
                            trajectory.steps[k].files_touched
                            or (
                                (trajectory.steps[k].args["path"],)
                                if trajectory.steps[k].args.get("path")
                                else ()
                            )
                        )
                    }
                    for k in range(span[0], span[-1] + 1)
                )
                if recovered:
                    continue
                wasted = tuple(span[1:])  # the first rejection was legitimate
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.EDIT_ERROR,
                        phase=Phase.EXECUTION,
                        severity=Severity.MEDIUM,
                        start=span[0],
                        end=span[-1],
                        wasted_steps=wasted,
                        evidence=(
                            f"{len(span)} rejected edits against {path} within "
                            f"{span[-1] - span[0]} steps"
                        ),
                        confidence=0.8,
                        detail={
                            "pattern": "rejected_edit_streak",
                            "path": path,
                            "occurrences": span,
                        },
                    )
                )
        return findings
