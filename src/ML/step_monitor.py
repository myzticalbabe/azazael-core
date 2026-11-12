# coding: utf-8
"""Online metacognition monitor: watches a live agent step stream and raises
stall/loop verdicts while there is still time to intervene.

The 2026-09-20 qwythos-xml run spun 205 empty steps unnoticed; this monitor
would have fired at step 5. Rules are deliberately conservative (high
thresholds, exact-match repetition) so normal deliberation never trips them.
Pure step logic, no I/O; one lock guards shared counters for free-threaded
callers.

Not CORE's MetaCognitionWorm (reflection engine); this watches step streams
for stalls/loops. Renamed from ML.metacog 2026-10-10.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass(frozen=True)
class StepEvent:
    """One agent-loop step as the monitor sees it."""

    kind: str  # "text" | "tool" | "reasoning" | "empty" | "error"
    text: str = ""
    tool: str = ""
    is_error: bool = False


@dataclass(frozen=True)
class Verdict:
    status: str  # "ok" | "warn" | "stall"
    reason: str = ""
    step: int = 0


@dataclass
class StepMonitor:
    """Stall/loop detector over a step stream.

    Thresholds: ``empty_limit`` consecutive empty turns, ``repeat_limit``
    consecutive identical outputs, ``error_limit`` consecutive errors.
    Warnings fire at half thresholds.
    """

    empty_limit: int = 5
    repeat_limit: int = 4
    error_limit: int = 5
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _step: int = 0
    _empties: int = 0
    _errors: int = 0
    _last_hash: str = ""
    _repeats: int = 0

    def reset(self) -> None:
        with self._lock:
            self._step = 0
            self._empties = 0
            self._errors = 0
            self._last_hash = ""
            self._repeats = 0

    @staticmethod
    def _digest(event: StepEvent) -> str:
        body = f"{event.kind}\x00{event.tool}\x00{event.text.strip()}"
        return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()

    def check(self, event: StepEvent) -> Verdict:
        """Feed one step; returns the current verdict."""
        with self._lock:
            self._step += 1
            digest = self._digest(event)
            self._repeats = self._repeats + 1 if digest == self._last_hash else 0
            self._last_hash = digest

            if event.kind == "empty":
                self._empties += 1
            else:
                self._empties = 0
            if event.is_error:
                self._errors += 1
            else:
                self._errors = 0
            if event.kind == "tool":
                self._repeats = 0

            step = self._step
            if self._empties >= self.empty_limit:
                return Verdict("stall", f"{self._empties} consecutive empty turns", step)
            if self._errors >= self.error_limit:
                return Verdict("stall", f"{self._errors} consecutive errors", step)
            if self._repeats >= self.repeat_limit:
                return Verdict("stall", f"identical output {self._repeats + 1}x in a row", step)
            if (
                self._empties >= self.empty_limit // 2 + 1
                or self._errors >= self.error_limit // 2 + 1
                or self._repeats >= self.repeat_limit // 2 + 1
            ):
                return Verdict("warn", "early stall pattern", step)
            return Verdict("ok", "", step)
