"""Small shared heuristics used by both adapters and detectors."""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

#: Sandbox roots that agents like to leak into every path they mention.
#: Only match at the path start, then peel stacked prefixes in successive passes,
#: e.g. ``/workspace/django__django-123/src/a.py`` -> ``src/a.py``.  Matching
#: interior components would corrupt real repository paths such as ``src/app/``.
_CONTAINER_ROOT = re.compile(
    r"^/?(?:testbed|workspace|repo|app|code|project|"
    r"[A-Za-z0-9_.+-]+__[A-Za-z0-9_.+-]+|[0-9a-f]{6,})/",
    re.IGNORECASE,
)


def repo_relative(path: str | None) -> str:
    """Normalize a path to its repo-relative form.

    ``/testbed/src/foo.py``, ``./src/foo.py`` and ``src/foo.py`` must all collapse
    to ``src/foo.py`` or the localization detector cannot compare the agent's
    edits against the ground-truth patch.
    """
    if not path:
        return ""
    p = str(path).strip().strip("'\"")
    if not p:
        return ""
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    for _ in range(3):  # a couple of nested sandbox prefixes do occur
        new = _CONTAINER_ROOT.sub("", p)
        if new == p:
            break
        p = new
    return p.lstrip("/")


# --------------------------------------------------------------------------
# Test execution
# --------------------------------------------------------------------------

# Deliberately conservative: a false "the agent ran tests" signal would silently
# corrupt the verification detector, so we only accept unambiguous invocations.
_TEST_COMMAND = re.compile(
    r"""(?x)
      (?:^|[\s;&|(]) (?:python[0-9.]*|py) \s+ -m \s+ (?:pytest|unittest|nose2?|tox)
    | (?:^|[\s;&|(]) pytest (?:\s|$)
    | (?:^|[\s;&|(]) (?:tox|nox) (?:\s|$)
    | (?:^|[\s;&|(]) (?:python[0-9.]*) \s+ (?:runtests|run_tests)\.py
    | (?:^|[\s;&|(]) (?:npm|pnpm|yarn|bun) \s+ (?:run \s+)? test (?:\s|$)
    | (?:^|[\s;&|(]) (?:make|mvn|gradle|\./gradlew) \s+ (?:test|check) (?:\s|$)
    | (?:^|[\s;&|(]) cargo \s+ test (?:\s|$)
    | (?:^|[\s;&|(]) go \s+ test (?:\s|$)
    | (?:^|[\s;&|(]) python[0-9.]* \s+ -m \s+ (?:pytest|unittest)
    | (?:^|[\s;&|(]) python[0-9.]* \s+ -m \s+ \S*run_?tests (?:\s|$)
    | (?:^|[\s;&|(]) python[0-9.]* \s+ -c (?:\s|$)
    | (?:^|[\s;&|(]) (?:\S*/)?(?:test_\w+|\w+_test)\.py (?:\s|$)
    """
)

#: Invocations that inspect pytest rather than running tests.
_NOT_A_TEST_RUN = re.compile(r"--help\b|\s-h\b|--version\b|--collect-only\b|--fixtures\b")

#: Words that mean "look at this" rather than "run this".
_INSPECT_WORDS = frozenset(
    {
        "cat", "grep", "rg", "ls", "find", "head", "tail", "less", "more",
        "view", "echo", "which", "type", "wc", "sed", "awk", "stat", "file",
        # Navigation and no-ops: ``cd /repo && pytest`` is a test run, and the
        # `cd` half must not be read as the command under test.  `test` and `[`
        # are shell builtins, not the test suite.
        "cd", "true", "false", "test", "[",
    }
)


