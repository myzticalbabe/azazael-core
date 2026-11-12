"""native_replay -- the dream agreement replay as a native function.

Pairing (URL/ts matching) stays in Python: it is pointer-chasing no compiler
types. Scoring is the compiled Cython kernel `_replay_cy` (built on this box
with MSVC 14.51; the pythran draft beside it cannot compile here -- its own
headers reject cl and the box has no clang-cl). The build MUST define
Py_GIL_DISABLED: without it the pragma in pyconfig.h pulls the absent
python316.lib, and any module that still links refuses to import with
SystemError on a free-threaded interpreter -- or silently re-enables the
GIL process-wide. The kernel computes nogil over malloc'd columns and
declares freethreading_compatible, so importing it never costs the process
its free threads (tools/build_native.py bottles this recipe for any .pyx).
When the compiled module is absent (fresh checkout, other envs), the same
math runs in pure Python: parity between backends is tested, never asserted.

One deliberate difference from intuition.dream: rungs aggregate only numeric
predictions. A guess with predicted=None still counts globally (legacy rule)
but has no numeric cell; dream() files it under "None". Global hits/pairs
are identical either way.
"""
from __future__ import annotations

_NATIVE_WHY = "not attempted"
_native_score = None
for _mod in ("eve_unleashed._replay_cy", "_replay_cy"):
    try:
        _native_score = __import__(_mod, fromlist=["score_pairs"]).score_pairs
        _NATIVE_WHY = "loaded " + _mod
        break
    except Exception as exc:
        # The fallback is recorded, not silent: backend() reports it, so a
        # run that silently drops to Python reads as Python-with-a-reason.
        _NATIVE_WHY = "%s: %s: %s" % (_mod, type(exc).__name__, exc)
        _native_score = None

BACKEND = "cython" if _native_score is not None else "python"


# Rung string to integer mapping (intuition.py all_rungs + commoncrawl)
RUNG_MAP = {
    "plain": 0,
    "browser-headers": 1,
    "tls-impersonation": 2,
    "render": 3,
    "commoncrawl": 4,
    "stealth": 5,
}
RUNG_REVERSE = {v: k for k, v in RUNG_MAP.items()}


def _rung_to_int(rung):
    """Convert rung string to integer, or -1 if unknown/None."""
    if rung is None:
        return -1
    if isinstance(rung, int):
        return rung
    return RUNG_MAP.get(rung, -1)


def fallback_reason() -> str:
    """Why the replay runs on the backend it runs on."""
    return _NATIVE_WHY


def pair_columns(records):
    """Parallel int columns for paired guesses/answers. Mirrors dream()."""
    guesses = [r for r in records if r.get("phase") == "fetch"
               and r.get("event") == "intuition" and r.get("url")]
    answers = [r for r in records if r.get("phase") == "fetch"
               and r.get("event") == "answer" and r.get("url")]
    by_url = {}
    for a in answers:
        by_url.setdefault(a["url"], []).append(a)
    pred_rung, pred_kind, ans_rung, ans_ok = [], [], [], []
    for g in guesses:
        later = [a for a in by_url.get(g["url"], [])
                 if a.get("ts", 0) >= g.get("ts", 0)]
        if not later:
            continue
        a = later[0]
        if g.get("guess") is None:
            pred_rung.append(-1)
            pred_kind.append(0)
        else:
            pr = g.get("predicted")
            pred_rung.append(_rung_to_int(pr))
            pred_kind.append(1 if g.get("guess") == "guess" else 2)
        ans_rung.append(_rung_to_int(a.get("rung")))
        ans_ok.append(1 if a.get("state") == "ok" else 0)
    return pred_rung, pred_kind, ans_rung, ans_ok


def py_score(pred_rung, pred_kind, ans_rung, ans_ok, n_rungs):
    """The kernel math in pure Python -- fallback and parity oracle."""
    pairs = len(pred_rung)
    hits = 0
    rung_pairs = [0] * n_rungs
    rung_hits = [0] * n_rungs
    for i in range(pairs):
        if pred_kind[i] == 0:
            won = (ans_rung[i] == pred_rung[i]) and (ans_ok[i] == 1)
        else:
            won = ((pred_kind[i] == 1) == (ans_ok[i] == 1)) and \
                (pred_rung[i] < 0 or ans_rung[i] == pred_rung[i])
        if won:
            hits += 1
        r = pred_rung[i]
        if 0 <= r < n_rungs:
            rung_pairs[r] += 1
            if won:
                rung_hits[r] += 1
    return hits, pairs, rung_pairs, rung_hits


def score_columns(pred_rung, pred_kind, ans_rung, ans_ok):
    """Score one replay; native when built, pure otherwise."""
    n_rungs = max([r for r in pred_rung if r >= 0] + [0]) + 1
    if _native_score is not None:
        try:
            out = _native_score(pred_rung, pred_kind, ans_rung,
                                ans_ok, n_rungs)
        except Exception as exc:
            global _NATIVE_WHY
            _NATIVE_WHY = "call failed (%s: %s), pure python" % (
                type(exc).__name__, exc)
        else:
            return tuple(out) + ("cython",)
    return py_score(pred_rung, pred_kind, ans_rung, ans_ok,
                    n_rungs) + ("python",)


def rung_rates(records):
    """Full replay: global rate plus per-rung cells (numeric rungs only)."""
    cols = pair_columns(records)
    hits, pairs, rung_pairs, rung_hits, backend = score_columns(*cols)
    by_rung = {}
    for r, (p, h) in enumerate(zip(rung_pairs, rung_hits)):
        if p:
            by_rung[str(r)] = {"pairs": p, "hits": h,
                               "rate": round(h / p, 3)}
    return {"pairs": pairs, "hits": hits,
            "hit_rate": round(hits / pairs, 3) if pairs else 0.0,
            "by_rung": by_rung, "backend": backend}
