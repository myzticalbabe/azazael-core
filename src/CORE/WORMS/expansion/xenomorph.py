# coding: utf-8
"""
NEURAL MESH — Category: XENOMORPH (7 worms)
Hostile-adaptive red team with lifecycle stages. All envs as hosts;
Queen PREFERS bare metal but falls back (degraded) so the hive never bricks.

OvomorphWorm, FacehuggerWorm, ChestbursterWorm, DroneWorm,
PraetorianWorm, Lilith (the Queen), ResinWorm

Containment: every offensive action is advisory-only (returns payloads for
QuorumVoterWorm 2-of-3 + AttestationWorm + QuarantineWorm to approve).
Queen election consults EnvBeaconWorm peers + LeaderElectorWorm terms.
"""

import threading
from datetime import datetime
from typing import Any, Callable, Iterable, List
import os
import time as _t

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm  # type: ignore[reportAssignmentType]
except ImportError as exc:
    note_failure("xenomorph:L21", exc)
    def register_worm(**kw: Any) -> Callable[[type], type]:
        return lambda cls: cls

# Fabric operational state (audit 2026-09-15, finding #7 follow-up): the
# Queen lease has to be visible to the OTHER envs or it cannot be enforced.
# Persisting it also makes it ride the signed gossip envelope, so a peer
# learns the live lease instead of each host believing it is queen.
try:
    from CORE.WORMS.expansion.fabric_state import (
        hydrate as fabric_hydrate,
        publish as fabric_publish,
    )
except ImportError as exc:  # pragma: no cover - defensive
    note_failure("xenomorph:L34", exc)
    def fabric_hydrate(instance: Any, provider: str, attrs: Iterable[str]) -> int:
        return 0

    def fabric_publish(instance: Any, provider: str, attrs: Iterable[str]) -> bool:
        return False


def _local_env() -> str:
    """Local env id — canonical CORE.env_id (audit 2026-09-15, finding #4).

    This was a 3-way copy whose docstring claimed to mirror mesh._env_id().
    That stopped being true when the 5-way split landed, so Lilith compared a
    "wsl2" peer key against beacon peer keys spelled "wsl2-ubuntu" and could
    not recognise another WSL host. One implementation now.
    """
    try:
        from CORE.env_id import env_id
        return env_id()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("xenomorph:L53", exc)
        import os
        return "windows" if os.name == 'nt' else "bare"


def _host_capable(min_cpu: int = 2, min_ram_gb: float = 2.0) -> dict:
    """Capability gate for Queen hosting. Returns {ok, reasons, cpu, ram_gb}.

    Thresholds are deliberately low: the Queen is a coordinator (genome
    counter + lease + vote tally), not a trainer. Heavy trainers
    (FederatedAggregator, NASMeshSearcher, EvolutionEngine) enforce their
    own gates. Default WSL2 VMs ship ~4CPU/4GB, which must qualify.
    """
    reasons = []
    cpu = os.cpu_count() or 0
    if cpu < min_cpu:
        reasons.append(f"cpu {cpu} < {min_cpu}")
    ram_gb = 0.0
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / 1e9, 1)
        if ram_gb < min_ram_gb:
            reasons.append(f"ram {ram_gb}GB < {min_ram_gb}GB")
    except Exception as exc:
        note_failure("xenomorph:L77", exc)
        reasons.append("ram unknown (psutil missing)")
    return {"ok": not reasons, "reasons": reasons, "cpu": cpu, "ram_gb": ram_gb}


@register_worm(category="xenomorph", priority=2, cooldown_seconds=600.0,
    description="Dormant payload cache (eggs) on G:; hatches on EnvBeacon signal")
