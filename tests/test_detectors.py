"""Detector behaviour, including the false positives that were found in the wild.

Several tests here pin down bugs that only surfaced once detector output was
annotated: an execution-loop rule that grouped edits by filename, a verification
rule that counted scratch scripts as source churn, and an environment rule that
treated ``cd`` as setup work.
"""

from __future__ import annotations

from trajdx.detectors import detect_all
from trajdx.detectors.base import Category, Severity
from trajdx.detectors.environment import EnvironmentStuckDetector
from trajdx.detectors.execution_loop import ExecutionLoopDetector
from trajdx.detectors.localization import BlindSearchDetector, RedundantReadDetector
from trajdx.detectors.termination import TerminationAnomalyDetector
from trajdx.detectors.verification import (
    VerificationGapDetector,
    WeakVerificationDetector,
    source_edits,
)
from trajdx.metrics import aggregate, attribute_waste, wasted_step_ratio
from trajdx.schema import AgentStep, StepKind
from tests.conftest import edit, make_trajectory, read, shell, submit, thought

# --------------------------------------------------------------------------
# Execution loop
# --------------------------------------------------------------------------


def test_exact_loop_detects_identical_failing_command():
    observation = "bash: pytest: command not found"
    steps = [shell(i, "pytest tests/", observation=observation) for i in range(3)]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    exact = [f for f in findings if f.detail["pattern"] == "exact"]
    assert exact, "three identical failing commands is a loop"
    assert exact[0].wasted_steps == (1, 2), "the first attempt is not waste"
    assert exact[0].severity is Severity.HIGH


def test_edit_churn_on_one_file_is_not_a_loop():
    """Regression: grouping edits by path made ordinary editing look repetitive."""
    steps = [edit(i, "src/pkg/core.py", payload=f"revision {i}") for i in range(5)]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    assert not [f for f in findings if f.detail["pattern"] == "exact"]


def test_loop_requires_identical_observation():
    """Same command, but the world changed between runs -> not waste."""
    steps = [
        shell(0, "cat src/a.py", observation="version one"),
        shell(1, "cat src/a.py", observation="version two"),
        shell(2, "cat src/a.py", observation="version three"),
    ]
    assert ExecutionLoopDetector().detect(make_trajectory(steps)) == []


def test_error_loop_detects_stuck_error_across_different_actions():
    """Same failure from three different commands, nothing changed in between."""
    observation = "ModuleNotFoundError: No module named 'widget'"
    steps = [
        shell(0, "python run.py", observation=observation),
        shell(1, "python -m pytest tests/", observation=observation, is_test_run=True),
        shell(2, "python setup.py test", observation=observation, is_test_run=True),
    ]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    error = [f for f in findings if f.detail["pattern"] == "error"]
    assert error and error[0].severity is Severity.HIGH


def test_an_edit_between_repetitions_is_not_a_loop():
    """Regression: this was the detector's dominant false-positive mode.

    Re-running a command after changing a file is how an agent tests a
    hypothesis.  On 32 annotated findings, repetitions with an intervening edit
    were judged valid 3.4% of the time; without one, 66.7%.
    """
    observation = "ModuleNotFoundError: No module named 'widget'"
    steps = [
        shell(0, "python run.py", observation=observation),
        edit(1, "src/a.py", payload="one"),
        shell(2, "python run.py", observation=observation),
        edit(3, "src/b.py", payload="two"),
        shell(4, "python run.py", observation=observation),
    ]
    assert ExecutionLoopDetector().detect(make_trajectory(steps)) == []


def test_oscillation_detects_alternation():
    steps = []
    for _ in range(4):
        steps.append(shell(len(steps), "python probe_a.py", observation="same output"))
        steps.append(shell(len(steps), "python probe_b.py", observation="same output"))
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    oscillation = [f for f in findings if f.detail["pattern"] == "oscillation"]
    assert oscillation
    assert oscillation[0].detail["cycles"] >= 3


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def test_environment_stuck_on_repeated_failing_install():
    observation = "ERROR: Could not find a version that satisfies the requirement foo"
    steps = [shell(i, "pip install foo", observation=observation) for i in range(3)]
    findings = EnvironmentStuckDetector().detect(make_trajectory(steps))
    assert findings and findings[0].category is Category.ENVIRONMENT_STUCK


