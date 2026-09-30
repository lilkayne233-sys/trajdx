"""Error fingerprinting: does it group the right things and separate the rest?"""

from __future__ import annotations

import pytest

from trajdx.fingerprints import classify_error, clean_output, extract_exit_code

# Every one of these must survive the cheap pre-filter in `classify_error`; a
# marker list that is too aggressive silently turns the whole toolkit blind.
MUST_DETECT = [
    ("ModuleNotFoundError: No module named 'marshmallow'", "import_error"),
    ("  File \"a.py\", line 3\n    def f(:\n            ^\nSyntaxError: invalid syntax", "syntax_error"),
    ("AttributeError: 'NoneType' object has no attribute 'get'", "attr_error"),
    ("TypeError: unsupported operand type(s)", "type_error"),
    ("FAILED tests/test_api.py::test_round - AssertionError", "test_failure"),
    ("E   assert 344 == 345", "assertion_error"),
    ("ERROR: Could not find a version that satisfies the requirement foo", "pip_resolve"),
    ("ERROR: Cannot install a and b because these package versions conflict", "dependency_conflict"),
    ("Temporary failure in name resolution", "network_error"),
    ("Command timed out after 120 seconds", "timeout"),
    ("bash: pytest: command not found", "command_not_found"),
    ("cat: src/nope.py: No such file or directory", "file_not_found"),
    ("Permission denied: '/root/x'", "permission"),
    ("ERROR collecting tests/test_x.py\nImportError while loading conftest", "collection_error"),
    ("Your proposed edit has introduced new syntax error(s)", "edit_rejected"),
    ("fatal: not a git repository", "git_error"),
    ("collect2: error: ld returned 1 exit status", "compile_error"),
    ("Killed", "oom"),
]


@pytest.mark.parametrize("text,expected_kind", MUST_DETECT)
def test_known_error_shapes_are_classified(text, expected_kind):
    kind, fingerprint = classify_error(text)
    assert kind is not None, f"pre-filter rejected a real error: {text!r}"
    assert kind == expected_kind
    assert fingerprint.startswith(expected_kind)


def test_clean_output_reports_no_error():
    for text in ("", "   ", "1 passed in 0.42s", "Marshmallow\nAUTHORS.rst\nsetup.py"):
        assert classify_error(text) == (None, None)


def test_fingerprint_ignores_line_numbers_but_not_module_names():
    a = classify_error("ModuleNotFoundError: No module named 'foo'")[1]
    b = classify_error("ModuleNotFoundError: No module named 'bar'")[1]
    assert a != b, "different missing modules must not collapse"

    c = classify_error("AttributeError: 'X' object has no attribute 'y'\n  File \"a.py\", line 10")[1]
    d = classify_error("AttributeError: 'X' object has no attribute 'y'\n  File \"a.py\", line 99")[1]
    assert c == d, "the same failure at a different line is the same failure"


def test_fingerprint_survives_moving_sandbox_root():
    a = classify_error("FileNotFoundError: '/testbed/src/a.py' not found")[1]
    b = classify_error("FileNotFoundError: '/workspace/repo/src/a.py' not found")[1]
    assert a == b


def test_clean_output_strips_ansi_and_carriage_returns():
    assert clean_output("\x1b[31mERROR\x1b[0m") == "ERROR"
    assert "\r" not in clean_output("progress 10%\rprogress 100%\ndone\n")


def test_extract_exit_code():
    assert extract_exit_code("Exit code: 1") == 1
    assert extract_exit_code("exit code=137") == 137
    assert extract_exit_code("all good") is None


def test_long_observations_are_truncated_head_and_tail():
    """A multi-megabyte file dump must not blow up analysis cost."""
    huge = "x" * 500_000 + "\nModuleNotFoundError: No module named 'tail_marker'"
    kind, fingerprint = classify_error(huge)
    assert kind == "import_error"
    assert "tail_marker" in fingerprint
