"""Error fingerprinting: turn noisy terminal output into a stable identity.

The whole rule-based approach hinges on this module.  Two observations that mean
"the same thing went wrong" must hash to the same fingerprint even though they
differ in line numbers, temp paths, addresses and timings -- otherwise an agent
stuck in a loop looks like it is making progress.

We deliberately keep identifiers in the signature (``No module named 'foo'`` vs
``'bar'`` stay distinct) and only scrub *incidental* tokens, because a detector
that collapses genuinely different failures into one produces the false
positives that make this kind of tooling useless in practice.
"""

from __future__ import annotations

import hashlib
import re

# --------------------------------------------------------------------------
# Output cleanup
# --------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_CR_PROGRESS = re.compile(r"^.*\r", re.M)


def clean_output(text: str | None) -> str:
    """Strip ANSI colours and carriage-return progress redraws."""
    if not text:
        return ""
    out = _ANSI.sub("", text)
    if "\r" in out:
        out = _CR_PROGRESS.sub("", out)
    return out


# --------------------------------------------------------------------------
# Error taxonomy
# --------------------------------------------------------------------------

# Order matters: the first matching rule wins, so specific patterns (collection
# errors, edit rejections) must precede the generic ones that would also match.
_ERROR_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # --- agent self-feedback: the tool refused the action -------------------
    ("edit_rejected",
     re.compile(r"(proposed edit has introduced new syntax error|"
                r"has NOT been applied|"
                r"DO NOT re-run the same failed edit|"
                r"Invalid `?path`? argument|"
                r"Parameter `?old_str`? is not found)")),
    # --- environment / dependency -----------------------------------------
    ("pip_resolve",
     re.compile(r"(Could not find a version that satisfies|No matching distribution found)")),
    ("dependency_conflict",
     re.compile(r"(ResolutionImpossible|Cannot install .* because these package versions|"
                r"has requirement .* but you have)")),
    ("network_error",
     re.compile(r"(Temporary failure in name resolution|Could not resolve host|"
                r"Connection refused|SSL(?:Error|: CERTIFICATE)|Read timed out|"
                r"Failed to establish a new connection)")),
    ("timeout",
     re.compile(r"(Command timed out|timed out after \d|TimeoutError|CommandTimeout|"
                r"exceeded the time limit)")),
    ("permission",
     re.compile(r"(Permission denied|EACCES|Operation not permitted|Read-only file system)")),
    # --- python static / import -------------------------------------------
    ("collection_error",
     re.compile(r"(ERROR collecting|errors during collection|ImportError while loading conftest)")),
    ("import_error", re.compile(r"\b(ModuleNotFoundError|ImportError)\b")),
    ("syntax_error", re.compile(r"\b(SyntaxError|IndentationError|TabError)\b")),
    # --- python runtime ----------------------------------------------------
    ("name_error", re.compile(r"\bNameError\b")),
    ("attr_error", re.compile(r"\bAttributeError\b")),
    ("type_error", re.compile(r"\bTypeError\b")),
    ("key_error", re.compile(r"\bKeyError\b")),
    ("index_error", re.compile(r"\bIndexError\b")),
    ("value_error", re.compile(r"\bValueError\b")),
    ("recursion_error", re.compile(r"\bRecursionError\b")),
    ("runtime_error", re.compile(r"\b(RuntimeError|OSError|IOError)\b")),
    # --- tests -------------------------------------------------------------
    ("test_failure", re.compile(r"^(?:FAILED|ERROR) [\w./\\-]+::", re.M)),
    ("assertion_error", re.compile(r"(\bAssertionError\b|^E\s+assert\b|Assertion failed)", re.M)),
    # --- toolchain ---------------------------------------------------------
    ("compile_error",
     re.compile(r"(undefined reference to|collect2: error|"
                r"error: .*\.(?:c|cc|cpp|h):\d|error\[E\d+\])")),
    ("git_error",
     re.compile(r"(^fatal: |error: patch failed|Merge conflict|"
                r"Your local changes .* would be overwritten)", re.M)),
    ("command_not_found", re.compile(r"(command not found|: not found\b|No such command)")),
    ("file_not_found",
     re.compile(r"(No such file or directory|FileNotFoundError|does not exist\b)")),
    ("oom", re.compile(r"(\bKilled\b|MemoryError|out of memory|Cannot allocate memory)")),
)