def test_environment_stuck_ignores_cd_prefixed_commands():
    """Regression: `cd` made this detector fire on 95% of all runs."""
    observation = "Traceback (most recent call last)\nValueError: boom"
    steps = [
        shell(i, f"cd /workspace/repo && python repro_{i}.py", observation=observation)
        for i in range(4)
    ]
    assert EnvironmentStuckDetector().detect(make_trajectory(steps)) == []


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def test_weak_verification_ignores_scratch_scripts():
    """Regression: writing and running reproduce_issue.py is verification."""
    steps = [edit(i, f"repro_{i}.py", payload="x") for i in range(6)]
    steps.append(shell(6, "python repro_5.py", observation="ran"))
    steps.append(submit(7))
    assert WeakVerificationDetector().detect(make_trajectory(steps)) == []


def test_weak_verification_fires_on_untested_source_churn():
    steps = [edit(i, "src/pkg/core.py", payload=f"v{i}") for i in range(6)]
    steps.append(submit(6))
    findings = WeakVerificationDetector().detect(make_trajectory(steps))
    assert findings
    assert findings[0].detail["n_edits"] == 6
    assert findings[0].detail["n_tests"] == 0
    assert findings[0].n_wasted == 0, "a coverage gap is not wasted steps"


def test_source_edits_separates_library_from_scratch():
    steps = [
        edit(0, "src/pkg/core.py"),
        edit(1, "tests/test_core.py"),
        edit(2, "reproduce_issue.py"),
        edit(3, "docs/index.md"),
    ]
    assert source_edits(make_trajectory(steps)) == [0, 3]


def test_never_verified_when_edits_precede_submit():
    steps = [edit(0, "src/pkg/core.py", payload="x"), submit(1)]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert [f for f in findings if f.detail["pattern"] == "never_verified"]


def test_running_tests_clears_never_verified():
    steps = [
        edit(0, "src/pkg/core.py", payload="x"),
        shell(1, "python -m pytest tests/", observation="1 passed", is_test_run=True),
        submit(2),
    ]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert not [f for f in findings if f.detail["pattern"] == "never_verified"]


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
        [edit(0, "reproduce_issue.py"), submit(1)], model_patch=patch
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


def test_redundant_read_needs_identical_content():
    same = [read(i, "src/pkg/core.py", observation="identical body") for i in range(3)]
    findings = RedundantReadDetector().detect(make_trajectory(same))
    assert [f for f in findings if f.detail["pattern"] == "redundant_read"]

    different = [
        read(0, "src/pkg/core.py", observation="first half"),
        read(1, "src/pkg/core.py", observation="second half"),
        read(2, "src/pkg/core.py", observation="third section"),
    ]
    assert RedundantReadDetector().detect(make_trajectory(different)) == []


# --------------------------------------------------------------------------
# Waste accounting
# --------------------------------------------------------------------------


def test_waste_attribution_is_a_partition():
    """Every wasted step is charged to exactly one category, so the parts sum."""
    from trajdx.detectors.base import Finding, Phase

    a = Finding(
        detector="a", category=Category.EXECUTION_LOOP, phase=Phase.EXECUTION,
        severity=Severity.HIGH, start=0, end=2, wasted_steps=(1, 2), evidence="",
    )
    b = Finding(
        detector="b", category=Category.BLIND_SEARCH, phase=Phase.PLANNING,
        severity=Severity.LOW, start=1, end=4, wasted_steps=(2, 3, 4), evidence="",
    )
    attribution = attribute_waste([a, b])
    assert attribution == {1: "execution_loop", 2: "execution_loop", 3: "blind_search",
                           4: "blind_search"}


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
    assert "redundant_read" in core
    assert "execution_loop" not in core


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


def test_plan_steps_are_not_treated_as_looping_actions():
    """`task_tracker` bookkeeping repeated is a habit, not waste."""
    from trajdx.schema import StepKind

    steps = [
        AgentStep(
            idx=i,
            kind=StepKind.PLAN,
            args={"verb": "plan", "task_list": []},
            raw_action="task_tracker plan",
            observation="Tasks updated.",
        )
        for i in range(5)
    ]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    assert findings == []


def test_views_of_different_regions_are_not_a_redundant_read():
    """Regression: prefix-only hashing made distinct regions collide."""
    base = "[File: /testbed/src/pkg/core.py (2000 lines total)]\n"
    steps = [
        read(0, "src/pkg/core.py", observation=base + "(340 more lines above)\ndef a(): pass\n"),
        read(1, "src/pkg/core.py", observation=base + "(350 more lines above)\ndef b(): pass\n"),
        read(2, "src/pkg/core.py", observation=base + "(360 more lines above)\ndef c(): pass\n"),
    ]
    assert RedundantReadDetector().detect(make_trajectory(steps)) == []
