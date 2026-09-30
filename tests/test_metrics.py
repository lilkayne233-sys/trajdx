"""Process-shape metrics: the part of a diagnosis that carries signal.

Waste accounting (WSR) is reported alongside these, but it does not separate
outcomes; verification intensity and edit volume do.  The tests here pin the
definitions, because a silent change in "what counts as a source edit" would move
every number in the README without failing anything else.

The module is imported whole rather than by name: ``metrics.test_runs`` and
friends would otherwise be collected by pytest as tests themselves.
"""

from __future__ import annotations

from trajdx import metrics
from trajdx.metrics import aggregate, wasted_step_ratio
from tests.conftest import edit, make_trajectory, read, shell, submit


def _test_shell(idx: int, command: str = "pytest tests/", **kw):
    return shell(idx, command, is_test_run=True, **kw)


def test_source_edits_excludes_tests_and_scratch():
    """A scratch rewrite is not library churn; counting it inverts the signal."""
    trajectory = make_trajectory(
        [
            edit(0, "src/pkg/core.py"),
            edit(1, "tests/test_core.py"),
            edit(2, "reproduce_issue.py"),
            edit(3, "src/pkg/api.py"),
            submit(4),
        ]
    )
    assert metrics.source_edits(trajectory) == 2


def test_source_edits_normalizes_container_paths():
    trajectory = make_trajectory([edit(0, "/testbed/src/pkg/core.py"), submit(1)])
    assert metrics.source_edits(trajectory) == 1


def test_test_runs_counts_only_executed_suites():
    trajectory = make_trajectory(
        [
            _test_shell(0, "pytest tests/"),
            _test_shell(1, "python -m pytest -q"),
            shell(2, "cat tests/test_core.py"),  # inspecting is not running
            read(3, "tests/test_core.py"),
            submit(4),
        ]
    )
    assert metrics.test_runs(trajectory) == 2


def test_tests_per_source_edit_ratio():
    trajectory = make_trajectory(
        [
            edit(0, "src/a.py"),
            edit(1, "src/b.py"),
            _test_shell(2),
            _test_shell(3),
            _test_shell(4),
            _test_shell(5),
            submit(6),
        ]
    )
    assert metrics.tests_per_source_edit(trajectory) == 2.0
    assert metrics.test_run_ratio(trajectory) == 4 / 7


def test_ratios_are_zero_safe():
    """A run that edits nothing and runs no tests must not divide by zero."""
    trajectory = make_trajectory([read(0, "README.md"), submit(1)])
    assert metrics.source_edits(trajectory) == 0
    assert metrics.tests_per_source_edit(trajectory) == 0.0
    assert metrics.test_run_ratio(trajectory) == 0.0


def test_waste_report_carries_process_shape():
    trajectory = make_trajectory(
        [edit(0, "src/a.py"), _test_shell(1), submit(2)],
        resolved=False,
    )
    report = wasted_step_ratio(trajectory, [])
    assert report.source_edits == 1
    assert report.test_runs == 1
    assert report.tests_per_source_edit == 1.0

    payload = report.to_dict(with_findings=False)
    assert payload["source_edits"] == 1
    assert payload["test_runs"] == 1
    assert "tests_per_source_edit" in payload
    assert "test_run_ratio" in payload


def test_aggregate_reports_process_shape_means():
    resolved = make_trajectory(
        [edit(0, "src/a.py"), _test_shell(1), _test_shell(2), submit(3)],
        instance_id="a",
        resolved=True,
    )
    unresolved = make_trajectory(
        [edit(0, "src/a.py"), edit(1, "src/b.py"), submit(2)],
        instance_id="b",
        resolved=False,
    )
    report = aggregate([wasted_step_ratio(t, []) for t in (resolved, unresolved)])
    assert report.by_outcome["resolved"].mean_source_edits == 1.0
    assert report.by_outcome["unresolved"].mean_source_edits == 2.0
    assert report.by_outcome["resolved"].mean_tests_per_source_edit == 2.0
    assert report.by_outcome["unresolved"].mean_tests_per_source_edit == 0.0
    assert "mean_source_edits" in report.to_dict()["overall"]