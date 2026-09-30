"""Adapter behaviour: the two log formats must land on the same event sequence."""

from __future__ import annotations

import json

import pytest

from trajdx.adapters import ADAPTERS, detect_adapter
from trajdx.adapters.openhands import OpenHandsAdapter
from trajdx.adapters.sweagent import SWEAgentAdapter
from trajdx.schema import StepKind

# --------------------------------------------------------------------------
# OpenHands: an OpenAI-style message list
# --------------------------------------------------------------------------


def _call(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


OPENHANDS_RECORD = {
    "trajectory_id": "t-1",
    "instance_id": "acme__widget-1",
    "repo": "acme/widget",
    "resolved": False,
    "exit_status": "submit",
    "model_patch": "+++ b/src/pkg/core.py\n",
    "trajectory": [
        {"role": "system", "content": "You are OpenHands."},
        {"role": "user", "content": "The bug: widget returns 344 instead of 345."},
        {
            "role": "assistant",
            "content": "Let me look around.",
            "tool_calls": [_call("c1", "execute_bash", {"command": "cd /testbed && ls"})],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "src\nsetup.py\n"},
        {
            "role": "assistant",
            "content": "Now I will patch it.",
            "tool_calls": [
                _call(
                    "c2",
                    "str_replace_editor",
                    {
                        "command": "str_replace",
                        "path": "/testbed/src/pkg/core.py",
                        "old_str": "return a",
                        "new_str": "return b",
                    },
                )
            ],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "File updated."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_call("c3", "execute_bash", {"command": "cd /testbed && pytest tests/ -q"})],
        },
        {"role": "tool", "tool_call_id": "c3", "content": "FAILED tests/test_x.py::test_a - AssertionError"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_call("c4", "finish", {"message": "done"})],
        },
    ],
}


def test_openhands_pairs_actions_with_their_observations():
    trajectory = OpenHandsAdapter.parse(OPENHANDS_RECORD)
    assert trajectory.instance_id == "acme__widget-1"
    assert trajectory.resolved is False
    assert trajectory.repo == "acme/widget"
    assert trajectory.problem_statement.startswith("The bug:")

    kinds = [s.kind for s in trajectory.steps]
    assert kinds == [StepKind.SHELL, StepKind.EDIT, StepKind.SHELL, StepKind.SUBMIT]

    shell_step = trajectory.steps[0]
    assert shell_step.thought == "Let me look around."
    assert shell_step.observation == "src\nsetup.py\n"


def test_openhands_normalizes_sandbox_paths():
    trajectory = OpenHandsAdapter.parse(OPENHANDS_RECORD)
    edit_step = trajectory.steps[1]
    assert edit_step.args["path"] == "src/pkg/core.py"
    assert edit_step.files_touched == ("src/pkg/core.py",)


def test_openhands_marks_test_runs_and_errors():
    trajectory = OpenHandsAdapter.parse(OPENHANDS_RECORD)
    test_step = trajectory.steps[2]
    assert test_step.is_test_run is True
    assert test_step.error_kind == "test_failure"

    submit_step = trajectory.steps[3]
    assert submit_step.observation is None
    assert not submit_step.failed


def test_openhands_accepts_arguments_as_json_string():
    record = dict(OPENHANDS_RECORD)
    record["trajectory"] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "x",
                    "function": {"name": "execute_bash", "arguments": '{"command": "ls -la"}'},
                }
            ],
        },
    ]
    trajectory = OpenHandsAdapter.parse(record)
    assert trajectory.steps[0].args["command"] == "ls -la"


# --------------------------------------------------------------------------
# SWE-agent: one object per step, with a state blob
# --------------------------------------------------------------------------


SWE_AGENT_TRAJ = {
    "environment": "swe_main",
    "info": {"instance_id": "acme__widget-2", "repo": "acme/widget"},
    "trajectory": [
        {
            "action": "ls -F\n",
            "observation": "AUTHORS.rst\nsetup.py\nsrc/\n",
            "response": "...",
            "thought": "Let's list the files.",
            "state": '{"open_file": "n/a", "working_dir": "/acme__widget"}\n',
        },
        {
            "action": "open src/pkg/core.py\n",
            "observation": "[File: /acme__widget/src/pkg/core.py (94 lines total)]\n1:import re\n",
            "response": "...",
            "thought": "Open the module.",
            "state": '{"open_file": "/acme__widget/src/pkg/core.py", "working_dir": "/acme__widget"}\n',
        },
        {
            # `edit` carries no filename -- it targets the open file from `state`
            "action": "edit 1:1\nimport os\nend_of_edit\n",
            "observation": "File updated. Please review the changes.\n",
            "response": "...",
            "thought": "Patch the import.",
            "state": '{"open_file": "/acme__widget/src/pkg/core.py", "working_dir": "/acme__widget"}\n',
        },
        {
            "action": "python -m pytest tests/ -q\n",
            "observation": "1 failed, 2 passed\n",
            "response": "...",
            "thought": "Run the tests.",
            "state": '{"open_file": "/acme__widget/src/pkg/core.py", "working_dir": "/acme__widget"}\n',
        },
        {
            "action": "submit\n",
            "observation": "",
            "response": "...",
            "thought": "Done.",
            "state": '{"open_file": "n/a", "working_dir": "/acme__widget"}\n',
        },
    ],
}