def split_segments(command: str) -> list[str]:
    """Split a shell command on ``&&``/``||``/``;``/``|``, respecting quotes.

    A naive regex split is wrong here and was actively harmful.  Agents grep for
    alternations, and the escaped pipe in ``grep -n "A\\|B" tests/test_x.py``
    looked like a separator: the command was cut mid-pattern and the tail read as
    a bare ``test_x.py`` invocation, so *searching* a test file was credited as
    *running* it.  Quoting and backslash escapes are honoured, so a separator
    only counts where the shell would see one.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(command)

    while index < length:
        char = command[index]
        if quote is None and char == "\\" and index + 1 < length:
            current.append(char)
            current.append(command[index + 1])
            index += 2
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        if char in ";|&":
            # `&&` and `||` are one separator; a lone `|` is a pipe.
            index += 2 if index + 1 < length and command[index + 1] == char else 1
            segments.append("".join(current))
            current = []
            continue
        current.append(char)
        index += 1

    segments.append("".join(current))
    return segments


def is_test_command(command: str | None) -> bool:
    """True when a shell command actually executes the test suite.

    Segment-by-segment, because a command can both inspect and run: the point is
    to never credit ``cat tests/test_foo.py`` as a test run, while still crediting
    ``cat tests/test_foo.py && pytest tests/``.
    """
    if not command:
        return False
    cmd = command.strip()
    if not cmd:
        return False
    if _NOT_A_TEST_RUN.search(cmd):
        return False

    for segment in split_segments(cmd):
        segment = segment.strip()
        if not segment:
            continue
        first_word = segment.split()[0]
        if first_word in _INSPECT_WORDS:
            continue  # looking at a file is not running it
        if _TEST_COMMAND.search(segment):
            return True
    return False


# --------------------------------------------------------------------------
# Shell command classification
# --------------------------------------------------------------------------

_INSTALL_CMD = re.compile(
    r"""(?x)
      (?:^|[\s;&|(]) pip[0-9.]* \s+ install
    | (?:^|[\s;&|(]) (?:conda|mamba) \s+ install
    | (?:^|[\s;&|(]) (?:apt|apt-get|yum|apk|brew) \s+ (?:install|update|upgrade)
    | (?:^|[\s;&|(]) (?:npm|pnpm|yarn) \s+ (?:install|add|ci) (?:\s|$)
    | (?:^|[\s;&|(]) poetry \s+ (?:add|install)
    | (?:^|[\s;&|(]) uv \s+ pip \s+ install
    """
)

_SETUP_CMD = re.compile(
    r"""(?x)
      (?:^|[\s;&|(]) git \s+ (?:clone|apply|reset|stash|fetch|pull)
    | (?:^|[\s;&|(]) (?:source|\.) \s+ \S*(?:activate|env)\S*
    | (?:^|[\s;&|(]) (?:conda|mamba) \s+ (?:create|activate|env|install)
    | (?:^|[\s;&|(]) (?:apt|apt-get|yum|apk|brew) \s+ (?:install|update|upgrade)
    """
)
# NOTE: `export`, `cd`, `mkdir` and `touch` are deliberately excluded.
#
# `cd`: almost every command in these logs is prefixed with
# `cd /workspace/<repo> &&`, so treating it as environment work marks the entire
# trajectory as a setup struggle -- an early version of this rule fired on 95% of
# runs, including 97% of the ones that succeeded.
#
# `export`: any prefix assignment qualified, so `export PYTHONPATH=. && python
# reproduce_issue.py` was classified as a dependency operation.  That is the
# agent's own scratch script being re-run while it iterates on a reproduction,
# which is ordinary debugging; the env-stuck detector then saw a "setup command"
# failing repeatedly when nothing about the environment was being touched.
#
# `git checkout` is also dropped from this list for the same reason: agents use
# it to discard their own experiments constantly, and it is not a repository or
# interpreter setup step.
_SETUP_CMD_REVERT = re.compile(
    r"""(?x)
      (?:^|[\s;&|(]) git \s+ (?:checkout|restore) (?:\s|$)
    | (?:^|[\s;&|(]) git \s+ (?:reset|stash) (?:\s|$)
    """
)


def is_revert_command(command: str | None) -> bool:
    """True for commands that roll the working tree back to an earlier state.

    Used to justify re-reading a file: after a revert the file's content is not
    what the last look saw, so a second look is not a redundant read.
    """
    if not command:
        return False
    return bool(_SETUP_CMD_REVERT.search(command))


def is_install_command(command: str | None) -> bool:
    """True for dependency mutation, i.e. the env-stuck detector's prey."""
    if not command:
        return False
    return bool(_INSTALL_CMD.search(command))


def is_setup_command(command: str | None) -> bool:
    """True for commands that prepare the repository or the interpreter environment."""
    if not command:
        return False
    return bool(_INSTALL_CMD.search(command) or _SETUP_CMD.search(command))


# --------------------------------------------------------------------------
# Test / scratch files
# --------------------------------------------------------------------------

#: Files that live under a test tree or are named like tests.
_TEST_FILE = re.compile(
    r"(^|/)(tests?|testing|spec|specs)/"
    r"|(^|/)test_[^/]*$"
    r"|_test\.[A-Za-z0-9]+$"
    r"|(^|/)conftest\.py$",
)

#: Throwaway scripts agents write to reproduce or probe an issue.
_SCRATCH_FILE = re.compile(
    r"(^|/)(repro|reproduce|repro_|scratch|playground|sandbox|tmp|temp|debug|check|demo|verify|final)[^/]*"
    r"\.(py|sh|js|ts|sql|txt|md|yml|yaml)$",
)


def is_test_or_scratch(path: str | None) -> bool:
    """True for files that are not part of the library source.

    This distinction carries a lot of weight.  Agents routinely write a
    ``reproduce_issue.py`` and immediately run it, which is *verification* -- but
    a naive edit count treats it as code churn and makes the run look like it
    edited heavily without testing.
    """
    if not path:
        return False
    p = repo_relative(path)
    return bool(_TEST_FILE.search(p) or _SCRATCH_FILE.search(p))


#: Documentation and project configuration.  Reading these is orientation, not
#: investigation of the defect.
_DOC_CONFIG_FILE = re.compile(
    r"(^|/)(readme|contributing|changelog|changes|license|authors|notice|"
    r"code_of_conduct|makefile|dockerfile)[^/]*$"
    r"|(^|/)requirements[^/]*\.txt$"
    r"|(^|/)(setup|conftest)\.(py|cfg)$"
    r"|\.(md|rst|txt|toml|ini|cfg|yml|yaml|lock)$",
    re.IGNORECASE,
)


def is_doc_or_config(path: str | None) -> bool:
    """True for READMEs, changelogs and project configuration files.

    An agent that opens ``README.md``, ``pyproject.toml`` and ``requirements.txt``
    in its first few steps is orienting itself in an unfamiliar repository.  That
    is not blind search, and counting it as investigation of the bug is what made
    the detector flag its opening moves.
    """
    if not path:
        return False
    return bool(_DOC_CONFIG_FILE.search(repo_relative(path)))
