# coding: utf-8
"""
NEURAL MESH — Category: FEDERATED (3 worms)
Distributed learning: Bare primary, Win/WSL2 workers, Kali reward.

FederatedAggregatorWorm, NASMeshSearcherWorm, RewardShaperMeshWorm
"""

import threading
from datetime import datetime
from typing import Any, Callable, Iterable, List, TypeVar
from collections import defaultdict
import math as _math

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Fabric operational state (audit 2026-09-15, finding #7): federated round
# counters, client submissions, NAS proposals and reward history used to be
# lost on every new manager/process.
try:
    from CORE.WORMS.expansion.fabric_state import (
        hydrate, publish, cap_list, cap_map,
    )
except ImportError as exc:  # pragma: no cover - defensive
    note_failure("federated:L21", exc)
    def hydrate(instance: Any, provider: str, attrs: Iterable[str]) -> int:
        return 0

    def publish(instance: Any, provider: str, attrs: Iterable[str]) -> bool:
        return False

    def cap_list(seq, limit):
        return seq

    def cap_map(d, limit):
        return d

try:
    from CORE.worm_registry import register_worm  # type: ignore[reportAssignmentType]
except ImportError as exc:
    note_failure("federated:L36", exc)
    def register_worm(**kw: Any) -> Callable[[type], type]:
        return lambda cls: cls


def _age_seconds(at: Any, now: datetime | None = None) -> float:
    """Seconds since an ISO timestamp; 0.0 when unparseable (never raises).

    A submission whose timestamp cannot be read is treated as *fresh* rather
    than stale: refusing to use it would silently drop a real client, and this
    is a weighting function, not a validity gate.
    """
    if not isinstance(at, str) or not at:
        return 0.0
    try:
        ts = datetime.fromisoformat(at)
    except Exception as exc:
        note_failure("federated:L52", exc)
        return 0.0
    try:
        return max(0.0, ((now or datetime.now()) - ts).total_seconds())
    except Exception as exc:
        note_failure("federated:L56", exc)
        return 0.0


@register_worm(category="federated", priority=7, cooldown_seconds=180.0,
    description="FedAvg over SB3Agent/GRPOTrainer policies; Ray backend")
class FederatedAggregatorWorm:
    """Federated averaging aggregator — hardened (audit §4 item 8)."""

    VERSION = "1.2.0"
    PROVIDER = "FederatedAggregatorWorm"
    _STATE_ATTRS = ("_clients", "_rounds", "_last_round")
    CLIENTS_MAX = 64  # finding #8 (one entry per env; bounded anyway)

    # ── Robust-aggregation parameters (audit §4 item 8 / TODO P4 item 13) ──
    #
    # A sample-weighted mean is the textbook Byzantine-vulnerable aggregator:
    # one client submitting garbage (or simply a diverged training run) drags
    # the average by its own weight, and nothing in the result says it
    # happened. Three guards, most important first:
    #
    #   1. DIMENSIONAL VALIDATION. A client with a shorter or longer vector
    #      used to raise `IndexError` *while holding the lock*, so one
    #      malformed submission broke aggregation for every other client. The
    #      modal dimension now wins and the rest are reported as dropped.
    #   2. STALENESS WEIGHTING. An hour-old submission describes an older
    #      policy; it is exponentially decayed rather than counted equally.
    #   3. TRIMMED MEAN per coordinate (Krum-family robustness). The extreme
    #      value on each coordinate is discarded once there are enough clients
    #      to define an extreme; with fewer than 3 clients there is nothing to
    #      trim, and the result says so rather than implying robustness it
    #      does not have.
    #
    # Deliberately NOT implemented: secure aggregation (masked updates) and
    # differential privacy. Both require a transport that does not exist yet,
    # and a half-built version would read as privacy that isn't there.
    BTRIM = 0.1
    STALE_HALF_LIFE_S = 600.0
    MIN_CLIENTS_TO_TRIM = 3

    def __init__(self):
        self._clients: dict[str, dict] = {}
        self._rounds = 0
        self._last_round: dict[str, Any] = {}
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def submit(self, env: str, weights: list[float], samples: int) -> dict[str, Any]:
        with self._lock:
            self._clients[env] = {"weights": list(weights), "samples": samples,
                                  "at": datetime.now().isoformat()}
            cap_map(self._clients, self.CLIENTS_MAX)
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "env": env, "clients": len(self._clients)}

    def aggregate(self) -> dict[str, Any]:
        """One robust FedAvg round. Never raises; never partial.

        Returns (additive over the original keys, which are unchanged):
        ``round_id``, ``clients_used``, ``dropped`` (dimension mismatch),
        ``byzantine`` (which guard actually ran), ``trimmed_per_coord`` and
        ``staleness``.
        """
        with self._lock:
            if not self._clients:
                return {"success": True, "aggregated": False, "reason": "no_clients"}

            # 1. Dimensional validation — modal dimension wins, mismatches are
            #    reported instead of raising inside the lock.
            by_dim: dict[int, list[str]] = defaultdict(list)
            for env, c in self._clients.items():
                weights = c.get("weights") if isinstance(c, dict) else None
                by_dim[len(weights) if isinstance(weights, (list, tuple)) else 0].append(env)
            dim, envs = max(by_dim.items(), key=lambda kv: (len(kv[1]), -kv[0]))
            dropped = sorted(e for d, group in by_dim.items() if d != dim for e in group)
            if dim == 0:
                return {"success": False, "aggregated": False,
                        "reason": "no_usable_weights", "dropped": dropped}
            envs = sorted(envs)
            used = [self._clients[e] for e in envs]

            # 2. Staleness — exponential decay on the submission's age.
            now = datetime.now()
            ages = [_age_seconds(c.get("at"), now) for c in used]
            weights = [max(0.0, float(c.get("samples") or 0))
                       * 0.5 ** (age / self.STALE_HALF_LIFE_S)
                       for c, age in zip(used, ages)]
            if sum(weights) <= 0:
                # No client declared samples: equal weights are the honest
                # fallback, and `staleness` still reports the ages.
                weights = [1.0] * len(used)

            # 3. Coordinate-wise trimmed mean. At least one value is trimmed
            #    from each tail (the standard trimmed-mean convention): a purely
            #    fractional trim yields `int(4 * 0.1) == 0`, i.e. NO robustness
            #    at four clients, which is precisely the case where one bad
            #    client can still swing the result.
            trim = (max(1, int(len(used) * self.BTRIM))
                    if len(used) >= self.MIN_CLIENTS_TO_TRIM else 0)
            avg: list[float] = []
            for i in range(dim):
                pairs = sorted((float(used[j]["weights"][i]), weights[j])
                               for j in range(len(used)))
                if trim:
                    pairs = pairs[trim:-trim]
                wsum = sum(w for _, w in pairs) or 1.0
                avg.append(sum(v * w for v, w in pairs) / wsum)

            self._rounds += 1
            round_id = f"r{self._rounds}"
            self._last_round = {
                "round_id": round_id, "dim": dim, "at": now.isoformat(),
                "clients": len(used), "dropped": dropped, "trimmed": trim,
                "oldest_age_s": round(max(ages), 1) if ages else None,
            }
            self._clients.clear()
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "aggregated": True, "rounds": self._rounds,
                    "round_id": round_id, "dim": dim,
                    "clients_used": len(used), "dropped": dropped,
                    "byzantine": ("trimmed_mean" if trim
                                  else "none_too_few_clients"),
                    "trimmed_per_coord": trim,
                    "staleness": {"half_life_s": self.STALE_HALF_LIFE_S,
                                  "oldest_age_s": round(max(ages), 1) if ages else None},
                    "avg_head": [round(v, 4) for v in avg[:4]]}

    def execute(self) -> dict[str, Any]:
        """Read-only summary — deliberately NOT an aggregation.

        Audit 2026-09-15 (finding #10): this used to call `aggregate()`, which
        consumes every pending submission and bumps the round counter. Once
        the Red Queen started probing the `federated` nature, a *probe* would
        have silently cleared real client state, and `execute()` is the method
        the probe path and `ClusterWORMZ` both reach for. Aggregation is now
        reachable only through `aggregate()`, which the fabric stage calls
        deliberately.
        """
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"rounds": self._rounds,
                               "waiting_clients": len(self._clients),
                               "clients": sorted(self._clients),
                               # Additive: the last round's id and its guards, so
                               # a probe reports the robustness that actually
                               # ran rather than implying it.
                               "last_round": dict(self._last_round)}}


