"""Small synthetic corpora exercise sampling and the strict gate without raw data."""
import json
from pathlib import Path
import subprocess
import sys

from scripts.validate_pool import sample_pool, validate
from trajdx.adapters.openhands import OpenHandsAdapter
from trajdx.detectors import detect_all
from trajdx.identity import finding_identity


def record(i, target=False):
    return {"instance_id": f"acme__repo-{i}", "target": target, "trajectory": [
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "execute_bash", "arguments": {"command": "pwd"}}}]},
        {"role": "tool", "tool_call_id": "c", "content": "ok\nExit code: 0"},
    ]}


def test_sampling_is_bounded_reproducible_and_reads_target(tmp_path):
    source = tmp_path / "pool.jsonl"
    source.write_text("\n".join(json.dumps(record(i, i % 2 == 0)) for i in range(20)) + "\nBAD", encoding="utf-8")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    meta = sample_pool(source, a, 20, 3, 42)
    sample_pool(source, b, 20, 3, 42)
    assert a.read_bytes() == b.read_bytes()
    assert meta["scanned"] == 20
    assert meta["sampled_strata"] == {"resolved/short": 3, "unresolved/short": 3}
    result = validate(a)
    assert result["totals"]["parsed"] == 6
    assert result["errors"] == []


def test_strict_gate_accepts_versioned_labels_and_rejects_sparse_coverage(tmp_path):
    raw = tmp_path / "runs.jsonl"
    labels = tmp_path / "labels.jsonl"
    rows = [record(i) for i in range(24)]
    raw.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    verdicts = []
    for row in rows:
        t = OpenHandsAdapter.parse(row)
        for f in detect_all(t, tier="core"):
            verdicts.append({**finding_identity(t, f), "detector": f.detector, "verdict": "valid"})
    labels.write_text("\n".join(map(json.dumps, verdicts)), encoding="utf-8")
    command = [sys.executable, "scripts/check_regression.py", "--raw", str(raw), "--labels", str(labels)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    labels.write_text(json.dumps(verdicts[0]), encoding="utf-8")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert "labelled coverage" in result.stdout
    assert "only 1 labels" in result.stdout
