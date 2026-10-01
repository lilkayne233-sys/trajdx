"""Verification gaps: the agent submitted a patch it never actually checked.

This is the cheapest failure to detect and one of the most predictive, because a
SWE-bench submission is only ever as good as the test run behind it.  The
detector deliberately charges no wasted steps -- skipping verification does not
burn budget, it *invalidates* the budget already spent, which shows up as a
failure rather than as waste.
"""

from __future__ import annotations

import re
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
from trajdx.heuristics import (
    is_doc_or_config,
    is_test_or_scratch,
    repo_relative,
    split_segments,
)
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


# --------------------------------------------------------------------------
# What counts as "the agent checked its own work"
# --------------------------------------------------------------------------

#: ``python repro.py``, ``bash try.sh`` -- an interpreter invoked on a script.
_INTERPRETED_SCRIPT = re.compile(
    r"""(?x)
      (?:^|[\s;&|(]) (?:python[0-9.]*|pypy[0-9.]*|bash|sh|zsh|ksh|node|deno|bun)
      \s+ ['"]? (\S+\.(?:py|sh|js|mjs|ts)) ['"]? (?:\s|$)
    """
)

#: A script executed directly (``./repro.py``), which the shell runs via its
#: shebang.  Only the *command word* qualifies: ``cat repro.py`` is a look, not
#: a run.
_SCRIPT_DIRECT = re.compile(r"\.(?:py|sh|js|mjs|ts)$")

_SCRIPT_EXT = re.compile(r"\.(?:py|sh|js|mjs|ts)$")


def _invoked_script_paths(command: str | None) -> set[str]:
    """Script files a shell command executes, per segment, quotes respected."""
    if not command:
        return set()
    paths: set[str] = set()
    for segment in split_segments(command.strip()):
        seg = segment.strip()
        if not seg:
            continue
        tokens = seg.split()
        if tokens and _SCRIPT_DIRECT.search(tokens[0]):
            paths.add(tokens[0])
        matched = _INTERPRETED_SCRIPT.search(seg)
        if matched:
            paths.add(matched.group(1))
    return paths


def agent_written_scripts(trajectory: Trajectory) -> set[str]:
    """Scratch scripts the agent itself wrote earlier in this run.

    The verification gap's top false positive was exactly this shape: the agent
    writes ``reproduce_issue.py`` and runs it after every edit, which *is*
    verification -- but ``is_test_command`` only credits ``test_*.py`` and the
    pytest family, so the run looked unverified.  Only scratch-named scripts
    count, and only ones this trajectory actually created, so running a script
    that shipped with the repo is never mistaken for self-checking.
    """
    out: set[str] = set()
    for step in trajectory.steps:
        if step.kind is not StepKind.EDIT or edit_was_rejected(step):
            continue
        files = step.files_touched or (
            (step.args["path"],) if step.args.get("path") else ()
        )
        for f in files:
            p = repo_relative(f)
            if p and _SCRIPT_EXT.search(p) and is_test_or_scratch(p):
                out.add(p)
    return out


def verification_steps(trajectory: Trajectory) -> list[int]:
    """Every step where the agent checked its own work, in order.

    Test-suite runs (``is_test_run``, which already covers ``python -c``
    probes) plus executions of scripts the agent wrote itself this run.  Both
    verification detectors read through this one definition, so "what counts
    as verification" can never drift between the gap rule and the intensity
    rule.
    """
    written = agent_written_scripts(trajectory)
    out: list[int] = []
    for idx, step in enumerate(trajectory.steps):
        if step.is_test_run:
            out.append(idx)
            continue
        if not written or step.kind is not StepKind.SHELL:
            continue
        for path in _invoked_script_paths(str(step.args.get("command") or "")):
            if repo_relative(path) in written:
                out.append(idx)
                break
    return out


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
    """Distinct source-edit *sessions* for counting, not for verification chronology.

    One session is continuous unverified work on the same region of the same
    file.  A new session starts when the agent verifies (a test run or one of
    its own scripts), moves to a different file, or edits a different region --
    that last condition keeps the pilot ruling that rewriting function ``f``
    five times is one piece of work while touching ``g`` afterwards is another.

    Every successful rewrite remains in ``source_edit_events`` so a post-test
    rewrite still invalidates verification timing.
    """
    events = source_edit_events(trajectory)
    if not events:
        return []
    verified = set(verification_steps(trajectory))
    out: list[int] = []
    prev_key: tuple[frozenset[str], str | None] | None = None
    prev_idx: int | None = None
    for idx in events:
        step = trajectory.steps[idx]
        files = frozenset(
            step.files_touched or ((step.args["path"],) if step.args.get("path") else ())
        )
        key = (files, _edit_region(step))
        same_session = (
            prev_idx is not None
            and key == prev_key
            and not any(prev_idx < v < idx for v in verified)
        )
        if not same_session:
            out.append(idx)
        prev_idx, prev_key = idx, key
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

        verify_steps = verification_steps(trajectory)
        edit_steps = source_edits(trajectory)
        edit_events = source_edit_events(trajectory)
        submit_steps = [i for i, s in enumerate(trajectory.steps) if s.kind is StepKind.SUBMIT]
        end = submit_steps[-1] if submit_steps else trajectory.n_steps - 1

        findings: list[Finding] = []

        # --- never verified at all -----------------------------------------
        if not verify_steps and edit_steps:
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
                        f"submitted after {len(edit_steps)} edit session(s) without ever "
                        f"executing the test suite or a self-written script"
                    ),
                    confidence=0.9,
                    detail={"pattern": "never_verified", "n_edits": len(edit_steps)},
                )
            )
            return findings

        # --- verified, but not after the final change ------------------------
        if verify_steps and edit_events:
            last_edit, last_verify = edit_events[-1], verify_steps[-1]
            if last_edit > last_verify and last_verify < end:
                findings.append(
                    Finding(
                        detector=self.name,
                        category=Category.VERIFICATION_GAP,
                        phase=Phase.VERIFICATION,
                        severity=Severity.MEDIUM,
                        start=last_verify,
                        end=end,
                        wasted_steps=(),
                        evidence=(
                            f"last verification ran at step {last_verify} but the final edit was "
                            f"step {last_edit}; {end - last_verify} later steps went "
                            f"unverified"
                        ),
                        confidence=0.8,
                        detail={
                            "pattern": "stale_verification",
                            "last_test": last_verify,
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

    The edit count deliberately excludes tests and scratch scripts, and counts
    *sessions* rather than raw edits: consecutive unverified rewrites of the
    same region are one piece of work, not N edits (see ``source_edits``).  An
    earlier version counted every file write, and it reported an 81% failure
    rate from a seemingly much stronger signal -- which turned out to be an
    artefact.  Agents constantly write a ``reproduce_issue.py`` and immediately
    run it, which is verification, not churn; across this dataset **67% of all
    edits touch scratch or test files**.  Counting them made heavy testers look
    like reckless editors.  LLM pre-annotation caught this, which is exactly
    what the annotation loop exists for.

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
        test_steps = verification_steps(trajectory)
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
