"""SWE-agent ``.traj`` adapter.

SWE-agent already stores one object per step, so the work here is mostly
*parsing its mini-language*: ``open``, ``edit N:M``, ``create``, ``search_dir``,
``find_file``, ``submit``.  The subtle part is that ``edit`` carries no filename
-- it acts on whatever file the editor currently has open, which is only visible
in the per-step ``state`` blob.
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


@register_adapter
class SWEAgentAdapter(Adapter):
    name: ClassVar[str] = "sweagent"

    # ------------------------------------------------------------------ sniff
    @classmethod
    def sniff(cls, payload: Any) -> float:
        if not isinstance(payload, dict):
            return 0.0
        steps = payload.get("trajectory")
        if not isinstance(steps, list) or not steps:
            return 0.0
        # SWE-agent steps carry `action`/`observation` and have no `role`.
        head = [s for s in steps[:5] if isinstance(s, dict)]
        if not head:
            return 0.0
        if all("role" in s for s in head):
            return 0.0
        if any("action" in s or "thought" in s for s in head):
            if "environment" in payload or "info" in payload:
                return 1.0
            return 0.7
        return 0.0

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, payload: Any) -> Trajectory:
        raw_steps = payload.get("trajectory") or []
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
        return Trajectory(
            instance_id=str(
                payload.get("instance_id")
                or info.get("instance_id")
                or payload.get("environment")
                or "unknown"
            ),
            framework=cls.name,
            steps=steps,
            resolved=payload.get("resolved", info.get("resolved")),
            repo=info.get("repo") or payload.get("repo"),
            problem_statement=clean_output(payload.get("problem_statement")) or None,
            model_patch=payload.get("model_patch") or info.get("model_patch"),
            exit_status=payload.get("exit_status") or info.get("exit_status"),
            meta={
                "environment": payload.get("environment"),
                "n_raw_steps": len(raw_steps),
                "model_name": info.get("model_name"),
            },
        )

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
