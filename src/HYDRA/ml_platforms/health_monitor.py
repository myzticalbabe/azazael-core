"""DEPRECATION SHIM: health_monitor.py

This file has been consolidated. The canonical source is:
    src/ORCHESTRATION/ml_platforms/health_monitor.py

This shim re-exports all public API from the canonical source.
Importers should update to import from ORCHESTRATION directly.

Consolidated: June 17, 2026 — 400+ lines of duplicated code eliminated.
"""
import warnings
warnings.warn(
    "HYDRA/ml_platforms/health_monitor.py is deprecated. "
    "Use ORCHESTRATION/ml_platforms/health_monitor.py instead.",
    DeprecationWarning, stacklevel=2
)

import sys
from pathlib import Path

# Remove own dir from path to prevent circular import
_own_dir = str(Path(__file__).resolve().parent)
if _own_dir in sys.path:
    sys.path.remove(_own_dir)

# Import from canonical source
_canonical_dir = str(Path(__file__).resolve().parent.parent.parent / "ORCHESTRATION" / "ml_platforms")
if _canonical_dir not in sys.path:
    sys.path.insert(0, _canonical_dir)

from health_monitor import (  # type: ignore[import-not-found]
    HealthStatus,
    HealthCheck,
    Alert,
    PlatformHealthMonitor,
    default_alert_handler,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "HealthStatus",
    "HealthCheck",
    "Alert",
    "PlatformHealthMonitor",
    "default_alert_handler",
]
