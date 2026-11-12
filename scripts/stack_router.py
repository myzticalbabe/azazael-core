"""stack_router.py -- contextual LinUCB router, SHADOW mode (STACK Phase 1).

Learns regime -> track-reward mapping from the track ledger and reports
what it WOULD pick next to Thompson sampling. Never routes: the director
keeps full authority until shadow agreement + holdout wins justify more.

Context features (from track_regime()): bias, hour sin/cos, day-of-week,
log store sizes, log recent outcome volume. Per-arm ridge models
(LinUCB, alpha configurable). Stdlib only.

Usage:
    python scripts/stack_router.py [--slots 10] [--alpha 1.0] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
import math as _m

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

DIM = 6
ALPHA = 1.0


def features(regime: dict) -> list[float]:
    """Regime dict -> fixed feature vector. Unknowns (-1/None) -> 0."""

    def num(v, default=0.0):
        return float(v) if isinstance(v, (int, float)) and v >= 0 else default

    hour = num(regime.get("hour"), 12.0) % 24
    return [
        1.0,
        _m.sin(2 * _m.pi * hour / 24.0),
        _m.cos(2 * _m.pi * hour / 24.0),
        num(regime.get("dow"), 3.0) / 6.0,
        _m.log1p(num(regime.get("db_mb_main"))),
        _m.log1p(num(regime.get("outcome_1h"))),
    ]


def _mat_inv(m: list[list[float]]) -> list[list[float]]:
    """Gauss-Jordan inverse (dim <= 6 in practice)."""
    n = len(m)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
           for i, row in enumerate(m)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        aug[col], aug[piv] = aug[piv], aug[col]
        pv = aug[col][col] or 1e-12
        aug[col] = [v / pv for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                f = aug[r][col]
                aug[r] = [a - f * b for a, b in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


class LinUCB:
    """Per-arm ridge regression + UCB pick. Deterministic given data."""

    def __init__(self, arms: list[str], dim: int = DIM, alpha: float = ALPHA):
        self.arms = list(arms)
        self.dim = dim
        self.alpha = alpha
        self._A = {a: [[1.0 if i == j else 0.0 for j in range(dim)]
                       for i in range(dim)] for a in self.arms}
        self._b = {a: [0.0] * dim for a in self.arms}
        self.n_obs: dict[str, int] = {a: 0 for a in self.arms}

    def update(self, arm: str, x: list[float], reward: float) -> None:
        if arm not in self._A or len(x) != self.dim:
            return
        A = self._A[arm]
        for i in range(self.dim):
            for j in range(self.dim):
                A[i][j] += x[i] * x[j]
            self._b[arm][i] += x[i] * reward
        self.n_obs[arm] += 1

    def scores(self, x: list[float]) -> dict[str, float]:
        out = {}
        for a in self.arms:
            inv = _mat_inv(self._A[a])
            theta = [sum(inv[i][j] * self._b[a][j] for j in range(self.dim))
                     for i in range(self.dim)]
            mu = sum(theta[i] * x[i] for i in range(self.dim))
            var = sum(x[i] * sum(inv[i][j] * x[j] for j in range(self.dim))
                      for i in range(self.dim))
            out[a] = mu + self.alpha * math.sqrt(max(var, 0.0))
        return out

    def pick(self, x: list[float]) -> str:
        s = self.scores(x)
        return max(self.arms, key=lambda a: (s[a], a))


def load_track_runs(limit_per_track: int = 200) -> dict[str, list[dict]]:
    """track -> [{slot, regime, reward}] newest last, stamped rows only."""
    from track_registry import ROTATION, REWARD_EVENTS
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    out: dict[str, list[dict]] = {}
    for algo in ROTATION:
        rows: list[dict] = []
        if algo in REWARD_EVENTS:
            et, field, src = REWARD_EVENTS[algo]
            for r in store.query_memories(event_type=et, limit=limit_per_track):
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        continue
                if src is not None and r.get("source") != src:
                    continue
                v = d.get(field)
                reg = d.get("regime")
                if isinstance(v, (int, float)) and isinstance(reg, dict):
                    rows.append({"slot": int(reg.get("slot", 0)),
                                 "regime": reg, "reward": float(v)})
        else:
            for r in store.fetch_all_memories(
                    event_type="sb3_training_orchestrated", limit=5000):
                d = r.get("data", {})
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        continue
                if d.get("algo") != algo:
                    continue
                v = d.get("eval_mean_reward")
                reg = d.get("regime")
                if isinstance(v, (int, float)) and isinstance(reg, dict):
                    rows.append({"slot": int(reg.get("slot", 0)),
                                 "regime": reg, "reward": float(v)})
        rows.sort(key=lambda r: r["slot"])
        out[algo] = rows
    return out


def backtest(tracks: dict[str, list[dict]], slots: int = 10,
             alpha: float = ALPHA) -> dict:
    """For the last `slots` decision points: train on prior data only,
    compare LinUCB pick vs Thompson pick. Honest (no peeking)."""
    import meta_director as _md
    from track_registry import ROTATION
    points = sorted({r["slot"] for rs in tracks.values() for r in rs})[-slots:]
    agree = 0
    detail = []
    for s in points:
        uc = LinUCB(ROTATION, alpha=alpha)
        reg = None
        for algo, rs in tracks.items():
            for r in rs:
                if r["slot"] < s:
                    uc.update(algo, features(r["regime"]), r["reward"])
                    reg = r["regime"]
        if reg is None:
            continue
        x = features(reg)
        lin = uc.pick(x)
        try:
            thomp = _md.pick(s, _md.load_ledger(ROTATION), ROTATION)
        except Exception:
            thomp = ROTATION[s % len(ROTATION)]
        detail.append({"slot": s, "linucb": lin, "thompson": thomp,
                       "agree": lin == thomp})
        agree += (lin == thomp)
    return {"points": len(detail),
            "agreement": round(agree / len(detail), 3) if detail else 0.0,
            "detail": detail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stack_router")
    ap.add_argument("--slots", type=int, default=10)
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tracks = load_track_runs()
    stamped = sum(len(v) for v in tracks.values())
    res = backtest(tracks, slots=args.slots, alpha=args.alpha)
    res["stamped_rows"] = stamped
    # Bandit-depth stub (2026-09-29): cluster labels for the shadow picks.
    # SB3 policy-gradient family vs classical/Q-learning family vs pipeline
    # tracks. Advisory only: no routing change, director authority unchanged.
    # A meta-learned prior (MetaTS) keys off these clusters when era volume
    # allows; until then the labels just make the shadow readable.
    from track_registry import ROTATION as _ROT
    _clusters = {
        "pg": {"PPO", "A2C", "SAC", "TD3", "DDPG", "MaskablePPO", "GRPO"},
        "value": {"DQN", "QRDQN", "TQC", "ARS", "TRPO"},
        "pipeline": {"DPO", "OFFLINE", "RL", "DREAMTUNE", "WORMGA", "ALLPAR"},
    }
    def _cluster_of(a):
        for k, vs in _clusters.items():
            if a in vs:
                return k
        return "other"
    res["clusters"] = {a: _cluster_of(a) for a in _ROT}
    res["note"] = ("shadow only: picks reported, director authority "
                   "unchanged")
    if args.json:
        print(json.dumps(res, indent=1, default=str))
        return 0
    print("=== STACK router shadow (last %d decision points) ===" % res["points"])
    print("stamped rows available: %d" % stamped)
    print("agreement with Thompson: %s" % res["agreement"])
    for d in res["detail"][-5:]:
        print("  slot=%d linucb=%s thompson=%s %s"
              % (d["slot"], d["linucb"], d["thompson"],
                 "AGREE" if d["agree"] else "split"))
    if stamped == 0:
        print("no stamped rows yet -- regime stamping just landed; "
              "re-run after tracks fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
