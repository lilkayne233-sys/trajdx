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
    source_edit_events,
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


def test_exact_and_error_loop_for_same_occurrences_is_reported_once():
    steps = [
        shell(i, "pytest", observation="bash: pytest: command not found")
        for i in range(3)
    ]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    assert [f.detail["pattern"] for f in findings] == ["exact"]


def test_error_only_loop_survives_higher_exact_threshold():
    steps = [
        shell(i, "pytest", observation="bash: pytest: command not found")
        for i in range(3)
    ]
    findings = ExecutionLoopDetector(min_repeats=5).detect(make_trajectory(steps))
    assert [f.detail["pattern"] for f in findings] == ["error"]
    assert findings[0].detail["occurrences"] == [0, 1, 2]


def test_error_only_loop_survives_distinct_exact_keys_with_same_coarse_key():
    steps = [
        shell(i, f"python /tmp/run{i}/probe.py", observation="ValueError: broken")
        for i in range(3)
    ]
    assert len({s.action_key for s in steps}) == 1
    assert len({s.exact_key for s in steps}) == 3
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    assert [f.detail["pattern"] for f in findings] == ["error"]


def test_error_only_loop_survives_shorter_exact_window():
    steps = [
        shell(0, "pytest", observation="bash: pytest: command not found"),
        thought(1),
        shell(2, "pytest", observation="bash: pytest: command not found"),
        thought(3),
        shell(4, "pytest", observation="bash: pytest: command not found"),
    ]
    findings = ExecutionLoopDetector(window=3).detect(make_trajectory(steps))
    assert [f.detail["pattern"] for f in findings] == ["error"]


def test_partial_exact_coverage_does_not_hide_wider_error_loop():
    steps = [
        shell(i, "pytest" if i < 3 else "python -m pytest", observation="ValueError: broken")
        for i in range(4)
    ]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    assert [f.detail["pattern"] for f in findings] == ["exact", "error"]
    assert findings[1].detail["occurrences"] == [0, 1, 2, 3]


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


def test_environment_stuck_threshold_is_deliberately_conservative():
    """Three failures, not two.

    The shipped corpus contains only eight failing setup/install commands in 300
    runs, so this rule is starved rather than mis-tuned.  Lowering the bar to two
    would add a single unvalidated finding, and the one annotated
    `environment_stuck` finding was judged invalid -- so the threshold is pinned
    here to make any future loosening a deliberate, evidenced change.
    """
    observation = "ERROR: Could not find a version that satisfies the requirement foo"
    two = [shell(i, "pip install foo", observation=observation) for i in range(2)]
    assert EnvironmentStuckDetector().detect(make_trajectory(two)) == []

    three = [shell(i, "pip install foo", observation=observation) for i in range(3)]
    assert EnvironmentStuckDetector().detect(make_trajectory(three))


def test_timeout_wall_needs_a_cluster_not_a_single_slow_run():
    """One timeout is usually a legitimately slow test run, not a stuck sandbox."""
    timeout = "Command timed out after 120 seconds"
    single = [shell(0, "pytest tests/", observation=timeout), submit(1)]
    assert EnvironmentStuckDetector().detect(make_trajectory(single)) == []

    wall = [shell(i, "pytest tests/", observation=timeout) for i in range(3)]
    assert EnvironmentStuckDetector().detect(make_trajectory(wall))


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
    # docs/config are not library source either
    assert source_edits(make_trajectory(steps)) == [0]


def test_never_verified_when_edits_precede_submit():
    steps = [edit(0, "src/pkg/core.py", payload="x"), submit(1)]
    findings = VerificationGapDetector().detect(make_trajectory(steps))
    assert [f for f in findings if f.detail["pattern"] == "never_verified"]


def test_repeated_edit_after_test_invalidates_verification_without_padding_count():
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
    assert source_edits(trajectory) == [0], "count the region only once"
    assert source_edit_events(trajectory) == [0, 2], "retain every edit event for timing"
    findings = VerificationGapDetector().detect(trajectory)
    assert len(findings) == 1
    assert findings[0].detail == {
        "pattern": "stale_verification", "last_test": 1, "last_edit": 2
    }
    assert WeakVerificationDetector().detect(trajectory) == []


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
    # `redundant_read` scored 4/4 in a round whose human verdicts are not shipped,
    # so no reproducible sample supports its precision.  A core tier asserts
    # measured precision, so it stays experimental until a shipped round backs it;
    # tests/test_readme_tables.py enforces that reading of the README.
    assert "redundant_read" not in core
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


