"""Localization failure: the agent edits the wrong place, or never commits to one.

Two grades of evidence are available and they are handled separately:

* **Process-only** (always available) -- reading without editing, re-reading the
  same file, and search that never converts into an edit.  These are proxies.
* **Ground-truth** (only when the gold patch is known) -- the agent's edits do
  not intersect the files the real fix touches.  This is the strong signal, and
  the reason :class:`LocalizationFailureDetector` stays silent without gold data
  instead of guessing.
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
    Tier,
    register_detector,
)
from trajdx.heuristics import repo_relative
from trajdx.schema import StepKind, Trajectory

_EXPLORE_KINDS = (StepKind.READ, StepKind.SEARCH)


@register_detector
class BlindSearchDetector(Detector):
    """Flags long stretches of exploration that never turn into an edit.

    Annotators mostly rejected this: a ten-step inspection run that walks a
    module, its tests and its callers looks identical to flailing from the
    outside, and the rule cannot tell them apart (0/1 valid on the held-out
    sample).  It stays available because the signal is real when it fires, but
    it is not something to put in front of a user by default.
    """

    name: ClassVar[str] = "blind_search"
    category: ClassVar[Category] = Category.BLIND_SEARCH
    phase: ClassVar[Phase] = Phase.PLANNING
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    def __init__(self, min_run: int = 10, explore_budget: int = 4) -> None:
        self.min_run = min_run
        self.explore_budget = explore_budget

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        return self._read_without_edit(trajectory)

    # -------------------------------------------------- exploration streaks
    def _read_without_edit(self, trajectory: Trajectory) -> list[Finding]:
        """A run of locate-only steps longer than the exploration budget.

        Some exploration is mandatory -- you cannot fix what you have not found --
        so only the steps beyond ``explore_budget`` are charged as wasted.
        """
        findings: list[Finding] = []
        run: list[int] = []

        def flush() -> None:
            if len(run) >= self.min_run:
                wasted = tuple(run[self.explore_budget:])
                files = sorted(
                    {
                        f
                        for i in run
                        for f in trajectory.steps[i].files_touched
                    }
                )
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.BLIND_SEARCH,
                        phase=Phase.PLANNING,
                        severity=Severity.MEDIUM,
                        start=run[0],
                        end=run[-1],
                        wasted_steps=wasted,
                        evidence=(
                            f"{len(run)} consecutive read/search steps with no edit "
                            f"({len(files)} distinct files inspected)"
                        ),
                        confidence=0.7,
                        detail={
                            "pattern": "read_without_edit",
                            "run_length": len(run),
                            "files_inspected": files[:20],
                        },
                    )
                )
            run.clear()

        for idx, step in enumerate(trajectory.steps):
            if step.kind in _EXPLORE_KINDS:
                run.append(idx)
            else:
                flush()
        flush()
        return findings


@register_detector
class RedundantReadDetector(Detector):
    """Re-opening a file and getting back byte-identical content.

    The signal is not "this file was viewed twice" -- checking your own work is
    good practice, and a file can legitimately be revisited after other files
    change.  What is provably wasted is viewing it again and having the second
    look return exactly what the first one did: the step bought no information.

    Measured at 4/4 valid on the held-out annotation sample -- but that sample is the
    v4 round, whose human verdicts are **not shipped in this repository**.  The
    reproducible v3 round contains no `redundant_read` findings at all, so within
    this repo the rule has no evidence either way.  A tier is a claim about
    measured precision, so it is held at ``EXPERIMENTAL`` until a round that is
    actually shipped supports it; the 4/4 is recorded here as provenance rather
    than as a validation.

    It lives in its own class rather than inside ``BlindSearchDetector`` because
    the two rules of that family behave quite differently (a clean 4/4 against a
    rule that fired once), and a reporting tier only means something when a
    detector holds rules of comparable, *verifiable* quality.
    """

    name: ClassVar[str] = "redundant_read"
    category: ClassVar[Category] = Category.BLIND_SEARCH
    phase: ClassVar[Phase] = Phase.PLANNING
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    def __init__(self, max_redundant_reads: int = 3) -> None:
        self.max_redundant_reads = max_redundant_reads

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        by_path: dict[str, list[int]] = defaultdict(list)
        for idx, step in enumerate(trajectory.steps):
            if step.kind is StepKind.READ and step.files_touched:
                by_path[step.files_touched[0]].append(idx)

        findings: list[Finding] = []
        for path, reads in by_path.items():
            by_content: dict[str, list[int]] = defaultdict(list)
            for idx in reads:
                by_content[trajectory.steps[idx].observation_key].append(idx)

            for repeats in by_content.values():
                if len(repeats) < self.max_redundant_reads:
                    continue
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.BLIND_SEARCH,
                        phase=Phase.PLANNING,
                        severity=Severity.LOW,
                        start=repeats[0],
                        end=repeats[-1],
                        wasted_steps=tuple(repeats[1:]),
                        evidence=(
                            f"{path} viewed {len(repeats)}x returning identical "
                            f"content each time"
                        ),
                        confidence=0.8,
                        detail={
                            "pattern": "redundant_read",
                            "path": path,
                            "reads": repeats,
                        },
                    )
                )
        return findings


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
