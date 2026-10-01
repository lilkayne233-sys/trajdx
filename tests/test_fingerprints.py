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


@pytest.mark.parametrize(
    "text,expected",
    [
        ("first\r\nValueError: bad\r\n", "first\nValueError: bad\n"),
        ("progress 10%\rprogress 100%\r\ndone\r\n", "progress 100%\ndone\n"),
        ("old\rnew\nnext old\rnext new\n", "new\nnext new\n"),
        ("\x1b[31mERROR\x1b[0m\r\n", "ERROR\n"),
    ],
)
def test_clean_output_preserves_crlf_body(text, expected):
    assert clean_output(text) == expected


def test_crlf_error_body_remains_classifiable():
    assert classify_error("Traceback (most recent call last):\r\nValueError: bad\r\n")[0] == "value_error"


@pytest.mark.parametrize(
    "footer,expected",
    [
        ("Exit code: 1", 1),
        ("exit code=137", 137),
        ("exit code 0", 0),
        ("[exit code: -9]", -9),
        ("[exit code: +2]", 2),
        ("[The command completed with exit code 0.]", 0),
        ("Process exited with code -15", -15),
    ],
)
def test_exit_footer_wins_over_body(footer, expected):
    observation = f"documented exit code 0; another exit code: 123\nValueError: bad\n{footer}\n\n"
    assert extract_exit_code(observation) == expected
    assert extract_exit_code(observation.replace("\n", "\r\n")) == expected
    if expected == 0:
        assert classify_error(observation) == (None, None)
    else:
        assert classify_error(observation)[0] == "value_error"


def test_last_exit_footer_wins_over_an_earlier_footer():
    assert extract_exit_code("Exit code: 0\nValueError: bad\n[exit code: 1]\n") == 1
    assert classify_error("Exit code: 1\nValueError: bad\n[exit code: 0]\n") == (None, None)


@pytest.mark.parametrize("code", [-15, +2, 0, 137])
def test_signed_exit_code_legacy_fallback(code):
    assert extract_exit_code(f"tool reported exit code={code:+d} during execution") == code


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


# --------------------------------------------------------------------------
# A command that reported success is never an error
# --------------------------------------------------------------------------
#
# This was the largest single source of false positives the pilot annotation
# found: 120 steps across 60 trajectories were marked as failures while the
# command had in fact exited 0.  The rule table matches text, and text lies -- a
# grep printing a source line containing `raise AttributeError(`, a docstring
# saying `ValueError if asked.`, or a minified bundle holding the word `error`
# all look like failures to a regex.


@pytest.mark.parametrize(
    "observation",
    [
        'src/a.py:12:    raise AttributeError("no field")\nexit code 0',
        "ValueError if asked.\n[The command completed with exit code 0.]",
        "Traceback (most recent call last):\n  ...\nTimeoutError\nexit code: 0",
        "app.js:1:...error...\nexit code 0",
        "FAILED tests/test_x.py::test_y\n1 failed, 1 passed\nexit code 0",
    ],
)
def test_a_zero_exit_code_suppresses_every_error_kind(observation):
    assert classify_error(observation) == (None, None)


def test_the_same_text_is_still_an_error_when_the_command_failed():
    kind, fingerprint = classify_error("ValueError if asked.\nexit code 1")
    assert kind == "value_error" and fingerprint


def test_an_unknown_exit_code_falls_back_to_text():
    """No status captured: keep the old behaviour rather than going blind."""
    kind, _ = classify_error("Traceback (most recent call last):\nValueError: bad")
    assert kind == "value_error"


def test_the_caller_may_supply_the_exit_code():
    assert classify_error("ValueError: bad", exit_code=0) == (None, None)
    assert classify_error("ValueError: bad", exit_code=1)[0] == "value_error"


def test_unclassifiable_error_gets_no_placeholder_fingerprint():
    """A bare 'generic_error:generic_error' fingerprint made unrelated failures
    look identical to the loop detector (a pilot verdict caught this)."""
    kind, fp = classify_error("Error")
    assert kind == "generic_error"
    assert fp is None, "no information means no fingerprint, not a shared placeholder"


def test_generic_error_fingerprint_uses_the_line_that_matched():
    _, a = classify_error("Error: the first thing broke")
    _, b = classify_error("Error: a completely different thing broke")
    assert a and b and a != b


def test_generic_error_with_a_real_signature_keeps_it():
    kind, fp = classify_error("Traceback (most recent call last):\n  File 'x'\nfoo.BarError: boom 42")
    assert fp is not None and "generic_error:generic_error" != fp
