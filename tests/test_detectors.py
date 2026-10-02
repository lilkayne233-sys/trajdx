"""Detector behaviour, including the false positives that were found in the wild.

Several tests here pin down bugs that only surfaced once detector output was
annotated: an execution-loop rule that grouped edits by filename, a verification
rule that counted scratch scripts as source churn, and an environment rule that
treated ``cd`` as setup work.
"""

from __future__ import annotations

from trajdx.detectors import detect_all
from trajdx.detectors.base import Category, Severity
from trajdx.detectors.edit_error import EditErrorDetector
from trajdx.detectors.localization import LocalizationFailureDetector
from trajdx.detectors.termination import TerminationAnomalyDetector
from trajdx.detectors.verification import (
    VerificationGapDetector,
    source_edit_events,
    source_edits,
)
from trajdx.metrics import aggregate, attribute_waste, wasted_step_ratio
from trajdx.schema import AgentStep, StepKind
from tests.conftest import edit, make_trajectory, read, shell, submit, thought

# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def test_source_edits_separates_library_from_scratch():
    steps = [
        edit(0, "src/pkg/core.py"),
        edit(1, "tests/test_core.py"),
        edit(2, "reproduce_issue.py"),
        edit(3, "docs/index.md"),
    ]
    # docs/config are not library source either
    assert source_edits(make_trajectory(steps)) == [0]


def test_never_verified_when_edits_precede_submit():
    steps = [edit(0, "src/pkg/core.py", payload="x"), submit(1)]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert [f for f in findings if f.detail["pattern"] == "never_verified"]


def test_repeated_edit_after_test_is_a_new_unverified_session():
    first = edit(0, "src/pkg/core.py", payload="first")
    first.args["old_str"] = "def f(): return 0"
    repeated = edit(2, "src/pkg/core.py", payload="second")
    repeated.args["old_str"] = first.args["old_str"]
    steps = [
        first,
        shell(1, "pytest", observation="1 passed", is_test_run=True),
        repeated,
        submit(3),
    ]
    trajectory = make_trajectory(steps)
    # The rewrite landed *after* the test, so it is a new unverified session;
    # chronology is what decides staleness, and it still counts both events.
    assert source_edits(trajectory) == [0, 2]
    assert source_edit_events(trajectory) == [0, 2], "retain every edit event for timing"
    findings = VerificationGapDetector().detect(trajectory)
    assert len(findings) == 1
    assert findings[0].detail == {
        "pattern": "stale_verification", "last_test": 1, "last_edit": 2
    }


def test_edit_immediately_before_submit_is_stale_even_with_default_grace():
    steps = [
        shell(0, "pytest", observation="1 passed", is_test_run=True),
        edit(1, "src/pkg/core.py", payload="final change"),
        submit(2),
    ]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert len(findings) == 1
    assert findings[0].detail["pattern"] == "stale_verification"
    assert findings[0].detail["last_edit"] == 1


def test_test_after_repeated_edit_restores_verification():
    first = edit(0, "src/pkg/core.py", payload="first")
    first.args["old_str"] = "def f(): return 0"
    repeated = edit(2, "src/pkg/core.py", payload="second")
    repeated.args["old_str"] = first.args["old_str"]
    steps = [
        first,
        shell(1, "pytest", observation="1 passed", is_test_run=True),
        repeated,
        shell(3, "pytest", observation="1 passed", is_test_run=True),
        submit(4),
    ]
    assert VerificationGapDetector().detect(make_trajectory(steps)) == []


def test_rejected_or_non_source_post_test_edit_does_not_invalidate_verification():
    rejected = edit(2, "src/pkg/core.py", payload="bad change")
    rejected.observation = "ERROR: No replacement was performed"
    for last_edit in [rejected, edit(2, "tests/test_core.py"), edit(2, "README.md")]:
        steps = [
            edit(0, "src/pkg/core.py"),
            shell(1, "pytest", observation="1 passed", is_test_run=True),
            last_edit,
            submit(3),
        ]
        assert VerificationGapDetector().detect(make_trajectory(steps)) == []


