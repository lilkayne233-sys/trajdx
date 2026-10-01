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


def test_patch_files_mixed_blocks_include_deletion_rename_and_binary():
    patch = (
        "diff --git a/src/deleted.py b/src/deleted.py\n"
        "deleted file mode 100644\n--- a/src/deleted.py\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-old\n"
        "diff --git a/src/core.py b/src/core.py\n"
        "--- a/src/core.py\n+++ b/src/core.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/old.py b/src/renamed.py\n"
        "similarity index 100%\nrename from src/old.py\nrename to src/renamed.py\n"
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )
    assert patch_files(patch) == [
        "src/deleted.py", "src/core.py", "src/renamed.py", "assets/logo.png"
    ]


def test_patch_files_plain_deletion_and_quoted_paths():
    assert patch_files("--- a/src/gone.py\n+++ /dev/null\n") == ["src/gone.py"]
    patch = (
        'diff --git "a/src/old name.py" "b/src/new name.py"\n'
        'rename from src/old name.py\nrename to src/new name.py\n'
        'diff --git a/src/new.py b/src/new.py\n--- /dev/null\n+++ b/src/new.py\n'
    )
    assert patch_files(patch) == ["src/new name.py", "src/new.py"]


def test_patch_files_plain_multi_file_diff_ignores_header_shaped_content():
    patch = (
        "--- a/src/first.py\n+++ b/src/first.py\n@@ -1 +1 @@\n"
        "--- a/not_a_file.py\n+++ b/not_a_file.py\n"
        "--- a/src/deleted.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-deleted\n"
        "--- /dev/null\n+++ b/src/added.py\n@@ -0,0 +1 @@\n+added\n"
    )
    assert patch_files(patch) == ["src/first.py", "src/deleted.py", "src/added.py"]


def test_edit_exact_key_preserves_real_paths_and_ranges():
    a = edit(0, "/tmp/run123/core.py", payload="same")
    b = edit(1, "/tmp/run456/core.py", payload="same")
    assert a.action_key == b.action_key
    assert a.exact_key != b.exact_key
    b.args["path"] = a.args["path"]
    a.args["range"] = "10:20"
    b.args["range"] = "30:40"
    assert a.exact_key != b.exact_key


def test_edit_exact_key_is_canonical_and_delimiter_safe():
    a = edit(0, "src/core.py", payload="b|c")
    a.args["old_str"] = "a"
    b = edit(1, "src/core.py", payload="c")
    b.args["old_str"] = "a|b"
    assert a.exact_key != b.exact_key
    b.args = dict(reversed(list(a.args.items())))
    b.args["file_text"] = None
    assert a.exact_key == b.exact_key
    b.args["insert_line"] = 10
    a.args["insert_line"] = "10"
    assert a.exact_key != b.exact_key


def test_failed_property_includes_error_kind_without_fingerprint():
    step = AgentStep(idx=0, kind=StepKind.SHELL, error_kind="nonzero_exit")
    assert step.error_fp is None
    assert step.failed
    assert make_trajectory([step]).failed_steps == [step]


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
