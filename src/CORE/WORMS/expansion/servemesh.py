# coding: utf-8
"""
NEURAL MESH — Category: SERVEMESH (3 worms)
Inference serving mesh: Win, Bare, WSL2.

InferenceLoadBalancerWorm, ProviderFailoverMeshWorm, ModelShardServerWorm

Inference-serving mesh; not per-env adapters (envmesh.py) or the fabric
itself (mesh.py).
"""

import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List
from collections import deque
import urllib.request
import os

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Fabric operational state (audit 2026-09-15, finding #7): the endpoint
# registry and shard catalogue are durable; in-flight load and per-host
# reachability are NOT (see the fixups in __init__).
try:
    from CORE.WORMS.expansion.fabric_state import (
        hydrate as fabric_hydrate,  # pyright: ignore[reportAssignmentType]
        publish as fabric_publish,  # pyright: ignore[reportAssignmentType],
        cap_list as fabric_cap_list,
        cap_map as fabric_cap_map,
    )
except ImportError as exc:  # pragma: no cover - defensive
    note_failure("servemesh:L25", exc)
    def fabric_hydrate(instance, provider, attrs):
        return 0

    def fabric_publish(instance, provider, attrs):
        return False

    def fabric_cap_list(seq, limit):
        return seq

    def fabric_cap_map(d, limit):
        return d

try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType]
except ImportError as exc:
    note_failure("servemesh:L40", exc)
    def register_worm(**kw):
        return lambda cls: cls

# Health layer (audit 2026-09-15, finding #12). The servemesh had none: an
# endpoint that kept failing stayed eligible and a caller that raised after
# `pick()` starved its endpoint forever. Rather than invent a second health
# machine, this reuses the one the project already ships —
# CORE/circuit_breaker.py — which implements exactly the CLOSED/OPEN/
# HALF_OPEN transitions, per-name registry and dead-letter store the finding
# asked for.
try:
    from CORE.circuit_breaker import (
        CircuitConfig, CircuitState, breaker_registry,
    )
except ImportError as exc:  # pragma: no cover - defensive
    note_failure("servemesh:L55", exc)
    CircuitConfig = None
    CircuitState = None
    breaker_registry = None


def _ok_probe():
    return True


def _fail_probe():
    raise RuntimeError("servemesh: endpoint reported failure")


def _breaker_state(breaker: Any) -> str:
    """`"open"` / `"closed"` / `"half_open"` / `"n/a"`, never raises."""
    try:
        return str(breaker.state.value)
    except Exception as exc:
        note_failure("servemesh:L73", exc)
        return "n/a"


@register_worm(category="servemesh", priority=8, cooldown_seconds=10.0,
    description="Least-loaded route over llama-server/LM Studio/NIM endpoints")
