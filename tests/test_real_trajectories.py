"""The adapters, run against real agent logs rather than invented ones.

The ``.traj`` files in ``tests/data/sweagent/`` are vendored verbatim from the
upstream SWE-agent repository (MIT licensed), which publishes a handful of real
run logs:

* ``function_calling_simple.traj`` -- the function-calling serialization.  This
  file is the reason the chat-history parser exists: the first version of the
  adapter could not read it at all, and nothing in the repository noticed, because
  every fixture up to that point had been written by hand from the same reading of
  the format as the parser.

* ``pydicom__pydicom-1458.traj`` -- a real SWE-bench run in the ``.traj`` step-list
  serialization.

Keeping them in the suite means "supports SWE-agent" is checked against bytes
produced by SWE-agent, offline and on every test run.  Refresh them with
``python scripts/fetch_sweagent_trajs.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trajdx.adapters import load_file
from trajdx.schema import StepKind

FIXTURES = sorted((Path(__file__).parent / "data" / "sweagent").glob("*.traj"))


def test_fixtures_are_present():
    assert FIXTURES, "no vendored SWE-agent logs found"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_real_sweagent_log_parses_into_known_step_kinds(path: Path):
    trajectories = load_file(path)
    assert trajectories, f"{path.name} produced no trajectory"

    for trajectory in trajectories:
        assert trajectory.framework == "sweagent"
        assert trajectory.n_steps > 0
        # `other` means an action the shared vocabulary could not classify: the
        # adapter silently losing information rather than failing.
        unmapped = [s for s in trajectory.steps if s.kind is StepKind.OTHER]
        assert not unmapped, (
            f"{path.name}: {len(unmapped)} step(s) unmapped, e.g. "
            f"{unmapped[0].raw_action!r}"
        )


def test_real_swebench_log_yields_a_usable_trajectory():
    path = Path(__file__).parent / "data" / "sweagent" / "pydicom__pydicom-1458.traj"
    trajectory = load_file(path)[0]
    assert trajectory.instance_id == "pydicom__pydicom-1458"
    kinds = {s.kind for s in trajectory.steps}
    assert StepKind.EDIT in kinds
    assert StepKind.SHELL in kinds


def test_real_function_calling_log_uses_the_chat_parser():
    path = (
        Path(__file__).parent / "data" / "sweagent" / "function_calling_simple.traj"
    )
    trajectory = load_file(path)[0]
    assert trajectory.meta["serialization"] == "history"
    # The edit in this run carries no path of its own; it must have been resolved
    # against the file the agent opened, or the step would name no file at all.
    edits = [s for s in trajectory.steps if s.kind is StepKind.EDIT]
    assert edits and all(s.files_touched for s in edits)