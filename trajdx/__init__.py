"""trajdx - step-level failure diagnosis for code agents.

The package turns heterogeneous agent logs (OpenHands, SWE-agent, ...) into a
single normalized event sequence, then runs deterministic rule detectors over it
to explain *why* a SWE-bench run failed and *how many steps* were wasted.
"""

from trajdx.schema import AgentStep, StepKind, Trajectory
from trajdx.detectors import REGISTRY, detect_all, Finding
from trajdx.metrics import WasteReport, wasted_step_ratio

__version__ = "0.1.0"

__all__ = [
    "AgentStep",
    "StepKind",
    "Trajectory",
    "REGISTRY",
    "detect_all",
    "Finding",
    "WasteReport",
    "wasted_step_ratio",
    "__version__",
]