class InferenceLoadBalancerWorm:
    """Least-loaded inference endpoint router with per-endpoint health.

    Audit 2026-09-15 (finding #12) — two defects, both fixed here:

    1. **Load leaked.** `pick()` incremented a counter that only the *caller*
       could decrement, and selection is `min(load, fail)`. A caller that
       raised after picking — or a process that died — starved that endpoint
       for the lifetime of the process. Leases now carry an acquisition time
       and `sweep()` reclaims them, so a crashed caller costs one
       `LEASE_TTL_S` window instead of the endpoint.
    2. **`fail` was only a tiebreak.** An endpoint that kept failing stayed
       eligible. Each endpoint now owns a `CircuitBreaker`, and `pick()`
       prefers endpoints whose breaker is not OPEN. An OPEN breaker that is
       past its `recovery_timeout` becomes eligible again (a half-open
       attempt) instead of being excluded forever.
    """

    VERSION = "1.2.0"
    PROVIDER = "InferenceLoadBalancerWorm"
    _STATE_ATTRS = ("_endpoints",)
    ENDPOINTS_MAX = 128  # finding #8
    LEASE_TTL_S = 120.0
    FAILURE_THRESHOLD = 3
    RECOVERY_BASE_S = 30.0
    RECOVERY_MAX_S = 600.0

    def __init__(self):
        self._endpoints: dict[str, dict] = {}
        # In-flight leases: name -> [acquired_at, ...]. Deliberately NOT
        # durable — an in-flight request cannot survive a restart, so a
        # restored lease would strand capacity (finding #7).
        self._inflight: dict[str, list] = {}
        self._lock = threading.Lock()
        fabric_hydrate(self, self.PROVIDER, self._STATE_ATTRS)
        # `load` is derived from the in-flight leases, so it is zeroed on
        # hydrate rather than restored.
        for ep in self._endpoints.values():
            if isinstance(ep, dict):
                ep["load"] = 0

    # -- health --------------------------------------------------------

    def _breaker(self, name: str):
        if breaker_registry is None:
            return None
        try:
            cfg = CircuitConfig(failure_threshold=self.FAILURE_THRESHOLD,  # pyright: ignore[reportOptionalCall]
                               recovery_timeout=self.RECOVERY_BASE_S,
                               half_open_max_requests=1)
            return breaker_registry.get(f"servemesh:lb:{name}", cfg)
        except Exception as exc:
            note_failure("servemesh:L130", exc)
            return None

    def _is_open(self, name: str) -> bool:
        """True while the breaker is OPEN and still inside its cooldown.

        After `recovery_timeout` elapses the endpoint is eligible again, so
        the next `release(ok=...)` is what drives the OPEN -> HALF_OPEN ->
        CLOSED transition. Without the time check an OPEN breaker would never
        be retried, because `CircuitBreaker.execute()` is only reached when a
        caller actually uses the endpoint.
        """
        b = self._breaker(name)
        if b is None or CircuitState is None:
            return False
        try:
            if b.state != CircuitState.OPEN:
                return False
            return (time.time() - b.last_failure_time) < b.config.recovery_timeout
        except Exception as exc:
            note_failure("servemesh:L149", exc)
            return False

    def _record(self, name: str, ok: bool) -> None:
        """Feed a real outcome into the breaker (it owns the threshold)."""
        b = self._breaker(name)
        if b is None:
            return
        try:
            b.execute(_ok_probe if ok else _fail_probe)
        except Exception as exc:
            # OPEN/HALF_OPEN refusal and the deliberate failure probe both
            # surface here; the breaker has already recorded the transition.
            note_failure("servemesh:L159", exc)

    # -- registry ------------------------------------------------------

    def register(self, name: str, url: str, kind: str = "llama-server") -> dict[str, Any]:
        with self._lock:
            self._endpoints[name] = {"url": url, "kind": kind, "load": 0,
                                     "ok": 0, "fail": 0,
                                     "at": datetime.now().isoformat()}
            self._inflight.setdefault(name, [])
            fabric_cap_map(self._endpoints, self.ENDPOINTS_MAX)
            fabric_publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "endpoints": len(self._endpoints)}

    # -- routing -------------------------------------------------------

    def _sweep_locked(self, now: float) -> int:
        """Reclaim expired leases. Caller holds `self._lock`."""
        reclaimed = 0
        for name, leases in list(self._inflight.items()):
            keep = [t for t in (leases or []) if (now - t) < self.LEASE_TTL_S]
            reclaimed += len(leases or []) - len(keep)
            self._inflight[name] = keep
            ep = self._endpoints.get(name)
            if isinstance(ep, dict):
                ep["load"] = len(keep)
        return reclaimed

    def sweep(self, now: float | None = None) -> dict[str, Any]:
        """Public lease reclaimer — the leak fix."""
        now = time.time() if now is None else now
        with self._lock:
            reclaimed = self._sweep_locked(now)
            if reclaimed:
                fabric_publish(self, self.PROVIDER, self._STATE_ATTRS)
        return {"success": True, "reclaimed": reclaimed,
                "leases": sum(len(v or []) for v in self._inflight.values())}

    def pick(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._sweep_locked(now)
            if not self._endpoints:
                return {"success": True, "picked": None, "reason": "no_endpoints"}
            healthy = [(n, ep) for n, ep in self._endpoints.items()
                       if not self._is_open(n)]
            # Fall back to the full pool rather than refusing to route: an
            # OPEN breaker past its cooldown is exactly the half-open case we
            # want to exercise, and "no endpoint" is worse than "a sick one".
            pool = healthy or list(self._endpoints.items())
            name = min(pool, key=lambda kv: (kv[1]["load"], kv[1]["fail"]))[0]
            self._endpoints[name]["load"] += 1
            self._inflight.setdefault(name, []).append(now)
            ep = dict(self._endpoints[name])
        return {"success": True, "picked": name, "endpoint": ep,
                "degraded_pool": not healthy}

    def release(self, name: str, ok: bool = True) -> dict[str, Any]:
        """Hand a lease back and record the outcome.

        Load is recomputed from the remaining leases rather than decremented,
        so it cannot drift below zero or above the true in-flight count.
        """
        with self._lock:
            ep = self._endpoints.get(name)
            if ep is None:
                return {"success": False, "error": "unknown_endpoint"}
            leases = self._inflight.setdefault(name, [])
            if leases:
                leases.pop(0)
            ep["load"] = len(leases)
            ep["ok" if ok else "fail"] += 1
            open_now = None
            fabric_publish(self, self.PROVIDER, self._STATE_ATTRS)
        self._record(name, ok)
        open_now = self._is_open(name)
        return {"success": True, "endpoint": name, "ok": bool(ok),
                "load": ep["load"], "open": bool(open_now),
                "breaker": _breaker_state(self._breaker(name))}

    def health(self) -> dict[str, Any]:
        """Per-endpoint view: load, counters and breaker state."""
        with self._lock:
            out = {}
            for name, ep in self._endpoints.items():
                b = self._breaker(name)
                out[name] = {"load": ep.get("load", 0),
                             "ok": ep.get("ok", 0),
                             "fail": ep.get("fail", 0),
                             "open": self._is_open(name),
                             "breaker": _breaker_state(b)}
        return {"success": True, "endpoints": out,
                "open": sorted(n for n, v in out.items() if v["open"])}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"endpoints": len(self._endpoints),
                               "loads": {k: v["load"] for k, v in self._endpoints.items()},
                               "open": sorted(n for n in self._endpoints
                                              if self._is_open(n))}}