@register_worm(category="federated", priority=6, cooldown_seconds=300.0,
    description="Evolutionary NAS (EvolutionEngine/MAPElites/SepCMAES) distributed across envs")
class NASMeshSearcherWorm:
    """Distributed neural architecture search coordinator."""

    VERSION = "1.1.0"
    PROVIDER = "NASMeshSearcherWorm"
    _STATE_ATTRS = ("_proposals", "_evaluated", "_best")
    PROPOSALS_MAX = 500  # finding #8: pending proposals await evaluate_batch()

    def __init__(self):
        self._proposals: list = []
        self._evaluated = 0
        self._best: dict | None = None
        self._lock = threading.Lock()
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)

    def propose(self, env: str, arch: dict, reward: float = 0.0) -> dict[str, Any]:
        with self._lock:
            self._proposals.append({"env": env, "arch": arch, "reward": reward})
            if self._best is None or reward > self._best.get("reward", -1):
                self._best = {"env": env, "arch": arch, "reward": reward}
            cap_list(self._proposals, self.PROPOSALS_MAX)
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "proposals": len(self._proposals),
                    "best_reward": self._best["reward"]}

    def evaluate_batch(self) -> dict[str, Any]:
        with self._lock:
            n = len(self._proposals)
            self._evaluated += n
            self._proposals.clear()
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            return {"success": True, "evaluated": n,
                    "total_evaluated": self._evaluated, "best": self._best}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"pending": len(self._proposals),
                               "evaluated": self._evaluated, "best": self._best}}


