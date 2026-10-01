"""The shipped independent AI review must stay versioned, explicit and complete."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = ROOT / "data" / "labels" / "reviewed_ai_identity_v5.jsonl"
SUMMARY = ROOT / "data" / "reports" / "review_v5" / "review_summary.json"
# The review round is defined over the 700-trajectory merged sample; without the
# gitignored raw file the gate half of this module cannot run.
RAW = ROOT / "data" / "raw" / "openhands_sample_v4.jsonl"


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
        # Adopted rows must say so; fresh rows must not claim adoption.
        if row.get("adopted_verdict"):
            assert row["adopted_verdict"] is True


def test_reviewed_labels_cover_every_detector_that_fires():
    rows = _rows()
    detectors = {row["detector"] for row in rows}
    assert detectors == {
        "termination_anomaly",
        "verification_gap",
        "execution_loop",
        "redundant_read",
        "edit_error",
        "weak_verification",
    }
    if SUMMARY.exists():
        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        assert summary["finding_count"] == len(rows)
        assert summary["human_verified"] is False
        assert summary["adopted_same_signature"] + summary["fresh_verdicts"] == len(rows)


def test_strict_gate_accepts_the_reviewed_labels_but_not_unversioned_ones():
    """The gate must pass on reviewed labels and refuse to guess from legacy IDs."""
    import subprocess
    import sys

    if not RAW.exists():
        import pytest

        pytest.skip("raw trajectories are gitignored")

    passing = subprocess.run(
        [sys.executable, "scripts/check_regression.py", "--raw",
         str(RAW.relative_to(ROOT)), "--labels",
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
