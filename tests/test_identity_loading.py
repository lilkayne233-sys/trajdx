"""Regression tests for bounded loading, claim identity and CLI contracts."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trajdx.adapters import iter_file, load_file
from trajdx.cli import app
from trajdx.detectors.base import Category, Finding, Phase, Severity
from trajdx.identity import finding_identity, run_id
from trajdx.schema import AgentStep, StepKind, Trajectory
from scripts import evaluate


def record(command="pwd"):
    return {"instance_id": "task", "trajectory_id": "reused", "trajectory": [
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "execute_bash", "arguments": {"command": command}}}]},
        {"role": "tool", "tool_call_id": "c", "content": "ok\nExit code: 0"},
    ]}


def claim(**kwargs):
    return Finding("execution_loop", Category.EXECUTION_LOOP, Phase.EXECUTION, Severity.HIGH, 0, 2, (1, 2), "same error", detail={"pattern": "error"}, **kwargs)


def test_limit_does_not_read_invalid_next_record(tmp_path):
    path = tmp_path / "bounded.jsonl"
    path.write_text(json.dumps(record()) + "\nNOT JSON\n", encoding="utf-8")
    assert len(load_file(path, limit=1)) == 1
    assert list(iter_file(path, limit=0)) == []
    with pytest.raises(ValueError, match="invalid JSON"):
        load_file(path)
    with pytest.raises(ValueError, match="non-negative"):
        load_file(path, limit=-1)
    with pytest.raises(ValueError, match="unknown framework"):
        load_file(path, framework="typo")


def test_run_identity_separates_attempts_with_reused_upstream_id():
    a, b = load_record("pwd"), load_record("ls")
    assert run_id(a) != run_id(b)
    assert run_id(a) == run_id(load_record("pwd"))


def load_record(command):
    from trajdx.adapters.openhands import OpenHandsAdapter
    return OpenHandsAdapter.parse(record(command))


def test_claim_identity_changes_with_mode_span_and_evidence():
    t = load_record("pwd")
    f = claim()
    baseline = finding_identity(t, f)
    for changed in (replace(f, end=3), replace(f, evidence="changed"), replace(f, detail={"pattern": "exact"})):
        assert finding_identity(t, changed)["finding_id"] != baseline["finding_id"]


def test_unversioned_labels_rejected_and_ambiguous_legacy_never_joined(monkeypatch):
    t = load_record("pwd")
    f = claim()
    identity = finding_identity(t, f)
    row = {**identity, "detector": f.detector}
    monkeypatch.setattr(evaluate, "live_findings", lambda raw: ({identity["finding_id"]: row}, 0))
    labels = {identity["legacy_finding_id"]: {"verdict": "valid"}}
    joined, info = evaluate.join_labels(Path("unused"), labels)
    assert joined == [] and info["rejected_legacy"] == 1
    assert len(evaluate.join_labels(Path("unused"), labels, allow_legacy=True)[0]) == 1
    row2 = {**row, "finding_id": "v2|other"}
    monkeypatch.setattr(evaluate, "live_findings", lambda raw: ({identity["finding_id"]: row, row2["finding_id"]: row2}, 0))
    assert evaluate.join_labels(Path("unused"), labels, allow_legacy=True)[0] == []


def test_versioned_label_must_match_claim_signature(monkeypatch):
    identity = finding_identity(load_record("pwd"), claim())
    row = {**identity, "detector": "execution_loop"}
    monkeypatch.setattr(evaluate, "live_findings", lambda raw: ({identity["finding_id"]: row}, 0))
    labels = {identity["finding_id"]: {"verdict": "valid", "finding_signature": "changed"}}
    assert evaluate.join_labels(Path("unused"), labels)[0] == []
    labels[identity["finding_id"]]["finding_signature"] = identity["finding_signature"]
    assert len(evaluate.join_labels(Path("unused"), labels)[0]) == 1


def test_cli_findings_tier_and_true_limit(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(record()) + "\nBAD\n", encoding="utf-8")
    out = tmp_path / "findings.jsonl"
    runner = CliRunner()
    result = runner.invoke(app, ["findings", str(path), "--out", str(out), "--limit", "1", "--tier", "all"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["findings", str(path), "--out", str(out), "--limit", "1", "--tier", "typo"])
    assert result.exit_code == 2