def test_sweagent_maps_actions_to_the_shared_step_kinds():
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_TRAJ)
    assert trajectory.instance_id == "acme__widget-2"
    assert [s.kind for s in trajectory.steps] == [
        StepKind.SHELL,
        StepKind.READ,
        StepKind.EDIT,
        StepKind.SHELL,
        StepKind.SUBMIT,
    ]


def test_sweagent_edit_targets_the_open_file_from_state():
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_TRAJ)
    edit_step = trajectory.steps[2]
    assert edit_step.files_touched == ("src/pkg/core.py",)
    assert "import os" in edit_step.args["new_str"]
    assert "end_of_edit" not in edit_step.args["new_str"]


def test_sweagent_recognises_test_execution():
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_TRAJ)
    assert trajectory.steps[3].is_test_run is True
    assert trajectory.steps[0].is_test_run is False


# --------------------------------------------------------------------------
# SWE-agent, function-calling serialization
# --------------------------------------------------------------------------

#: Mirrors the shape of the real `trajectories/demonstrations/function_calling_simple.traj`
#: shipped upstream: a chat `history` instead of a `trajectory` list, with
#: structured `tool_calls` alongside a redundant mini-language `action` string.
#: Running the adapter over the upstream logs is what surfaced this format.
SWE_AGENT_HISTORY = {
    "instance_id": "swe-agent__test-repo-i2",
    "history": [
        {"role": "system", "content": "SETTING: you are an agent",
         "message_type": "system_prompt", "agent": "main"},
        {"role": "user", "content": "ISSUE: SyntaxError in missing_colon.py",
         "message_type": "observation", "agent": "main"},
        {
            "role": "assistant",
            "content": "Find the file.",
            "thought": "Find the file.",
            "action": "find_file missing_colon.py",
            "message_type": "action",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {
                "name": "find_file", "arguments": '{"file_name": "missing_colon.py"}'}}],
        },
        {"role": "tool", "content": 'Found 1 matches for "missing_colon.py" in /repo:\n'
                                    "/repo/tests/missing_colon.py\n(Open file: n/a)",
         "message_type": "observation", "tool_call_ids": ["call_1"]},
        {
            "role": "assistant",
            "content": "Open it.",
            "thought": "Open it.",
            "action": 'open "tests/missing_colon.py"',
            "message_type": "action",
            "tool_calls": [{"id": "call_2", "type": "function", "function": {
                "name": "open", "arguments": '{"path": "tests/missing_colon.py"}'}}],
        },
        {"role": "tool", "content": "[File: tests/missing_colon.py]\n1:def division(a, b)\n"
                                    "(Open file: /repo/tests/missing_colon.py)",
         "message_type": "observation", "tool_call_ids": ["call_2"]},
        {
            "role": "assistant",
            "content": "Add the missing colon.",
            "thought": "Add the missing colon.",
            "action": "edit 'def division(a, b)' 'def division(a, b):'",
            "message_type": "action",
            "tool_calls": [{"id": "call_3", "type": "function", "function": {
                "name": "edit",
                "arguments": '{"search": "def division(a, b)", "replace": "def division(a, b):"}'}}],
        },
        {"role": "tool", "content": "Text replaced.\n(Open file: /repo/tests/missing_colon.py)",
         "message_type": "observation", "tool_call_ids": ["call_3"]},
        {
            "role": "assistant",
            "content": "Run it.",
            "thought": "Run it.",
            "action": "python tests/missing_colon.py",
            "message_type": "action",
            "tool_calls": [{"id": "call_4", "type": "function", "function": {
                "name": "bash", "arguments": '{"command": "python tests/missing_colon.py"}'}}],
        },
        {"role": "tool", "content": "8.2\n(Open file: /repo/tests/missing_colon.py)",
         "message_type": "observation", "tool_call_ids": ["call_4"]},
        {
            "role": "assistant",
            "content": "Done.",
            "thought": "Done.",
            "action": "submit",
            "message_type": "action",
            "tool_calls": [{"id": "call_5", "type": "function", "function": {
                "name": "submit", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "diff --git a/tests/missing_colon.py b/tests/missing_colon.py",
         "message_type": "observation", "tool_call_ids": ["call_5"]},
    ],
}


def test_sweagent_reads_the_function_calling_serialization():
    """A format the first version could not read at all -- and failed loudly on."""
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_HISTORY)
    assert trajectory.framework == "sweagent"
    assert trajectory.meta["serialization"] == "history"
    assert trajectory.problem_statement.startswith("ISSUE: SyntaxError")
    assert [s.kind for s in trajectory.steps] == [
        StepKind.SEARCH,
        StepKind.READ,
        StepKind.EDIT,
        StepKind.SHELL,
        StepKind.SUBMIT,
    ]


