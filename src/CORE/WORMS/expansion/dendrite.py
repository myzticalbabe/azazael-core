# coding: utf-8
"""
NEURAL MESH — Category: DENDRITE (4 worms)
Fan-in aggregation for neural mesh: Win, WSL2-Ubuntu, WSL2-Kali, Bare, G:.

DendriteAggregatorWorm, AttentionGateWorm, ContextConcatWorm, SensoryFuseWorm
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
    note_failure("dendrite:L16", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="dendrite", priority=7, cooldown_seconds=15.0,
    description="Weighted fan-in over N worm outputs; per-env gain")
class DendriteAggregatorWorm:
    """Weighted sum integrator with per-source gain."""

    VERSION = "1.0.0"

    def __init__(self):
        self._gains: dict[str, float] = {}
        self._integrations = 0
        self._lock = threading.Lock()

    def set_gain(self, source: str, gain: float) -> dict[str, Any]:
        with self._lock:
            self._gains[source] = max(0.0, min(5.0, gain))
            return {"success": True, "source": source, "gain": self._gains[source]}

    def integrate(self, inputs: dict[str, float]) -> dict[str, Any]:
        with self._lock:
            total = sum(v * self._gains.get(k, 1.0) for k, v in inputs.items())
            self._integrations += 1
            return {"success": True, "output": round(total, 4),
                    "fan_in": len(inputs), "integrations": self._integrations}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"gains": len(self._gains), "integrations": self._integrations}}


@register_worm(category="dendrite", priority=8, cooldown_seconds=15.0,
    description="Top-k attention gate over dendritic inputs; MoE-adjacent")
class AttentionGateWorm:
    """Top-k gate; pairs with synapse:MoEDispatcherWorm."""

    VERSION = "1.0.0"

    def __init__(self, top_k: int = 3):
        self._top_k = top_k
        self._bias: dict[str, float] = {}
        self._lock = threading.Lock()

    def gate(self, scores: dict[str, float]) -> dict[str, Any]:
        with self._lock:
            adj = {k: v + self._bias.get(k, 0.0) for k, v in scores.items()}
            ranked = sorted(adj.items(), key=lambda kv: -kv[1])[:self._top_k]
            total = sum(s for _, s in ranked) or 1.0
            return {"success": True, "pass": [k for k, _ in ranked],
                    "weights": {k: round(s / total, 3) for k, s in ranked}}

    def set_bias(self, source: str, bias: float) -> dict[str, Any]:
        with self._lock:
            self._bias[source] = bias
            return {"success": True, "source": source, "bias": bias}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"top_k": self._top_k, "biases": len(self._bias)}}


@register_worm(category="dendrite", priority=6, cooldown_seconds=30.0,
    description="Concat + truncate multi-source context for LLM prompt build")
class ContextConcatWorm:
    """Deterministic context merger with char budget."""

    VERSION = "1.0.0"

    def __init__(self, budget_chars: int = 8000):
        self._budget = budget_chars
        self._merges = 0
        self._lock = threading.Lock()

    def merge(self, chunks: dict[str, str]) -> dict[str, Any]:
        with self._lock:
            # deterministic order = sorted keys; per-chunk fair share
            keys = sorted(chunks.keys())
            per = max(256, self._budget // max(1, len(keys)))
            parts = []
            for k in keys:
                parts.append(f"[{k}]\n{chunks[k][:per]}")
            out = "\n\n".join(parts)[:self._budget]
            self._merges += 1
            return {"success": True, "chars": len(out), "sources": keys,
                    "merges": self._merges, "text": out[:500]}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"budget": self._budget, "merges": self._merges}}


@register_worm(category="dendrite", priority=6, cooldown_seconds=30.0,
    description="Fuse SystemZ/ProcessZ/NetworXZ + Kali findings into one sensory vector")
class SensoryFuseWorm:
    """Sensory fusion: monitor + security -> normalized vector."""

    VERSION = "1.0.0"

    def __init__(self):
        self._fusions = 0
        self._lock = threading.Lock()

    def fuse(self, monitor: dict | None = None, security: dict | None = None) -> dict[str, Any]:
        monitor = monitor or {}
        security = security or {}
        vec = {
            "cpu_load": min(1.0, float(monitor.get("cpu", 0)) / 100.0),
            "mem_load": min(1.0, float(monitor.get("mem", 0)) / 100.0),
            "net_anomaly": 1.0 if monitor.get("net_anomaly") else 0.0,
            "vuln_count_norm": min(1.0, float(security.get("vulns", 0)) / 20.0),
            "threat": 1.0 if security.get("threat") else 0.0,
        }
        with self._lock:
            self._fusions += 1
            n = self._fusions
        return {"success": True, "vector": vec, "fusions": n,
                "at": datetime.now().isoformat()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"fusions": self._fusions}}
