"""Gold-patch attachment, which is what makes localization_failure computable.

The rule is silent without ground truth *by design*, so the failure mode to guard
against is the opposite one: a sidecar that attaches nothing, or attaches paths
that never match because one side carries a container prefix.  Both would look
exactly like "the agent localized correctly".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trajdx.adapters import load_file
from trajdx.detectors.localization import LocalizationFailureDetector
from trajdx.gold import attach_gold, coverage, load_gold
from tests.conftest import edit, make_trajectory, submit

GOLD_DIFF = """\
diff --git a/src/pkg/core.py b/src/pkg/core.py
--- a/src/pkg/core.py
+++ b/src/pkg/core.py
@@ -1 +1 @@
-old
+new
"""


def _sidecar(tmp_path, rows, name="gold.jsonl"):
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_load_gold_accepts_files_shape(tmp_path):
    path = _sidecar(tmp_path, [{"instance_id": "acme__widget-1", "files": ["src/a.py"]}])
    assert load_gold(path) == {"acme__widget-1": ["src/a.py"]}


def test_load_gold_parses_a_patch(tmp_path):
    path = _sidecar(tmp_path, [{"instance_id": "acme__widget-1", "patch": GOLD_DIFF}])
    assert load_gold(path) == {"acme__widget-1": ["src/pkg/core.py"]}


def test_load_gold_tolerates_bom(tmp_path):
    """Annotation tooling writes BOMs; the gold sidecar is no different."""
    path = tmp_path / "gold.jsonl"
    path.write_text(
        json.dumps({"instance_id": "x", "files": ["a.py"]}) + "\n",
        encoding="utf-8-sig",
    )
    assert load_gold(path) == {"x": ["a.py"]}


def test_attach_gold_reports_coverage():
    trajectories = [
        make_trajectory([submit(0)], instance_id="hit"),
        make_trajectory([submit(0)], instance_id="miss"),
    ]
    attached = attach_gold(trajectories, {"hit": ["src/a.py"]})
    assert attached == 1
    assert trajectories[0].meta["gold_files"] == ["src/a.py"]
    assert "gold_files" not in trajectories[1].meta

    stats = coverage(trajectories)
    assert stats.attached == 1 and stats.trajectories == 2
    assert "1/2" in str(stats)


def test_localization_failure_is_silent_without_gold():
    trajectory = make_trajectory([edit(0, "src/wrong.py"), submit(1)])
    assert LocalizationFailureDetector().detect(trajectory) == []


def test_localization_failure_fires_when_edits_miss_the_gold_file():
    trajectory = make_trajectory(
        [edit(0, "src/wrong.py"), submit(1)],
        model_patch="""\
diff --git a/src/wrong.py b/src/wrong.py
--- a/src/wrong.py
+++ b/src/wrong.py
@@ -1 +1 @@
-a
+b
""",
    )
    trajectory.meta["gold_files"] = ["src/pkg/core.py"]
    findings = LocalizationFailureDetector().detect(trajectory)
    assert len(findings) == 1
    assert findings[0].detail["gold_files"] == ["src/pkg/core.py"]


def test_localization_failure_abstains_when_the_patch_hits_the_gold_file():
    trajectory = make_trajectory(
        [edit(0, "src/pkg/core.py"), submit(1)],
        model_patch="""\
diff --git a/src/pkg/core.py b/src/pkg/core.py
--- a/src/pkg/core.py
+++ b/src/pkg/core.py
@@ -1 +1 @@
-a
+b
""",
    )
    trajectory.meta["gold_files"] = ["src/pkg/core.py"]
    assert LocalizationFailureDetector().detect(trajectory) == []


def test_gold_and_patch_paths_are_compared_after_normalization():
    """A container-prefixed patch must still match a repo-relative gold file.

    Comparing raw paths would report a localization failure on *every* instance,
    since one side is written by the sandbox and the other by the task dataset.
    """
    trajectory = make_trajectory(
        [edit(0, "/testbed/src/pkg/core.py"), submit(1)],
        model_patch="""\
diff --git a/src/pkg/core.py b/src/pkg/core.py
--- a/src/pkg/core.py
+++ b/src/pkg/core.py
@@ -1 +1 @@
-a
+b
""",
    )
    trajectory.meta["gold_files"] = ["src/pkg/core.py"]
    assert LocalizationFailureDetector().detect(trajectory) == []


def test_load_file_attaches_gold_by_path(tmp_path):
    dump = tmp_path / "traj.jsonl"
    dump.write_text(
        json.dumps(
            {
                "instance_id": "acme__widget-1",
                "trajectory": [
                    {"action": "open src/a.py", "observation": "x", "thought": "t", "state": {}},
                    {"action": "submit", "observation": "", "thought": "t", "state": {}},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    gold = _sidecar(tmp_path, [{"instance_id": "acme__widget-1", "files": ["src/a.py"]}])

    trajectories = load_file(dump, gold=gold)
    assert trajectories[0].meta["gold_files"] == ["src/a.py"]

    # And the mapping form works too, so callers need not round-trip through disk.
    trajectories = load_file(dump, gold={"acme__widget-1": ["src/b.py"]})
    assert trajectories[0].meta["gold_files"] == ["src/b.py"]


SAMPLE = Path("data/raw/openhands_sample.jsonl")
SIDECAR = Path("data/gold/swe-rebench-gold.jsonl")


@pytest.mark.skipif(
    not (SAMPLE.exists() and SIDECAR.exists()),
    reason="the corpus and gold sidecar are external, gitignored data",
)
def test_localization_failure_discriminates_on_the_real_corpus():
    """Lock the numbers the README quotes for the gold channel.

    Both inputs are gitignored, so this is an opt-in check: it runs wherever the
    data was fetched and is skipped in a bare clone.  It is nonetheless the only
    guard on the one rule whose evidence is external ground truth rather than
    annotation -- and the only rule whose precision therefore cannot be computed
    from anything shipped in this repository.
    """
    from trajdx.detectors import detect_all

    trajectories = load_file(SAMPLE, gold=SIDECAR)
    stats = coverage(trajectories)
    assert stats.attached == stats.trajectories == 300, (
        "the README quotes these rates at 300/300 coverage; a partial sidecar would "
        "silently validate different numbers"
    )

    total = {"resolved": 0, "unresolved": 0}
    hit = {"resolved": 0, "unresolved": 0}
    for trajectory in trajectories:
        if not trajectory.meta.get("gold_files"):
            continue
        group = "resolved" if trajectory.resolved else "unresolved"
        total[group] += 1
        fired = any(
            finding.category.value == "localization_failure"
            for finding in detect_all(trajectory)
        )
        hit[group] += int(fired)

    resolved_rate = hit["resolved"] / total["resolved"]
    unresolved_rate = hit["unresolved"] / total["unresolved"]
    assert total["resolved"] > 100 and total["unresolved"] > 100
    assert unresolved_rate - resolved_rate > 0.10, (
        f"localization_failure no longer discriminates: "
        f"{resolved_rate:.3f} resolved vs {unresolved_rate:.3f} unresolved"
    )