@register_worm(category="federated", priority=6, cooldown_seconds=60.0,
    description="Provider-tier inference reward during training (real chain incl. local model tier); DLQ on fail")
class RewardShaperMeshWorm:
    """Multi-provider reward shaper."""

    VERSION = "1.0.0"
    PROVIDERS = ("groq", "openrouter", "nim", "lmstudio", "llamaserver",
                 "llama_mythos", "llama_mythos_v2", "llama_qwopus",
                 "llama_ornith", "accelerator_sidecar", "edge0", "openvino",
                 "huggingface_direct", "llama_cpp_direct", "ollama")

    PROVIDER = "RewardShaperMeshWorm"
    _STATE_ATTRS = ("_scores", "_dlq")
    # finding #8: `weights()`/`score()` slice the history for reporting, but the
    # backing list itself grew one entry per outcome forever.
    SCORES_PER_PROVIDER_MAX = 100
    DLQ_MAX = 200

    def __init__(self):
        self._scores: dict[str, list] = defaultdict(list)
        self._dlq: list = []
        self._lock = threading.Lock()
        # hydrate() does a dict.update, so restored values land as plain
        # lists inside a defaultdict — `.append` and `[]` both still work.
        hydrate(self, self.PROVIDER, self._STATE_ATTRS)
        for prov, hist in list(self._scores.items()):
            if not isinstance(hist, list):
                self._scores[prov] = list(hist or [])

    def score(self, provider: str, reward: float, ok: bool = True) -> dict[str, Any]:
        with self._lock:
            if not ok:
                self._dlq.append({"provider": provider, "at": datetime.now().isoformat()})
                cap_list(self._dlq, self.DLQ_MAX)
                publish(self, self.PROVIDER, self._STATE_ATTRS)
                return {"success": True, "provider": provider, "dlq": True,
                        "dlq_depth": len(self._dlq)}
            self._scores[provider].append(reward)
            cap_list(self._scores[provider], self.SCORES_PER_PROVIDER_MAX)
            cap_map(self._scores, 64)
            publish(self, self.PROVIDER, self._STATE_ATTRS)
            hist = self._scores[provider][-100:]
            avg = round(sum(hist) / len(hist), 4)
            return {"success": True, "provider": provider, "avg": avg,
                    "samples": len(hist)}

    def weights(self) -> dict:
        with self._lock:
            avgs = {p: (sum(v[-50:]) / len(v[-50:]) if v else 0.0)
                    for p, v in self._scores.items()}
            total = sum(max(0.0, a) for a in avgs.values()) or 1.0
            return {"weights": {p: round(max(0.0, a) / total, 3) for p, a in avgs.items()},
                    "dlq_depth": len(self._dlq)}

    _REWARD_MODEL = None  # lazy-loaded task5 logistic

    @classmethod
    def _load_reward_model(cls) -> dict | None:
        if cls._REWARD_MODEL is not None:
            return cls._REWARD_MODEL
        try:
            import json as _json
            cls._REWARD_MODEL = _json.load(
                open("G:/source-of-truth/azazael_models/reward_model.json", encoding="utf-8"))
        except Exception as exc:
            note_failure("federated:L309", exc)
            cls._REWARD_MODEL = {}
        return cls._REWARD_MODEL

    def local_score(self, rtt_ms: float = 50.0, fail_rate: float = 0.0,
                    size_kb: float = 100.0) -> dict[str, Any]:
        """Offline reward P(prefer | rtt, fail_rate, size) from task5 model.
        Falls back to the rtt/fail heuristic when the model file is missing."""
        m = self._load_reward_model() or {}
        x = [min(2.0, rtt_ms / 2000.0), max(0.0, min(1.0, fail_rate)),
             min(1.0, size_kb / 5000.0)]
        if m.get("coef"):
            z = sum(c * v for c, v in zip(m["coef"], x)) + (m.get("intercept") or [0.0])[0]
            p = 1.0 / (1.0 + _math.exp(-z))
            src = "reward_model.json"
        else:
            p = 1.0 if (rtt_ms < 300 and fail_rate < 0.3) else 0.0
            src = "heuristic"
        return {"success": True, "reward": round(p, 4), "src": src,
                "features": {"rtt_ms": rtt_ms, "fail_rate": fail_rate, "size_kb": size_kb}}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.weights()}