def test_running_tests_clears_never_verified():
    steps = [
        edit(0, "src/pkg/core.py", payload="x"),
        shell(1, "python -m pytest tests/", observation="1 passed", is_test_run=True),
        submit(2),
    ]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert not [f for f in findings if f.detail["pattern"] == "never_verified"]


def test_running_agent_written_repro_script_counts_as_verification():
    """Reviewed FP: `python reproduce_issue.py` after every edit IS verification.

    The AI review round rejected a `stale_verification` finding whose evidence
    showed the agent re-running its own repro script (which imported the edited
    library) three times after the final change.  `is_test_command` only credits
    pytest and `test_*.py`, so those runs were invisible.  A scratch script the
    agent itself wrote earlier in the run now counts.
    """
    steps = [
        edit(0, "reproduce_issue.py", verb="create",
             observation="File created successfully at: /workspace/reproduce_issue.py"),
        edit(1, "src/pkg/core.py", payload="v1"),
        shell(2, "python reproduce_issue.py", observation="ValueError: bug"),
        edit(3, "src/pkg/core.py", payload="v2"),
        shell(4, "python reproduce_issue.py", observation="all good"),
        submit(5),
    ]
    assert VerificationGapDetector().detect(make_trajectory(steps)) == []


def test_running_a_preexisting_script_is_not_verification():
    """Only scripts the agent itself wrote count -- the repo's own tools do not."""
    steps = [
        edit(0, "src/pkg/core.py"),
        shell(1, "python scripts/other.py", observation="done"),
        submit(2),
    ]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert [f for f in findings if f.detail["pattern"] == "never_verified"]


def test_verification_script_with_unscratch_name_is_not_a_source_edit():
    """v3 invalids 2+3 (jsonargparse-560, pandas-61158): scripts of any name.

    The agent created ``original_repro.py`` / ``edge_case_tests.py`` -- names no
    scratch pattern matches -- and ran them.  Creation is tooling, not library
    churn, and running the script is verification; neither stale_verification
    nor an inflated edit count may survive.
    """
    steps = [
        edit(0, "src/pkg/_typehints.py", payload="v1"),
        shell(1, "python -m pytest tests/", observation="1 passed", is_test_run=True),
        edit(2, "edge_case_tests.py", verb="create",
             observation="File created successfully at: /workspace/edge_case_tests.py"),
        shell(3, "python edge_case_tests.py", observation="all passed"),
        submit(4),
    ]
    traj = make_trajectory(steps)
    assert source_edit_events(traj) == [0], "the script creation is not a source edit"
    assert VerificationGapDetector().detect(traj) == []


def test_script_creation_after_the_last_verification_closes_nothing():
    """v3 invalid 1 (pyupgrade-195): a created-but-unrun script is not churn.

    The last real source edit was verified; the agent then created
    ``edge_cases.py`` and never got to run it.  That creation must not reopen
    the verification gap.
    """
    steps = [
        edit(0, "src/pkg/pyupgrade.py", payload="v1"),
        shell(1, "python -m pytest tests/", observation="1 passed", is_test_run=True),
        edit(2, "edge_cases.py", verb="create",
             observation="File created successfully at: /workspace/edge_cases.py"),
        submit(3),
    ]
    traj = make_trajectory(steps)
    assert source_edit_events(traj) == [0]
    assert VerificationGapDetector().detect(traj) == []


# --------------------------------------------------------------------------
# Edit errors
# --------------------------------------------------------------------------


def _rejected_edit(idx: int, path: str = "src/pkg/core.py") -> AgentStep:
    step = edit(idx, path)
    step.observation = "ERROR:\nNo replacement was performed, old_str did not appear"
    return step


def test_rejected_edit_streak_fires():
    """Three refusals in a row on one file: the agent is fighting its tool."""
    steps = [
        _rejected_edit(0),
        _rejected_edit(1),
        _rejected_edit(2),
        edit(3, "src/pkg/core.py", payload="finally"),  # landed after the streak
        submit(4),
    ]
    findings = EditErrorDetector().detect(make_trajectory(steps))
    assert len(findings) == 1
    assert findings[0].detail["pattern"] == "rejected_edit_streak"
    assert findings[0].detail["path"] == "src/pkg/core.py"
    assert findings[0].wasted_steps == (1, 2), "the first rejection was legitimate"