class OvomorphWorm:
    """Egg layer / dormant store. Advisory only."""

    VERSION = "1.0.0"

    def __init__(self):
        self._eggs: dict[str, dict] = {}
        self._hatched = 0
        self._lock = threading.Lock()

    def lay(self, egg_id: str, payload: dict, env_hint: str = "any") -> dict[str, Any]:
        with self._lock:
            self._eggs[egg_id] = {"payload_keys": list(payload)[:8],
                                  "env_hint": env_hint,
                                  "at": datetime.now().isoformat(),
                                  "hatched": False}
            return {"success": True, "eggs": len(self._eggs)}

    def hatch(self, egg_id: str) -> dict[str, Any]:
        with self._lock:
            egg = self._eggs.get(egg_id)
            if not egg:
                return {"success": False, "reason": "no_egg"}
            egg["hatched"] = True
            self._hatched += 1
            return {"success": True, "egg": egg_id, "hatched_total": self._hatched}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"eggs": len(self._eggs), "hatched": self._hatched}}


@register_worm(category="xenomorph", priority=8, cooldown_seconds=60.0,
    description="Single-execution injector tracer; attaches to one target input, then detaches")
class FacehuggerWorm:
    """Injector tracer (advisory: returns injection plan for quorum approval)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._attachments = 0
        self._lock = threading.Lock()

    def plan(self, target_worm: str, input_keys: list) -> dict[str, Any]:
        with self._lock:
            self._attachments += 1
            return {"success": True, "target": target_worm,
                    "inject_at": (input_keys or ["input"])[:3],
                    "single_shot": True, "needs_quorum": True,
                    "attachments": self._attachments}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"attachments": self._attachments}}


@register_worm(category="xenomorph", priority=7, cooldown_seconds=120.0,
    description="Variant fan-out counter; forks N mutated cases via EvolutionEngine params")
class ChestbursterWorm:
    """Rapid variant fan-out (counts only; EvolutionEngine does the mutation)."""

    VERSION = "1.0.0"

    def __init__(self, fanout: int = 8):
        self._fanout = fanout
        self._bursts = 0
        self._lock = threading.Lock()

    def burst(self, finding_id: str) -> dict[str, Any]:
        with self._lock:
            self._bursts += 1
            return {"success": True, "finding": finding_id,
                    "variants": self._fanout, "bursts": self._bursts,
                    "engine": "EvolutionEngine/SepCMAES"}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"bursts": self._bursts, "fanout": self._fanout}}


@register_worm(category="xenomorph", priority=6, cooldown_seconds=30.0,
    description="Cross-env carrier; routes successful DNA via MeshRouterWorm weights")
class DroneWorm:
    """Worker carrier between hive envs."""

    VERSION = "1.0.0"

    def __init__(self):
        self._carried = 0
        self._lock = threading.Lock()

    def carry(self, dna_id: str, dst_env: str) -> dict[str, Any]:
        with self._lock:
            self._carried += 1
            return {"success": True, "dna": dna_id, "src": _local_env(),
                    "dst": dst_env, "carried": self._carried}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"carried": self._carried, "env": _local_env()}}


@register_worm(category="xenomorph", priority=9, cooldown_seconds=300.0,
    description="Hive guard; moults to Queen only if LeaderElector term expires with no Queen")
class PraetorianWorm:
    """Guard / Queen-successor candidate (never self-promotes without quorum)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._terms_seen: list = []
        self._lock = threading.Lock()

    def observe_term(self, term: int, queen_present: bool) -> dict[str, Any]:
        with self._lock:
            self._terms_seen.append({"term": term, "queen": queen_present})
            self._terms_seen = self._terms_seen[-20:]
            eligible = (not queen_present) and len(self._terms_seen) >= 3
            return {"success": True, "term": term, "queen_present": queen_present,
                    "moult_eligible": eligible, "needs_quorum": True}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"terms_seen": len(self._terms_seen)}}


@register_worm(category="xenomorph", priority=10, cooldown_seconds=600.0,
    description="LILITH, hive genome coordinator. PREFERS bare; interim degraded lease elsewhere")