def test_sweagent_history_pairs_results_by_tool_call_ids():
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_HISTORY)
    assert "Found 1 matches" in (trajectory.steps[0].observation or "")
    assert "Text replaced" in (trajectory.steps[2].observation or "")
    assert trajectory.steps[1].args["path"] == "tests/missing_colon.py"


def test_sweagent_history_edit_targets_the_open_file():
    """The edit tool call names no path, so the open-file hint has to supply it."""
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_HISTORY)
    edit_step = trajectory.steps[2]
    assert edit_step.files_touched == ("tests/missing_colon.py",)
    assert edit_step.args["old_str"] == "def division(a, b)"
    assert edit_step.args["new_str"] == "def division(a, b):"


def test_sweagent_history_marks_shell_steps():
    trajectory = SWEAgentAdapter.parse(SWE_AGENT_HISTORY)
    assert trajectory.steps[3].kind is StepKind.SHELL
    assert trajectory.steps[3].args["command"] == "python tests/missing_colon.py"
    assert trajectory.steps[3].is_test_run is False


def test_sweagent_sniffs_the_history_serialization():
    assert SWEAgentAdapter.sniff(SWE_AGENT_HISTORY) >= 0.5
    assert OpenHandsAdapter.sniff(SWE_AGENT_HISTORY) == 0.0
    assert detect_adapter(SWE_AGENT_HISTORY) is SWEAgentAdapter


def test_sweagent_history_without_tool_calls_falls_back_to_the_action_string():
    payload = {
        "instance_id": "x",
        "history": [
            {"role": "assistant", "action": "open src/a.py", "thought": "look",
             "message_type": "action"},
            {"role": "assistant", "content": "just thinking",
             "message_type": "action"},
            {"role": "assistant", "action": "submit", "message_type": "action"},
        ],
    }
    trajectory = SWEAgentAdapter.parse(payload)
    assert [s.kind for s in trajectory.steps] == [
        StepKind.READ,
        StepKind.THOUGHT,
        StepKind.SUBMIT,
    ]


def test_generic_chat_history_is_not_claimed_by_sweagent():
    """Only SWE-agent's own `message_type` marker earns recognition."""
    generic = {
        "history": [
            {"role": "system", "content": "hi"},
            {"role": "user", "content": "hello"},
        ]
    }
    assert SWEAgentAdapter.sniff(generic) < 0.5


# --------------------------------------------------------------------------
# Adapter selection
# --------------------------------------------------------------------------


def test_sniff_distinguishes_the_two_formats():
    assert OpenHandsAdapter.sniff(OPENHANDS_RECORD) == 1.0
    assert OpenHandsAdapter.sniff(SWE_AGENT_TRAJ) == 0.0
    assert SWEAgentAdapter.sniff(OPENHANDS_RECORD) == 0.0
    assert SWEAgentAdapter.sniff(SWE_AGENT_TRAJ) > 0.5


def test_detect_adapter_picks_the_right_one():
    assert detect_adapter(OPENHANDS_RECORD) is OpenHandsAdapter
    assert detect_adapter(SWE_AGENT_TRAJ) is SWEAgentAdapter


def test_detect_adapter_rejects_unknown_payloads():
    with pytest.raises(ValueError):
        detect_adapter({"unrelated": "data"})


def test_both_frameworks_registered():
    assert set(ADAPTERS) == {"openhands", "sweagent"}


def test_openhands_and_sweagent_agree_on_the_shared_vocabulary():
    """The whole point of the schema: two very different logs, one representation."""
    a = OpenHandsAdapter.parse(OPENHANDS_RECORD)
    b = SWEAgentAdapter.parse(SWE_AGENT_TRAJ)
    for trajectory in (a, b):
        assert trajectory.steps
        assert all(isinstance(s.kind, StepKind) for s in trajectory.steps)
        assert any(s.kind is StepKind.SUBMIT for s in trajectory.steps)
        # every step exposes the same identity keys, whatever the source format
        for step in trajectory.steps:
            assert isinstance(step.action_key, str) and step.action_key
            assert isinstance(step.exact_key, str) and step.exact_key
            assert isinstance(step.observation_key, str) and step.observation_key
