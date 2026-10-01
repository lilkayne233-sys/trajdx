"""Versioned identities: a task is not a run, and a location is not a claim."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from trajdx.schema import Trajectory
from trajdx.detectors.base import Finding


def digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def run_id(trajectory: Trajectory) -> str:
    # Include content even when an upstream ID exists: reused IDs cannot merge runs.
    content = {
        "framework": trajectory.framework,
        "instance_id": trajectory.instance_id,
        "upstream_id": trajectory.meta.get("trajectory_id"),
        "steps": [s.to_dict() for s in trajectory.steps],
        "patch": trajectory.model_patch,
        "exit_status": trajectory.exit_status,
    }
    return digest(content)


def finding_signature(finding: Finding) -> str:
    # Version, mode, span, waste, severity and evidence all participate.
    return digest({"identity_version": 2, **finding.to_dict()})


def finding_identity(trajectory: Trajectory, finding: Finding) -> dict[str, Any]:
    rid = run_id(trajectory)
    signature = finding_signature(finding)
    return {
        "finding_id": f"v2|{rid}|{finding.detector}|{signature}",
        "identity_version": 2,
        "run_id": rid,
        "finding_signature": signature,
        "legacy_finding_id": f"{trajectory.instance_id}|{finding.detector}|{finding.start}",
    }