#: Generic "something went wrong" markers used only as a last resort.
_GENERIC_ERROR = re.compile(
    r"(Traceback \(most recent call last\)|^\s*E\s+\w*(?:Error|Exception)\b|"
    r"\b\w+(?:Error|Exception)\b\s*:|^\s*Error\b|Exit code: [1-9])",
    re.M,
)

#: Cheap lowercase substrings that must appear before the full rule set is worth
#: running.  Observations are frequently multi-kilobyte file dumps and the vast
#: majority carry no error at all, so this keeps trajectory parsing cheap.
#: ``scripts/verify_prefilter.py`` asserts this filter never changes the verdict.
_FAST_MARKERS: tuple[str, ...] = (
    "rror", "fail", "traceback", "exception", "no such file", "not found",
    "killed", "denied", "timed out", "timeout", "conflict", "fatal",
    "cannot", "unable", "warning", "invalid", "applied", "abort",
    "not satisfy", "no matching", "missing", "unsatisf", "refused",
    # pytest reports a bare assertion as `E   assert 344 == 345`, which contains
    # none of the markers above -- omitting this made the pre-filter silently
    # drop the single most common test failure shape.
    "assert",
)

#: Analysis is done on the head and tail of long observations: errors surface at
#: the end of a traceback, the command that produced them at the start.
_MAX_ANALYZE = 4000


#: Extracts the final ``ExceptionType: message`` line of a Python traceback.
_TRACEBACK_LAST = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning))"
    r"\s*:\s*(.*)$",
    re.M,
)

#: ``file.py:123`` / ``file.py:123:45`` references inside messages.
_LOCATION = re.compile(r"([\w./\\+-]+\.\w+):\d+(?::\d+)?")
_HEX_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_QUOTED_PATH = re.compile(r"(['\"])(/[^'\"]*?)/([^/'\"]+)\1")
_EXIT_CODE = re.compile(r"\bexit code[:= ]+(\d+)\b", re.I)


def _normalize_signature(text: str, limit: int = 160) -> str:
    """Scrub incidental tokens from an error message, keeping identifiers."""
    sig = _HEX_ADDR.sub("0xADDR", text.strip())
    # absolute path -> basename, so the signature survives a changing sandbox root
    sig = _QUOTED_PATH.sub(lambda m: f"{m.group(1)}{m.group(3)}{m.group(1)}", sig)
    sig = _LOCATION.sub(lambda m: f"{m.group(1)}:N", sig)
    sig = re.sub(r"'/[^']*/([^/']+)'", r"'\1'", sig)
    sig = re.sub(r'"/[^"]*/([^/"]+)"', r'"\1"', sig)
    sig = re.sub(r"\bline \d+", "line N", sig)
    sig = re.sub(r"\b\d+\.\d+\.\d+(?:\.\w+)*\b", "<VER>", sig)
    sig = re.sub(r"\b[0-9a-f]{8,}\b", "<HEX>", sig)
    sig = re.sub(r"\b\d{3,}\b", "N", sig)
    sig = re.sub(r"\b\d+(?:\.\d+)?s\b", "Ns", sig)
    sig = re.sub(r"\s+", " ", sig).strip()

    if len(sig) > limit:
        digest = hashlib.sha256(sig.encode("utf-8", "replace")).hexdigest()[:8]
        sig = f"{sig[:limit]}…#{digest}"
    return sig