# --------------------------------------------------------------------------
# Rulings from the pilot annotation round
# --------------------------------------------------------------------------


def test_a_re_read_after_a_revert_is_not_redundant():
    """Ruling: after the working tree is rolled back, looking again is justified.

    Three pilot findings hinged on this.  The content came back byte-identical
    only *because* the agent had reverted its own experiment, so the second look
    bought information -- it is the only way to know what the file now says.
    """
    body = "def parse(): ...\n"
    steps = [
        read(0, "src/pkg/core.py", observation=body),
        shell(1, "cd /repo && git checkout HEAD -- src/pkg/core.py"),
        read(2, "src/pkg/core.py", observation=body),
        shell(3, "cd /repo && git checkout HEAD -- src/pkg/core.py"),
        read(4, "src/pkg/core.py", observation=body),
    ]
    assert RedundantReadDetector().detect(make_trajectory(steps)) == []


def test_an_edit_between_reads_also_justifies_the_second_look():
    body = "def parse(): ...\n"
    steps = [
        read(0, "src/pkg/core.py", observation=body),
        edit(1, "src/pkg/core.py", payload="changed"),
        read(2, "src/pkg/core.py", observation=body),
        edit(3, "src/pkg/core.py", payload="changed again"),
        read(4, "src/pkg/core.py", observation=body),
    ]
    assert RedundantReadDetector().detect(make_trajectory(steps)) == []


def test_three_untouched_identical_reads_are_still_redundant():
    """The rule must keep firing when nothing moved in between."""
    body = "def parse(): ...\n"
    steps = [read(i, "src/pkg/core.py", observation=body) for i in range(3)]
    findings = RedundantReadDetector().detect(make_trajectory(steps))
    assert len(findings) == 1
    assert findings[0].detail["reads"] == [0, 1, 2]


def test_opening_readme_and_config_is_not_blind_search():
    """Ruling: the opening moves of a run are orientation, not investigation."""
    steps = [
        read(0, "README.md", observation="install me"),
        read(1, "pyproject.toml", observation="[project]"),
        read(2, "requirements.txt", observation="-e ."),
        read(3, "environment.yml", observation="name: x"),
        read(4, "pytest.ini", observation="[pytest]"),
        edit(5, "src/pkg/core.py"),
        submit(6),
    ]
    assert BlindSearchDetector().detect(make_trajectory(steps)) == []


def test_documentation_reads_do_not_lengthen_a_streak():
    """Docs read *inside* a run are orientation too, not attempts to locate."""
    source = [read(i, "src/pkg/module_%d.py" % i) for i in range(4)]
    docs = [read(10, "docs/index.rst"), read(11, "CHANGELOG.md")]
    more = [read(20 + i, "src/pkg/other_%d.py" % i) for i in range(4)]
    steps = source + docs + more + [edit(30, "src/pkg/core.py"), submit(31)]
    assert BlindSearchDetector().detect(make_trajectory(steps)) == []


def test_a_real_streak_of_source_reads_still_fires():
    # 13 reads, of which the first `warmup` (=3) are excluded, leaves the
    # min_run of 10 that the rule needs.
    steps = [read(i, "src/pkg/module_%d.py" % i) for i in range(13)]
    steps += [edit(13, "src/pkg/core.py"), submit(14)]
    findings = BlindSearchDetector().detect(make_trajectory(steps))
    assert len(findings) == 1
    assert findings[0].detail["run_length"] == 10


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


def test_weak_verification_not_fired_by_padded_edit_count():
    """5 rewrites of one region + 1 rejected + docs must not read as 'heavy editing'."""
    steps = []
    for i in range(5):
        s = edit(i, "src/pkg/core.py", payload=f"v{i}")
        s.args["old_str"] = "def f(x):\n    return x"
        steps.append(s)
    bad = edit(5, "src/pkg/core.py"); bad.observation = "ERROR:\nNo replacement was performed"
    steps += [bad, edit(6, "README.md"), submit(7)]
    assert WeakVerificationDetector().detect(make_trajectory(steps)) == []


def test_loop_detector_ignores_errors_without_a_fingerprint():
    """Three different failures that share only the placeholder are not a loop."""
    steps = [
        shell(0, "python a.py", observation="Exit code: 1"),
        shell(1, "python b.py", observation="Exit code: 2"),
        shell(2, "python c.py", observation="Exit code: 3"),
        submit(3),
    ]
    findings = ExecutionLoopDetector().detect(make_trajectory(steps))
    assert not [f for f in findings if f.detail.get("pattern") == "error"]


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
