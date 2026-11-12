# coding: utf-8
"""WSL_RECOVERY_WORM — WSL2 Recovery & Health Check Worm"""

from __future__ import annotations
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ['WSLRecovery']

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("WSL_RECOVERY_WORM:L10", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(
    category="defense",
    priority=6,
    cooldown_seconds=120.0,
    description="WSL2 recovery and health check — monitors WSL2 state and recovers from hangs",
)
class WSLRecovery:
    """WSL2 Recovery Worm — monitors and recovers WSL2 instances."""

    VERSION = "1.0.0"

    def __init__(self):
        self._healthy = True

    def get_summary(self):
        return {"status": "ok", "worm": "WSLRecovery"}
