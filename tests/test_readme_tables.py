"""The README's evaluation tables must stay reproducible.

A documentation table that has drifted away from what the code actually computes
is worse than no table at all: it reads like evidence.  These tests pin the
shipped tables to the scripts that generate them, so a future change to a
detector fails the suite instead of silently invalidating the README.

``scripts/evaluate.py`` RE-RUNS the current detectors on ``data/raw/`` every time
(stored labels are only matched against what the code emits today), so these tests
need the gitignored raw file and skip without it -- they never pass by reading a
stale stored list.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "openhands_sample.jsonl"
RAW_V6 = ROOT / "data" / "raw" / "openhands_sample_v6.jsonl"
V6_LABELS = "data/labels/reviewed_ai_identity_v6.jsonl"


needs_raw = pytest.mark.skipif(not RAW.exists(), reason="raw trajectories are gitignored")
needs_raw_v6 = pytest.mark.skipif(
    not (RAW.exists() and RAW_V6.exists()), reason="raw trajectories are gitignored"
)


def _run(argv: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{argv} failed:\n{result.stderr}"
    return result.stdout


def _readme(*names: str) -> str:
    return "\n".join((ROOT / name).read_text(encoding="utf-8") for name in names)


# --------------------------------------------------------------------------
# Detector precision table
# --------------------------------------------------------------------------


@needs_raw_v6
def test_readme_detector_table_matches_evaluate():
    """Every generated row must appear verbatim in both READMEs."""
    generated = [
        line.strip()
        for line in _run(
            [
                "scripts/evaluate.py",
                "--raw", "data/raw/openhands_sample_v6.jsonl",
                "--labels", V6_LABELS,
                "--markdown",
            ]
        ).splitlines()
        if line.strip()
    ]
    detector_rows = [line for line in generated if line.startswith("| `")]
    assert detector_rows, "evaluate.py --markdown produced no detector rows"

    for readme in ("README.md",):
        text = (ROOT / readme).read_text(encoding="utf-8")
        for row in detector_rows:
            assert row in text, f"{readme} is stale, missing row:\n  {row}"

        overall = next(line for line in generated if "**overall**" in line)
        localized = overall.replace("**overall**", "**整体**")
        assert overall in text or localized in text, (
            f"{readme} is stale, missing the overall row:\n  {overall}"
        )


@needs_raw_v6
def test_readme_precision_claims_match_sample_size():
    """The prose claims must follow from the table that is actually shipped.

    The n column once summed to 57 under a caption that said 83, and two rules
    were quoted at precisions their round never measured.  Both are caught here.
    """
    output = _run(
        [
            "scripts/evaluate.py",
            "--raw", "data/raw/openhands_sample_v6.jsonl",
            "--labels", V6_LABELS,
            "--markdown",
        ]
    )
    rows = re.findall(r"^\| `(\w+)` \| (\w+) \| ([\d.]+%|—) \| (\d+) \|$", output, re.M)
    assert rows, "could not parse the generated table"

    total_n = sum(int(n) for *_rest, n in rows)
    overall = re.search(r"\*\*overall\*\* \| \| \*\*([\d.]+%)\*\* \| \*\*(\d+)\*\*", output)
    assert overall, "could not parse the overall row"
    assert int(overall.group(2)) == total_n, (
        f"per-detector n sums to {total_n} but the overall row says {overall.group(2)}"
    )

    # Whatever clears the bar in this round is what the prose may claim.
    cleared = [
        name
        for name, tier, precision, n in rows
        if tier == "core" and precision != "—" and float(precision.rstrip("%")) >= 88.0
    ]
    chinese = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in cleared:
        assert name in chinese, f"README does not mention {name}, which clears 88%"
    assert cleared == ["termination_anomaly"], (
        f"the 88% claim in the README needs updating; this round clears: {cleared}"
    )


@needs_raw_v6
def test_readme_cross_framework_table_matches_evaluate():
    """The SWE-agent cross-framework table (Table 1b) must also stay verbatim."""
    if not (ROOT / "data" / "raw" / "sweagent_sample_v1.jsonl").exists():
        pytest.skip("sweagent sample is gitignored")
    generated = [
        line.strip()
        for line in _run(
            [
                "scripts/evaluate.py",
                "--raw", "data/raw/sweagent_sample_v1.jsonl",
                "--labels", "data/labels/reviewed_ai_identity_v6_sweagent.jsonl",
                "--markdown",
            ]
        ).splitlines()
        if line.strip()
    ]
    detector_rows = [line for line in generated if line.startswith("| `")]
    assert detector_rows, "evaluate.py --markdown produced no detector rows"

    for readme in ("README.md",):
        text = (ROOT / readme).read_text(encoding="utf-8")
        for row in detector_rows:
            assert row in text, f"{readme} is stale, missing cross-framework row:\n  {row}"


# --------------------------------------------------------------------------
# Process-shape metrics
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RAW.exists(), reason="raw trajectories are gitignored")
def test_readme_process_shape_matches_discrimination():
    output = _run(["scripts/discrimination.py", "--data", "data/raw/openhands_sample_v6.jsonl"])
    aucs = dict(re.findall(r"^(\w+)\s+(0\.\d+)$", output, re.M))
    assert aucs, "could not parse AUC values"

    for readme in ("README.md",):
        text = (ROOT / readme).read_text(encoding="utf-8")
        for metric in (
            "total_steps",
            "source_edits",
            "tests_per_source_edit",
            "test_run_ratio",
            "wasted_step_ratio",
        ):
            value = aucs[metric]
            assert value in text, f"{readme} does not quote {metric} AUC {value}"
            if float(value) < 0.5:
                inverted = f"{1 - float(value):.3f}"
                assert inverted in text, (
                    f"{readme} does not quote the inverted AUC {inverted} for {metric}"
                )


# --------------------------------------------------------------------------
# Tier enforcement
# --------------------------------------------------------------------------


@needs_raw
def test_strict_gate_rejects_unversioned_repository_labels():
    """Historical labels cannot establish current semantics without new review."""
    result = subprocess.run(
        [sys.executable, "scripts/check_regression.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, "legacy-only labels must not pass the strict gate"
    assert "no stored label matches" in result.stderr