"""Environment-stuck detection: the agent fighting the sandbox instead of the bug.

These failures look like progress in the logs -- the agent is running commands --
but nothing it does can affect the outcome, because the environment it needs to
run the tests in never becomes usable.

Why the thresholds are left conservative
----------------------------------------
This rule fires almost never on the shipped SWE-rebench/OpenHands corpus, and the
reason is worth recording so it is not mistaken for a bug:

======================================================  =====
measured on the 300-trajectory sample
======================================================  =====
failing setup/install commands, whole corpus                8
trajectories containing any of them                         6
timeout steps, whole corpus                                 4
trajectories with more than one timeout                     0
findings at ``min_failures=3``                              0
findings at ``min_failures=2``                              1
======================================================  =====

The environments in this dataset are pre-built containers, so agents rarely have
to fight them.  Loosening the threshold to 2 buys exactly one extra finding, and
the single annotated ``environment_stuck`` finding in the label set was judged
*invalid*, so there is no evidence that the looser rule would be precise.  The
thresholds therefore stay where they are: a rule that stays quiet on data that
does not contain the phenomenon is behaving correctly, and manufacturing volume
by lowering the bar would only add unvalidated claims.
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
from trajdx.heuristics import is_install_command, is_setup_command
from trajdx.schema import StepKind, Trajectory


@register_detector
class EnvironmentStuckDetector(Detector):
    """Flags repeated failing setup/install commands and timeout walls."""

    name: ClassVar[str] = "environment_stuck"
    category: ClassVar[Category] = Category.ENVIRONMENT_STUCK
    phase: ClassVar[Phase] = Phase.EXECUTION

    def __init__(
        self,
        min_failures: int = 3,
        window: int = 20,
        min_timeouts: int = 3,
    ) -> None:
        self.min_failures = min_failures
        self.window = window
        self.min_timeouts = min_timeouts

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._repeated_failing_setup(trajectory))
        findings.extend(self._timeout_wall(trajectory))
        return findings

    # ------------------------------------------------- failing env commands
    def _repeated_failing_setup(self, trajectory: Trajectory) -> list[Finding]:
        groups: dict[str, list[int]] = defaultdict(list)
        for idx, step in enumerate(trajectory.steps):
            if step.kind is not StepKind.SHELL or not step.failed:
                continue
            command = str(step.args.get("command") or "")
            if is_install_command(command) or is_setup_command(command):
                groups[step.action_key].append(idx)

        findings: list[Finding] = []
        for key, positions in groups.items():
            for span in dense_groups(positions, self.min_failures, self.window):
                sample = trajectory.steps[span[0]]
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.ENVIRONMENT_STUCK,
                        phase=Phase.EXECUTION,
                        severity=Severity.HIGH,
                        start=span[0],
                        end=span[-1],
                        wasted_steps=tuple(span[1:]),
                        evidence=(
                            f"setup/install command failed {len(span)}x in "
                            f"{span[-1] - span[0] + 1} steps: {sample.action_summary(72)}"
                        ),
                        confidence=0.9,
                        detail={"pattern": "failing_setup", "action_key": key, "occurrences": span},
                    )
                )
        return findings

    # ------------------------------------------------------------ timeouts
    def _timeout_wall(self, trajectory: Trajectory) -> list[Finding]:
        positions = [
            idx for idx, step in enumerate(trajectory.steps) if step.error_kind == "timeout"
        ]
        findings: list[Finding] = []
        for span in dense_groups(positions, self.min_timeouts, self.window):
            findings.append(
                Finding(
                    detector=self.name,
                    category=Category.ENVIRONMENT_STUCK,
                    phase=Phase.EXECUTION,
                    severity=Severity.HIGH,
                    start=span[0],
                    end=span[-1],
                    wasted_steps=tuple(span[1:]),
                    evidence=(
                        f"{len(span)} commands hit a timeout between steps "
                        f"{span[0]}-{span[-1]}"
                    ),
                    confidence=0.85,
                    detail={"pattern": "timeout", "occurrences": span},
                )
            )
        return findings

