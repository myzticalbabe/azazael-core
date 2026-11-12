"""Native azazael ML package.

Owns the eight classical estimator families behind one uniform interface.
Stateless factories only (no module-level mutable state) so the package is
safe under Python 3.14t free-threading: every call builds a fresh estimator.
"""

from ML.estimators import (
    ESTIMATOR_FAMILIES,
    EstimatorSpec,
    fit_score,
    make_estimator,
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
    "ESTIMATOR_FAMILIES",
    "EstimatorSpec",
    "fit_score",
    "make_estimator",
]
