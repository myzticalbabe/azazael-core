# coding: utf-8
"""
CORE.inference.bus_utils — Shared MemoryBus publisher for inference worms.

Extracted from InferenceWorm and NimInferenceWorm to eliminate ~15 lines
of duplicated _publish_to_bus logic. Both worms now delegate to this module.

Usage:
    from CORE.inference.bus_utils import InferenceBusPublisher

    class MyWorm:
        def __init__(self):
            self._bus_pub = InferenceBusPublisher(source="my_worm")

        def execute(self, ...):
            self._bus_pub.publish({"event": "inference.complete", ...})
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

if TYPE_CHECKING:
    from CORE.MemoryBus import MemoryBus


class InferenceBusPublisher:
    """Lazy, best-effort MemoryBus publisher for inference events.

    Never raises — if MemoryBus is unavailable (e.g., standalone test),
    publish failures are silently ignored.
    """

    def __init__(self, source: str = "inference_worm"):
        self._bus: (MemoryBus | None) = None  # Lazy init
        self._source: str = source

    def publish(self, data: dict[str, Any]) -> None:
        """Publish an event to MemoryBus topic 'inference.result'.

        Best-effort — silently ignores errors if MemoryBus is unavailable.
        """
        try:
            if self._bus is None:
                from CORE.MemoryBus import MemoryBus
                self._bus = MemoryBus()
            self._bus.publish("inference.result", data, source=self._source)
        except Exception as exc:
            note_failure("bus_utils:L48", exc) # MemoryBus unavailable — non-fatal

    def __repr__(self) -> str:
        return (
            f"InferenceBusPublisher(source={self._source!r}, "
            f"bus={'connected' if self._bus else 'lazy'})"
        )
