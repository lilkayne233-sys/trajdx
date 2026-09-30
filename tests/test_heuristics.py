"""Heuristics: path normalisation, test recognition, scratch-file detection.

These predicates are the load-bearing walls of the toolkit -- a false positive in
`is_test_command` silently inflates the verification detectors, and a false
positive in `is_setup_command` once made the environment detector fire on 95% of
runs.  Both bugs are pinned by tests here.
"""

from __future__ import annotations

import pytest

from trajdx.heuristics import (
    is_install_command,
    is_setup_command,
    is_test_command,
    is_test_or_scratch,
    repo_relative,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/testbed/src/pkg/core.py", "src/pkg/core.py"),
        ("./src/pkg/core.py", "src/pkg/core.py"),
        ("src/pkg/core.py", "src/pkg/core.py"),
        ("/workspace/django__django-123/src/a.py", "src/a.py"),
        ("/repo/x.py", "x.py"),
        (None, ""),
        ("", ""),
    ],
)
def test_repo_relative(path, expected):
    assert repo_relative(path) == expected


@pytest.mark.parametrize(
    "command,expected",
    [
        ("python -m pytest tests/ -v", True),
        ("cd /workspace/repo && python -m pytest tests/", True),
        ("pytest", True),
        ("./test_reframe.py -k storage", True),
        ("test_reframe.py -k storage", True),
        ("make test", True),
        ("tox -e py39", True),
        # must NOT count as a test run
        ("python reproduce_issue.py", False),
        ("pytest --help", False),
        ("pytest --collect-only", False),
        ("cat tests/test_foo.py", False),
        ("grep -rn 'def test' tests/", False),
        # Searching a test file is still not running it, even when the search
        # pattern carries an escaped alternation.  The naive segment split used
        # to cut on the `\|` inside the quotes and read the tail as a bare
        # `test_oracle.py` invocation.
        ('grep -n "XMLTABLE\\|XML_TABLE" tests/dialects/test_oracle.py', False),
        ('cd /repo && grep -n "A\\|B" tests/test_x.py', False),
        # `python -c` runs code the agent wrote on the spot.  Treated as a test
        # execution by decision: `weak_verification` is about whether the run
        # checked itself at all, and an ad-hoc probe is a check.
        ("python -c 'import x'", True),
        ("python3 -c \"import sqlglot\"", True),
        ("python -m tornado.test.runtests", True),
        ("git status", False),
        ("", False),
    ],
)
def test_is_test_command(command, expected):
    assert is_test_command(command) is expected


def test_is_test_command_sees_past_an_inspection_prefix():
    """`cat x && pytest` does run the suite; `cat x` alone does not."""
    assert is_test_command("cat tests/test_a.py && pytest tests/")
    assert not is_test_command("cat tests/test_a.py")


@pytest.mark.parametrize(
    "command,expected",
    [
        ("pip install -e .[dev]", True),
        ("pip install pytest==8.0", True),
        ("conda install numpy", True),
        ("apt-get install -y build-essential", True),
        ("git clone https://x/y", True),
        ("source /opt/venv/bin/activate", True),
        # `export` must not count either.  It was matching as a prefix
        # assignment, so `export PYTHONPATH=. && python reproduce_issue.py` was
        # read as a dependency operation rather than the agent re-running its own
        # scratch repro.
        ("export PYTHONPATH=/repo", False),
        ("export PYTHONPATH=. && python reproduce_issue.py", False),
        # `cd` must not count -- this was the 95% false-positive bug
        ("cd /workspace/repo && python repro.py", False),
        ("mkdir -p /tmp/x", False),
        ("python -m pytest tests/", False),
        ("git checkout HEAD -- src/a.py", False),
        ("ls -la", False),
    ],
)
def test_is_setup_command_excludes_cd(command, expected):
    assert is_setup_command(command) is expected


def test_is_install_command_is_narrower_than_setup():
    assert is_install_command("pip install foo")
    assert not is_install_command("git clone https://x/y")
    assert is_setup_command("git clone https://x/y")


@pytest.mark.parametrize(
    "path,expected",
    [
        ("reproduce_issue.py", True),
        ("repro.py", True),
        ("final_verification.py", True),
        ("debug_coord_names.py", True),
        ("tests/test_core.py", True),
        ("src/pkg/tests/test_x.py", True),
        ("conftest.py", True),
        ("src/pkg/core.py", False),
        ("django/db/models/query.py", False),
        ("setup.py", False),
    ],
)
def test_is_test_or_scratch(path, expected):
    assert is_test_or_scratch(path) is expected


# --------------------------------------------------------------------------
# Quote-aware segmentation
# --------------------------------------------------------------------------


def test_segments_respect_quotes_and_escapes():
    """The bug that made searching a test file look like running it.

    Agents grep for alternations.  A naive split on ``|`` cut the command at the
    escaped pipe inside ``grep -n "A\\|B" tests/test_x.py``, and the tail then
    matched the bare ``test_x.py`` alternative -- so a search was credited as a
    test execution and the verification detectors were fed a lie.
    """
    from trajdx.heuristics import split_segments

    assert split_segments('grep -n "A\\|B" tests/test_x.py') == [
        'grep -n "A\\|B" tests/test_x.py'
    ]
    assert split_segments("a && b || c") == ["a ", " b ", " c"]
    assert split_segments("echo 'x;y' ; ls") == ["echo 'x;y' ", " ls"]


def test_a_search_with_an_alternation_is_not_a_test_run():
    assert not is_test_command('grep -n "XMLTABLE\\|XML_TABLE" tests/dialects/test_oracle.py')
    assert not is_test_command("rg 'pass|fail' tests/")
    # ...but a real run after a pipeline still counts
    assert is_test_command("cd /repo && pytest tests/")


# --------------------------------------------------------------------------
# Documentation / configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("README.md", True),
        ("CONTRIBUTING.md", True),
        ("docs/index.rst", True),
        ("pyproject.toml", True),
        ("pytest.ini", True),
        ("setup.cfg", True),
        ("environment.yml", True),
        ("requirements.txt", True),
        ("dev-requirements.txt", True),
        ("src/pkg/core.py", False),
        ("lexicon/providers/memset.py", False),
    ],
)
def test_is_doc_or_config(path, expected):
    from trajdx.heuristics import is_doc_or_config

    assert is_doc_or_config(path) is expected


def test_revert_commands_are_recognised():
    from trajdx.heuristics import is_revert_command

    assert is_revert_command("git checkout HEAD -- src/a.py")
    assert is_revert_command("cd /repo && git reset --hard")
    assert is_revert_command("git stash pop")
    # not reverts
    assert not is_revert_command("git status")
    assert not is_revert_command("pytest tests/")
    assert not is_revert_command(None)
