# coding: utf-8
"""
NEURAL MESH — Category: THALAMUS (3 worms)
Relay + arousal gating before cortex (mesh).

ThalamicGateWorm, SensoryRelayWorm, ArousalSwitchWorm
"""

import threading
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
    note_failure("thalamus:L15", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="thalamus", priority=8, cooldown_seconds=15.0,
    description="First-pass filter: drops low-salience inputs pre-cortex")
class ThalamicGateWorm:
    """Salience gate with per-modality thresholds."""

    VERSION = "1.0.0"

    def __init__(self):
        self._thr: dict[str, float] = {"monitor": 0.3, "security": 0.2, "mesh": 0.4}
        self._passed = 0
        self._dropped = 0
        self._lock = threading.Lock()

    def filter(self, modality: str, salience: float) -> dict[str, Any]:
        thr = self._thr.get(modality, 0.3)
        passed = salience >= thr
        with self._lock:
            if passed:
                self._passed += 1
            else:
                self._dropped += 1
            return {"success": True, "modality": modality, "passed": passed,
                    "thr": thr, "passed_n": self._passed, "dropped_n": self._dropped}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"passed": self._passed, "dropped": self._dropped}}


@register_worm(category="thalamus", priority=7, cooldown_seconds=20.0,
    description="Routes gated sensory to cortex worms by modality map")
class SensoryRelayWorm:
    """Modality -> cortex worm routing table."""

    VERSION = "1.0.0"
    DEFAULT_MAP = {
        "monitor": "SystemZ",
        "security": "VulnScanner",
        "mesh": "MeshRouterWorm",
        "memory": "RecallRouterWorm",
    }

    def __init__(self):
        self._map = dict(self.DEFAULT_MAP)
        self._relays = 0
        self._lock = threading.Lock()

    def relay(self, modality: str) -> dict[str, Any]:
        with self._lock:
            self._relays += 1
            target = self._map.get(modality, "MetaCluster")
            return {"success": True, "modality": modality, "target": target,
                    "relays": self._relays}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"relays": self._relays, "map": self._map}}


@register_worm(category="thalamus", priority=7, cooldown_seconds=30.0,
    description="wake / sleep / recon modes; Kali quiet, Bare full")
class ArousalSwitchWorm:
    """Global arousal state: wake, sleep, recon."""

    VERSION = "1.0.0"
    MODES = ("wake", "sleep", "recon")

    def __init__(self):
        self._mode = "wake"
        self._lock = threading.Lock()

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in self.MODES:
            return {"success": False, "error": f"mode must be one of {self.MODES}"}
        with self._lock:
            self._mode = mode
            return {"success": True, "mode": self._mode, "at": datetime.now().isoformat()}

    def get_mode(self) -> dict[str, Any]:
        with self._lock:
            budgets = {"wake": {"scan": 1.0, "train": 1.0},
                       "sleep": {"scan": 0.1, "train": 0.3},
                       "recon": {"scan": 1.0, "train": 0.0}}
            return {"success": True, "mode": self._mode,
                    "budgets": budgets[self._mode]}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.get_mode()}