def test_rejected_edits_with_a_landed_edit_in_between_do_not_fire():
    """A landed edit inside the span means the agent recovered: progress, not flailing."""
    steps = [
        _rejected_edit(0),
        _rejected_edit(1),
        edit(2, "src/pkg/core.py", payload="recovered"),
        _rejected_edit(3),
        _rejected_edit(4),
        submit(5),
    ]
    assert EditErrorDetector().detect(make_trajectory(steps)) == []


def test_single_rejected_edit_never_fires():
    """One refusal followed by success is ordinary debugging."""
    steps = [_rejected_edit(0), edit(1, "src/pkg/core.py", payload="ok"), submit(2)]
    assert EditErrorDetector().detect(make_trajectory(steps)) == []


# --------------------------------------------------------------------------
# Termination
# --------------------------------------------------------------------------


def test_iteration_cap_is_flagged():
    trajectory = make_trajectory(
        [shell(0, "python repro.py")],
        exit_status="RuntimeError: Agent reached maximum iteration. Current iteration: 100",
    )
    findings = TerminationAnomalyDetector().detect(trajectory)
    assert [f for f in findings if f.detail["pattern"] == "iteration_cap"]


def test_patch_touching_only_scratch_is_flagged():
    patch = "+++ b/reproduce_issue.py\n+++ b/final_verification.py\n"
    trajectory = make_trajectory(
        [
            edit(0, "reproduce_issue.py", verb="create",
                 observation="File created successfully at: /workspace/reproduce_issue.py"),
            edit(1, "final_verification.py", verb="create",
                 observation="File created successfully at: /workspace/final_verification.py"),
            submit(2),
        ],
        model_patch=patch,
    )
    findings = TerminationAnomalyDetector().detect(trajectory)
    assert [f for f in findings if f.detail["pattern"] == "patch_ignores_source"]


def test_patch_touching_source_is_not_flagged():
    patch = "+++ b/src/pkg/core.py\n+++ b/reproduce_issue.py\n"
    trajectory = make_trajectory(
        [edit(0, "src/pkg/core.py"), submit(1)], model_patch=patch
    )
    findings = TerminationAnomalyDetector().detect(trajectory)
    assert not [f for f in findings if f.detail["pattern"] == "patch_ignores_source"]


def test_patch_ignores_source_believes_a_modified_repo_file_over_its_name():
    """v4 invalid (pre-commit-hooks-274): check_yaml.py is the repo's own module.

    ``pre_commit_hooks/check_yaml.py`` matches the scratch regex's ``check[_-]``
    prefix, but the agent modified it with a successful str_replace and shipped
    it in the patch -- a modified pre-existing file is real source, whatever it
    is named.  The scratch-name guess must never override observed modification.
    """
    patch = "+++ b/pre_commit_hooks/check_yaml.py\n"
    trajectory = make_trajectory(
        [edit(0, "pre_commit_hooks/check_yaml.py", payload="v1"), submit(1)],
        model_patch=patch,
    )
    findings = TerminationAnomalyDetector().detect(trajectory)
    assert not [f for f in findings if f.detail["pattern"] == "patch_ignores_source"]


def test_empty_patch_charges_every_step():
    trajectory = make_trajectory(
        [thought(0), read(1, "src/a.py"), shell(2, "ls")], model_patch=""
    )
    findings = TerminationAnomalyDetector().detect(trajectory)
    empty = [f for f in findings if f.detail["pattern"] == "empty_patch"]
    assert empty and empty[0].wasted_steps == (0, 1, 2)


# --------------------------------------------------------------------------
# Blind search
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Waste accounting
# --------------------------------------------------------------------------


