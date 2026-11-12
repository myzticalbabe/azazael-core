# coding: utf-8
"""A/B testing worm -- ABTestRunner (split from learning.py, 2026-09-07)."""

import json
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import defaultdict
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("learning_ab:L17", exc)
    _REGISTRY = False
def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("learning_ab:L27", exc)
    _FREETHREAD = False
FreeThreadPool = None
ThreadSafeDict = dict
ThreadSafeList = list
def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("learning_ab:L36", exc)
            return False

@register_worm(category="learning", priority=4, cooldown_seconds=120.0,
    description="A/B test runner — split-test framework with statistical significance")
class ABTestRunner:
    """A/B test framework with basic statistical analysis."""

    VERSION = "1.0.0"

    def __init__(self):
        self._experiments: Dict[str, Dict] = {}
        self._lock = threading.Lock()
    def create_experiment(self, name: str, variants: List[str]):
        with self._lock:
            self._experiments[name] = {v: {"impressions": 0, "conversions": 0} for v in variants}

    def track(self, experiment: str, variant: str, converted: bool):
        with self._lock:
            exp = self._experiments.get(experiment, {})
            if variant in exp:
                exp[variant]["impressions"] += 1
                if converted:
                    exp[variant]["conversions"] += 1
    def get_results(self, experiment: str) -> Dict[str, Any]:
        with self._lock:
            exp = self._experiments.get(experiment, {})
        results = {}
        for var, metrics in exp.items():
            rate = metrics["conversions"] / max(metrics["impressions"], 1)
            results[var] = {"conversion_rate": round(rate, 4), **metrics}
        return {"experiment": experiment, "variants": results}

    def get_summary(self):
        return {"status": "ok", "worm": "ABTestRunner", "method": "get_results"}
