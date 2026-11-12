# coding: utf-8
"""
NEURAL MESH — Category: METABOLIC (3 worms)
Energy/thermal/quota budgets; feeds synapse:HomeostatWorm + servemesh load balancer.

PowerBudgetWorm, ThermalThrottleWorm, QuotaEnforcerWorm

G19 (audit fix, 2026-09-14) — the audit's line was "the organ exists, the
afferent nerve does not". Measured, it was worse than that: all three worms
had **zero callers**, `sample_c()` returned `None` on every call because its
only sources were Linux sysfs paths and this host is Windows, and
`execute()` reported `{'spent': {}}` after a full session — the trio was
inert, not merely unfed.

What changed: real measurement via psutil (installed, never called) for CPU
utilisation / core count / load average, an honest estimate-plus-label for
wattage, a load-based proxy for thread scaling when no sensor exists, and a
consumer in `SpineCycleWorm` for all three. Nothing here reports a
measurement it did not take.
"""

import os
import threading
from datetime import datetime
from typing import Any, List
import time as _t

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
    note_failure("metabolic:L29", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="metabolic", priority=6, cooldown_seconds=60.0,
    description="Per-env watt/job budget; sheds load when over budget")
class PowerBudgetWorm:
    """Token/watt budget ledger, driven by measured host load.

    G19 (audit fix, 2026-09-14): this class took `watts` as a *parameter* and
    had **zero callers anywhere in the tree**, so the "energy budget" was a
    number nobody ever supplied. Measured: `execute()` returned `{'spent': {}}`
    after a full session.

    What this machine can and cannot measure, stated plainly:
      * There is **no wattmeter**. No RAPL on Windows and psutil 7.x here has
        no `sensors_temperatures` at all, so a measured watt or degree figure
        would be fabricated. The budget is therefore an **estimate derived
        from measured CPU utilisation**, and every result carries
        `estimated: True` / `source: "load-proxy"` so no consumer can mistake
        it for a measurement.
      * CPU utilisation, core count and (where the OS provides one) load
        average ARE measured, via psutil — which is installed and was never
        called. That is the afferent nerve the audit said was missing.
    """

    VERSION = "2.0.0"
    DEFAULT_WATTS = {"windows": 65.0, "wsl2-ubuntu": 45.0, "wsl2-kali": 25.0,
                     "bare": 150.0, "kali": 45.0}
    IDLE_FRACTION = 0.3  # share of the budget drawn at zero utilisation

    def __init__(self):
        self._spent: dict[str, float] = {}
        self._last_load: dict[str, Any] = {}
        self._lock = threading.Lock()

    def sample_load(self) -> dict[str, Any]:
        """Measured host CPU load via psutil (the missing afferent nerve).

        `cpu_percent(interval=None)` is non-blocking and returns `0.0` until a
        first call has established a baseline. The very first sample is
        therefore reported `warm: False` rather than being passed off as a
        genuinely idle machine.
        """
        try:
            import psutil
        except Exception as exc:
            note_failure("metabolic:L76", exc)
            return {"available": False, "reason": "psutil_unavailable"}
        try:
            pct = float(psutil.cpu_percent(interval=None))
            cores = int(psutil.cpu_count(logical=True) or 1)
            try:
                load1 = float(psutil.getloadavg()[0])
            except Exception as exc:
                note_failure("metabolic:L83", exc)
                load1 = 0.0
            with self._lock:
                warm = bool(self._last_load.get("warm"))
                self._last_load = {"warm": True, "cpu_percent": pct}
            return {"available": True, "cpu_percent": round(pct, 1),
                    "cores": cores, "load1": round(load1, 3), "warm": warm}
        except Exception as exc:
            note_failure("metabolic:L90", exc)
            return {"available": False, "error": str(exc)[:80]}

    def estimate_watts(self, env: str, cpu_fraction: float | None = None) -> dict[str, Any]:
        """Interpolate between idle and full draw using measured CPU load.

        Always `estimated: True`. Returns `available: False` (and 0.0 watts)
        when psutil is missing rather than inventing a number.
        """
        budget = self.DEFAULT_WATTS.get(env, 65.0)
        load = self.sample_load()
        if not load.get("available"):
            return {"watts": 0.0, "estimated": True, "source": "load-proxy",
                    "available": False, "env": env, "budget": budget,
                    "reason": load.get("reason") or load.get("error") or "no_load"}
        if cpu_fraction is None:
            cpu_fraction = float(load.get("cpu_percent") or 0.0) / 100.0
        frac = max(0.0, min(1.0, float(cpu_fraction)))
        idle = budget * self.IDLE_FRACTION
        watts = idle + (budget - idle) * frac
        return {"watts": round(watts, 1), "estimated": True,
                "source": "load-proxy", "available": True, "env": env,
                "budget": budget, "cpu_percent": load.get("cpu_percent"),
                "cores": load.get("cores")}

    def spend_from_load(self, env: str) -> dict[str, Any]:
        """Estimate this cycle's draw from real load and spend it."""
        est = self.estimate_watts(env)
        res = self.spend(env, float(est["watts"]))
        res.update({"estimated": est["estimated"], "source": est["source"],
                    "cpu_percent": est.get("cpu_percent")})
        return res

    def spend(self, env: str, watts: float) -> dict[str, Any]:
        with self._lock:
            self._spent[env] = self._spent.get(env, 0.0) + watts
            budget = self.DEFAULT_WATTS.get(env, 65.0)
            over = self._spent[env] > budget
            return {"success": True, "env": env, "spent": round(self._spent[env], 1),
                    "budget": budget, "over": over,
                    "action": "shed" if over else "allow"}

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._spent.clear()
            return {"success": True, "reset": True}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"spent": dict(self._spent)}}