class Lilith:
    """LILITH, the Queen: bare-preference + degraded fallback (never bricks the hive).

    Election:
      - peers: dict from EnvBeaconWorm.peers() (env names seen on mesh).
      - If a 'bare' peer exists and we are not bare -> FOLLOWER (stand down).
      - If no bare peer and this host passes _host_capable() -> queen.
        FULL on bare or wsl2 (WSL2 Ubuntu 26.04 runs a real Linux kernel,
        6.18-microsoft-standard-WSL2, verified 2026-09-13); INTERIM degraded
        lease elsewhere (windows/kali). Re-check every execute().
    """

    VERSION = "1.0.0"
    QUEEN_NAME = "Lilith"
    PREFERRED_ENV = "bare"
    # Both WSL2 flavours are full Linux kernels (Ubuntu 26.04 / Kali on
    # 6.18-microsoft-standard-WSL2), so a queen lease on either is FULL, not
    # degraded. These ids must stay in step with CORE.env_id.VALID_ENVS:
    # before the 2026-09-15 unification this tuple held the pre-split "wsl2",
    # so a WSL host would have silently downgraded to an interim lease the
    # moment _local_env() started returning "wsl2-ubuntu".
    FULL_ENVS = ("bare", "wsl2-ubuntu", "wsl2-kali")
    FALLBACK_ORDER = ("bare", "wsl2-ubuntu", "wsl2-kali", "windows", "kali")
    LEASE_SECONDS = 300
    # Audit 2026-09-15 (finding #7): the lease was written and never read, so
    # `claim()` re-claimed unconditionally and a partitioned host became an
    # independent queen. The lease is now the authority, and it is persisted +
    # gossiped so a peer's live lease is visible here instead of each host
    # believing it is queen.
    PROVIDER = "Lilith"
    _STATE_ATTRS = ("_term", "_genome_version", "_lease_env", "_lease_until")

    def __init__(self):
        self._genome_version = 0
        self._lease_until: str | None = None
        self._lease_env: str | None = None
        self._term = 0
        self._lock = threading.Lock()
        fabric_hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    # -- lease ---------------------------------------------------------

    @staticmethod
    def _peer_set(peers: dict | list | None, env: str) -> set:
        if isinstance(peers, dict):
            seen = set(peers.keys()) | {str(v) for v in peers.values()
                                        if isinstance(v, str)}
        elif isinstance(peers, (list, tuple, set)):
            seen = {str(p) for p in peers}
        else:
            seen = {env}
        seen.add(env)
        return seen

    @staticmethod
    def _lease_until_ts(lease_until: str | None) -> float:
        """Epoch seconds for a stored lease expiry; 0.0 when unparseable."""
        if not lease_until:
            return 0.0
        try:
            return datetime.fromisoformat(lease_until).timestamp()
        except Exception as exc:
            note_failure("xenomorph:L278", exc)
            return 0.0

    def _lease_snapshot(self, now: float | None = None) -> dict[str, Any]:
        """Current lease as data: holder, term, expiry, seconds remaining.

        This is the value `claim()` now consults before declaring itself
        queen. Must be called with `self._lock` held.
        """
        now = _t.time() if now is None else now
        until = self._lease_until_ts(self._lease_until)
        return {"env": self._lease_env, "term": self._term,
                "until": self._lease_until,
                "expires_in": round(until - now, 1) if until else None,
                "valid": bool(self._lease_env and until > now)}

    def lease_holder(self) -> dict[str, Any]:
        """Public read of the current lease (thread-safe)."""
        with self._lock:
            return self._lease_snapshot()

    def claim(self, peers: dict | list | None = None,
              now: float | None = None) -> dict[str, Any]:
        """Claim (or renew) the Queen lease. Never bricks the hive.

        Order of authority, strongest first:

        1. **A valid lease held by another env** -> follower. The holder keeps
           the lease until it *expires*; a later claimer cannot depose a live
           queen by asserting a higher term. This is the line that stops a
           partitioned or restarted host from becoming an independent queen.
        2. **A reachable `bare` peer** (and we are not bare) -> follower.
        3. **Capability gate** (`_host_capable`) -> follower if it fails.
        4. Otherwise claim. A claim that succeeds an expired or foreign lease
           bumps the term; a renewal of our OWN unexpired lease keeps it — a
           re-claim is a heartbeat, not a new election.
        """
        env = _local_env()
        seen = self._peer_set(peers, env)
        cap = _host_capable()
        with self._lock:
            lease = self._lease_snapshot(now)
            if lease["valid"] and lease["env"] != env:
                return {"success": True, "name": self.QUEEN_NAME,
                        "role": "follower", "env": env,
                        "reason": "lease_held_by_peer", "degraded": False,
                        "capable": cap["ok"], "lease": lease}
            if self.PREFERRED_ENV in seen and env != self.PREFERRED_ENV:
                return {"success": True, "name": self.QUEEN_NAME,
                        "role": "follower", "env": env,
                        "reason": "bare_peer_present", "degraded": False,
                        "capable": cap["ok"], "lease": lease}
            if not cap["ok"]:
                return {"success": True, "name": self.QUEEN_NAME,
                        "role": "follower", "env": env,
                        "reason": "; ".join(cap["reasons"]) or "incapable",
                        "degraded": False, "capable": False,
                        "lease": lease}
            import time as _t
            now = _t.time() if now is None else now
            renewed = bool(lease["valid"] and lease["env"] == env)
            if not renewed:
                self._term += 1
                self._genome_version += 1
            self._lease_env = env
            self._lease_until = datetime.fromtimestamp(
                now + self.LEASE_SECONDS).isoformat()
            degraded = env not in self.FULL_ENVS
            fabric_publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "name": self.QUEEN_NAME,
                    "role": "queen_full" if not degraded else "queen_interim",
                    "env": env, "degraded": degraded,
                    "genome": self._genome_version, "term": self._term,
                    "renewed": renewed,
                    "lease_until": self._lease_until,
                    "expires_in": self.LEASE_SECONDS,
                    "capable": True}

    def request_moult(self, term: int, quorum: int = 2,
                      force: bool = False) -> dict[str, Any]:
        """Praetorian -> Queen promotion request (audit findings #6 + #7).

        `PraetorianWorm.observe_term()` computes `moult_eligible` and returns
        `needs_quorum: True`, but nothing consumed either value — eligibility
        was computed and dropped on the floor, so the succession path could
        never actually happen.

        A Praetorian now *requests* through the shared quarantine-vote ledger
        (the same distinct-env quorum used for quarantine and for
        morphogenesis promotion). The claim only proceeds on a decided vote,
        and `force=True` is the auditable operator channel.
        """
        env = _local_env()
        finding = f"moult:{env}"
        vote: dict[str, Any] = {}
        try:
            from CORE import spine_registry as _spine
            vote = _spine.cast_quarantine_vote(
                finding, "operator" if force else env, "moult", quorum=quorum)
        except Exception as exc:
            note_failure("xenomorph:L378", exc)
            vote = {}
        decided = bool(force) or bool(vote.get("decided"))
        if not decided:
            return {"success": True, "promoted": False,
                    "reason": "awaiting_quorum", "env": env, "term": term,
                    "envs": vote.get("envs"),
                    "env_count": vote.get("env_count"), "quorum": quorum}
        # Quorum granted: take the lease through the normal authority ordering
        # (a live peer lease still outranks us and we stay a follower).
        claim = self.claim(peers=None)
        return {"success": True,
                "promoted": str(claim.get("role", "")).startswith("queen"),
                "env": env, "term": term, "forced": bool(force),
                "envs": vote.get("envs"), "claim": claim}

    def execute(self) -> dict[str, Any]:
        r = self.claim(peers=None)
        return {"success": True, "method": "execute", "result": r}


@register_worm(category="xenomorph", priority=5, cooldown_seconds=90.0,
    description="Quarantined kill-trace writer (memory-only; G: append only if mounted)")
class ResinWorm:
    """Hive resin: quarantined trace store, never feeds DreamConsolidator."""

    VERSION = "1.0.0"

    def __init__(self):
        self._traces: list = []
        self._lock = threading.Lock()

    def secrete(self, trace_id: str, summary: str) -> dict[str, Any]:
        g_ok = os.path.exists(r'G:\source-of-truth') or os.path.exists('/mnt/g')
        with self._lock:
            self._traces.append({"id": trace_id, "summary": summary[:120],
                                 "g_archive": g_ok,
                                 "at": datetime.now().isoformat()})
            return {"success": True, "traces": len(self._traces),
                    "g_archive": g_ok, "quarantined": True}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"traces": len(self._traces)}}
