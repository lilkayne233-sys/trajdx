"""OpenHands trajectory adapter.

OpenHands emits a flat OpenAI-style message list.  The interesting structure is
implicit: an ``assistant`` message carrying ``tool_calls`` and the ``tool``
message that answers it via ``tool_call_id`` form one logical step, and they can
be separated by nothing at all or by other messages.  We pair them by id rather
than by position so that parallel tool calls and interleaved reasoning survive.
"""

from __future__ import annotations

from typing import Any, ClassVar

from trajdx.adapters.base import Adapter, register_adapter
from trajdx.fingerprints import classify_error, clean_output, extract_exit_code
from trajdx.heuristics import is_test_command, repo_relative
from trajdx.schema import ERROR_BEARING_KINDS, AgentStep, StepKind, Trajectory

#: ``str_replace_editor`` sub-command -> normalized step kind.
_EDITOR_KIND: dict[str, StepKind] = {
    "view": StepKind.READ,
    "create": StepKind.EDIT,
    "str_replace": StepKind.EDIT,
    "insert": StepKind.EDIT,
    "undo_edit": StepKind.EDIT,
}


@register_adapter
class OpenHandsAdapter(Adapter):
    name: ClassVar[str] = "openhands"

    # ------------------------------------------------------------------ sniff
    @classmethod
    def sniff(cls, payload: Any) -> float:
        if not isinstance(payload, dict):
            return 0.0
        messages = payload.get("trajectory")
        if not isinstance(messages, list) or not messages:
            return 0.0
        # OpenHands records are message lists, and it is tempting to accept any
        # list whose entries carry `role`.  That is too loose: SWE-agent's
        # published trajectory corpus uses the same `trajectory` key with a
        # role/text stream, so it was claimed here and then parsed into nothing.
        # The two role vocabularies are disjoint -- `assistant`/`tool` here,
        # `ai` there -- so require OpenHands' own names instead.
        roles = {m.get("role") for m in messages if isinstance(m, dict)}
        if "ai" in roles:
            return 0.0
        if roles & {"assistant", "tool"}:
            return 1.0
        return 0.0

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, payload: Any) -> Trajectory:
        messages = payload.get("trajectory") or []
        steps: list[AgentStep] = []
        pending: dict[str, int] = {}   # tool_call_id -> index into `steps`
        system_prompt: str | None = None
        problem: str | None = None

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")

            if role == "system":
                system_prompt = clean_output(message.get("content"))
                continue

            if role == "user":
                # The first user turn is the issue text; later ones are nudges
                # ("please continue") and carry no diagnostic value.
                if problem is None:
                    problem = clean_output(message.get("content"))
                continue

            if role == "assistant":
                content = clean_output(message.get("content")) or ""
                tool_calls = message.get("tool_calls") or []

                if not tool_calls:
                    if content.strip():
                        steps.append(
                            AgentStep(
                                idx=len(steps),
                                kind=StepKind.THOUGHT,
                                thought=content,
                                args={"thought": content},
                                raw_action=content,
                                raw_tool="think",
                            )
                        )
                    continue

                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    name = function.get("name") or "unknown"
                    args = cls._as_args(function.get("arguments"))
                    step = cls._build_step(len(steps), name, args, content)
                    call_id = call.get("id")
                    if call_id:
                        pending[call_id] = step.idx
                    steps.append(step)
                continue

            if role == "tool":
                call_id = message.get("tool_call_id")
                target = pending.get(call_id) if call_id else None
                if target is None or target >= len(steps):
                    continue
                observation = clean_output(message.get("content"))
                step = steps[target]
                step.observation = observation
                if step.kind in ERROR_BEARING_KINDS:
                    step.error_kind, step.error_fp = classify_error(observation)
                    step.exit_code = extract_exit_code(observation)
                continue

        return Trajectory(
            instance_id=str(payload.get("instance_id") or payload.get("trajectory_id") or "unknown"),
            framework=cls.name,
            steps=steps,
            resolved=payload.get("resolved"),
            repo=payload.get("repo"),
            problem_statement=problem,
            model_patch=payload.get("model_patch"),
            exit_status=payload.get("exit_status"),
            meta={
                "trajectory_id": payload.get("trajectory_id"),
                "n_messages": len(messages),
                "system_prompt": system_prompt,
                "gen_tests_correct": payload.get("gen_tests_correct"),
                "pred_passes_gen_tests": payload.get("pred_passes_gen_tests"),
            },
        )

    # ------------------------------------------------------------------ tools
    @classmethod
    def _build_step(cls, idx: int, name: str, args: dict[str, Any], thought: str) -> AgentStep:
        base = AgentStep(idx=idx, raw_tool=name, thought=thought or None)

        if name == "execute_bash":
            command = str(args.get("command") or "")
            base.kind = StepKind.SHELL
            base.args = {"command": command, "timeout": args.get("timeout")}
            base.raw_action = command
            base.is_test_run = is_test_command(command)
            return base

        if name == "think":
            text = str(args.get("thought") or thought or "")
            base.kind = StepKind.THOUGHT
            base.thought = text
            base.args = {"thought": text}
            base.raw_action = text
            return base

        if name == "finish":
            base.kind = StepKind.SUBMIT
            base.args = {"message": str(args.get("message") or "")}
            base.raw_action = "finish"
            return base

        if name == "task_tracker":
            verb = str(args.get("command") or "plan")
            base.kind = StepKind.PLAN
            base.args = {"verb": verb, "task_list": args.get("task_list")}
            base.raw_action = f"task_tracker {verb}"
            return base

        if name == "str_replace_editor":
            verb = str(args.get("command") or "view")
            path = repo_relative(args.get("path"))
            kind = _EDITOR_KIND.get(verb, StepKind.EDIT)
            base.kind = kind
            base.args = {
                "path": path,
                "verb": verb,
                "view_range": args.get("view_range"),
                "old_str": args.get("old_str"),
                "new_str": args.get("new_str"),
                "file_text": args.get("file_text"),
                "insert_line": args.get("insert_line"),
            }
            base.raw_action = f"{verb} {path}"
            base.files_touched = (path,) if path else ()
            return base

        # Unknown tool: keep the payload so nothing is silently dropped.
        base.kind = StepKind.OTHER
        base.args = args
        base.raw_action = f"{name} {args}"
        return base
