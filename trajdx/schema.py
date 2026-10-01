"""The normalized, framework-agnostic view of an agent trajectory.

Everything downstream (detectors, metrics, reports) speaks only this vocabulary,
so adding a new agent framework means writing one adapter and nothing else.

Design note
-----------
A single :class:`AgentStep` is one *decision-action-observation* triple, not a
raw log line.  That is the granularity at which failures become visible: a loop
is only recognizable when you can pair the action with the result it produced.
SWE-agent's ``.traj`` format already has this shape (``thought``/``action``/
``observation``); for OpenHands we fold each assistant ``tool_call`` together
with the ``tool`` message that answers it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator

from trajdx.fingerprints import clean_output


class StepKind(str, Enum):
    """Normalized action taxonomy shared by every adapter."""

    THOUGHT = "thought"   # reasoning only, no side effect
    READ = "read"         # open / view a file
    EDIT = "edit"         # create or modify a file
    SHELL = "shell"       # execute a shell command
    SEARCH = "search"     # grep / find / glob for symbols or files
    PLAN = "plan"         # task tracking / self-scheduling
    SUBMIT = "submit"     # finish, submit, give up
    OTHER = "other"       # framework-specific, unmapped


#: Tools whose ``args["command"]`` selects a shell command.
_SHELL_KEYS = ("command", "cmd", "script")

#: Step kinds whose observation is *program output*, and therefore can carry an
#: error.  A file view is the contents of a file: a line reading
#: ``raise TypeError(msg)`` is source code, not a failure, and classifying it as
#: one was a major source of false positives.
ERROR_BEARING_KINDS: frozenset["StepKind"] = frozenset(
    {StepKind.SHELL, StepKind.EDIT, StepKind.OTHER}
)


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:n]


@dataclass
class AgentStep:
    """One normalized step of an agent trajectory."""

    idx: int
    kind: StepKind = StepKind.OTHER

    # --- what the agent produced -------------------------------------------
    thought: str | None = None
    raw_tool: str | None = None          # tool name as it appeared in the log
    args: dict[str, Any] = field(default_factory=dict)  # normalized arguments
    raw_action: str | None = None        # original action text, for replay

    # --- what the environment answered -------------------------------------
    observation: str | None = None
    exit_code: int | None = None

    # --- derived signals ----------------------------------------------------
    files_touched: tuple[str, ...] = ()
    error_fp: str | None = None          # normalized error fingerprint
    error_kind: str | None = None        # coarse error class, e.g. "import_error"
    is_test_run: bool = False
    state: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ keys
    @property
    def tool(self) -> str:
        """Coarse tool family: shell / read / edit / search / think / plan / submit."""
        return self.kind.value

    @property
    def action_key(self) -> str:
        """Coarse identity of the action, for spotting repeated *behaviour*.

        Arguments that vary run-to-run (temp dirs, line numbers, snapshot ids)
        are deliberately excluded so that semantically identical retries collide.
        """
        if self.kind is StepKind.SHELL:
            cmd = self.args.get("command") or self.raw_action or ""
            return f"shell:{_norm_ws(_strip_volatile(str(cmd)))}"

        if self.kind is StepKind.READ:
            return f"read:{_strip_volatile(str(self.args.get('path', '')))}"

        if self.kind is StepKind.EDIT:
            path = _strip_volatile(str(self.args.get("path", "")))
            verb = str(self.args.get("verb", "") or "edit")
            # `verb` separates create / str_replace / insert on the same file;
            # the exact text lives in `exact_key`.
            return f"edit:{verb}:{path}"

        if self.kind is StepKind.SEARCH:
            pattern = _norm_ws(str(self.args.get("pattern", "")))
            where = _strip_volatile(str(self.args.get("path", "") or ""))
            return f"search:{pattern}:{where}"

        if self.kind is StepKind.THOUGHT:
            return "thought"

        if self.kind is StepKind.PLAN:
            return f"plan:{_norm_ws(str(self.args.get('verb', '')))}"

        if self.kind is StepKind.SUBMIT:
            return "submit"

        return f"other:{self.raw_tool or 'unknown'}"

    @property
    def exact_key(self) -> str:
        """Full identity of the action including its payload.

        Two consecutive steps sharing an ``exact_key`` are a literal no-op retry,
        which is the strongest possible signal of an execution loop.
        """
        if self.kind is StepKind.EDIT:
            # Canonical JSON preserves argument boundaries and types (unlike a
            # delimiter-joined payload), and includes ranges and the real path.
            # Coarse action_key intentionally scrubs paths; exact identity must
            # not merge edits to distinct temporary files or numbered modules.
            semantic_args = {k: v for k, v in self.args.items() if v is not None}
            semantic_args["path"] = self.args.get("path") or ""
            semantic_args["verb"] = self.args.get("verb") or "edit"
            payload = json.dumps(
                semantic_args, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            return f"edit:#{_short_hash(payload, 16)}"
        if self.kind is StepKind.SHELL:
            return f"{self.action_key}#{_short_hash(str(self.args.get('command', '')), 10)}"
        return self.action_key

    @property
    def observation_key(self) -> str:
        """Identity of the *result* the agent got back.

        Pairing this with ``action_key`` is what separates a genuine retry from a
        cognitive deadlock: if the action changed but ``observation_key`` did not,
        the agent is stuck against an environment that will not move.

        The whole body is hashed.  An earlier version hashed only the first 400
        characters after scrubbing digits, which made two views of different
        regions of the same file collide -- the largest single source of false
        positives in the redundant-read detector.
        """
        if self.error_fp:
            return f"err:{self.error_fp}"
        body = clean_output(self.observation)
        if not body.strip():
            return "empty"
        return f"ok:{len(body)}:{_short_hash(body, 16)}"

    @property
    def failed(self) -> bool:
        """True when this step produced an error signal of any kind."""
        return (
            self.error_fp is not None
            or self.error_kind is not None
            or self.exit_code not in (None, 0)
        )

    # ------------------------------------------------------------- rendering
    def action_summary(self, width: int = 96) -> str:
        """One-line human readable description of what the agent did."""
        if self.kind is StepKind.SHELL:
            text = str(self.args.get("command") or self.raw_action or "")
        elif self.kind is StepKind.EDIT:
            text = f"{self.args.get('verb', 'edit')} {self.args.get('path', '?')}"
        elif self.kind is StepKind.READ:
            text = f"view {self.args.get('path', '?')}"
        elif self.kind is StepKind.SEARCH:
            text = f"search {self.args.get('pattern', '?')!r}"
        elif self.kind is StepKind.SUBMIT:
            text = "submit"
        elif self.kind is StepKind.THOUGHT:
            text = self.thought or ""
        else:
            text = self.raw_action or self.raw_tool or "?"
        text = _norm_ws(text)
        return text if len(text) <= width else text[: width - 1] + "…"

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "kind": self.kind.value,
            "thought": self.thought,
            "raw_tool": self.raw_tool,
            "args": self.args,
            "raw_action": self.raw_action,
            "observation": self.observation,
            "exit_code": self.exit_code,
            "files_touched": list(self.files_touched),
            "error_fp": self.error_fp,
            "error_kind": self.error_kind,
            "is_test_run": self.is_test_run,
            "state": self.state,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentStep":
        data = dict(data)
        data["kind"] = StepKind(data.get("kind", "other"))
        data["files_touched"] = tuple(data.get("files_touched") or ())
        return cls(**data)


# --------------------------------------------------------------------------
# Volatile-token scrubbing
# --------------------------------------------------------------------------

#: Patterns that make otherwise-identical actions look different between runs.
_VOLATILE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/tmp/[A-Za-z0-9_./-]+"), "/tmp/<TMP>"),
    (re.compile(r"/(?:home|root)/[A-Za-z0-9_.-]+"), "/<HOME>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<HEX>"),          # commit / blob / pid hashes
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.]+Z?\b"), "<TS>"),
    (re.compile(r"\bpid[= ]\d+\b", re.I), "pid=<N>"),
    (re.compile(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b"), "<VER>"),  # version numbers
    (re.compile(r"\b\d{2,}\b"), "<N>"),                     # bare large integers (line nos)
)


def _strip_volatile(text: str) -> str:
    """Replace run-specific tokens so equal actions compare equal."""
    out = text
    for pattern, repl in _VOLATILE_PATTERNS:
        out = pattern.sub(repl, out)
    return out


# --------------------------------------------------------------------------
# Trajectory
# --------------------------------------------------------------------------


@dataclass
class Trajectory:
    """A full normalized agent run against one SWE-bench instance."""

    instance_id: str
    framework: str
    steps: list[AgentStep] = field(default_factory=list)

    # --- ground truth carried over from the dataset -------------------------
    resolved: bool | None = None
    repo: str | None = None
    problem_statement: str | None = None
    model_patch: str | None = None
    exit_status: str | None = None

    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- accessors
    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> Iterator[AgentStep]:
        return iter(self.steps)

    def __getitem__(self, i: int) -> AgentStep:
        return self.steps[i]

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    @property
    def failed_steps(self) -> list[AgentStep]:
        return [s for s in self.steps if s.failed]

    @property
    def patch_files(self) -> tuple[str, ...]:
        """Files the agent actually modified, parsed from the final diff."""
        return tuple(patch_files(self.model_patch))

    def kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.kind.value] = counts.get(step.kind.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "framework": self.framework,
            "resolved": self.resolved,
            "repo": self.repo,
            "problem_statement": self.problem_statement,
            "model_patch": self.model_patch,
            "exit_status": self.exit_status,
            "meta": self.meta,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trajectory":
        data = dict(data)
        data["steps"] = [AgentStep.from_dict(s) for s in data.get("steps") or ()]
        return cls(**data)


# --------------------------------------------------------------------------
# Unified diff helpers
# --------------------------------------------------------------------------

_DIFF_PATH_ALT = re.compile(r'^diff --git (?:"a/.*?"|a/.*?) ("b/.*"|b/.*)$')


def _patch_path(raw: str, strip_prefix: bool = True) -> str:
    """Decode a diff header path, ignoring unified-diff timestamps."""
    raw = raw.split("\t", 1)[0].strip()
    if raw.startswith('"'):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = raw.strip('"')
    if raw == "/dev/null":
        return ""
    return raw[2:] if strip_prefix and raw.startswith(("a/", "b/")) else raw


def patch_files(patch: str | None) -> list[str]:
    """Extract touched paths in diff-block order, including deletions/binaries.

    Prefer the destination for modifications/renames and the source for deleted
    files.  Header-only rename and binary blocks fall back to their git header;
    fallback must happen per block, not only when the entire patch has no +++.
    """
    if not patch:
        return []
    seen: dict[str, None] = {}
    old_path = new_path = fallback = ""
    has_new_header = False
    in_hunk = False
    old_remaining = new_remaining = 0

    def flush() -> None:
        path = (new_path or old_path) if has_new_header else (new_path or fallback or old_path)
        if path:
            seen.setdefault(path, None)

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush()
            old_path = new_path = fallback = ""
            has_new_header = in_hunk = False
            old_remaining = new_remaining = 0
            match = _DIFF_PATH_ALT.match(line)
            if match:
                fallback = _patch_path(match.group(1))
        elif line.startswith("@@"):
            match = re.match(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            if match:
                old_remaining = int(match.group(1) or "1")
                new_remaining = int(match.group(2) or "1")
                in_hunk = bool(old_remaining or new_remaining)
        elif in_hunk:
            # Header-shaped source lines (+++ / ---) inside a hunk are content.
            if line.startswith(" "):
                old_remaining -= 1
                new_remaining -= 1
            elif line.startswith("-"):
                old_remaining -= 1
            elif line.startswith("+"):
                new_remaining -= 1
            in_hunk = old_remaining > 0 or new_remaining > 0
        elif line.startswith("--- "):
            if has_new_header:
                flush()
                old_path = new_path = fallback = ""
                has_new_header = False
            old_path = _patch_path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            if has_new_header:
                flush()
                old_path = new_path = fallback = ""
            new_path = _patch_path(line[4:])
            has_new_header = True
        elif not in_hunk and line.startswith("rename to "):
            new_path = _patch_path(line[len("rename to "):], strip_prefix=False)
    flush()
    return list(seen)


def iter_signatures(trajectory: Trajectory, key: str = "action_key") -> Iterable[str]:
    """Yield one identity string per step, for sequence-level analyses."""
    for step in trajectory.steps:
        yield getattr(step, key)
