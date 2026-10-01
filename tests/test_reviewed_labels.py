"""The shipped independent AI review must stay versioned, explicit and complete."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = ROOT / "data" / "labels" / "reviewed_ai_identity_v3.jsonl"
SUMMARY = ROOT / "data" / "reports" / "review_v3" / "review_summary.json"


def _rows() -> list[dict]:
    return [
        json.loads(line)
        for line in REVIEWED.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def test_reviewed_labels_are_versioned_and_ai_only():
    rows = _rows()
    assert rows, "the reviewed label file is empty"
    ids = [row["finding_id"] for row in rows]
    assert len(ids) == len(set(ids)), "duplicate finding_id in the reviewed labels"
    for row in rows:
        assert row["finding_id"].startswith("v2|")
        assert row["identity_version"] == 2
        assert row["run_id"] and row["finding_signature"] and row["code_sha256"]
        assert row["reviewer_type"] == "ai"
        assert row["human_verified"] is False
        assert row["verdict"] in {"valid", "invalid", "uncertain"}
        assert row["reason"].strip() and row["evidence_steps"]
        # Evidence indices must be ints so a reader can actually jump to the step.
        assert all(isinstance(step, int) for step in row["evidence_steps"])


def test_reviewed_labels_cover_every_detector_that_fires():
    rows = _rows()
    detectors = {row["detector"] for row in rows}
    assert detectors == {
        "termination_anomaly",
        "verification_gap",
        "execution_loop",
        "redundant_read",
        "edit_error",
    }
    if SUMMARY.exists():
        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        assert summary["finding_count"] == len(rows)
        assert summary["human_verified"] is False


def test_strict_gate_accepts_the_reviewed_labels_but_not_unversioned_ones():
    """The gate must pass on reviewed labels and refuse to guess from legacy IDs."""
    import subprocess
    import sys

    raw = ROOT / "data" / "raw" / "openhands_sample.jsonl"
    if not raw.exists():
        import pytest

        pytest.skip("raw trajectories are gitignored")

    passing = subprocess.run(
        [sys.executable, "scripts/check_regression.py", "--labels",
         str(REVIEWED.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert "100.0%" in passing.stdout

    refusing = subprocess.run(
        [sys.executable, "scripts/check_regression.py", "--labels",
         "data/labels/labelled_v3_*.jsonl"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert refusing.returncode == 1
    assert "no stored label matches" in refusing.stderr
