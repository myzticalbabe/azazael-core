# coding: utf-8
"""
NEURAL MESH — Category: AXON (4 worms)
Signal transport with delay compensation: W11 <-> WSL2-Ubuntu <-> WSL2-Kali <-> Bare <-> G:.

MyelinSheathWorm, SaltatoryRouterWorm, SpikeEncoderWorm, LatencyCompensatorWorm
"""

import threading
import time
from datetime import datetime
from typing import Any, List

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
    note_failure("axon:L16", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="axon", priority=8, cooldown_seconds=15.0,
    description="Priority fast-lane: myelinated routes skip gossip hops")
class MyelinSheathWorm:
    """Fast-lane registry; wraps mesh:MeshRouterWorm weights."""

    VERSION = "1.0.0"

    def __init__(self):
        self._fast: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def myelinate(self, src: str, dst: str, boost: float = 2.0) -> dict[str, Any]:
        with self._lock:
            self._fast[(src, dst)] = max(0.5, min(5.0, boost))
            return {"success": True, "route": f"{src}>{dst}", "boost": self._fast[(src, dst)]}

    def boost_for(self, src: str, dst: str) -> float:
        with self._lock:
            return self._fast.get((src, dst), 1.0)

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"fast_lanes": len(self._fast)}}


@register_worm(category="axon", priority=7, cooldown_seconds=20.0,
    description="Hop-skipping router: direct env send when partition-free")
class SaltatoryRouterWorm:
    """Saltatory conduction: 1-hop direct vs multi-hop gossip."""

    VERSION = "1.0.0"

    def __init__(self):
        self._direct = 0
        self._multi = 0
        self._lock = threading.Lock()

    def send(self, dst_env: str, partitioned: bool = False) -> dict[str, Any]:
        with self._lock:
            if partitioned:
                self._multi += 1
                mode = "multi-hop"
            else:
                self._direct += 1
                mode = "direct"
            return {"success": True, "dst": dst_env, "mode": mode,
                    "direct": self._direct, "multi": self._multi}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"direct": self._direct, "multi": self._multi}}


@register_worm(category="axon", priority=6, cooldown_seconds=30.0,
    description="Rate/temporal spike encoder for metric streams")
class SpikeEncoderWorm:
    """Threshold-crossing spike encoder over scalar streams."""

    VERSION = "1.0.0"

    def __init__(self, threshold: float = 0.7):
        self._thr = threshold
        self._spikes: list = []
        self._lock = threading.Lock()

    def encode(self, stream: list[float]) -> dict[str, Any]:
        spikes = [i for i, v in enumerate(stream) if v >= self._thr]
        with self._lock:
            self._spikes.extend(spikes)
            self._spikes = self._spikes[-500:]
            return {"success": True, "spikes": spikes, "count": len(spikes),
                    "retained": len(self._spikes)}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"threshold": self._thr, "retained": len(self._spikes)}}


@register_worm(category="axon", priority=7, cooldown_seconds=30.0,
    description="CLOCKZ-aware delay compensation per env pair")
class LatencyCompensatorWorm:
    """EMA latency table; predicts arrival-adjusted ordering."""

    VERSION = "1.0.0"

    def __init__(self, alpha: float = 0.2):
        self._ema: dict[str, float] = {}
        self._alpha = alpha
        self._lock = threading.Lock()

    def observe(self, route: str, rtt_ms: float) -> dict[str, Any]:
        with self._lock:
            old = self._ema.get(route, rtt_ms)
            new = old + self._alpha * (rtt_ms - old)
            self._ema[route] = round(new, 2)
            return {"success": True, "route": route, "ema_ms": self._ema[route]}

    def compensate(self, route: str) -> dict[str, Any]:
        with self._lock:
            ema = self._ema.get(route, 50.0)
            return {"success": True, "route": route, "ema_ms": ema,
                    "deadline_pad_ms": round(ema * 1.5, 1)}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"routes": len(self._ema), "table": dict(list(self._ema.items())[:10])}}
