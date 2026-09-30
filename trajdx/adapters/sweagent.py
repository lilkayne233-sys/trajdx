"""SWE-agent ``.traj`` adapter.

SWE-agent already stores one object per step, so the work here is mostly
*parsing its mini-language*: ``open``, ``edit N:M``, ``create``, ``search_dir``,
``find_file``, ``submit``.  The subtle part is that ``edit`` carries no filename
-- it acts on whatever file the editor currently has open, which is only visible
in the per-step ``state`` blob.

Three serializations are supported
----------------------------------
Running this adapter over the real logs vendored in the upstream SWE-agent
repository surfaced a second format the first version could not read at all: the
**function-calling** serialization, which stores a chat ``history`` (roles
``system``/``user``/``assistant``/``tool``) instead of a ``trajectory`` list, and
carries structured ``tool_calls`` arguments rather than a mini-language string.

A third one arrives with the published ``nebius/SWE-agent-trajectories`` corpus
(80,036 real runs, with a ``target`` success label).  It also calls its message
list ``trajectory``, but the entries are a flat role/text stream -- roles
``system``/``user``/**``ai``** -- and the action is *embedded in prose*: the
mini-language sits in the last fenced block of each ``ai`` turn, with the
reasoning before it, and the next ``user`` turn carries the observation.

That last point is why this is the most dangerous of the three to get wrong.
``trajectory`` + ``role`` used to be enough for the OpenHands adapter to claim a
record with full confidence, so these rows were silently parsed by the wrong
adapter into **zero steps** instead of raising.  Two things now prevent it: the
role vocabularies are disjoint (``ai`` here, ``assistant``/``tool`` there), and
the loader refuses to pass a zero-step record off as an empty run.

All three are parsed here rather than in separate adapters because they are the
same agent and produce the same steps; only the envelope differs.  For the chat
forms the structured arguments win over the ``action`` string when both are
present, since they do not have to be re-parsed, and the open-file pointer is
tracked from the ``open`` calls and from the ``(Open file: …)`` hint every tool
result carries.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from trajdx.adapters.base import Adapter, register_adapter
from trajdx.fingerprints import classify_error, clean_output, extract_exit_code
from trajdx.heuristics import is_test_command, repo_relative
from trajdx.schema import ERROR_BEARING_KINDS, AgentStep, StepKind, Trajectory

_OPEN = re.compile(r"^open\s+(?P<path>\S+)(?:\s+(?P<line>\d+))?\s*$")
_CREATE = re.compile(r"^create\s+(?P<path>\S+)\s*$")
_EDIT = re.compile(r"^edit\s*(?P<range>\d+:\d+)?\s*$")
_GOTO = re.compile(r"^goto\s+\d+\s*$")
_SCROLL = re.compile(r"^scroll_(?:down|up)\s*$")
_FIND_FILE = re.compile(r"^find_file\s+(?P<pattern>.+?)(?:\s+(?P<path>\S+))?\s*$")
_SEARCH = re.compile(
    r"^search(?:_dir|_file)?\s+(?P<pattern>\".*?\"|'.*?'|\S+)(?:\s+(?P<path>\S+))?\s*$"
)
_SUBMIT = re.compile(r"^(?:submit|exit|exit_error|exit_cost|exit_api|exit_context)\s*$")

#: Tool results in the chat serialization end with `(Open file: /path/to/file)`.
#: It is the only place the currently-open file is recorded in that format.
_OPEN_FILE_HINT = re.compile(r"\(Open file:\s*([^)\r\n]+)\)")

#: In the ``nebius/SWE-agent-trajectories`` stream the action is not a field: it
#: is the last fenced block of the ``ai`` turn, with the reasoning before it.
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

#: Roles that identify each serialization.  SWE-agent's own message stream uses
#: ``ai``; OpenHands uses ``assistant``/``tool``.  They never overlap, which is
#: what makes the role vocabulary a safe discriminator between the two.
_STREAM_ROLES = frozenset({"ai"})

#: Function-calling tool name -> normalized step kind.
_TOOL_KINDS: dict[str, StepKind] = {
    "open": StepKind.READ,
    "open_file": StepKind.READ,
    "find_file": StepKind.SEARCH,
    "search": StepKind.SEARCH,
    "search_dir": StepKind.SEARCH,
    "search_file": StepKind.SEARCH,
    "edit": StepKind.EDIT,
    "str_replace": StepKind.EDIT,
    "insert": StepKind.EDIT,
    "create": StepKind.EDIT,
    "create_file": StepKind.EDIT,
    "undo_edit": StepKind.EDIT,
    "bash": StepKind.SHELL,
    "execute_bash": StepKind.SHELL,
    "run": StepKind.SHELL,
    "submit": StepKind.SUBMIT,
    "finish": StepKind.SUBMIT,
}


@register_adapter
class SWEAgentAdapter(Adapter):
    name: ClassVar[str] = "sweagent"

    # ------------------------------------------------------------------ sniff
    @classmethod
    def sniff(cls, payload: Any) -> float:
        if not isinstance(payload, dict):
            return 0.0
        steps = payload.get("trajectory")
        if isinstance(steps, list) and steps:
            head = [s for s in steps[:5] if isinstance(s, dict)]
            if not head:
                return 0.0
            stream = cls._message_stream_roles(steps)
            if stream is not None:
                # `ai` present means a run that acted; its absence means the
                # model never produced an action, which is a weaker signal but
                # still unambiguous -- no other format uses these role names.
                return 1.0 if stream & _STREAM_ROLES else 0.8
            if all("role" in s for s in head):
                # Some other chat format, not one of ours.
                return 0.0
            # SWE-agent steps carry `action`/`observation` and have no `role`.
            if any("action" in s or "thought" in s for s in head):
                if "environment" in payload or "info" in payload:
                    return 1.0
                return 0.7
            return 0.0

        # Function-calling serialization: a chat history instead of a step list.
        # `message_type` is SWE-agent's own marker, and requiring it keeps this
        # from swallowing any generic chat dump that happens to have `role`.
        history = payload.get("history")
        if isinstance(history, list) and history:
            head = [m for m in history[:10] if isinstance(m, dict)]
            if head and any("role" in m for m in head):
                if any(m.get("message_type") for m in head):
                    return 0.9
                return 0.3
        return 0.0

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, payload: Any) -> Trajectory:
        raw_steps = payload.get("trajectory")
        if isinstance(raw_steps, list) and raw_steps:
            # The dispatch must use the same test as `sniff`.  Reading the roles
            # only for `ai` here and accepting `system`/`user` there once sent a
            # message stream down the `.traj` path, where each message became a
            # fabricated THOUGHT step.
            if cls._message_stream_roles(raw_steps) is not None:
                return cls._parse_messages(payload, raw_steps)
            return cls._parse_traj(payload, raw_steps)
        history = payload.get("history")
        if isinstance(history, list) and history:
            return cls._parse_history(payload, history)
        return cls._parse_traj(payload, [])

    @staticmethod
    def _message_stream_roles(steps: list) -> set[Any] | None:
        """Role set if ``steps`` is the role/text stream, else ``None``.

        Shared by :meth:`sniff` and :meth:`parse` so the two can never disagree
        about which serialization a record uses.
        """
        head = [s for s in steps[:5] if isinstance(s, dict)]
        if not head or not all("role" in s for s in head):
            return None
        roles = {s.get("role") for s in steps if isinstance(s, dict)}
        if roles & _STREAM_ROLES:
            return roles
        # A run that failed before acting has no `ai` turn; fall back to the
        # envelope, which uses `text` where OpenHands uses `content`.
        if roles and roles <= _STREAM_ROLES | {"system", "user"} and all(
            "text" in s for s in head
        ):
            return roles
        return None

    @classmethod
    def _parse_traj(cls, payload: Any, raw_steps: list) -> Trajectory:
        steps: list[AgentStep] = []
        open_file: str = ""

        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue

            state = cls._parse_state(raw.get("state"))
            # `state` is captured as the step begins, so it tells us which file
            # the editor is about to act on.
            open_file = repo_relative(state.get("open_file")) or open_file

            thought = clean_output(raw.get("thought")) or None
            action_raw = raw.get("action") or ""
            observation = clean_output(raw.get("observation"))
            if observation is None:
                observation = None

            step = cls._build_step(
                idx=len(steps),
                action=action_raw,
                thought=thought,
                response=clean_output(raw.get("response")),
                open_file=open_file,
                state=state,
            )
            step.observation = observation
            if step.kind in ERROR_BEARING_KINDS:
                step.error_kind, step.error_fp = classify_error(observation)
                step.exit_code = extract_exit_code(observation)
            steps.append(step)

        info = payload.get("info") or {}
        return cls._build_trajectory(
            payload,
            steps,
            problem_statement=clean_output(payload.get("problem_statement")) or None,
            meta={
                "environment": payload.get("environment"),
                "n_raw_steps": len(raw_steps),
                "model_name": info.get("model_name"),
            },
        )

    # --------------------------------------------------- chat-history format
    @classmethod
    def _parse_history(cls, payload: Any, history: list) -> Trajectory:
        """Parse the function-calling serialization: roles, not step objects.

        Each ``assistant`` turn is one step, and the ``tool`` turn that answers it
        supplies the observation, paired by ``tool_call_ids``.  Structured call
        arguments are preferred over the redundant ``action`` string; the string
        is only used when a turn has no ``tool_calls`` at all.
        """
        steps: list[AgentStep] = []
        pending: dict[str, int] = {}
        open_file = ""
        system_prompt: str | None = None
        problem: str | None = None

        for message in history:
            if not isinstance(message, dict):
                continue
            role = message.get("role")

            if role == "system":
                system_prompt = clean_output(message.get("content"))
                continue

            if role == "user":
                # First user turn is the issue; later ones are nudges.
                if problem is None:
                    problem = clean_output(message.get("content"))
                continue

            if role == "assistant":
                thought = (
                    clean_output(message.get("thought") or message.get("content")) or None
                )
                calls = [c for c in (message.get("tool_calls") or []) if isinstance(c, dict)]
                if calls:
                    for call in calls:
                        function = call.get("function") or {}
                        name = str(function.get("name") or "unknown")
                        args = cls._as_args(function.get("arguments"))
                        step = cls._build_tool_step(len(steps), name, args, open_file, thought)
                        open_file = cls._track_open_file(step, open_file)
                        call_id = call.get("id")
                        if call_id:
                            pending[call_id] = step.idx
                        steps.append(step)
                    continue

                action = str(message.get("action") or "")
                if action.strip():
                    step = cls._build_step(
                        idx=len(steps),
                        action=action,
                        thought=thought,
                        response=None,
                        open_file=open_file,
                        state={},
                    )
                    open_file = cls._track_open_file(step, open_file)
                    steps.append(step)
                elif thought:
                    steps.append(
                        AgentStep(
                            idx=len(steps),
                            kind=StepKind.THOUGHT,
                            thought=thought,
                            args={"thought": thought},
                            raw_action=thought,
                            raw_tool="think",
                        )
                    )
                continue

            if role == "tool":
                observation = clean_output(message.get("content"))
                ids = list(message.get("tool_call_ids") or ())
                if not ids and message.get("tool_call_id"):
                    ids = [message["tool_call_id"]]
                for call_id in ids:
                    target = pending.get(call_id)
                    if target is None or target >= len(steps):
                        continue
                    step = steps[target]
                    step.observation = observation
                    if step.kind in ERROR_BEARING_KINDS:
                        step.error_kind, step.error_fp = classify_error(observation)
                        step.exit_code = extract_exit_code(observation)
                hint = _OPEN_FILE_HINT.search(observation or "")
                if hint:
                    resolved = repo_relative(hint.group(1))
                    if resolved and resolved.lower() != "n/a":
                        open_file = resolved
                continue

        info = payload.get("info") or {}
        return cls._build_trajectory(
            payload,
            steps,
            problem_statement=problem,
            meta={
                "environment": payload.get("environment"),
                "n_raw_steps": len(payload.get("history") or []),
                "model_name": info.get("model_name"),
                "serialization": "history",
                "system_prompt": system_prompt,
            },
        )

    # ------------------------------------------------- role/text message stream
    @classmethod
    def _parse_messages(cls, payload: Any, messages: list) -> Trajectory:
        """Parse the ``nebius/SWE-agent-trajectories`` role/text stream.

        One ``ai`` turn is one step, and the ``user`` turn after it is that
        step's observation.  The action is not a field but the last fenced block
        of the ``ai`` text, so the block is lifted out here and handed to the
        same mini-language parser the ``.traj`` format uses -- ``open``, ``edit
        N:M``, ``create``, ``search_dir`` and the rest all mean the same thing.
        """
        steps: list[AgentStep] = []
        open_file = ""
        system_prompt: str | None = None
        problem: str | None = None
        awaiting: int | None = None

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            text = message.get("text")

            if role == "system":
                system_prompt = clean_output(text) or system_prompt
                continue

            if role == "user":
                # The opening user turn is the issue statement; every later one
                # is the environment's reply to the step just taken.
                if problem is None:
                    problem = clean_output(text)
                    continue
                observation = clean_output(text)
                if awaiting is not None and awaiting < len(steps):
                    step = steps[awaiting]
                    step.observation = observation
                    if step.kind in ERROR_BEARING_KINDS:
                        step.error_kind, step.error_fp = classify_error(observation)
                        step.exit_code = extract_exit_code(observation)
                awaiting = None
                hint = _OPEN_FILE_HINT.search(observation or "")
                if hint:
                    path = repo_relative(hint.group(1))
                    if path and path.lower() != "n/a":
                        open_file = path
                continue

            if role != "ai":
                continue

            thought, action = _split_action(text)
            step = cls._build_step(
                idx=len(steps),
                action=action,
                thought=thought,
                response=None,
                open_file=open_file,
                state={},
            )
            open_file = cls._track_open_file(step, open_file)
            steps.append(step)
            awaiting = step.idx

        return cls._build_trajectory(
            payload,
            steps,
            problem_statement=problem,
            meta={
                "n_raw_steps": len(messages),
                "model_name": payload.get("model_name"),
                "serialization": "messages",
                "system_prompt": system_prompt,
            },
        )

    # ------------------------------------------------------------- assembling
    @classmethod
    def _build_trajectory(
        cls,
        payload: Any,
        steps: list[AgentStep],
        *,
        problem_statement: str | None,
        meta: dict[str, Any],
    ) -> Trajectory:
        """Assemble a :class:`Trajectory` from fields the three formats share.

        They disagree about where the outcome and the final patch live -- the
        message stream calls them ``target`` and ``generated_patch`` -- so the
        lookups are collected here instead of being repeated per format.
        """
        info = payload.get("info") or {}
        resolved = payload.get("resolved")
        if resolved is None:
            resolved = info.get("resolved")
        if resolved is None:
            resolved = payload.get("target")
        return Trajectory(
            instance_id=str(
                payload.get("instance_id")
                or info.get("instance_id")
                or payload.get("environment")
                or "unknown"
            ),
            framework=cls.name,
            steps=steps,
            resolved=resolved,
            repo=info.get("repo") or payload.get("repo"),
            problem_statement=problem_statement,
            model_patch=payload.get("model_patch")
            or info.get("model_patch")
            or payload.get("generated_patch"),
            exit_status=payload.get("exit_status") or info.get("exit_status"),
            meta=meta,
        )

    @staticmethod
    def _track_open_file(step: AgentStep, open_file: str) -> str:
        """Follow the editor pointer, which `edit` depends on in both formats."""
        if step.kind in (StepKind.READ, StepKind.EDIT):
            path = step.args.get("path")
            if path:
                return str(path)
        return open_file

    @classmethod
    def _build_tool_step(
        cls,
        idx: int,
        name: str,
        args: dict[str, Any],
        open_file: str,
        thought: str | None,
    ) -> AgentStep:
        """Map one structured ``tool_call`` (name + arguments) to a step."""
        kind = _TOOL_KINDS.get(name, StepKind.OTHER)
        step = AgentStep(idx=idx, kind=kind, raw_tool=name, thought=thought)

        if kind is StepKind.SUBMIT:
            step.args = {}
            step.raw_action = name
            return step

        if kind is StepKind.SHELL:
            command = str(args.get("command") or args.get("cmd") or "")
            step.args = {"command": command}
            step.raw_action = command
            step.is_test_run = is_test_command(command)
            return step

        if kind is StepKind.READ:
            path = repo_relative(args.get("path") or args.get("file_name"))
            step.args = {"path": path, "verb": "view"}
            step.raw_action = f"open {path}"
            step.files_touched = (path,) if path else ()
            return step

        if kind is StepKind.SEARCH:
            pattern = str(
                args.get("file_name")
                or args.get("search_term")
                or args.get("pattern")
                or args.get("query")
                or ""
            )
            step.args = {"pattern": pattern, "verb": "search"}
            step.raw_action = f"{name} {pattern}"
            return step

        if kind is StepKind.EDIT:
            # `edit`/`insert` act on the open file; the arguments name no path.
            path = repo_relative(args.get("path") or args.get("file_name")) or open_file
            if name in ("create", "create_file"):
                verb = "create"
            elif name == "insert":
                verb = "insert"
            else:
                verb = "str_replace"
            step.args = {
                "path": path,
                "verb": verb,
                "old_str": args.get("search") or args.get("old_str"),
                "new_str": args.get("replace") or args.get("new_str") or args.get("text"),
                "file_text": args.get("file_text"),
                "insert_line": args.get("insert_line"),
            }
            step.raw_action = f"{verb} {path}"
            step.files_touched = (path,) if path else ()
            return step

        step.args = args
        step.raw_action = f"{name} {args}"
        return step

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _parse_state(raw: Any) -> dict[str, Any]:
        """SWE-agent serializes ``state`` as a JSON *string*."""
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _build_step(
        cls,
        idx: int,
        action: str,
        thought: str | None,
        response: str | None,
        open_file: str,
        state: dict[str, Any],
    ) -> AgentStep:
        text = (action or "").strip()
        step = AgentStep(idx=idx, thought=thought, raw_action=text, state=state)

        if not text:
            step.kind = StepKind.THOUGHT
            step.args = {"thought": thought or ""}
            step.raw_tool = "think"
            return step

        first = text.split("\n", 1)[0].strip()

        # --- submission -----------------------------------------------------
        if _SUBMIT.match(first):
            step.kind = StepKind.SUBMIT
            step.raw_tool = "submit"
            step.args = {}
            return step

        # --- navigation -----------------------------------------------------
        if _GOTO.match(first) or _SCROLL.match(first):
            step.kind = StepKind.READ
            step.raw_tool = first.split()[0]
            step.args = {"path": open_file, "verb": "navigate"}
            step.files_touched = (open_file,) if open_file else ()
            return step

        # --- file viewing ---------------------------------------------------
        match = _OPEN.match(first)
        if match:
            path = repo_relative(match.group("path"))
            step.kind = StepKind.READ
            step.raw_tool = "open"
            step.args = {"path": path, "line": match.group("line"), "verb": "view"}
            step.files_touched = (path,) if path else ()
            return step

        # --- file creation --------------------------------------------------
        match = _CREATE.match(first)
        if match:
            path = repo_relative(match.group("path"))
            step.kind = StepKind.EDIT
            step.raw_tool = "create"
            step.args = {"path": path, "verb": "create", "file_text": _body(text, first)}
            step.files_touched = (path,) if path else ()
            return step

        # --- in-place edit (targets the currently open file) ----------------
        match = _EDIT.match(first)
        if match:
            step.kind = StepKind.EDIT
            step.raw_tool = "edit"
            step.args = {
                "path": open_file,
                "verb": "str_replace",
                "range": match.group("range"),
                "new_str": _body(text, first),
            }
            step.files_touched = (open_file,) if open_file else ()
            return step

        if first.startswith("undo_edit"):
            step.kind = StepKind.EDIT
            step.raw_tool = "undo_edit"
            step.args = {"path": open_file, "verb": "undo_edit"}
            step.files_touched = (open_file,) if open_file else ()
            return step

        # --- search ---------------------------------------------------------
        match = _FIND_FILE.match(first)
        if match:
            step.kind = StepKind.SEARCH
            step.raw_tool = "find_file"
            step.args = {
                "pattern": match.group("pattern").strip("\"'"),
                "path": repo_relative(match.group("path")),
                "verb": "find_file",
            }
            return step

        match = _SEARCH.match(first)
        if match:
            step.kind = StepKind.SEARCH
            step.raw_tool = first.split()[0]
            step.args = {
                "pattern": match.group("pattern").strip("\"'"),
                "path": repo_relative(match.group("path")),
                "verb": "search",
            }
            return step

        # --- anything else is a shell command -------------------------------
        step.kind = StepKind.SHELL
        step.raw_tool = "bash"
        step.args = {"command": text}
        step.is_test_run = is_test_command(text)
        return step


def _body(action: str, first_line: str) -> str:
    """The payload of an ``edit``/``create`` command (lines before ``end_of_edit``)."""
    rest = action[len(first_line):]
    if "end_of_edit" in rest:
        rest = rest.split("end_of_edit", 1)[0]
    return rest.strip("\n")


def _split_action(text: Any) -> tuple[str | None, str]:
    """Split an ``ai`` turn into its reasoning and its action.

    SWE-agent puts the command in the last fenced block of the turn, so the
    *last* block is the action and everything before it is the thought.  A turn
    with no block is pure reasoning and yields an empty action, which the
    mini-language parser turns into a ``THOUGHT`` step.
    """
    raw = text if isinstance(text, str) else ""
    blocks = list(_FENCE.finditer(raw))
    if not blocks:
        return clean_output(raw) or None, ""
    last = blocks[-1]
    # The blank line that separates the reasoning from the fence is not part of
    # the reasoning, so strip it rather than carrying it into every thought.
    thought = (clean_output(raw[: last.start()]) or "").strip() or None
    return thought, last.group(1).strip()