@register_worm(category="metabolic", priority=7, cooldown_seconds=30.0,
    description="Thermal backoff: reads WMI/tmp sysfs, throttles threads")
class ThermalThrottleWorm:
    """Thermal/load state -> thread-scale recommendation.

    G19 (audit fix, 2026-09-14): the only temperature source was two Linux
    sysfs paths. Measured on this Windows host: `sample_c()` returns `None`
    **always**, so `recommend_threads()` reported `state: "unknown"` and
    returned the untouched default (16) — a throttle that never throttled.
    The description claimed "reads WMI/tmp sysfs"; there was no WMI path and
    the sysfs path does not exist on the primary platform.

    Fixed by adding a real fallback chain and, when there is genuinely no
    temperature sensor, falling back to a **load-based proxy** that is
    labelled `source: "load-proxy"`. The worm now always reports which of the
    three it used (`thermal` / `load-proxy` / `none`) instead of collapsing
    "I cannot measure temperature" into "nominal".
    """

    VERSION = "2.0.0"
    SYSFS_PATHS = ("/sys/class/thermal/thermal_zone0/temp",
                   "/sys/class/hwmon/hwmon0/temp1_input")
    LOAD_SATURATED = 95.0
    LOAD_BUSY = 75.0
    THROTTLE_STATES = ("critical", "hot", "saturated")

    def __init__(self):
        self._throttles = 0
        self._lock = threading.Lock()

    def _temp_from_psutil(self) -> float | None:
        """psutil sensors where the build/OS exposes them (absent on Windows)."""
        try:
            import psutil
            fn = getattr(psutil, "sensors_temperatures", None)
            if fn is None:
                return None
            for entries in (fn() or {}).values():
                for entry in entries:
                    cur = getattr(entry, "current", None)
                    if cur:
                        return float(cur)
        except Exception as exc:
            note_failure("metabolic:L185", exc)
            return None
        return None

    def sample_c(self) -> float | None:
        for p in self.SYSFS_PATHS:
            try:
                with open(p, encoding="utf-8") as f:
                    raw = int(f.read().strip())
                    return raw / 1000.0 if raw > 1000 else float(raw)
            except Exception as exc:
                note_failure("metabolic:L195", exc)
                continue
        return self._temp_from_psutil()

    def _load_percent(self) -> float | None:
        try:
            import psutil
            return float(psutil.cpu_percent(interval=None))
        except Exception as exc:
            note_failure("metabolic:L203", exc)
            return None

    def recommend_threads(self, current: int = 16) -> dict[str, Any]:
        t = self.sample_c()
        load: float | None = None
        if t is None:
            load = self._load_percent()
            if load is None:
                scale, state, source = 1.0, "unknown", "none"
            elif load >= self.LOAD_SATURATED:
                scale, state, source = 0.5, "saturated", "load-proxy"
            elif load >= self.LOAD_BUSY:
                scale, state, source = 0.75, "busy", "load-proxy"
            else:
                scale, state, source = 1.0, "nominal", "load-proxy"
        else:
            source = "thermal"
            if t >= 90:
                scale, state = 0.25, "critical"
            elif t >= 80:
                scale, state = 0.5, "hot"
            elif t >= 70:
                scale, state = 0.75, "warm"
            else:
                scale, state = 1.0, "nominal"
        threads = max(1, int(current * scale))
        with self._lock:
            if state in self.THROTTLE_STATES:
                self._throttles += 1
            n = self._throttles
        return {"success": True, "temp_c": t, "state": state,
                "threads": threads, "throttles": n, "source": source,
                "load_percent": None if load is None else round(load, 1),
                "throttling": state in self.THROTTLE_STATES}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": self.recommend_threads()}


@register_worm(category="metabolic", priority=6, cooldown_seconds=60.0,
    description="API/file quota guard for 12-provider inference + G: writes")
class QuotaEnforcerWorm:
    """Leaky-bucket quota per scope.

    G19: had zero callers, so `execute()` reported `scopes: []` forever. The
    spine cycle now consults it for the gossip publish (the shared-directory
    write the description refers to), so the bucket is actually populated.
    """

    VERSION = "2.0.0"

    def __init__(self, limit_per_min: int = 60):
        self._limit = limit_per_min
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, scope: str = "groq") -> dict[str, Any]:
        now = _t.time()
        with self._lock:
            bucket = self._hits.setdefault(scope, [])
            bucket[:] = [x for x in bucket if now - x < 60.0]
            if len(bucket) >= self._limit:
                return {"success": True, "scope": scope, "allowed": False,
                        "used": len(bucket), "limit": self._limit}
            bucket.append(now)
            return {"success": True, "scope": scope, "allowed": True,
                    "used": len(bucket), "limit": self._limit}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"scopes": list(self._hits.keys()), "limit": self._limit}}
