# coding: utf-8
"""
NEURAL MESH — Category: MESH (4 worms)
Fabric transport for all environments: Windows 11 <-> WSL2 <-> Bare Metal <-> Kali <-> G:

MeshRouterWorm, GossipSyncWorm, PartitionHealerWorm, EnvBeaconWorm

Transport fabric, not the whole codebase: per-env adapters live in
envmesh.py, inference serving in servemesh.py, the wire in
mesh_transport.py. Unrelated to the Sep-28 intermesh audit (a document).
"""

import os
import threading
import platform
from datetime import datetime
from typing import Any, Iterable, List
import time as _time
import os as _os
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def _bounded_exists(path: str, timeout_s: float = 5.0) -> bool | None:
    """`os.path.exists` with a deadline. None means the probe never returned.

    A MISSING path answers instantly. An UNREACHABLE one does not: a
    disconnected network drive, a stale SMB share or a spun-down disk blocks
    inside the filesystem call for minutes, and `os.path.exists` takes no
    timeout argument. Verified live -- `os.path.exists(r"G:\\source-of-truth")`
    hung the entire test suite on this host (a fault dump placed the frame at
    mesh.py:406 in `detect`), because G: is unreachable here. That is a worm
    disabling the caller that invoked it, and it is the second instance of this
    shape found today (the first was piping `wsl -l -v` into `communicate()`).

    Returns True/False when the probe answers and None when it does not.
    Callers decide what "unknown" means: for a partition check it is treated as
    unmounted, because the fabric genuinely cannot use a path it cannot reach
    -- and silence would read as healthy.
    """
    box: dict[str, Any] = {}

    def _probe() -> None:
        try:
            box["exists"] = bool(os.path.exists(path))
        except Exception as exc:
            note_failure("mesh:L37", exc)
            box["exists"] = False

    worker = threading.Thread(target=_probe, name="exists-probe", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return None
    return box.get("exists")


# Single canonical env identity (audit 2026-09-15, finding #4). Env id is a
# primary key for spine-map nodes, gossip filenames, route Q-keys and beacon
# peer sets, so there must be exactly one implementation. It lives in
# CORE.env_id; this module and three others used to carry stale copies.
try:
    from CORE.env_id import env_id as _canonical_env_id
    from CORE.env_id import describe_env as _canonical_describe_env
    from CORE.env_id import is_known_env, VALID_ENVS
    _CANONICAL_ENV = True
except ImportError as exc:  # pragma: no cover - defensive
    note_failure("mesh:L57", exc)
    _CANONICAL_ENV = False
    VALID_ENVS = ("windows", "wsl2-ubuntu", "wsl2-kali", "bare", "kali")

    def is_known_env(name: str) -> bool:
        return bool(name) and any(
            v.startswith(name) or name.startswith(v) for v in VALID_ENVS)

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("mesh:L68", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5,
                      cooldown_seconds: float = 30.0, description: str = "",
                      tags: (set[str] | None) = None) -> Any:
        return lambda cls: cls

try:
    from CORE.freethreading import is_free_threaded, ThreadSafeDict
    _FREETHREAD = True
except ImportError as exc:
    note_failure("mesh:L76", exc)
    _FREETHREAD = False
    ThreadSafeDict = dict
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("mesh:L83", exc)
            return False

# Fabric operational state (audit 2026-09-15, finding #7): hydrate in
# __init__, publish on mutation, so routing/partition/beacon state survives a
# new ClusterManager or a restart instead of living only in memory.
try:
    from CORE.WORMS.expansion.fabric_state import (
        hydrate, publish, cap_list, cap_map,
    )
except ImportError as exc:  # pragma: no cover - defensive, keeps mesh importable alone
    note_failure("mesh:L93", exc)
    def hydrate(instance: Any, provider: str, attrs: Iterable[str]) -> int:
        return 0

    def publish(instance: Any, provider: str, attrs: Iterable[str]) -> bool:
        return False

    def cap_list(seq, limit):
        return seq

    def cap_map(d, limit):
        return d


def _stamp(value: Any) -> str:
    """Best-effort recency stamp from a merged value ("" when absent)."""
    if not isinstance(value, dict):
        return ""
    for key in ("updated_at", "at", "last_at", "exported_at", "saved_at"):
        ts = value.get(key)
        if isinstance(ts, str) and ts:
            return ts
    return ""

# Back-compat alias. The old tuple hard-coded the pre-2026-09-13 3-way
# spelling ("wsl2") and was never updated when envmesh split WSL2 into
# wsl2-ubuntu/wsl2-kali, so `dst_env in _MESH_ENVS` reported the two most
# common non-Windows targets as unreachable (`hops = -1`). Route liveness is
# now decided by is_known_env(), which is prefix-tolerant.
_MESH_ENVS = tuple(VALID_ENVS) + ("g_archive", "standalone")


def _read_text(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as exc:
        note_failure("mesh:L129", exc)
        return ""


def _env_id() -> str:
    """Canonical 5-way env id: windows|wsl2-ubuntu|wsl2-kali|bare|kali.

    Thin delegate to CORE.env_id.env_id() — the one implementation. Kept as
    a private alias because this module was historically the "reference"
    every other copy claimed to mirror.
    """
    if _CANONICAL_ENV:
        return _canonical_env_id()
    if os.name == 'nt':
        return "windows"
    ver = _read_text('/proc/version').lower()
    if 'microsoft' in ver:
        return "wsl2-ubuntu"
    if 'kali' in _read_text('/etc/os-release').lower():
        return "kali"
    return "bare"


def describe_env() -> dict:
    """Richer host description: virt, container flavour, memory, FT.

    Delegates to CORE.env_id.describe_env() (audit 2026-09-15, finding #14:
    the local copy had a dead try/except that hard-coded "hyperv_host" on
    Windows without ever checking Hyper-V, and dropped the free-threading
    flag the canonical version reports).
    """
    if _CANONICAL_ENV:
        out = _canonical_describe_env()
        out["ft"] = is_free_threaded()
        return out
    return {"env": _env_id(), "virt": "unknown", "cpu": os.cpu_count() or 0,
            "mem_gb": 0.0, "ft": is_free_threaded(), "bare_likely": False}


@register_worm(category="mesh", priority=9, cooldown_seconds=10.0,
    description="Cross-env mesh router — wraps MessageBroker+IPCBridge+ProtocolNegotiator")
class MeshRouterWorm:
    """Env-aware routing Win<->WSL2<->Bare<->Kali<->G:."""

    VERSION = "1.1.0"

    #: Destination names that collide with a homeostat namespace. A route to
    #: one of these is never a real observation -- it is a name that arrived
    #: from somewhere that was not thinking about the ledger's key shape.
    _RESERVED_ENV_NAMES = frozenset({"route", "ebpf", "telemetry"})

    # Persisted operational state (audit 2026-09-15, finding #7).
    PROVIDER = "MeshRouterWorm"
    _STATE_ATTRS = ("_routes",)
    ROUTES_MAX = 512  # finding #8: bounded growth

    def __init__(self):
        self._routes: dict = {}
        self._lock = threading.Lock()
        self._env = _env_id()
        # Count of outcomes the transport reported UNLEARNABLE (refused /
        # timeout / reset). Not persisted: it is a per-process observability
        # counter, and the durable count of the same thing is the ledger's
        # `censored` bucket (see record_route_result).
        self._env_errors = 0
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    @staticmethod
    def _route_key(src: str, dst: str) -> str:
        """String route key — the same `src>dst` form the route Q-table uses.

        Keys used to be `(src, dst)` tuples, which cannot survive JSON
        persistence, so registered route weights were among the state the
        mesh lost on every restart (finding #7).
        """
        return f"{src}>{dst}"

    def add_route(self, src: str, dst: str, weight: float = 1.0):
        with self._lock:
            self._routes[self._route_key(src, dst)] = {
                "weight": weight, "updated": datetime.now().isoformat(),
            }
            cap_map(self._routes, self.ROUTES_MAX)
            publish(self, self.PROVIDER, self._STATE_ATTRS)

    def route(self, dst_env: str, payload: dict) -> dict[str, Any]:
        """Route with learned weight: registered weight x learned reliability.

        The learning signal lives in CORE.spine_registry's Q-table under
        nature="route", worm="<src>><dst>": every record_route_result()
        call is auto-reinforced by the brainstem (success +1 / failure -1,
        EMA). route() multiplies the static registered weight by (1 + q).
        Fails open to the static weight when the spine ledger is unavailable.

        The bound is applied by `spine_registry.route_weight`, not here, and
        the q it receives is the EVIDENCE-SHRUNK value. This docstring used to
        promise a [0.1, 5.0] range, which is unreachable: with q in [-1, 1] the
        factor is [0, 2], so for the only registered base_w (1.0) the honest
        ceiling is 2.0. The advertised 50x range was a claim about capacity the
        arithmetic could not deliver -- and it was duplicated in
        `get_route_usage`, which is how two files agreed on the same wrong
        number. Both now call the one implementation.
        """
        with self._lock:
            key = self._route_key(self._env, dst_env)
            w = self._routes.get(key, {"weight": 1.0})
        base_w = float(w.get("weight", 1.0))
        learned: dict[str, Any] = {"q": 0.0, "evidence": 0,
                                   "q_effective": 0.0,
                                   "effective_weight": base_w}
        try:
            from CORE import spine_registry as _spine
            learned = _spine.route_weight_learned(self._env, dst_env, base_w)
        except Exception as exc:
            note_failure("mesh:L244", exc) # fail open on the static weight; routing must not fail on learning
        return {
            "success": True, "src": self._env, "dst": dst_env,
            "weight": base_w,
            "effective_weight": learned.get("effective_weight", base_w),
            "q": learned.get("q", 0.0),
            "q_effective": learned.get("q_effective", 0.0),
            "evidence": learned.get("evidence", 0),
            "stale_discount": learned.get("stale_discount", 1.0),
            "hops": 1 if is_known_env(dst_env) else -1,
            "payload_keys": list(payload)[:10],
        }

    def invalidate_route(self, dst_env: str, reason: str = "") -> dict[str, Any]:
        """Discount the learned penalty on one route, because its cause is fixed.

        A deprioritised route is never re-chosen, so it never produces the
        successes that would raise it: a penalty outlives its cause and is
        self-sustaining. This is the explicit act that breaks the loop, and it
        belongs at the ROUTER level rather than in a repair script, because the
        learned state is the router's and a standalone scheduled script has no
        import path to `spine_registry` to reach it.

        Called by whoever fixed the fault. Halves evidence rather than zeroing
        it: `route:windows>wsl2-ubuntu` sat below neutral on 30 failures at
        exactly 8000ms -- a timeout signature belonging to the sync path bug
        fixed in commit 56c15679 -- and that history was true when recorded.
        """
        try:
            from CORE import spine_registry as _spine
            return _spine.invalidate_q(f"route:{self._env}>{dst_env}",
                                       reason or f"route repair: {dst_env}")
        except Exception as exc:
            note_failure("mesh:L277", exc)
            return {"success": False, "error": str(exc)[:120]}

    def record_route_result(self, dst_env: str, success: bool,
                            duration_ms: float = 0.0,
                            censored: bool = False,
                            error: str | None = None) -> None:
        """Feed a route outcome into the brainstem (route learning).

        ``censored=True`` marks an outcome that is NOT an observation of the
        route: a refused connection because no listener was running, a timeout
        in an unrelated subsystem, a reset. A missing listener is not a bad
        route. Such an outcome is counted in its own bucket and changes
        nothing else -- no success/failure counter (it would poison fail-rate
        stats), no Q update, no consec_fail reflex.

        This is the ONLY seam through which route outcomes reach the ledger.
        The censored case used to call ``record_outcome`` inline instead of
        coming through here, so the censored half of the traffic bypassed
        every hook on this method. Measured 2026-09-29: zero `censored`
        counters across all 492 live ledger entries, against 180 recorded
        route failures -- the mechanism existed, was documented, and had never
        executed even once.
        """
        try:
            from CORE import spine_registry as _spine
            src = str(self._env or "").strip()
            dst = str(dst_env or "").strip()
            # A route key is `route:<src>><dst>`, so an empty dst -- or one that
            # already contains '>' -- yields a key no reader can parse back into
            # a route. A dst that collides with a ledger namespace is also always
            # a mistake: `route:windows>route` sat in the live ledger with 2
            # successes, created when a model-ranked environment name reached
            # this seam unvalidated. Refuse the write rather than mint a key
            # that only looks like a route. (The upstream fix is the candidate
            # filter in scripts/spine_cycle_apply.py; this is the backstop.)
            if (not src or not dst or ">" in src or ">" in dst
                    or dst in self._RESERVED_ENV_NAMES):
                return
            _spine.record_outcome("route", f"{src}>{dst}",
                                  bool(success), float(duration_ms),
                                  error=error, censored=censored)
        except Exception as exc:
            note_failure("mesh:L319", exc) # learning is best-effort; routing must not fail on it

    def send(self, dst_env: str, payload: dict,
             timeout: float = 10.0) -> dict[str, Any]:
        """Score via route(), deliver bytes via MeshTransport, learn outcome.

        This is the data-plane counterpart to route(): same scoring dict
        plus delivery proof. The Q-table learns real successes/failures --
        but NEVER infrastructure noise: when the transport reports the
        outcome unlearnable (env_error: refused, timeout, reset -- a missing
        listener is not a bad route), the outcome is CENSORED into its own
        bucket instead of recorded as a failure (2026-09-28; the transport
        has typed every result since the 2026-09-27 audit, and send() was
        ignoring the types and poisoning routes with -1s for dead peers).
        """
        scored = self.route(dst_env, payload)
        t0 = _time.time()
        try:
            from CORE.mesh_transport import MeshTransport
            res = MeshTransport(self._env).send(dst_env, payload,
                                                timeout=timeout)
        except Exception as e:
            note_failure("mesh:L342", e)
            res = {"success": False, "error": str(e)[:120],
                   "status": "env_error", "learnable": False}
        learnable = bool(res.get("learnable", True))
        status = res.get("status") or ("delivered" if res.get("success")
                                       else "peer_rejected")
        duration_ms = (_time.time() - t0) * 1000.0
        if learnable:
            self.record_route_result(
                dst_env, bool(res.get("success")), duration_ms=duration_ms)
        else:
            # Infrastructure noise, not a route verdict. Counted (censored),
            # surfaced as a spine event, and deliberately NOT recorded as a
            # failure -- the error text rides the event rather than the
            # censored bucket so the bucket stays a pure count.
            self._env_errors += 1
            self.record_route_result(
                dst_env, False, duration_ms=duration_ms, censored=True)
            try:
                from CORE import spine_registry as _spine
                _spine.record_spine_event("route_env_error", {
                    "src": self._env, "dst": dst_env,
                    "status": status,
                    "error": str(res.get("error") or "")[:120],
                    "ms": round(duration_ms, 2)})
            except Exception as exc:
                note_failure("mesh:L367", exc)
        scored.update({"delivered": bool(res.get("success")),
                       "receipt": res.get("receipt"),
                       "error": res.get("error"),
                       "status": status, "learned": learnable})
        return scored

    def get_routing_table(self) -> dict:
        with self._lock:
            return {"env": self._env, "routes": len(self._routes),
                    "ft": is_free_threaded()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": self.get_routing_table()}


@register_worm(category="mesh", priority=8, cooldown_seconds=15.0,
    description="Epidemic state gossip — worm health, model versions; heals sync drift")
class GossipSyncWorm:
    """Gossip merge for mesh state convergence — trust-gated, recency-ordered.

    Audit 2026-09-15 (finding #9): the previous merge was first-writer-wins
    (``if k not in self._state``), which cannot converge. Once a key was seen
    its value was frozen forever, so a changed value could never propagate and
    two envs that ever disagreed stayed disagreed. It also had no notion of the
    spine's provenance model, even though the spine already owned a signed,
    seq-anti-replayed, recency-wins merge.

    Two paths now:

    * **Spine-shaped payload** (carries ``spine_map``/``homeostat``/``seq`` …)
      — provenance is enforced by ``spine_registry.check_gossip_trust``
      (Ed25519 signature when available, monotonic seq, TOFU key pinning). An
      untrusted payload applies nothing and reports ``rejected``. Pass
      ``merge_ledger=True`` to also run the full trust-checked ledger merge;
      the default only records the trust verdict, because ``SpineGossipWorm``
      is the transport that owns that merge (re-merging would be duplicated
      work on the hot path).
    * **Plain mapping** — per-key recency merge on ``at``/``updated_at``. A
      timestamped value may replace an older one; a value with no timestamp can
      only fill a gap, never overwrite.
    """

    VERSION = "2.0.0"
    PROVIDER = "GossipSyncWorm"
    _STATE_ATTRS = ("_state", "_rounds")
    STATE_KEYS_MAX = 512  # finding #8
    _SPINE_PAYLOAD_KEYS = ("spine_map", "homeostat", "q_weights", "seq",
                           "gossip_from", "exported_at", "fabric_state")
    _ENVELOPE_KEYS = ("signature", "signer_pub")

    def __init__(self):
        self._state: dict = {}
        self._lock = threading.Lock()
        self._rounds = 0
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def gossip(self, peer_state: dict,
               merge_ledger: bool = False) -> dict[str, Any]:
        """Merge a peer payload. Trust-gated; recency-wins; never raises."""
        if not isinstance(peer_state, dict):
            return {"success": False, "error": "payload must be a dict"}
        out: dict[str, Any] = {"success": True, "new": 0, "applied": 0,
                               "stale_skipped": 0, "rejected": None,
                               "trust": "n/a", "merge": "plain"}

        if any(k in peer_state for k in self._SPINE_PAYLOAD_KEYS):
            out["merge"] = "spine"
            try:
                from CORE import spine_registry as _spine
            except Exception as exc:  # pragma: no cover - defensive
                note_failure("mesh:L439", exc)
                return {"success": False, "error": str(exc)[:800]}
            trust = _spine.check_gossip_trust(peer_state)
            out["trust"] = trust.get("signature", "?")
            out["env"] = trust.get("env", "")
            if not trust.get("ok"):
                out["rejected"] = trust.get("reason", "untrusted")
                with self._lock:
                    self._rounds += 1
                    out["rounds"] = self._rounds
                return out
            if merge_ledger:
                try:
                    out["spine_merge"] = _spine.merge_spine_gossip(peer_state)
                except Exception as exc:
                    note_failure("mesh:L453", exc)
                    out["spine_merge"] = {"merged": False,
                                          "error": str(exc)[:80]}

        with self._lock:
            for k, v in peer_state.items():
                if k in self._ENVELOPE_KEYS:
                    continue
                cur = self._state.get(k)
                if cur is None:
                    self._state[k] = v
                    out["new"] += 1
                    continue
                ts_new, ts_cur = _stamp(v), _stamp(cur)
                if ts_new and (not ts_cur or ts_new > ts_cur):
                    self._state[k] = v
                    out["applied"] += 1
                elif ts_new and ts_new == ts_cur:
                    pass  # idempotent re-merge of an identical payload
                else:
                    out["stale_skipped"] += 1
            cap_map(self._state, self.STATE_KEYS_MAX)
            self._rounds += 1
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            out["known_keys"] = len(self._state)
            out["rounds"] = self._rounds
        return out

    def get_state(self) -> dict:
        with self._lock:
            return {"keys": len(self._state), "rounds": self._rounds}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": self.get_state()}


@register_worm(category="mesh", priority=7, cooldown_seconds=60.0,
    description="Detects split-brain (WSL stopped, G: unmounted), replays DeadLetterQueue")
class PartitionHealerWorm:
    """Partition detection + DLQ replay."""

    VERSION = "1.1.0"
    PROVIDER = "PartitionHealerWorm"
    _STATE_ATTRS = ("_partitions",)
    PARTITIONS_MAX = 200  # finding #8

    def __init__(self):
        self._partitions: list = []
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def detect(self) -> dict[str, Any]:
        import subprocess
        import tempfile
        issues = []
        if os.name == 'nt':
            # BOUNDED, and this is the call that hung the suite. An unreachable
            # G: blocked `os.path.exists` for minutes with no timeout; "unknown"
            # is reported as unmounted because the fabric cannot use a path it
            # cannot reach.
            if _bounded_exists(r'G:\source-of-truth', timeout_s=5.0) is not True:
                issues.append("g_unmounted")
            # WSL distro liveness (best-effort, 10s cap).
            #
            # Output goes to TEMP FILES, not pipes. With capture_output=True this
            # call could hang FOREVER on Windows: `wsl.exe` is a launcher, so a
            # grandchild inherits the stdout pipe, and when the 10s TimeoutExpired
            # fires `subprocess.run` kills the direct child and then reaps it with
            # `communicate()` **without a timeout** -- which blocks while the
            # grandchild still holds that pipe. Verified live: this was the exact
            # frame the test suite stopped in (RedQueenWorm.pressure_probe ->
            # detect() -> here), and it hung the entire run rather than failing one
            # test. A file handle cannot deadlock the same way, because no pipe has
            # to be drained to EOF.
            # Delegation, not a second copy. `mesh.py` grew the temp-file
            # shape below first; `envmesh.WinBridgeWorm.wsl_distros()` kept the
            # buggy `capture_output=True` version and hung the test process at
            # shutdown (2026-09-17). Both now call the one bounded helper, which
            # also runs on a daemon thread so an unkillable `wsl.exe` cannot pin
            # the interpreter, and decodes the UTF-16LE redirect case.
            try:
                from CORE.fs_probe import bounded_subprocess_output
            except Exception as exc:  # pragma: no cover - helper optional
                note_failure("mesh:L537", exc)
                bounded_subprocess_output = None
            probe = (bounded_subprocess_output(["wsl", "-l", "-v"], timeout_s=10.0)
                     if bounded_subprocess_output else
                     {"ok": False, "out": "", "timeout": True, "error": "no_helper"})
            if not probe.get("ok") or probe.get("timeout"):
                issues.append("wsl_probe_failed")
            else:
                out = probe.get("out", "")
                if "kali-linux" in out and "Running" not in out:
                    issues.append("kali_stopped")
                if "Ubuntu" in out and "Running" not in out:
                    issues.append("ubuntu_stopped")
            # transport peer ports (mesh data plane)
            try:
                import socket as _sock
                for _env, _port in (("wsl2-kali", 18212), ("wsl2-ubuntu", 18211)):
                    _s = _sock.socket()
                    _s.settimeout(1)
                    try:
                        _s.connect(("127.0.0.1", _port))
                    except Exception as exc:
                        note_failure("mesh:L558", exc)
                        issues.append(f"peer_down:{_env}")
                    finally:
                        _s.close()
            except Exception as exc:
                note_failure("mesh:L562", exc)
        else:
            if _bounded_exists('/mnt/g', timeout_s=5.0) is not True:
                issues.append("g_unmounted")
        with self._lock:
            if issues:
                self._partitions.append({
                    "at": datetime.now().isoformat(), "issues": issues,
                })
                cap_list(self._partitions, self.PARTITIONS_MAX)
                publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "partitioned": bool(issues),
                    "issues": issues, "events": len(self._partitions)}

    @staticmethod
    def _registry_execute(worm_name: str, config: dict) -> dict[str, Any]:
        """Default DLQ replay driver: instantiate from WORM_REGISTRY, execute."""
        try:
            from CORE.worm_registry import WORM_REGISTRY
            info = WORM_REGISTRY.get(worm_name) or {}
            cls = info.get("class")
            if cls is None:
                return {"success": False,
                        "error": f"unregistered worm {worm_name}"}
            inst = cls()
            res = inst.execute() if hasattr(inst, "execute") else {}
            if not isinstance(res, dict):
                res = {"success": True, "result": res}
            res.setdefault("success", True)
            return res
        except Exception as e:
            note_failure("mesh:L593", e)
            return {"success": False, "error": str(e)[:200]}

    def heal(self, execute_fn=None) -> dict[str, Any]:
        """Really heal: replay the DeadLetterQueue, then clear partitions.

        Returns actual replay counts — no more fake 'queued'.
        """
        try:
            from CORE.WORMS.dead_letter_queue import DeadLetterQueue
            dlq = DeadLetterQueue()
            results = dlq.replay_all(execute_fn or self._registry_execute)
            ok = sum(1 for r in results if r.get("success"))
        except Exception as e:
            note_failure("mesh:L606", e)
            return {"success": False, "error": str(e)[:200]}
        with self._lock:
            n = len(self._partitions)
            self._partitions.clear()
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "replayed_partitions": n,
                    "dlq_attempted": len(results), "dlq_ok": ok,
                    "dlq_failed": len(results) - ok}

    def execute(self) -> dict[str, Any]:
        d = self.detect()
        if d["partitioned"]:
            return {"success": True, "method": "execute", "result": d}
        return {"success": True, "method": "execute", "result": self.heal()}


@register_worm(category="mesh", priority=6, cooldown_seconds=15.0,
    description="Heartbeat + capability advertisement for router weights")
class EnvBeaconWorm:
    """Capability beacon for mesh routing weights."""

    VERSION = "1.1.0"
    PROVIDER = "EnvBeaconWorm"
    _STATE_ATTRS = ("_beacons",)
    BEACONS_MAX = 64  # finding #8

    def __init__(self):
        self._beacons: dict = {}
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def beacon(self) -> dict[str, Any]:
        cap = {
            "env": _env_id(),
            "ft": is_free_threaded(),
            "platform": platform.platform()[:60],
            "cpu": _os.cpu_count(),
            "at": datetime.now().isoformat(),
        }
        try:
            import importlib.util as _iu
            cap["tools"] = {
                t: bool(_iu.find_spec(t)) for t in ("nmap", "torch", "onnx")
            }
        except Exception as exc:
            note_failure("mesh:L652", exc)
            cap["tools"] = {}
        with self._lock:
            self._beacons[cap["env"]] = cap
            cap_map(self._beacons, self.BEACONS_MAX)
            publish(self, self.PROVIDER, self._STATE_ATTRS)
        return {"success": True, "beacon": cap}

    def peers(self) -> dict:
        with self._lock:
            return {"peers": list(self._beacons.keys())}

    def execute(self) -> dict[str, Any]:
        r = self.beacon()
        return {"success": True, "method": "execute", "result": r["beacon"]}