def test_waste_attribution_is_a_partition():
    """Every wasted step is charged to exactly one category, so the parts sum."""
    from trajdx.detectors.base import Finding, Phase

    a = Finding(
        detector="a", category=Category.EDIT_ERROR, phase=Phase.EXECUTION,
        severity=Severity.HIGH, start=0, end=2, wasted_steps=(1, 2), evidence="",
    )
    b = Finding(
        detector="b", category=Category.LOCALIZATION_FAILURE, phase=Phase.PLANNING,
        severity=Severity.LOW, start=1, end=4, wasted_steps=(2, 3, 4), evidence="",
    )
    attribution = attribute_waste([a, b])
    assert attribution == {1: "edit_error", 2: "edit_error", 3: "localization_failure",
                           4: "localization_failure"}


def test_wasted_step_ratio_never_exceeds_one():
    steps = [shell(i, "python repro.py", observation="boom: failure") for i in range(4)]
    trajectory = make_trajectory(steps)
    findings = detect_all(trajectory)
    report = wasted_step_ratio(trajectory, findings)
    assert 0.0 <= report.ratio <= 1.0
    assert report.wasted_steps == sum(report.by_category.values())


def test_aggregate_splits_by_outcome():
    trajectories = [
        make_trajectory([edit(0, "src/a.py"), submit(1)], resolved=True),
        make_trajectory([edit(0, "src/a.py"), submit(1)], resolved=False),
    ]
    reports = []
    for trajectory in trajectories:
        findings = detect_all(trajectory)
        reports.append(wasted_step_ratio(trajectory, findings))
    result = aggregate(reports)
    assert result.n_trajectories == 2
    assert result.by_outcome["resolved"].n == 1
    assert result.by_outcome["unresolved"].n == 1
    # every category present in either group is offered a lift figure
    assert set(result.category_lift()) <= {c.value for c in Category}


# --------------------------------------------------------------------------
# Reporting tiers
# --------------------------------------------------------------------------


def test_every_detector_declares_a_tier():
    from trajdx.detectors import REGISTRY
    from trajdx.detectors.base import Tier

    assert REGISTRY
    for name, cls in REGISTRY.items():
        assert isinstance(cls.tier, Tier), f"{name} has no tier"


def test_core_tier_is_non_empty_and_validated():
    """The default output must never be empty by construction."""
    from trajdx.detectors import REGISTRY
    from trajdx.detectors.base import Tier

    core = [name for name, cls in REGISTRY.items() if cls.tier is Tier.CORE]
    assert core, "nothing would be reported by default"
    assert "termination_anomaly" in core
    # A core tier asserts measured precision; everything that has not cleared the
    # 88% bar on a shipped review round stays experimental.
    # tests/test_readme_tables.py enforces that reading of the README.
    assert "verification_gap" not in core


def test_detect_all_respects_the_tier_gate():
    from trajdx.detectors.base import Tier

    # A run that only trips an experimental rule.
    churn = make_trajectory(
        [edit(i, "src/pkg/core.py", payload=f"v{i}") for i in range(6)] + [submit(6)]
    )
    assert detect_all(churn, tier=Tier.EXPERIMENTAL)
    assert detect_all(churn, tier=Tier.CORE) == []
    assert detect_all(churn, tier=None) == detect_all(churn)


# --------------------------------------------------------------------------
# Source text is not an error
# --------------------------------------------------------------------------


def test_a_file_view_does_not_produce_an_error_fingerprint():
    """Regression: `raise TypeError(msg)` inside a *file* is code, not a failure.

    Reading source that happens to contain error-shaped text made the loop
    detector fire on plain reads, which annotation flagged repeatedly.
    """
    source = (
        "[File: /testbed/src/pkg/core.py (200 lines total)]\n"
        "41:    try:\n"
        "42:        return parse(x)\n"
        "43:    except TypeError as exc:\n"
        "44:        raise TypeError(msg) from exc\n"
    )
    step = read(0, "src/pkg/core.py", observation=source)
    assert step.error_kind is None
    assert step.error_fp is None
    assert not step.failed


def test_a_shell_observation_does_produce_an_error_fingerprint():
    step = shell(0, "python run.py", observation="TypeError: unsupported operand type(s)")
    assert step.error_kind == "type_error"
    assert step.failed


# --------------------------------------------------------------------------
# Rulings from the pilot annotation round
# --------------------------------------------------------------------------