@register_worm(category="servemesh", priority=8, cooldown_seconds=15.0,
    description="Provider failover chain (RULE 0D) with the local model tier up front; mesh-aware, not just client lib")
class ProviderFailoverMeshWorm:
    """Mesh-aware provider failover chain with half-open auto-recovery.

    Audit 2026-09-15 (finding #12): `mark_up()` had no caller anywhere, so a
    provider that was marked down stayed down for the life of the process —
    recovery was manual and, in practice, never happened. Now:

    * Each provider owns a `CircuitBreaker`, so the failure threshold lives in
      one place (CORE/circuit_breaker.py) instead of being re-implemented.
    * `reprobe()` performs a REAL reachability check, but only for providers
      the breaker says are due (i.e. `recovery_timeout` has elapsed since the
      last failure), so probing is rate-limited by construction rather than
      by a timer someone has to remember to set.
    * Repeated failures back the cooldown off exponentially
      (`RECOVERY_BASE_S` -> `RECOVERY_MAX_S`), and a success resets it.

    `_down` also records this HOST's reachability observation, which is why it
    is deliberately NOT persisted: a provider unreachable from env A may be
    fine from env B, and a restart should re-probe rather than inherit a
    permanent verdict.
    """

    VERSION = "1.1.0"
    CHAIN = ("llamaserver", "llama_mythos", "llama_mythos_v2", "llama_qwopus",
             "llama_ornith", "accelerator_sidecar", "edge0", "openvino",
             "huggingface_direct", "llama_cpp_direct", "lmstudio", "nim",
             "groq", "openrouter", "ollama", "vllm", "sglang", "openwebui",
             "jan", "kilo", "opencode")
    # CHAIN uses short names; the RULE 0D registry uses its own ids.
    PROVIDER_ALIASES = {"nim": "nvidia_nim"}

    PROVIDER = "ProviderFailoverMeshWorm"
    _STATE_ATTRS = ("_uses",)
    FAILURE_THRESHOLD = 2
    RECOVERY_BASE_S = 30.0
    RECOVERY_MAX_S = 900.0
    REPROBE_LIMIT = 2
    PROBE_TIMEOUT_S = 2.0
    REPROBE_ENV = "AZAZAEL_SERVEMESH_REPROBE"

    def __init__(self):
        self._down: set = set()
        self._uses: dict[str, int] = {p: 0 for p in self.CHAIN}
        self._fail_streak: dict[str, int] = {}
        # Why a provider was withdrawn. A credential problem is NOT fixed by a
        # network probe, so `probe_due()` must not waste a probe on it (and
        # must not let a 401 be read as "recovered").
        self._down_reason: dict[str, str] = {}
        self._lock = threading.Lock()
        fabric_hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    # -- health --------------------------------------------------------

    def _breaker(self, provider: str):
        if breaker_registry is None:
            return None
        try:
            cfg = CircuitConfig(failure_threshold=self.FAILURE_THRESHOLD,  # pyright: ignore[reportOptionalCall]
                               recovery_timeout=self.RECOVERY_BASE_S,
                               half_open_max_requests=1)
            return breaker_registry.get(f"servemesh:provider:{provider}", cfg)
        except Exception as exc:
            note_failure("servemesh:L326", exc)
            return None

    def _backoff_locked(self, provider: str) -> float:
        """Exponential cooldown from the consecutive-failure count.

        The breaker's threshold/state machine is left alone; only its
        `recovery_timeout` is widened, which is the knob that decides when the
        next half-open attempt is allowed.
        """
        streak = int(self._fail_streak.get(provider, 0))
        delay = min(self.RECOVERY_BASE_S * (2 ** max(0, streak - 1)),
                    self.RECOVERY_MAX_S)
        return float(delay)

    def _apply_backoff_locked(self, provider: str) -> None:
        b = self._breaker(provider)
        if b is None:
            return
        try:
            b.config.recovery_timeout = self._backoff_locked(provider)
        except Exception as exc:
            note_failure("servemesh:L347", exc)

    def _is_open(self, provider: str) -> bool:
        b = self._breaker(provider)
        if b is None or CircuitState is None:
            return False
        try:
            if b.state != CircuitState.OPEN:
                return False
            return (time.time() - b.last_failure_time) < b.config.recovery_timeout
        except Exception as exc:
            note_failure("servemesh:L358", exc)
            return False

    def probe_due(self, now: float | None = None) -> list:
        """Providers the breaker says may be re-probed right now.

        A provider is due when it is marked down AND its cooldown has elapsed
        AND its breaker is not still OPEN-inside-cooldown. Because the
        cooldown widens on each consecutive failure, a persistently dead
        provider is retried logarithmically often, not on every cycle.
        """
        now = time.time() if now is None else now
        with self._lock:
            down = sorted(self._down)
            reasons = dict(self._down_reason)
        due = []
        for p in down:
            if str(reasons.get(p, "")).startswith("credential"):
                continue               # a probe cannot supply a missing key
            b = self._breaker(p)
            if b is None:
                due.append(p)          # no breaker => nothing to gate on
                continue
            try:
                if now - b.last_failure_time >= b.config.recovery_timeout:
                    due.append(p)
            except Exception as exc:
                note_failure("servemesh:L384", exc)
                due.append(p)
        return due

    # -- state changes -------------------------------------------------

    def mark_down(self, provider: str, error: str = "") -> dict[str, Any]:
        reason = str(error or "unreachable")[:40]
        with self._lock:
            self._down.add(provider)
            self._down_reason[provider] = reason
            self._fail_streak[provider] = int(self._fail_streak.get(provider, 0)) + 1
            self._apply_backoff_locked(provider)
            streak = self._fail_streak[provider]
            delay = self._backoff_locked(provider)
        b = self._breaker(provider)
        if b is not None:
            try:
                b.execute(_fail_probe)
            except Exception as exc:
                note_failure("servemesh:L403", exc)
        return {"success": True, "provider": provider, "down": True,
                "reason": reason, "streak": streak, "retry_in": delay,
                "error": str(error)[:80]}

    def mark_up(self, provider: str) -> dict[str, Any]:
        with self._lock:
            was_down = provider in self._down
            self._down.discard(provider)
            self._down_reason.pop(provider, None)
            self._fail_streak.pop(provider, None)
            b = self._breaker(provider)
            if b is not None:
                try:
                    b.reset()
                    b.config.recovery_timeout = self.RECOVERY_BASE_S
                except Exception as exc:
                    note_failure("servemesh:L420", exc)
        return {"success": True, "provider": provider, "down": False,
                "was_down": was_down}

    @classmethod
    def _resolve_chain(cls):
        """Split CHAIN into (resolvable, unresolvable) via the RULE 0D registry.

        A name is unresolvable when the registry holds no entry for it after
        alias mapping (`PROVIDER_ALIASES`; the registry's vocabulary is not the
        chain's). `next_provider` must never hand such a name back: the caller
        would spend a request on an endpoint that cannot exist, and the failure
        would look like an outage rather than a dangling name. The split is
        delegated to the registry rather than hard-coded so a provider that
        lands there later becomes selectable with no edit here.

        If the registry cannot be imported at all the split is UNKNOWN and the
        whole chain is treated as resolvable -- fail-open, because a missing
        import is not evidence that an endpoint is gone.
        """
        try:
            from CORE.inference.providers import PROVIDER_REGISTRY
        except Exception as exc:
            note_failure("servemesh:L443", exc)
            return list(cls.CHAIN), []
        resolvable: list = []
        unresolvable: list = []
        for p in cls.CHAIN:
            pid = cls.PROVIDER_ALIASES.get(p, p)
            (resolvable if PROVIDER_REGISTRY.get(pid)
             else unresolvable).append(p)
        return resolvable, unresolvable

    def next_provider(self) -> dict[str, Any]:
        resolvable, unresolvable = self._resolve_chain()
        skipped = sorted(unresolvable)
        with self._lock:
            for p in resolvable:
                if p in self._down or self._is_open(p):
                    continue
                self._uses[p] = int(self._uses.get(p, 0)) + 1
                fabric_publish(self, self.PROVIDER, self._STATE_ATTRS)
                return {"success": True, "provider": p,
                        "down": sorted(self._down),
                        "skipped_unresolvable": skipped}
            return {"success": False, "provider": None,
                    "reason": "all_down" if resolvable else "all_unresolvable",
                    "down": sorted(self._down),
                    "skipped_unresolvable": skipped}

    # -- recovery ------------------------------------------------------

    @staticmethod
    def _registry_base_url(provider: str) -> str:
        try:
            from CORE.inference.providers import PROVIDER_REGISTRY
        except Exception as exc:
            note_failure("servemesh:L476", exc)
            return ""
        pid = ProviderFailoverMeshWorm.PROVIDER_ALIASES.get(provider, provider)
        return str((PROVIDER_REGISTRY.get(pid) or {}).get("base_url") or "")

    @classmethod
    def _http_probe(cls, provider: str, timeout: float | None = None) -> bool:
        """Real reachability check against the provider's registered base URL.

        Any HTTP answer (including 401/404) proves the host is reachable; only
        a transport error counts as down. That is deliberately weaker than
        "can serve a completion" — an authenticated provider with no key would
        otherwise be judged permanently dead by a health check.
        """
        base = cls._registry_base_url(provider)
        if not base:
            return False
        url = base.rstrip("/") + "/models"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout or cls.PROBE_TIMEOUT_S):
                return True
        except urllib.error.HTTPError as exc:  # pyright: ignore[reportAttributeAccessIssue]
            note_failure("servemesh:L499", exc)
            return True
        except Exception as exc:
            note_failure("servemesh:L501", exc)
            return False

    def reprobe(self, probe=None, limit: int | None = None) -> dict[str, Any]:
        """Half-open re-probe: the auto-recovery path.

        `probe(provider) -> bool` is injectable for tests. The default performs
        a real HTTP reachability check, and only for providers `probe_due()`
        returns — a provider whose cooldown has not elapsed is not touched, so
        a dead endpoint is not hammered. Set `AZAZAEL_SERVEMESH_REPROBE=0` to
        disable the default network probe entirely.
        """
        limit = self.REPROBE_LIMIT if limit is None else max(0, int(limit))
        due = self.probe_due()
        if not due:
            return {"success": True, "due": 0, "recovered": [], "still_down": []}
        if probe is None:
            if os.environ.get(self.REPROBE_ENV, "1") == "0":
                return {"success": True, "due": len(due), "attempted": 0,
                        "skipped": "probe_disabled", "recovered": [],
                        "still_down": []}
            probe = self._http_probe
        recovered, still_down = [], []
        for provider in due[:limit]:
            try:
                ok = bool(probe(provider))
            except Exception as exc:
                note_failure("servemesh:L528", exc)
                ok = False
            if ok:
                self.mark_up(provider)
                recovered.append(provider)
            else:
                self.mark_down(provider, "reprobe_failed")
                still_down.append(provider)
        return {"success": True, "due": len(due), "attempted": min(limit, len(due)),
                "recovered": recovered, "still_down": still_down}

    def stats(self) -> dict:
        with self._lock:
            return {"uses": dict(self._uses), "down": sorted(self._down),
                    "down_reason": dict(self._down_reason),
                    "fail_streak": dict(self._fail_streak),
                    "cooldowns": {p: self._backoff_locked(p) for p in self._down}}

    def health(self) -> dict[str, Any]:
        out = {}
        for p in self.CHAIN:
            b = self._breaker(p)
            out[p] = {"down": p in self._down, "open": self._is_open(p),
                      "breaker": _breaker_state(b),
                      "reason": self._down_reason.get(p, ""),
                      "streak": int(self._fail_streak.get(p, 0)),
                      "uses": int(self._uses.get(p, 0))}
        return {"success": True, "providers": out,
                "down": sorted(self._down), "due": self.probe_due()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.stats()}


