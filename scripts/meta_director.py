"""meta_director.py — Thompson-sampling director over training tracks.

The "1 that does everything" is not one algorithm: SB3 policies, GRPO
selection, dream tuning and the RL pipeline solve different problems and
no single one subsumes the rest. The achievable end-state is ONE
meta-learner that DIRECTS all training compute (which track trains each
slot), INTEGRATES through shared pools/lineage ledgers, and MUTATES via
the existing meta-ES — with tracks as bandit arms.

Mechanism: per-track Beta(a, b) posteriors over P(run beats the track's
own median). Reward is scale-free by construction (median-relative), so
SB3 episode totals (~250) and GRPO rates (~0.4) compete fairly. Empty
history counts as a win (optimistic init — new tracks get tried).
Choice is deterministic given (slot, ledger): slot-seeded Thompson
sample with a uniform exploration floor, so the monitor replicates the
pick exactly from the same ledger file.

Ledger lives in config/track_bandit.json (atomic writes). Updates happen
on successful runs only — crashes carry no performance signal.
Stdlib only.
"""

import json
import os
import random

LEDGER_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "config", "track_bandit.json"))

EPSILON = 0.15  # uniform-exploration floor (same seed stream as picks)
STALE_SLOTS = 48  # backstop (4h): no track goes longer unpicked (2026-09-09
# fix: pure Thompson converged on WORMGA/DPO and starved 11 SB3 tracks for
# 4+ hours. Starved SB3 also rots DPO's chosen/rejected pairs, so coverage
# is systemic, not cosmetic.)
COOLDOWN_SLOTS = 4  # after a pick, a track sits out 4 slots (~20 min):
# caps any single track at ~20% share so concentration can't recur.


def _blank(tracks):
    return {t: {"a": 1, "b": 1, "picks": 0, "wins": 0, "last_slot": None,
                "cool": 0} for t in tracks}


def load_ledger(tracks):
    """Load {algo: {a,b,picks,wins,last_slot}}; missing tracks start (1,1)."""
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    out = _blank(tracks)
    for t in tracks:
        st = raw.get(t) or {}
        try:
            out[t] = {"a": max(1, int(st.get("a", 1))),
                      "b": max(1, int(st.get("b", 1))),
                      "picks": int(st.get("picks", 0)),
                      "wins": int(st.get("wins", 0)),
                      "last_slot": st.get("last_slot"),
                      "cool": int(st.get("cool", 0) or 0)}
        except (ValueError, TypeError):
            pass
    return out


def save_ledger(ledger):
    """Atomic write (single writer per run process; last-writer-wins)."""
    try:
        tmp = LEDGER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
        os.replace(tmp, LEDGER_PATH)
    except Exception:
        pass


def pick(slot, ledger, tracks, epsilon=EPSILON):
    """Deterministic track choice: staleness backstop, cooldown cap, then
    epsilon-greedy Thompson. Never-seen tracks (last_slot None) count as
    infinitely stale so newcomers are tried immediately."""
    slot = int(slot)
    pool = [t for t in tracks
            if int(((ledger or {}).get(t) or {}).get("cool", 0) or 0) <= slot]
    if not pool:
        pool = list(tracks)
    stalest, stalest_age = None, -1
    for t in pool:
        st = (ledger or {}).get(t) or {}
        ls = st.get("last_slot")
        age = (slot - int(ls)) if ls is not None else 10 ** 9
        if age > stalest_age:
            stalest, stalest_age = t, age
    if stalest is not None and stalest_age > STALE_SLOTS:
        return stalest
    rng = random.Random(slot)
    if rng.random() < epsilon:
        return rng.choice(pool)
    best, best_v = None, -1.0
    for t in pool:
        st = (ledger or {}).get(t) or {"a": 1, "b": 1}
        v = rng.betavariate(max(1, int(st.get("a", 1))),
                            max(1, int(st.get("b", 1))))
        if v > best_v:
            best, best_v = t, v
    return best if best is not None else pool[0]
    best, best_v = None, -1.0
    for t in tracks:
        st = (ledger or {}).get(t) or {"a": 1, "b": 1}
        v = rng.betavariate(max(1, int(st.get("a", 1))),
                            max(1, int(st.get("b", 1))))
        if v > best_v:
            best, best_v = t, v
    return best if best is not None else tracks[0]


def observe(ledger, algo, won, slot=None):
    """Fold one binary outcome into the ledger and persist."""
    st = ledger.get(algo)
    if st is None:
        return ledger
    if won:
        st["a"] += 1
        st["wins"] += 1
    else:
        st["b"] += 1
    st["picks"] += 1
    st["last_slot"] = slot
    try:
        st["cool"] = int(slot or 0) + COOLDOWN_SLOTS
    except (ValueError, TypeError):
        pass
    save_ledger(ledger)
    return ledger


def posterior_mean(st):
    """Mean of Beta(a, b): the track's estimated win rate."""
    st = st or {"a": 1, "b": 1}
    a, b = max(1, int(st.get("a", 1))), max(1, int(st.get("b", 1)))
    return a / (a + b)


def top_tracks(ledger, tracks, n=3):
    """Top-n (algo, mean) by posterior mean, for display."""
    ranked = sorted(((t, posterior_mean((ledger or {}).get(t))) for t in tracks),
                    key=lambda kv: -kv[1])
    return ranked[:n]
