# coding: utf-8
"""
NEURAL MESH — Category: PLASTICITY (4 worms)
Learning rules: Hebbian, STDP, EWC anti-forgetting, decay.

HebbianUpdaterWorm, STDPClockWorm, ElasticConsolidationWorm, ForgettingCurveWorm
"""

import math
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, List
import math as _m

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("plasticity:L18", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="plasticity", priority=7, cooldown_seconds=30.0,
    description="Hebbian edge update: fire-together wire-together")
class HebbianUpdaterWorm:
    """Hebbian delta on synapse weights."""

    VERSION = "1.0.0"

    def __init__(self, lr: float = 0.05):
        self._lr = lr
        self._updates = 0
        self._lock = threading.Lock()

    def update(self, w: float, pre: float, post: float) -> dict[str, Any]:
        dw = self._lr * pre * post
        with self._lock:
            self._updates += 1
            n = self._updates
        return {"success": True, "w_old": w, "w_new": round(w + dw, 5),
                "dw": round(dw, 5), "updates": n}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"lr": self._lr, "updates": self._updates}}


@register_worm(category="plasticity", priority=7, cooldown_seconds=30.0,
    description="STDP window: pre-before-post potentiates, reverse depresses")
class STDPClockWorm:
    """Spike-timing-dependent plasticity kernel."""

    VERSION = "1.0.0"

    def __init__(self, a_plus: float = 0.1, a_minus: float = 0.12, tau_ms: float = 20.0):
        self._ap = a_plus
        self._am = a_minus
        self._tau = tau_ms
        self._events = 0
        self._lock = threading.Lock()

    def kernel(self, dt_ms: float) -> float:
        if dt_ms >= 0:
            return self._ap * math.exp(-dt_ms / self._tau)
        return -self._am * math.exp(dt_ms / self._tau)

    def apply(self, w: float, dt_ms: float) -> dict[str, Any]:
        dw = self.kernel(dt_ms)
        with self._lock:
            self._events += 1
            n = self._events
        return {"success": True, "dt_ms": dt_ms, "dw": round(dw, 5),
                "w_new": round(w + dw, 5), "events": n}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"events": self._events, "tau_ms": self._tau}}


@register_worm(category="plasticity", priority=6, cooldown_seconds=60.0,
    description="EWC-lite: Fisher-weighted penalty anchors old tasks")
class ElasticConsolidationWorm:
    """EWC penalty accumulator per named task."""

    VERSION = "1.0.0"

    def __init__(self, lam: float = 100.0):
        self._lam = lam
        self._fisher: dict[str, float] = defaultdict(float)
        self._anchor: dict[str, float] = {}
        self._lock = threading.Lock()

    def anchor_task(self, task: str, params: dict[str, float], fisher: dict[str, float]) -> dict[str, Any]:
        with self._lock:
            for k, v in params.items():
                self._anchor[f"{task}:{k}"] = v
            for k, v in fisher.items():
                self._fisher[f"{task}:{k}"] += v
            return {"success": True, "task": task, "anchored": len(params)}

    def penalty(self, task: str, params: dict[str, float]) -> dict[str, Any]:
        with self._lock:
            pen = 0.0
            for k, v in params.items():
                key = f"{task}:{k}"
                if key in self._anchor:
                    pen += self._fisher.get(key, 0.0) * (v - self._anchor[key]) ** 2
            pen *= (self._lam / 2.0)
            return {"success": True, "task": task, "penalty": round(pen, 4)}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"anchored_keys": len(self._anchor), "lambda": self._lam}}


@register_worm(category="plasticity", priority=5, cooldown_seconds=120.0,
    description="Ebbinghaus decay scheduler for engram review timing")
class ForgettingCurveWorm:
    """Schedules engram review before predicted recall drops below threshold."""

    VERSION = "1.0.0"

    def __init__(self, strength: float = 1.0, threshold: float = 0.5):
        self._s = strength
        self._thr = threshold
        self._lock = threading.Lock()

    def recall_prob(self, hours_since: float) -> float:
        return math.exp(-hours_since / (24.0 * max(0.1, self._s)))

    def next_review_hours(self) -> dict[str, Any]:
        if self._thr >= 1.0:
            h = 1.0
        else:
            h = -24.0 * self._s * _m.log(max(1e-6, self._thr))
        return {"success": True, "hours": round(h, 1), "threshold": self._thr,
                "strength": self._s}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.next_review_hours()}
