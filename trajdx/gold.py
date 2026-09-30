"""Ground-truth patch attachment for the localization detector.

``localization_failure`` is the one rule that cannot be computed from the agent's
log alone: deciding whether the agent edited the *wrong* file requires knowing
which file the right answer was in.  That information lives in the task dataset
(the ``patch`` field of SWE-bench / SWE-rebench), not in the trajectory dump.

This module is the single seam through which it enters.  A sidecar JSONL keyed by
``instance_id`` is loaded once and attached to ``trajectory.meta["gold_files"]``.
Without it the detector stays silent by design rather than guessing, because an
unverifiable localization claim is exactly the false positive that discredits
rule-based labelling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from trajdx.schema import Trajectory, patch_files


def load_gold(path: str | Path) -> dict[str, list[str]]:
    """Read a gold sidecar: one JSON object per line.

    Two shapes are accepted so a sidecar can be written either by hand or by a
    fetch script:

    * ``{"instance_id": "...", "files": ["src/a.py", "tests/b.py"]}``
    * ``{"instance_id": "...", "patch": "<unified diff>"}``

    The second is what a task dataset hands you, so the diff's file list is
    parsed with the very same helper that reads the agent's own patch.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    out: dict[str, list[str]] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON line") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: expected a JSON object")
        instance_id = row.get("instance_id")
        if not instance_id:
            raise ValueError(f"{path}:{line_no}: missing 'instance_id'")
        files = row.get("files")
        if files is None:
            files = patch_files(row.get("patch"))
        out[str(instance_id)] = [str(f) for f in (files or [])]
    return out


def attach_gold(
    trajectories: Iterable[Trajectory],
    gold: Mapping[str, list[str]],
) -> int:
    """Attach gold file sets to matching trajectories.

    Returns the number of trajectories that actually received a non-empty set, so
    a caller can report coverage instead of assuming every instance was matched --
    a silently empty attachment looks identical to "the rule found nothing".
    """
    attached = 0
    for trajectory in trajectories:
        files = gold.get(trajectory.instance_id)
        if files:
            trajectory.meta["gold_files"] = list(files)
            attached += 1
    return attached


@dataclass
class GoldCoverage:
    """How much of a corpus the gold sidecar actually covers."""

    trajectories: int
    attached: int

    @property
    def ratio(self) -> float:
        return self.attached / self.trajectories if self.trajectories else 0.0

    def __str__(self) -> str:
        return f"{self.attached}/{self.trajectories} trajectories have gold files ({self.ratio:.0%})"


def coverage(trajectories: Iterable[Trajectory]) -> GoldCoverage:
    """Report gold coverage for a corpus, for the CLI to print."""
    trajectories = list(trajectories)
    attached = sum(1 for t in trajectories if t.meta.get("gold_files"))
    return GoldCoverage(len(trajectories), attached)