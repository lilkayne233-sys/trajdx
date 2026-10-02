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
from trajdx.detectors.edit_error import EditErrorDetector
from trajdx.detectors.localization import LocalizationFailureDetector
from trajdx.detectors.termination import TerminationAnomalyDetector
from trajdx.detectors.verification import VerificationGapDetector

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
    "EditErrorDetector",
    "LocalizationFailureDetector",
    "TerminationAnomalyDetector",
    "VerificationGapDetector",
]
