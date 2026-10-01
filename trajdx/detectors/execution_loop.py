"""Execution-loop detection: the agent repeating itself without making progress.

Three distinct signatures are checked, because "the agent is stuck" looks
different depending on whether the *action* repeats, the *error* repeats, or the
two alternate:

``exact``
    The same action is issued again within a short window.
``error``
    Different actions keep producing the *same* error fingerprint.  This is the
    cognitive-deadlock signature: the agent is editing code, but not the code
    that the failure depends on.
``oscillation``
    Two actions alternate A-B-A-B, a thrash pattern that neither of the above
    catches.

The load-bearing condition
--------------------------
All three patterns are gated on the same rule: **nothing may have been edited
between the repetitions**.  Re-running a command after changing a file is how an
agent tests a hypothesis, and the first version of this detector fired on that
constantly.

Measured on annotated findings, the distinction is stark:

===========================================  =====  =========
condition                                    n      precision
===========================================  =====  =========
an edit happened between the repetitions     29      6.9%
nothing was edited between the repetitions    1    100.0%
===========================================  =====  =========

So on OpenHands trajectories, "the agent repeated a command" is almost never
evidence of waste on its own -- which is a real result, and the opposite of what
a naive loop detector assumes.  The gated rule is rare (5 findings corpus-wide,
down from 136 when the gate is off), and the no-edit cell holds a single case:
too few to claim precision, which is why this detector ships as experimental.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, ClassVar

from trajdx.detectors.base import (
    Category,
    Detector,
    Finding,
    Phase,
    Severity,
    dense_groups,
    register_detector,
)
from trajdx.schema import StepKind, Trajectory

#: Steps that are bookkeeping rather than action.  A `think` or `task_tracker`
#: call repeated three times is a habit, not a loop the budget is being burnt on.
_NON_ACTION_KINDS = frozenset({StepKind.THOUGHT, StepKind.PLAN})


@register_detector
class ExecutionLoopDetector(Detector):
    """Flags repeated actions, repeated errors and A-B-A-B thrashing."""

    name: ClassVar[str] = "execution_loop"
    category: ClassVar[Category] = Category.EXECUTION_LOOP
    phase: ClassVar[Phase] = Phase.EXECUTION

    def __init__(
        self,
        min_repeats: int = 3,
        window: int = 10,
        error_repeats: int = 3,
        error_window: int = 20,
        oscillate_cycles: int = 3,
        ignore_thoughts: bool = True,
        require_same_observation: bool = True,
        require_no_intervening_edit: bool = True,
    ) -> None:
        self.min_repeats = min_repeats
        self.window = window
        self.error_repeats = error_repeats
        self.error_window = error_window
        self.oscillate_cycles = oscillate_cycles
        self.ignore_thoughts = ignore_thoughts
        self.require_same_observation = require_same_observation
        self.require_no_intervening_edit = require_no_intervening_edit

    # ---------------------------------------------------------------- helpers
    def _edited_between(self, trajectory: Trajectory, lo: int, hi: int) -> bool:
        """Did the agent modify any file between the first and last repetition?

        This single condition decides whether a repetition is waste.  Re-running
        a command *after changing something* is how an agent tests a hypothesis;
        re-running it with nothing changed is the only case where the step
        provably bought no information.
        """
        return any(
            trajectory.steps[i].kind is StepKind.EDIT for i in range(lo, hi + 1)
        )

    # ---------------------------------------------------------------- detect
    def detect(self, trajectory: Trajectory) -> list[Finding]:
        steps = trajectory.steps
        if len(steps) < min(self.min_repeats, self.error_repeats, 2 * self.oscillate_cycles):
            return []

        exact = self._exact_action_loops(trajectory)
        findings = list(exact)
        # Suppress an error diagnosis only when an emitted exact finding really
        # covers all its occurrences.  A shared coarse action_key does not prove
        # coverage: exact thresholds/windows or payloads may differ.
        exact_coverage = [set(f.detail["occurrences"]) for f in exact]
        for finding in self._error_loops(trajectory):
            occurrences = set(finding.detail["occurrences"])
            if not any(occurrences <= covered for covered in exact_coverage):
                findings.append(finding)
        findings.extend(self._oscillations(trajectory))
        return findings

    # ------------------------------------------------------ repeated actions
    def _exact_action_loops(self, trajectory: Trajectory) -> list[Finding]:
        # `exact_key`, not `action_key`: two edits to the same file with different
        # new content are different actions, and only byte-identical repeats are
        # provably wasted.  LLM pre-annotation caught an earlier version that
        # grouped every `str_replace` on a file together by path alone and fired
        # on ordinary incremental editing -- its top false-positive mode.
        keys = [
            (
                None
                if self.ignore_thoughts and s.kind in _NON_ACTION_KINDS
                else s.exact_key
            )
            for s in trajectory.steps
        ]
        groups: dict[str, list[int]] = defaultdict(list)
        for idx, key in enumerate(keys):
            if key is not None:
                groups[key].append(idx)

        findings: list[Finding] = []
        for key, positions in groups.items():
            for span in dense_groups(positions, self.min_repeats, self.window):
                if self.require_no_intervening_edit and self._edited_between(
                    trajectory, span[0], span[-1]
                ):
                    continue
                wasted = tuple(span[1:])  # the first attempt was legitimate
                span_steps = [trajectory.steps[i] for i in span]
                n_failed = sum(1 for s in span_steps if s.failed)

                # A repeated action only proves a loop if it produced the same
                # result.  When observations differ the agent learned something
                # new each time, so the repetition was justified -- this is what
                # stops over-aggressive argument scrubbing from inventing loops.
                if self.require_same_observation:
                    dominant = Counter(s.observation_key for s in span_steps).most_common(1)[0][1]
                    if dominant < len(span) - 1:
                        continue
                # A repeat that keeps failing is a much stronger signal than a
                # repeat that succeeds (re-reading a file can be legitimate).
                severity = (
                    Severity.HIGH
                    if n_failed >= len(span) - 1
                    else Severity.MEDIUM if n_failed else Severity.LOW
                )
                sample = span_steps[0]
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.EXECUTION_LOOP,
                        phase=Phase.EXECUTION,
                        severity=severity,
                        start=span[0],
                        end=span[-1],
                        wasted_steps=wasted,
                        evidence=(
                            f"same action repeated {len(span)}x within {self.window} steps: "
                            f"{sample.action_summary(72)}"
                            + (f" ({n_failed} of them failed)" if n_failed else "")
                        ),
                        confidence=0.95 if n_failed else 0.7,
                        detail={
                            "pattern": "exact",
                            "action_key": key,
                            "occurrences": span,
                            "n_failed": n_failed,
                        },
                    )
                )
        return findings

    # -------------------------------------------------------- repeated error
    def _error_loops(self, trajectory: Trajectory) -> list[Finding]:
        """Same error fingerprint recurring while the agent keeps changing tactics."""
        groups: dict[str, list[int]] = defaultdict(list)
        for idx, step in enumerate(trajectory.steps):
            if step.error_fp:
                groups[step.error_fp].append(idx)

        findings: list[Finding] = []
        for fingerprint, positions in groups.items():
            for span in dense_groups(positions, self.error_repeats, self.error_window):
                if self.require_no_intervening_edit and self._edited_between(
                    trajectory, span[0], span[-1]
                ):
                    continue
                wasted = tuple(span[1:])
                actions = {trajectory.steps[i].action_key for i in span}
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.EXECUTION_LOOP,
                        phase=Phase.EXECUTION,
                        severity=Severity.HIGH,
                        start=span[0],
                        end=span[-1],
                        wasted_steps=wasted,
                        evidence=(
                            f"identical error returned {len(span)}x over "
                            f"{span[-1] - span[0]} steps despite {len(actions)} distinct "
                            f"action(s): {fingerprint[:100]}"
                        ),
                        confidence=0.9,
                        detail={
                            "pattern": "error",
                            "error_fp": fingerprint,
                            "occurrences": span,
                            "distinct_actions": len(actions),
                        },
                    )
                )
        return findings

    # ---------------------------------------------------------- oscillation
    def _oscillations(self, trajectory: Trajectory) -> list[Finding]:
        """Detect A-B-A-B-A-B style alternation between two actions."""
        keys = [s.action_key for s in trajectory.steps]
        findings: list[Finding] = []
        reported: set[int] = set()
        i = 0
        n = len(keys)
        while i + 3 < n:
            a, b = keys[i], keys[i + 1]
            if a == b:
                i += 1
                continue
            cycles = 0
            j = i
            # Each iteration advances by two, so we always land on an "A"
            # position: the pattern is A-B-A-B, not an alternating expectation.
            while j + 1 < n and keys[j] == a and keys[j + 1] == b:
                cycles += 1
                j += 2
            if cycles >= self.oscillate_cycles and i not in reported:
                span = list(range(i, min(i + cycles * 2, n)))
                if self.require_no_intervening_edit and self._edited_between(
                    trajectory, span[0], span[-1]
                ):
                    i += 1
                    continue
                reported.add(i)
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.EXECUTION_LOOP,
                        phase=Phase.EXECUTION,
                        severity=Severity.MEDIUM,
                        start=span[0],
                        end=span[-1],
                        wasted_steps=tuple(span[2:]),
                        evidence=(
                            f"oscillating between two actions for {cycles} cycles: "
                            f"{trajectory.steps[i].action_summary(48)} <-> "
                            f"{trajectory.steps[i + 1].action_summary(48)}"
                        ),
                        confidence=0.75,
                        detail={"pattern": "oscillation", "cycles": cycles},
                    )
                )
                i += cycles * 2
                continue
            i += 1
        return findings
