"""Built-in detectors.  Importing this package registers every detector."""

from trajdx.detectors.base import (
    REGISTRY,
    Category,
    Detector,
    Finding,
    Phase,
    Severity,
    Tier,
    detect_all,
    filter_findings,
    register_detector,
)
from trajdx.detectors.environment import EnvironmentStuckDetector
from trajdx.detectors.execution_loop import ExecutionLoopDetector
from trajdx.detectors.localization import (
    BlindSearchDetector,
    LocalizationFailureDetector,
    RedundantReadDetector,
)
from trajdx.detectors.termination import TerminationAnomalyDetector
from trajdx.detectors.verification import (
    VerificationGapDetector,
    WeakVerificationDetector,
)

__all__ = [
    "REGISTRY",
    "Category",
    "Detector",
    "Finding",
    "Phase",
    "Severity",
    "Tier",
    "detect_all",
    "filter_findings",
    "register_detector",
    "BlindSearchDetector",
    "EnvironmentStuckDetector",
    "ExecutionLoopDetector",
    "LocalizationFailureDetector",
    "RedundantReadDetector",
    "TerminationAnomalyDetector",
    "VerificationGapDetector",
    "WeakVerificationDetector",
]