def _extract_signature(kind: str, text: str) -> str:
    """Pull the most informative line(s) out of the output for this error kind."""
    # A Python traceback's value is in its last line; everything above is frame
    # noise that changes on every edit.
    matches = list(_TRACEBACK_LAST.finditer(text))
    if matches:
        last = matches[-1]
        return _normalize_signature(f"{last.group(1)}: {last.group(2)}")

    # pytest's `E   assert ...` block is the assertion payload.
    if kind in ("assertion_error", "test_failure"):
        for line in text.splitlines():
            if line.strip().startswith("E "):
                return _normalize_signature(line.strip()[2:])
        for line in text.splitlines():
            if line.startswith(("FAILED", "ERROR ")):
                return _normalize_signature(line)

    # Otherwise: the first line that carries the matched error signal.
    for pattern_kind, rule in _ERROR_RULES:
        if pattern_kind != kind:
            continue
        match = rule.search(text)
        if match:
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            line = text[start: end if end != -1 else len(text)]
            return _normalize_signature(line)

    return _normalize_signature(kind)


def classify_error(observation: str | None, *, exit_code: int | None = None) -> tuple[str | None, str | None]:
    """Map raw output to ``(error_kind, error_fingerprint)``.

    Returns ``(None, None)`` when the output carries no error signal, which is
    the common case for successful commands.

    **A command that reported success is never an error.**  The rule table below
    matches text, and text lies: a successful ``grep`` that prints a line of the
    form ``raise AttributeError(``, a docstring phrase like ``ValueError if
    asked.``, or a minified bundle containing the word ``error`` all look like
    failures to a regex while the command exited 0.  On 60 OpenHands trajectories
    that accounted for 120 steps marked as errors that had in fact succeeded, and
    those bogus fingerprints then drove the loop and environment detectors.  So
    the exit status is checked first and wins outright.

    ``exit_code`` may be passed in when the caller already extracted it;
    otherwise it is read from the observation.
    """
    text = clean_output(observation)
    if not text or not text.strip():
        return None, None

    if exit_code is None:
        exit_code = extract_exit_code(text)
    if exit_code == 0:
        return None, None

    if len(text) > 2 * _MAX_ANALYZE:
        text = text[:_MAX_ANALYZE] + "\n…\n" + text[-_MAX_ANALYZE:]

    # Cheap reject before touching the 24-rule table.
    lowered = text.lower()
    if not any(marker in lowered for marker in _FAST_MARKERS):
        return None, None

    for kind, rule in _ERROR_RULES:
        if rule.search(text):
            return kind, f"{kind}:{_extract_signature(kind, text)}"

    # Last resort: a traceback-shaped blob we could not classify.
    if _GENERIC_ERROR.search(text):
        signature = _extract_signature("generic_error", text)
        if signature == "generic_error":
            # `generic_error` has no rule of its own, so the extractor falls back to
            # the bare kind name.  Use the line that actually tripped the generic
            # pattern instead: it is the only information available.
            match = _GENERIC_ERROR.search(text)
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            signature = _normalize_signature(text[start: end if end != -1 else len(text)])
        if not signature or signature.lower() in ("generic_error", "error", "exception", "errors"):
            # Still nothing informative: a placeholder fingerprint would be identical
            # for every unrelated failure, letting the loop detector call two
            # different errors "the same error" (a pilot verdict caught exactly
            # that).  The step is still an error; withhold the fingerprint.
            return "generic_error", None
        return "generic_error", f"generic_error:{signature}"

    return None, None


def extract_exit_code(observation: str | None) -> int | None:
    """Best-effort exit status from a shell observation, if the tool reports one."""
    text = clean_output(observation)
    match = _EXIT_CODE.search(text)
    if match:
        return int(match.group(1))
    return None


#: Fragment deduped out of an observation before hashing it for loop detection,
#: so that a long file dump does not mask an unchanged short prefix.
def observation_digest(observation: str | None, head: int = 400) -> str:
    """Stable short digest of the informative head of an observation."""
    text = clean_output(observation)[:head]
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