@register_worm(category="servemesh", priority=6, cooldown_seconds=60.0,
    description="Serves GGUF/ONNX shards (GGUF Q4_K_M on Win CPU, GPTQ on Bare GPU)")
class ModelShardServerWorm:
    """Model shard catalogue."""

    VERSION = "1.1.0"
    PROVIDER = "ModelShardServerWorm"
    _STATE_ATTRS = ("_shards",)
    SHARDS_MAX = 1024  # finding #8

    def __init__(self):
        self._shards: dict[str, dict] = {}
        self._lock = threading.Lock()
        fabric_hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def publish(self, model: str, shard: str, env: str, fmt: str,
                quant: str = "Q4_K_M",
                artifact_path: str | None = None) -> dict[str, Any]:
        with self._lock:
            entry: dict[str, Any] = {
                "model": model, "shard": shard, "env": env,
                "format": fmt, "quant": quant,
                "at": datetime.now().isoformat(),
            }
            if artifact_path:
                entry["artifact_path"] = artifact_path
                try:
                    _p = Path(artifact_path)
                    if _p.is_file():
                        entry["bytes"] = _p.stat().st_size
                except OSError as exc:
                    note_failure("servemesh:publish-artifact-stat", exc)
            self._shards[f"{model}:{shard}"] = entry
            fabric_cap_map(self._shards, self.SHARDS_MAX)
            fabric_publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "shards": len(self._shards)}

    def verify(self, model: str, shard: str) -> dict[str, Any]:
        with self._lock:
            entry = self._shards.get(f"{model}:{shard}")
            if entry is None:
                return {"verified": False, "reason": "shard not published"}
            if "bytes" in entry:
                return {"verified": True, "bytes": entry["bytes"]}
            return {"verified": True, "bytes": 0}

    def locate(self, model: str) -> dict[str, Any]:
        with self._lock:
            found = [v for k, v in self._shards.items() if v["model"] == model]
            return {"success": True, "model": model, "shards": found}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            models = sorted({v["model"] for v in self._shards.values()})
            return {"success": True, "method": "execute",
                    "result": {"models": models, "shards": len(self._shards)}}
