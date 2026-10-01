"""Framework adapters.  Importing this package registers every built-in adapter."""

from trajdx.adapters.base import (
    ADAPTERS,
    Adapter,
    detect_adapter,
    load_file,
    iter_file,
    register_adapter,
)
from trajdx.adapters.openhands import OpenHandsAdapter
from trajdx.adapters.sweagent import SWEAgentAdapter

__all__ = [
    "ADAPTERS",
    "Adapter",
    "OpenHandsAdapter",
    "SWEAgentAdapter",
    "detect_adapter",
    "load_file",
    "iter_file",
    "register_adapter",
]
