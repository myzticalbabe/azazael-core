# coding: utf-8
"""
CORE.Widgets — Thread-Safe Tkinter UI Components for Azazael

Provides base classes for tkinter popup monitors used across
ORCHESTRATION (TARBABY, HORUS, HYDRA) with enforced thread-safety
for Python 3.14t free-threading on Windows.

Usage:
    from CORE.Widgets import BasePopupMonitor, QueueMessage

    class MyMonitor(BasePopupMonitor):
        def handle_message(self, msg: QueueMessage):
            if msg.action == "log":
                self._write_log(msg.payload)
"""

from CORE.Widgets.BasePopupMonitor import (
    BasePopupMonitor,
    QueueMessage,
    SubprocessError,
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
    "BasePopupMonitor",
    "QueueMessage",
    "SubprocessError",
]
