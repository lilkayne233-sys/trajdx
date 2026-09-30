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
        ("python -c 'import x'", False),
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
        ("export PYTHONPATH=/repo", True),
        # `cd` must not count -- this was the 95% false-positive bug
        ("cd /workspace/repo && python repro.py", False),
        ("mkdir -p /tmp/x", False),
        ("python -m pytest tests/", False),
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
