"""Shared test fixtures and trajectory builders."""

from __future__ import annotations

import pytest

from trajdx.schema import AgentStep, StepKind, Trajectory


def shell(idx: int, command: str, observation: str = "ok", **kw) -> AgentStep:
    from trajdx.fingerprints import classify_error

    kind, fp = classify_error(observation)
    return AgentStep(
        idx=idx,
        kind=StepKind.SHELL,
        args={"command": command},
        raw_action=command,
        raw_tool="bash",
        observation=observation,
        error_kind=kw.pop("error_kind", kind),
        error_fp=kw.pop("error_fp", fp),
        is_test_run=kw.pop("is_test_run", False),
        **kw,
    )


def read(idx: int, path: str, observation: str = "content", **kw) -> AgentStep:
    return AgentStep(
        idx=idx,
        kind=StepKind.READ,
        args={"path": path, "verb": "view"},
        raw_action=f"view {path}",
        raw_tool="str_replace_editor",
        observation=observation,
        files_touched=(path,),
        **kw,
    )


def edit(idx: int, path: str, payload: str = "x", verb: str = "str_replace", **kw) -> AgentStep:
    step = AgentStep(
        idx=idx,
        kind=StepKind.EDIT,
        args={"path": path, "verb": verb, "new_str": payload},
        raw_action=f"{verb} {path}",
        raw_tool="str_replace_editor",
        observation="File updated.",
        files_touched=(path,),
        **kw,
    )
    return step


def thought(idx: int, text: str = "thinking") -> AgentStep:
    return AgentStep(
        idx=idx,
        kind=StepKind.THOUGHT,
        thought=text,
        args={"thought": text},
        raw_action=text,
    )


def submit(idx: int) -> AgentStep:
    return AgentStep(idx=idx, kind=StepKind.SUBMIT, args={}, raw_action="submit", observation="")


def make_trajectory(steps, **kw) -> Trajectory:
    return Trajectory(
        instance_id=kw.pop("instance_id", "acme__widget-1"),
        framework=kw.pop("framework", "test"),
        steps=list(steps),
        **kw,
    )


@pytest.fixture
def trajectory_factory():
    return make_trajectory