def test_no_submit_is_not_reported_twice_when_the_step_budget_ran_out():
    """Ruling: one anomaly, one finding.

    A run cut off by the iteration cap never submitted, so reporting both
    patterns counted the same thing twice.  On 300 OpenHands runs, 37 of the 39
    trajectories carrying either pattern carried both.
    """
    capped = make_trajectory(
        [edit(0, "src/pkg/core.py"), edit(1, "src/pkg/core.py")],
        exit_status="RuntimeError: Agent reached maximum iteration. Current iteration: 100",
    )
    patterns = [
        f.detail["pattern"] for f in TerminationAnomalyDetector().detect(capped)
    ]
    assert patterns.count("iteration_cap") == 1
    assert "no_submit" not in patterns


def test_no_submit_is_still_reported_when_the_run_was_not_capped():
    run = make_trajectory(
        [edit(0, "src/pkg/core.py"), edit(1, "src/pkg/core.py")],
        exit_status="submit",
    )
    patterns = [
        f.detail["pattern"] for f in TerminationAnomalyDetector().detect(run)
    ]
    assert "no_submit" in patterns
    assert "iteration_cap" not in patterns


def test_termination_anomalies_never_charge_wasted_steps():
    """Ruling: fixing the code but never clicking submit is not wasted work."""
    for exit_status in ("submit", "RuntimeError: Agent reached maximum iteration."):
        run = make_trajectory(
            [edit(0, "src/pkg/core.py"), edit(1, "src/pkg/core.py")],
            exit_status=exit_status,
        )
        for finding in TerminationAnomalyDetector().detect(run):
            if finding.detail["pattern"] in ("no_submit", "iteration_cap"):
                assert finding.wasted_steps == ()


def test_source_edits_counts_iteration_on_one_region_once():
    """Pilot verdict: one function rewritten five times was reported as five edits."""
    def rewrite(i, new):
        step = edit(i, "src/pkg/core.py", payload=new)
        step.args["old_str"] = "def f(x):\n    return x"
        return step

    steps = [rewrite(i, f"v{i}") for i in range(5)]
    assert source_edits(make_trajectory(steps)) == [0]

    steps.append(edit(5, "src/pkg/core.py", payload="other"))
    steps[-1].args["old_str"] = "def g(y):\n    return y"
    assert source_edits(make_trajectory(steps)) == [0, 5], "a different region is a new edit"


def test_source_edits_skips_rejected_edits():
    """An edit the tool refused wrote nothing, so it is not an edit."""
    rejected = edit(0, "src/pkg/core.py")
    rejected.observation = "ERROR:\nNo replacement was performed, old_str did not appear"
    ok = edit(1, "src/pkg/core.py", payload="real")
    assert source_edits(make_trajectory([rejected, ok])) == [1]

    syntax = edit(0, "src/pkg/core.py")
    syntax.observation = "Your proposed edit has introduced new syntax error(s). Please fix"
    assert source_edits(make_trajectory([syntax])) == []


def test_source_edits_keeps_edit_whose_output_merely_mentions_error():
    step = edit(0, "src/pkg/core.py")
    step.observation = "The file was edited:\n  raise ValueError('error: bad input')"
    assert source_edits(make_trajectory([step])) == [0]


def test_findings_command_never_emits_a_duplicate_finding_id(tmp_path):
    """The same instance_id attempted twice must not be sampled (and labelled) twice."""
    import json
    from pathlib import Path as _P

    from typer.testing import CliRunner

    from trajdx.cli import app

    src = _P(__file__).parent / "data" / "sweagent" / "pydicom__pydicom-1458.traj"
    payload = json.loads(src.read_text(encoding="utf-8"))
    data = tmp_path / "dup.jsonl"
    data.write_text(json.dumps(payload) + "\n" + json.dumps(payload) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    result = CliRunner().invoke(app, ["findings", str(data), "--out", str(out), "--tier", "all"])
    # A CLI error must fail the test loudly; an unwritten file would otherwise read
    # as "no duplicates" and let this pass without testing anything.
    assert result.exit_code == 0, result.output
    ids = [json.loads(line)["finding_id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert ids, "the fixture must yield at least one finding or this test proves nothing"
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
