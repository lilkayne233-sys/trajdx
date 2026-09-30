"""Schema-level behaviour: identity keys, patching, scrubbing."""

from __future__ import annotations

from trajdx.schema import AgentStep, StepKind, patch_files
from tests.conftest import edit, make_trajectory, read, shell, submit, thought


def test_patch_files_reads_both_diff_forms():
    patch = (
        "diff --git a/src/pkg/core.py b/src/pkg/core.py\n"
        "--- a/src/pkg/core.py\n"
        "+++ b/src/pkg/core.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "diff --git a/tests/test_core.py b/tests/test_core.py\n"
        "--- a/tests/test_core.py\n"
        "+++ b/tests/test_core.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    assert patch_files(patch) == ["src/pkg/core.py", "tests/test_core.py"]


def test_patch_files_ignores_dev_null_and_empty():
    assert patch_files(None) == []
    assert patch_files("") == []
    assert patch_files("+++ /dev/null\n") == []


def test_action_key_scrubs_temp_paths():
    """Two reads of the same file under different sandbox roots must collide."""
    a = read(0, "/tmp/xyz123/src/pkg/core.py")
    b = read(1, "/tmp/abc999/src/pkg/core.py")
    assert a.action_key == b.action_key


def test_action_key_keeps_short_distinguishing_arguments():
    """Only incidental tokens are scrubbed, so real argument differences survive.

    Scrubbing too eagerly is how a loop detector starts inventing loops out of
    distinct commands, so single digits stay significant.
    """
    x = shell(0, "pytest tests/test_a.py -k case_1")
    y = shell(1, "pytest tests/test_a.py -k case_2")
    assert x.action_key != y.action_key
    assert x.exact_key != y.exact_key


def test_action_key_scrubs_long_incidental_numbers():
    x = shell(0, "git checkout 1234567890abcdef")
    y = shell(1, "git checkout fedcba0987654321")
    assert x.action_key == y.action_key


def test_exact_key_separates_different_edits_to_one_file():
    a = edit(0, "src/pkg/core.py", payload="return 1")
    b = edit(1, "src/pkg/core.py", payload="return 2")
    assert a.action_key == b.action_key, "coarse key groups by file"
    assert a.exact_key != b.exact_key, "fine key must separate the payloads"


def test_exact_key_matches_identical_edits():
    a = edit(0, "src/pkg/core.py", payload="return 1")
    b = edit(5, "src/pkg/core.py", payload="return 1")
    assert a.exact_key == b.exact_key


def test_observation_key_prefers_error_fingerprint():
    failing = shell(0, "pytest", observation="ModuleNotFoundError: No module named 'foo'")
    assert failing.observation_key.startswith("err:")

    empty = shell(1, "true", observation="")
    assert empty.observation_key == "empty"

    ok = shell(2, "ls", observation="a.py\nb.py\n")
    assert ok.observation_key.startswith("ok:")


def test_failed_property_sees_both_signals():
    assert shell(0, "x", observation="Command timed out after 30s").failed
    assert not shell(1, "x", observation="all good").failed
    assert AgentStep(idx=0, kind=StepKind.SHELL, exit_code=2).failed
    assert not AgentStep(idx=0, kind=StepKind.SHELL, exit_code=0).failed


def test_trajectory_roundtrip_is_lossless():
    trajectory = make_trajectory(
        [thought(0), read(1, "src/a.py"), edit(2, "src/a.py"), submit(3)],
        resolved=False,
        repo="acme/widget",
    )
    restored = type(trajectory).from_dict(trajectory.to_dict())
    assert restored.instance_id == trajectory.instance_id
    assert restored.resolved is False
    assert [s.kind for s in restored.steps] == [s.kind for s in trajectory.steps]
    assert restored.steps[2].files_touched == trajectory.steps[2].files_touched
