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


def created_files(trajectory: Trajectory) -> set[str]:
    """Files (any name, any extension) this trajectory created with a successful edit.

    The v3 review's three remaining false positives were all the same shape:
    the agent writes ``edge_cases.py`` / ``original_repro.py`` /
    ``edge_case_tests.py`` as verification tooling and runs it, but the names
    match no scratch pattern, so the *creation* counted as a source edit and
    made the run look unverified after its last real change.  The reviewer's
    ruling is the definition now: a file the agent created this run is tooling
    the agent brought into existence, not library source it modified, whatever
    it is called.
    """
    out: set[str] = set()
    for step in trajectory.steps:
        if step.kind is not StepKind.EDIT or edit_was_rejected(step):
            continue
        created = str(step.args.get("verb") or "") == "create" or (
            step.observation or ""
        ).lstrip().startswith("File created successfully")
        if not created:
            continue
        files = step.files_touched or (
            (step.args["path"],) if step.args.get("path") else ()
        )
        for f in files:
            p = repo_relative(f)
            if p:
                out.add(p)
    return out


def created_scripts(trajectory: Trajectory) -> set[str]:
    """The subset of :func:`created_files` that are runnable scripts."""
    return {p for p in created_files(trajectory) if _SCRIPT_EXT.search(p)}


def agent_written_scripts(trajectory: Trajectory) -> set[str]:
    """Scripts the agent itself wrote earlier in this run, by any name.

    The verification gap's top false positive was exactly this shape: the agent
    writes ``reproduce_issue.py`` and runs it after every edit, which *is*
    verification -- but ``is_test_command`` only credits ``test_*.py`` and the
    pytest family, so the run looked unverified.  Membership is decided by
    provenance (created by an edit this run), not by name, so running a script
    that shipped with the repo is never mistaken for self-checking.
    """
    return created_scripts(trajectory)


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
    created = created_scripts(trajectory)
    out: list[int] = []
    for idx, step in enumerate(trajectory.steps):
        if step.kind is not StepKind.EDIT or edit_was_rejected(step):
            continue
        files = step.files_touched or ((step.args["path"],) if step.args.get("path") else ())
        # Unknown paths are counted conservatively.
        if files and all(
            is_test_or_scratch(f) or is_doc_or_config(f) or repo_relative(f) in created
            for f in files
        ):
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
class SubmitDespiteFailureDetector(Detector):
    """The final verification failed and the agent submitted anyway.

    ``verification_gap`` covers *absence* of checking; this covers the harder
    case -- the agent did check, saw the run fail, and submitted the patch
    regardless, with no later passing check.  The last verification step's own
    outcome (exit code, error fingerprint) is a logged fact, not an inference.

    A failed test run mid-trajectory is ordinary debugging and never fires:
    only the *last* verification before a real ``submit`` is examined.  Runs
    without a submit step belong to ``termination_anomaly`` and are skipped.
    """

    name: ClassVar[str] = "submit_despite_failure"
    category: ClassVar[Category] = Category.SUBMIT_DESPITE_FAILURE
    phase: ClassVar[Phase] = Phase.VERIFICATION
    tier: ClassVar[Tier] = Tier.EXPERIMENTAL

    def detect(self, trajectory: Trajectory) -> list[Finding]:
        submit_steps = [
            i for i, s in enumerate(trajectory.steps) if s.kind is StepKind.SUBMIT
        ]
        if not submit_steps or not source_edits(trajectory):
            return []
        end = submit_steps[-1]

        verify_steps = [i for i in verification_steps(trajectory) if i < end]
        if not verify_steps:
            return []
        last = verify_steps[-1]
        step = trajectory.steps[last]
        if not (step.error_kind or (step.exit_code is not None and step.exit_code != 0)):
            return []

        return [
            Finding(
                detector=self.name,
                category=Category.SUBMIT_DESPITE_FAILURE,
                phase=Phase.VERIFICATION,
                severity=Severity.HIGH,
                start=last,
                end=end,
                wasted_steps=(),
                evidence=(
                    f"last verification before submit (step {last}) failed"
                    + (f": {step.error_kind}" if step.error_kind else "")
                    + (f" (exit {step.exit_code})" if step.exit_code else "")
                    + f"; submitted at step {end} with no later passing check"
                ),
                confidence=0.8,
                detail={
                    "pattern": "submitted_after_failed_verification",
                    "last_verification": last,
                    "submit_step": end,
                    "error_kind": step.error_kind,
                    "exit_code": step.exit_code,
                },
            )
        ]
