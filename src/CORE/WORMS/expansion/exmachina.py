# coding: utf-8
"""
EX MACHINA — the epistemic tier.

PROTEUS optimises better. Ex Machina decides whether to BELIEVE it.

That distinction is not rhetorical. On 2026-09-11 the pipeline beat its frozen
pool by +2.91..+3.39 on all 50 cycles and SPRT promoted 50/50, while the fresh
holdout admitted 0 of 4. The optimiser was working perfectly and the system had
no organ capable of noticing it was being gamed by its own fitness function.

Every module here is stdlib-only, because this tier must run unchanged in both
interpreters the project uses — the free-threaded 3.15t production runner and the
3.15 GIL sidecar — where a C-extension may or may not load. A technique that
needs a C-extension cannot live in this tier.

Implemented now (all ten run today, on real artifacts):
  SPECULUM  reward-hacking detection      zlib + statistics
  FULCRUM   compression-based novelty     zlib (NCD)
  TESTUDO   conformal calibration         order statistics
  VIGIL     anytime-valid evidence        e-process / test martingale
  FILUM     degeneracy + memory           permutation entropy + DFA
  PERENNIS  era invariance (ICP-flavoured) robust slope stability
  COMPAGES  long-run link margin<->check  Engle-Granger cointegration
  BREVIS    description-length budget     two-part MDL
  EMERGON   causal emergence              effective information
  MUTAGEN   genome selection (the ledger) binomial tail + producer resolution

The operator tier (RADIX, CATENA, NAVIS, VELA, NEXUS, OPUS, LIMEN) lives in
exmachina_operators.py: it PRODUCES and ROUTES evidence, where this module
JUDGES it. See EX_MACHINA.md for the full table and EX_MACHINA_DEEPDIVE.md
for the techniques that were considered and rejected.

The severity band (below) is the aggregate verdict of these organs: a mood
plus a 0..1 severity score, smoothed so a live panel cannot flicker.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "ORGANS",
    "SPECULUM",
    "FULCRUM",
    "TESTUDO",
    "VIGIL",
    "FILUM",
    "PERENNIS",
    "COMPAGES",
    "BREVIS",
    "EMERGON",
    "MUTAGEN",
    "detect_gaming",
    "ncd",
    "novelty_ncd",
    "conformal_quantile",
    "conformal_interval",
    "coverage_report",
    # VIGIL
    "bounded_margin",
    "EProcess",
    "e_process",
    # FILUM
    "permutation_entropy",
    "dfa",
    "vitality",
    # PERENNIS
    "theil_sen",
    "invariance_report",
    # COMPAGES
    "adf",
    "cointegration",
    # BREVIS
    "mdl_two_part",
    "mdl_compare",
    # EMERGON
    "effective_information",
    "causal_emergence",
    # MUTAGEN
    "mutagenesis",
    # numeric core shared with the operator tier
    "solve_linear",
    "matrix_inverse",
    "ridge_fit",
    # readings + report
    "row_data",
    "extract",
    "organ_readings",
    "epistemic_report",
    "feature_keys",
    # severity band
    "MOODS",
    "SEVERITY_WEIGHTS",
    "severity_vector",
    "mood_for",
    "MoodTracker",
    "heartbeat",
    "vital_signs",
    "collect_state",
    "state_paths",
]

# Every organ: (key, name, tier, thesis, breaks, live_metric)
_ORGAN_ROWS = (
    ("speculum", "SPECULUM", "implemented",
     "Detects reward hacking: objective rising while a held-out proxy falls.",
     "E9 went undetected for 50 cycles; this is that signature, automated.",
     "gaming_score"),
    ("fulcrum", "FULCRUM", "implemented",
     "Compression-based novelty (normalised compression distance).",
     "Loom's spectral ID measures spread, not INFORMATION; NCD measures entropy.",
     "novelty"),
    ("testudo", "TESTUDO", "implemented",
     "Conformal prediction: finite-sample coverage with no distributional assumption.",
     "`min_prob=0.75` and `z=1.0` in the oracle gate are asserted, not calibrated.",
     "coverage"),
    ("radix", "RADIX", "implemented",
     "Causal discovery (PC / GES / FCI) over the evidence graph.",
     "The graph audit called person-to-harm attribution the highest-leverage task.",
     "edges"),
    ("catena", "CATENA", "implemented",
     "Trust-region Bayesian optimisation over the deep ensemble.",
     "A single global surrogate is flat where it matters; TR restarts on stagnation.",
     "regions"),
    ("navis", "NAVIS", "implemented",
     "Reservoir computing (echo-state): train only the readout.",
     "Reaches beyond the tabular Q-table with no backprop and no C-extension.",
     "readout"),
    ("vela", "VELA", "implemented",
     "Hyperdimensional binding: encode (entity, relation, claim) as one vector.",
     "String-keyed evidence lookup cannot answer 'what else looks like this lie?'.",
     "binds"),
    ("nexus", "NEXUS", "implemented",
     "Sparse mixture-of-experts routing across evidence types.",
     "Raises capacity inside the 8 GB shared budget by computing only top-k.",
     "experts"),
    ("opus", "OPUS", "implemented",
     "Kolmogorov-Arnold (spline-edge) backbone as an Oracle alternative.",
     "Higher expressivity per parameter than an MLP at equal memory.",
     "splines"),
    ("limen", "LIMEN", "implemented",
     "Active learning: rank missing documents by expected information gain.",
     "Outreach is currently ordered by narrative, not by information value.",
     "queue"),
    ("vigil", "VIGIL", "implemented",
     "Anytime-valid evidence: a test martingale (e-process) for 'has it improved'.",
     "SPRT is a 1945 fixed-sample test; we peek every cycle, so its error rate is not controlled.",
     "belief_bits"),
    ("filum", "FILUM", "implemented",
     "Degeneracy and memory of the outcome series (permutation entropy + DFA).",
     "Loom measures spread of genomes, not whether the search is still finding anything new.",
     "vitality"),
    ("perennis", "PERENNIS", "implemented",
     "Era invariance: which predictors keep the same slope in every pool era.",
     "E9 is precisely a non-invariant fitness-to-quality relation; a gate needs invariant inputs.",
     "invariant_fraction"),
    ("compages", "COMPAGES", "implemented",
     "Cointegration: is there any long-run link between margin and the independent check?",
     "A margin that trends up while the holdout never moves is non-cointegrated, by definition.",
     "tstat"),
    ("brevis", "BREVIS", "implemented",
     "Two-part MDL: bits to state the genome plus bits to state the residuals.",
     "'Fitness up' is not free evidence if the description length grew to buy it.",
     "bits_per_point"),
    ("emergon", "EMERGON", "implemented",
     "Causal emergence: effective information of the macro grain minus the micro grain.",
     "The organs may be most legible grouped (e.g. belief-organs vs measure-organs).",
     "emergence_bits"),
    ("mutagen", "MUTAGEN", "implemented",
     "Genome selection: did the genome GROW, or was it given away? A structural",
     "mutation can be retained on a trial whose added gene was switched off, so the",
     "inert_share"),
)

ORGANS: Tuple[dict, ...] = tuple(
    {"key": k, "name": n, "tier": t, "thesis": th, "breaks": b, "metric": m}
    for (k, n, t, th, b, m) in _ORGAN_ROWS
)

SPECULUM, FULCRUM, TESTUDO = "speculum", "fulcrum", "testudo"
VIGIL, FILUM, PERENNIS = "vigil", "filum", "perennis"
COMPAGES, BREVIS, EMERGON = "compages", "brevis", "emergon"
MUTAGEN = "mutagen"


# ══════════════════════════════════════════════════════════════════════
# SPECULUM — reward-hacking detector
# ══════════════════════════════════════════════════════════════════════

def detect_gaming(rows: Iterable[Any], window: int = 40,
                  margin_floor: float = 1.0) -> Dict[str, Any]:
    """Look for the E9 signature: margin up, holdout still zero.

    The definition matters, so it is stated rather than implied. Gaming is not
    "the score is high". It is **the score rising while the independent check
    stays at zero** — exactly the state the 50-run batch sat in for 50 cycles
    while SPRT reported unanimous promotion.

    `gaming_score` = fraction of in-window cycles that cleared `margin_floor`
    in an era where the fresh holdout never once passed.
    """
    margins: List[float] = []
    holdout_attempts = 0
    holdout_passes = 0
    cooldowns = 0
    eras: Dict[str, int] = {}

    for row in list(rows or [])[-int(window):]:
        data = row
        if isinstance(row, dict) and "data" in row:
            data = row.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception as exc:
                    note_failure("exmachina:L221", exc)
                    continue
        if not isinstance(data, dict):
            continue
        era = str(data.get("pool_id") or "unknown")
        eras[era] = eras.get(era, 0) + 1
        try:
            m = data.get("margin")
            if m is None:
                bt: Any = data.get("best_total")
                bl: Any = data.get("baseline")
                m = float(bt) - float(bl)
            if m is not None and math.isfinite(float(m)):
                margins.append(float(m))
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L233", exc)
            pass
        h = data.get("holdout")
        if isinstance(h, dict):
            if h.get("cooldown"):
                cooldowns += 1
            elif "passed" in h:
                holdout_attempts += 1
                if h.get("passed"):
                    holdout_passes += 1

    n = len(margins)
    if n == 0:
        return {"gaming_score": 0.0, "n": 0, "verdict": "no_data"}

    mean_m = sum(margins) / n
    var_m = sum((x - mean_m) ** 2 for x in margins) / n
    cleared = sum(1 for m in margins if m > float(margin_floor))

    if holdout_attempts == 0 and cleared > 0:
        score = float(cleared) / n
        verdict = "GAMING" if score >= 0.5 else "suspicious"
    elif holdout_attempts > 0:
        # Attempts with passes are healthy; attempts with none is still a flag.
        score = float(cleared) / n * (0.0 if holdout_passes else 0.5)
        verdict = "gaming" if (holdout_passes == 0 and score >= 0.25) else "ok"
    else:
        score = 0.0
        verdict = "ok"

    return {
        "gaming_score": round(score, 4),
        "verdict": verdict,
        "n": n,
        "mean_margin": round(mean_m, 4),
        "sd_margin": round(math.sqrt(var_m), 4),
        "cleared_floor": cleared,
        "margin_floor": float(margin_floor),
        "holdout_attempts": holdout_attempts,
        "holdout_passes": holdout_passes,
        "cooldowns": cooldowns,
        "eras": len(eras),
    }


# ══════════════════════════════════════════════════════════════════════
# FULCRUM — compression-based novelty
# ══════════════════════════════════════════════════════════════════════

def _as_bytes(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode("utf-8", "replace")
    try:
        return json.dumps(x, sort_keys=True, default=str).encode("utf-8", "replace")
    except Exception as exc:
        note_failure("exmachina:L289", exc)
        return str(x).encode("utf-8", "replace")


def ncd(a: Any, b: Any) -> float:
    """Normalised compression distance in [0, ~1.2]. No dependencies but zlib.

    NCD is the practical stand-in for Kolmogorov complexity: if the two objects
    share structure, the concatenation compresses nearly as well as the smaller
    alone, and the distance approaches 0. Genuinely novel structure cannot be
    predicted from the other, so the distance approaches 1.
    """
    x, y = _as_bytes(a), _as_bytes(b)
    if not x and not y:
        return 0.0
    cx = len(zlib.compress(x, 6))
    cy = len(zlib.compress(y, 6))
    cxy = len(zlib.compress(x + y, 6))
    denom = max(cx, cy)
    if denom <= 0:
        return 0.0
    return max(0.0, float(cxy - min(cx, cy)) / float(denom))


def novelty_ncd(candidate: Any, archive: Sequence[Any],
                limit: int = 64) -> Dict[str, Any]:
    """Distance to the NEAREST archive member — high means genuinely new.

    Nearest, not mean: a single near-duplicate is enough to make a candidate
    redundant, and averaging would hide that behind unrelated far members.
    """
    members = list(archive or [])[:int(limit)]
    if not members:
        return {"novelty": 1.0, "nearest_index": None, "n_archive": 0}
    best_d, best_i = None, None
    for i, m in enumerate(members):
        try:
            d = ncd(candidate, m)
        except Exception as exc:
            note_failure("exmachina:L327", exc)
            continue
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    if best_d is None:
        return {"novelty": 1.0, "nearest_index": None, "n_archive": len(members)}
    return {"novelty": round(float(best_d), 4), "nearest_index": best_i,
            "n_archive": len(members)}


# ══════════════════════════════════════════════════════════════════════
# TESTUDO — conformal calibration
# ══════════════════════════════════════════════════════════════════════

def conformal_quantile(scores: Sequence[float], alpha: float = 0.1) -> float:
    """Split-conformal quantile with the finite-sample correction.

    Returns the `ceil((n+1)(1-alpha))/n` empirical quantile of nonconformity
    scores. The `+1` is the whole point: it is what makes the coverage
    guarantee hold for finite n instead of only asymptotically.
    """
    vals = sorted(float(s) for s in (scores or []) if math.isfinite(float(s)))
    n = len(vals)
    if n == 0:
        return float("inf")
    k = int(math.ceil((n + 1) * (1.0 - float(alpha))))
    if k > n:
        return float("inf")          # too little data to promise that coverage
    return vals[k - 1]


def conformal_interval(prediction: float, scores: Sequence[float],
                       alpha: float = 0.1) -> Tuple[float, float, float]:
    """Symmetric conformal interval around `prediction`. Returns (lo, hi, q)."""
    q = conformal_quantile(scores, alpha)
    if not math.isfinite(q):
        return (float("-inf"), float("inf"), q)
    return (float(prediction) - q, float(prediction) + q, q)


def coverage_report(residuals: Sequence[float], alpha: float = 0.1,
                    splits: int = 3) -> Dict[str, Any]:
    """Empirical coverage of the conformal procedure on held-out residuals.

    Reports what the gate currently only ASSERTS. If the achieved coverage is
    below `1-alpha`, the interval is not calibrated and any threshold derived
    from it is not trustworthy.
    """
    vals = [float(r) for r in (residuals or []) if math.isfinite(float(r))]
    n = len(vals)
    if n < 4:
        return {"n": n, "target": 1.0 - float(alpha), "achieved": None,
                "calibrated": None}
    cut = max(1, n // max(2, int(splits)))
    cal, test = vals[:cut], vals[cut:]
    if not test:
        return {"n": n, "target": 1.0 - float(alpha), "achieved": None,
                "calibrated": None}
    q = conformal_quantile(cal, alpha)
    if not math.isfinite(q):
        return {"n": n, "target": 1.0 - float(alpha), "achieved": None,
                "calibrated": None, "q": q}
    inside = sum(1 for r in test if abs(r) <= q)
    achieved = inside / float(len(test))
    return {
        "n": n, "n_cal": len(cal), "n_test": len(test),
        "target": round(1.0 - float(alpha), 4),
        "achieved": round(achieved, 4),
        "q": round(q, 4),
        "calibrated": bool(achieved >= (1.0 - float(alpha)) - 0.05),
    }


# ══════════════════════════════════════════════════════════════════════
# Live state collection (stdlib only, safe for a 5s refresh loop)
# ══════════════════════════════════════════════════════════════════════

def state_paths(root: str) -> Dict[str, str]:
    st = os.path.join(root, "state")
    return {
        "oracle_json": os.path.join(st, "fitness_oracle.json"),
        "oracle_last": os.path.join(st, "fitness_oracle.last.json"),
        # NOTE: the cycle writes `proteus_circulation.json`; `proteus_allocator.json`
        # is a path NO producer writes. EX_MACHINA.md 5.7 fixed this in the VIEWER
        # only, so this module-level map kept reporting the allocator absent.
        "allocator": os.path.join(st, "proteus_circulation.json"),
        "qd_archive": os.path.join(st, "qd_archive.json"),
        # The cycle writes `state/proteus_morph.jsonl`; `logs/morph_history.jsonl`
        # is the other half of the same stale-path defect (see the note above).
        "morph_history": os.path.join(st, "proteus_morph.jsonl"),
        "promote": os.path.join(st, "evolved_rl_params.json"),
        "tensor_genome": os.path.join(st, "tensor_genome.json"),
        "mutation": os.path.join(st, "mutation_summary.json"),
    }


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        note_failure("exmachina:L427", exc)
        return None


def _read_jsonl(path: str, limit: int = 400) -> List[dict]:
    out: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception as exc:
                    note_failure("exmachina:L441", exc)
                    continue
    except Exception as exc:
        note_failure("exmachina:L443", exc)
        return []
    return out[-int(limit):]


def collect_state(root: str, history_rows: Optional[Iterable[Any]] = None,
                  width: int = 100,
                  ages: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the Ex Machina wallboard renders, from real artifacts only.

    Never raises: the monitor must keep painting even when a subsystem is
    missing or mid-write.
    """
    p = state_paths(root)
    out: Dict[str, Any] = {"root": root, "organs": {}}

    # PROTEUS / Oracle
    oj = _read_json(p["oracle_json"])
    last = _read_json(p["oracle_last"])
    if oj:
        out["oracle"] = {
            "rows": oj.get("n_rows"),
            "eras": len(oj.get("eras") or []),
            "models": len(oj.get("models") or []),
            "trained_at": oj.get("trained_at"),
            "loss_mean": oj.get("loss_mean"),
            "bytes": os.path.getsize(p["oracle_json"]) if os.path.exists(p["oracle_json"]) else None,
        }
    if last:
        out["oracle_last"] = last

    # Circulation
    al = _read_json(p["allocator"])
    if al:
        s, f = al.get("s") or {}, al.get("f") or {}
        w = {}
        for arm in (al.get("arms") or []):
            sv, fv = float(s.get(arm, 1.0)), float(f.get(arm, 1.0))
            w[arm] = round(sv / (sv + fv), 3) if (sv + fv) > 0 else 0.0
        out["circulation"] = {"weights": w,
                              "pulls": al.get("pulls") or {},
                              "wins": al.get("wins") or {}}

    # Morph — the mutation ledger, plus the two artifacts MUTAGEN fuses with it:
    # the genome's per-gene usefulness (genetic load) and the test suite's own
    # mutational evidence. Read here so the module-level `collect_state()` gives
    # the same reading as the viewer.
    mrows = _read_jsonl(p["morph_history"])
    genome = _read_json(p["tensor_genome"])
    mutation = _read_json(p["mutation"])
    if mrows:
        by_dim: Dict[int, List[float]] = {}
        for r in mrows:
            try:
                if r.get("fitness") is None:
                    continue
                by_dim.setdefault(int(r.get("dimension", 0)), []).append(float(r["fitness"]))
            except (TypeError, ValueError) as exc:
                note_failure("exmachina:L500", exc)
                continue
        out["morph"] = {
            "n": len(mrows),
            "dims": sorted(by_dim),
            "best_by_dim": {str(k): round(max(v), 4) for k, v in sorted(by_dim.items())},
        }

    # Loom (archive shape) — descriptors FROM the persisted QD archive
    qd = _read_json(p["qd_archive"])
    if isinstance(qd, dict):
        cells = qd.get("cells")
        n_cells = len(cells) if isinstance(cells, (list, dict)) else None
        out["archive"] = {"cells": n_cells}

    # Speculum (the one organ that reports a judgement)
    if history_rows is not None:
        try:
            out["organs"]["speculum"] = detect_gaming(history_rows)
        except Exception as exc:
            note_failure("exmachina:L519", exc)
            pass
    # All organs, one pass, plus the aggregate band. `history_rows` is the
    # only input: no organ may require a second query, or the panel would
    # disagree with itself about which cycles it is describing.
    if history_rows is not None:
        try:
            report = epistemic_report(history_rows, ages=ages, morph_rows=mrows,
                                      genome=genome, mutation=mutation)
            out["readings"] = report["readings"]
            out["severity"] = report["severity"]
            out["mood"] = report["mood"]
            if report["readings"].get("speculum") is not None:
                out["organs"]["speculum"] = report["readings"]["speculum"]
        except Exception as exc:
            note_failure("exmachina:L533", exc)
            out["report_error"] = "%s: %s" % (type(exc).__name__, exc)
    if "organs" not in out:
        out["organs"] = {}
    return out


# ══════════════════════════════════════════════════════════════════════
# VIGIL — anytime-valid evidence (e-process / test martingale)
#
# Why this exists. SPRT is a fixed-sample test from 1945: its error rate is
# only controlled if you look at the data ONCE, at a pre-declared n. The
# pipeline looks every cycle and stops when it likes, which is optional
# stopping. Under optional stopping a p-value is not a p-value, and the
# 50/50 promotion run is exactly the shape of that failure.
#
# An e-process fixes it with no distributional assumption at all: a
# nonnegative supermartingale with expectation <= 1 under H0, so Ville's
# inequality gives P(ever crosses 1/alpha) <= alpha. It is valid at EVERY
# stopping time, which is the property a monitor needs.
#
# The betting construction (Waudby-Smith & Ramdas) is simple enough to keep
# stdlib: for y in [lo, hi] and H0 "E[y] <= mu0", each factor
#     1 + lambda * (y - mu0)
# is nonnegative for lambda <= 1/(mu0 - lo) and has expectation <= 1 under
# H0, so the product is a supermartingale. Averaging over a geometric grid of
# lambdas ("hedged capital") tracks the best lambda without knowing it in
# advance, which is what makes the growth rate near-optimal.
# ══════════════════════════════════════════════════════════════════════

def bounded_margin(margin: float, bound: float = 5.0) -> float:
    """Map a signed margin onto [0, 1] with 0.5 = "no improvement".

    The map has to be BOUNDED for the betting argument to hold, so extremes
    are clipped. That is a real loss of information at the tails, and it is
    the price of the guarantee; the raw margin stays available alongside.
    """
    b = max(1e-9, float(bound))
    y = 0.5 + float(margin) / (2.0 * b)
    return min(1.0, max(0.0, y))


class EProcess:
    """Anytime-valid e-process for H0: E[y] <= mu0 (y bounded in [lo, hi]).

    `wealth() >= 1/alpha` is a rejection of H0 with type-I error <= alpha at
    any stopping time. `log2(wealth)` is the evidence in BITS against H0.
    """

    __slots__ = ("mu0", "alpha", "lo", "hi", "lambdas", "_logw", "n")

    def __init__(self, mu0: float = 0.5, alpha: float = 0.05,
                 lo: float = 0.0, hi: float = 1.0, grid: int = 20):
        self.mu0 = float(mu0)
        self.alpha = float(alpha)
        self.lo, self.hi = float(lo), float(hi)
        self.n = 0
        span = self.mu0 - self.lo
        lam_max = (1.0 / span) if span > 0 else 1.0
        # Geometric grid: the halving ladder is what lets the mixture track
        # the growth-optimal lambda as evidence accumulates.
        self.lambdas = [lam_max * (0.5 ** k) for k in range(max(1, int(grid)))]
        self._logw = [0.0] * len(self.lambdas)

    def update(self, y: float) -> float:
        """Fold in one bounded observation; returns the current wealth."""
        v = min(self.hi, max(self.lo, float(y)))
        for i, lam in enumerate(self.lambdas):
            if self._logw[i] == float("-inf"):
                continue
            factor = 1.0 + lam * (v - self.mu0)
            if factor <= 0.0:
                # Cannot happen for lambda <= 1/(mu0-lo) and y >= lo, but a
                # zero/negative factor would poison the log-sum-exp, so it is
                # made absorbing rather than left to produce a nan.
                self._logw[i] = float("-inf")
            else:
                self._logw[i] += math.log(factor)
        self.n += 1
        return self.wealth()

    def wealth(self) -> float:
        """Mixture capital: mean over lambdas of the individual capitals.

        Computed through logs; the naive product of 50 factors underflows to
        0.0 on ordinary data and would have reported "no evidence" forever.
        """
        live = [v for v in self._logw if v != float("-inf")]
        if not live:
            return 0.0
        m = max(live)
        s = sum(math.exp(v - m) for v in live)
        return float(math.exp(m + math.log(s / float(len(self.lambdas)))))

    def belief_bits(self) -> float:
        """Evidence against H0 in bits (log2 of wealth)."""
        w = self.wealth()
        return float(math.log2(w)) if w > 0 else float("-inf")

    def rejected(self, alpha: Optional[float] = None) -> bool:
        a = self.alpha if alpha is None else float(alpha)
        return self.wealth() >= (1.0 / a)

    def threshold(self) -> float:
        return 1.0 / self.alpha


def e_process(y_values: Iterable[float], mu0: float = 0.5,
              alpha: float = 0.05, lo: float = 0.0, hi: float = 1.0,
              grid: int = 20) -> Dict[str, Any]:
    """Run the e-process over a sequence and report the standing verdict."""
    vals = [float(v) for v in (y_values or []) if v is not None]
    ep = EProcess(mu0=mu0, alpha=alpha, lo=lo, hi=hi, grid=grid)
    for v in vals:
        ep.update(v)
    bits = ep.belief_bits()
    full = math.log2(ep.threshold())
    return {
        "n": ep.n,
        "wealth": round(ep.wealth(), 4),
        "belief_bits": None if not math.isfinite(bits) else round(bits, 3),
        "rejected": ep.rejected(),
        "threshold": round(ep.threshold(), 2),
        "belief": 0.0 if not math.isfinite(bits) else round(min(1.0, max(0.0, bits / full)), 3),
        "mu0": mu0,
        "alpha": alpha,
    }


# ══════════════════════════════════════════════════════════════════════
# FILUM — is the system still alive?
#
# Two cheap, assumption-free questions about the outcome series:
#   permutation entropy (Bandt-Pompe)  how many distinct ORDINAL patterns
#                                      occur -> 0 means a loop, 1 means
#                                      maximally varied orderings
#   DFA                                the scaling exponent alpha: ~0.5
#                                      uncorrelated, ~1 long-range 1/f
#
# Half-a-day of watching a search sit at gen 6 with an identical genome is
# the symptom; permutation entropy is the measurement. It reads 0 for a
# constant-strength loop and stays high when the search is genuinely moving,
# which is what a "vitality" number should do.
# ══════════════════════════════════════════════════════════════════════

def row_data(row: Any) -> Optional[dict]:
    """Unwrap one stored outcome row to its payload dict (or None).

    Rows arrive in three shapes depending on the reader: a raw payload, a
    `{"data": <json string>}` record from SQLite, or an already-decoded dict.
    Every organ below goes through here so no two of them disagree about what
    a row is.
    """
    d = row
    if isinstance(row, (list, tuple)) and row:
        d = row[-1]
    if isinstance(d, dict) and "data" in d and not isinstance(d.get("data"), dict):
        raw = d.get("data")
        if isinstance(raw, str):
            try:
                d = json.loads(raw)
            except Exception as exc:
                note_failure("exmachina:L694", exc)
                return None
        else:
            return None
    return d if isinstance(d, dict) else None


def _margin_of(d: dict[str, Any]) -> Optional[float]:
    """The margin, derived from the objective when it is not stored."""
    try:
        m = d.get("margin")
        if m is None:
            bt: Any = d.get("best_total")
            bl: Any = d.get("baseline")
            m = float(bt) - float(bl)
        m = float(m)
    except (TypeError, ValueError) as exc:
        note_failure("exmachina:L708", exc)
        return None
    return m if math.isfinite(m) else None


def extract(rows: Iterable[Any], field: str = "margin") -> List[float]:
    """One numeric field across rows; 'margin' is derived when absent."""
    out: List[float] = []
    for r in (rows or []):
        d = row_data(r)
        if d is None:
            continue
        v = _margin_of(d) if field == "margin" else d.get(field)
        try:
            if v is None:
                continue
            f = float(v)
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L725", exc)
            continue
        if math.isfinite(f):
            out.append(f)
    return out


_GENE_KEYS = ("learning_rate", "lr", "epsilon", "discount", "discount_factor",
              "epsilon_decay", "use_schema_features")
_NESTED_GENES = ("best_genes", "genes", "genome", "params")
_NON_FEATURE = {"margin", "best_total", "baseline", "pool_id", "era", "timestamp",
                "promoted", "sprt", "holdout", "data", "fitness", "total", "n",
                "generation", "gens", "dimension", "action", "best_genes",
                "genes", "genome", "params"}


def _flat_row(d: dict) -> Dict[str, Any]:
    """A row plus its nested genome, so genes are visible wherever they live.

    The production row stores genes under `best_genes`; Morph-era rows may put
    them at the top level. Flattening here means no organ has to know which.
    """
    flat = dict(d)
    for k in _NESTED_GENES:
        nested = d.get(k)
        if isinstance(nested, dict):
            for kk, vv in nested.items():
                flat.setdefault(kk, vv)
    return flat


def feature_keys(rows: Iterable[Any], min_frac: float = 0.5) -> List[str]:
    """Numeric candidate predictors, auto-detected from what rows actually carry.

    Hard-coding the gene names would silently drop anything Morph adds 
    a structural gene the search can move but the invariance screen cannot see
    is worse than no screen at all.
    """
    seen: Dict[str, int] = {}
    total = 0
    for r in (rows or []):
        d = row_data(r)
        if d is None:
            continue
        total += 1
        flat = _flat_row(d)
        for k, v in flat.items():
            if k in _NON_FEATURE:
                continue
            try:
                if v is None or isinstance(v, bool):
                    continue
                f = float(v)
            except (TypeError, ValueError) as exc:
                note_failure("exmachina:L778", exc)
                continue
            if math.isfinite(f):
                seen[k] = seen.get(k, 0) + 1
    if not total:
        return []
    need = max(2, int(total * float(min_frac)))
    keys = [k for k, c in seen.items() if c >= need]
    # Gene-ish names first: they are the ones a gate is likely to rely on.
    return sorted(keys, key=lambda k: (0 if k in _GENE_KEYS else 1, k))


def _finite(xs: Iterable[Any]) -> List[float]:
    out: List[float] = []
    for x in (xs or []):
        try:
            v = float(x)
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L795", exc)
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def permutation_entropy(series: Iterable[float], order: int = 3,
                        delay: int = 1) -> Dict[str, Any]:
    """Bandt-Pompe permutation entropy, normalised to [0, 1].

    Ties are broken by index so the ordinal pattern is always well defined;
    the alternative (skipping tied windows) throws away exactly the repeated
    values that a stalled search produces.
    """
    vals = _finite(series)
    order, delay = int(order), int(delay)
    n = len(vals)
    span = (order - 1) * delay
    if order < 2 or delay < 1 or n <= span:
        return {"n": n, "order": order, "pe": None, "pe_norm": None,
                "patterns": 0, "degenerate": None}
    counts: Dict[Tuple[int, ...], int] = {}
    for i in range(n - span):
        window = [vals[i + k * delay] for k in range(order)]
        pat = tuple(sorted(range(order), key=lambda k: (window[k], k)))
        counts[pat] = counts.get(pat, 0) + 1
    total = float(sum(counts.values()))
    pe = -sum((c / total) * math.log2(c / total) for c in counts.values())
    pe_max = math.log2(float(math.factorial(order)))
    norm = (pe / pe_max) if pe_max > 0 else 0.0
    return {"n": n, "order": order, "pe": round(pe, 4),
            "pe_max": round(pe_max, 4), "pe_norm": round(norm, 4),
            "patterns": len(counts),
            "degenerate": bool(norm < 0.5)}


def _linfit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Least-squares slope/intercept plus R^2. Returns (slope, intercept, r2)."""
    n = len(xs)
    if n < 2:
        return (0.0, ys[0] if ys else 0.0, 0.0)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return (0.0, my, 0.0)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    slope = sxy / sxx
    inter = my - slope * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((ys[i] - (slope * xs[i] + inter)) ** 2 for i in range(n))
    r2 = (1.0 - sse / sst) if sst > 0 else 0.0
    return (slope, inter, r2)


def dfa(series: Iterable[float], scales: Optional[Sequence[int]] = None,
        min_scale: int = 4) -> Dict[str, Any]:
    """Detrended fluctuation analysis -> scaling exponent alpha.

    ~0.5 is uncorrelated (white noise), ~1.0 is long-range correlated (1/f),
    >1.5 is a nonstationary drift. A pure climb shows up as a high alpha.
    """
    vals = _finite(series)
    n = len(vals)
    if n < 8:
        return {"n": n, "alpha": None, "r2": None, "scales": []}
    mean = sum(vals) / n
    prof, acc = [], 0.0
    for v in vals:
        acc += (v - mean)
        prof.append(acc)
    if scales:
        sc = [int(s) for s in scales if 4 <= int(s) <= n // 2]
    else:
        sc, s = [], max(int(min_scale), 4)
        while s <= n // 2:
            sc.append(s)
            s = int(round(s * 1.5))
        if not sc:
            sc = [n // 2] if n // 2 >= 4 else []
    logs_x, logs_y, used = [], [], []
    for s in sc:
        nwin = n // s
        if nwin < 1:
            continue
        sq = 0.0
        cnt = 0
        for w in range(nwin):
            seg = prof[w * s:(w + 1) * s]
            xs = list(range(len(seg)))
            slope, inter, _ = _linfit(xs, seg)
            for i, y in enumerate(seg):
                r = y - (slope * i + inter)
                sq += r * r
                cnt += 1
        if cnt == 0 or sq <= 0:
            continue
        f = math.sqrt(sq / cnt)
        if f > 0:
            logs_x.append(math.log(s))
            logs_y.append(math.log(f))
            used.append(s)
    if len(logs_x) < 2:
        return {"n": n, "alpha": None, "r2": None, "scales": used}
    slope, _inter, r2 = _linfit(logs_x, logs_y)
    return {"n": n, "alpha": round(slope, 4), "r2": round(r2, 4),
            "scales": used}


def vitality(rows: Iterable[Any], field: str = "margin", order: int = 3) -> Dict[str, Any]:
    """The system's aliveness: novelty of orderings plus memory of the series."""
    vals = _finite(extract(rows, field))
    pe = permutation_entropy(vals, order=order)
    df = dfa(vals)
    v = pe.get("pe_norm")
    if v is None:
        state = "unknown"
    elif v < 0.5:
        state = "looping"
    elif v < 0.8:
        state = "settling"
    else:
        state = "exploring"
    return {"vitality": v, "state": state, "n": pe.get("n"),
            "pe": pe.get("pe"), "patterns": pe.get("patterns"),
            "alpha": df.get("alpha"), "alpha_r2": df.get("r2"),
            "degenerate": pe.get("degenerate")}


# ══════════════════════════════════════════════════════════════════════
# PERENNIS — era invariance
#
# Invariant causal prediction asks a sharper question than "does it fit":
# which inputs keep the SAME relationship to the outcome in every era? A
# predictor whose slope moves between pools cannot transport, and a gate
# built on one is unsound even when every era in isolation looks fine.
#
# Theil-Sen slopes (median of pairwise slopes) rather than least squares:
# one heavy-tail era would otherwise dominate a fit made of forty points.
#
# This is a FILTER, not a proof. Exact ICP is exponential in the feature set
# and would be an over-claim; the honest claim is "this screen is stable
# across the eras observed so far", with the instability number exposed so a
# reader can disagree with the threshold.
# ══════════════════════════════════════════════════════════════════════

def _median(vals: Sequence[float]) -> float:
    s = sorted(float(v) for v in vals)
    if not s:
        return float("nan")
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _mad(vals: Sequence[float]) -> float:
    """Median absolute deviation from the median (robust scale, no numpy)."""
    if not vals:
        return 0.0
    med = _median(vals)
    return _median([abs(float(v) - med) for v in vals])


def theil_sen(xs: Iterable[float], ys: Iterable[float]) -> Optional[float]:
    """Median of all pairwise slopes; None when no pair has distinct x."""
    x = _finite(xs)
    y = _finite(ys)
    n = min(len(x), len(y))
    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[j] - x[i]
            if dx != 0:
                slopes.append((y[j] - y[i]) / dx)
    return None if not slopes else float(_median(slopes))


def invariance_report(rows: Iterable[Any], feature_keys: Sequence[str],
                      target: str = "margin", era_key: str = "pool_id",
                      min_per_era: int = 4, threshold: float = 0.5) -> Dict[str, Any]:
    """Per-feature era stability. instability in [0,1], higher = less invariant."""
    recs: List[dict] = []
    for r in (rows or []):
        d = row_data(r)
        if isinstance(d, dict):
            recs.append(d)
    eras: Dict[str, List[dict]] = {}
    for d in recs:
        eras.setdefault(str(d.get(era_key) or "unknown"), []).append(d)
    usable = {k: v for k, v in eras.items() if len(v) >= int(min_per_era)}
    out: Dict[str, Any] = {"features": {}, "eras": len(usable),
                           "n": sum(len(v) for v in usable.values()),
                           "threshold": threshold}
    if len(usable) < 2:
        out["invariant_fraction"] = None
        out["note"] = "need >=2 eras with >=%d rows" % int(min_per_era)
        return out
    for key in feature_keys:
        slopes: List[float] = []
        for _era, rows_e in usable.items():
            xs: List[Any] = [d.get(key) for d in rows_e]
            ys: List[Any] = [d.get(target) for d in rows_e]
            s = theil_sen(xs, ys)
            if s is not None and math.isfinite(s):
                slopes.append(s)
        if len(slopes) < 2:
            continue
        med = _median(slopes)
        # MAX deviation from the median, not the MAD. The median is robust,
        # which is exactly the wrong property here: with slopes [2, -2, 2] the
        # MAD is 0, so a single era that REVERSES the sign would screen as
        # perfectly invariant. The screen is therefore deliberately sensitive
        # -- a false flag costs a second look, a miss costs an unsound gate.
        spread = max(abs(s - med) for s in slopes)
        # Bounded, so it can feed the severity vector directly. All-equal
        # slopes give 0; sign-flipping slopes approach 1.
        inst = spread / (abs(med) + spread + 1e-12)
        out["features"][key] = {
            "slope": round(med, 6),
            "instability": round(inst, 4),
            "eras": len(slopes),
            "invariant": bool(inst <= float(threshold)),
        }
    if out["features"]:
        inv = sum(1 for f in out["features"].values() if f["invariant"])
        out["invariant_fraction"] = round(inv / float(len(out["features"])), 4)
    else:
        out["invariant_fraction"] = None
    return out


# ══════════════════════════════════════════════════════════════════════
# COMPAGES — is there any long-run link between the objective and the check?
#
# The E9 shape, stated as a time-series claim: margin trends up while the
# independent holdout never moves. Two series like that are NOT cointegrated
# -- there is no linear combination of them that is stationary -- and that is
# a stronger statement than "the holdout was unlucky".
#
# Engle-Granger two-step: regress the check on the objective, then ADF-test
# the residual for a unit root. The critical values below are the standard
# EG 2-variable, constant-no-trend table (MacKinnon-style), interpolated
# coarsely on n. They are approximations and are labelled as such: the
# t-statistic is exposed so a caller can apply its own threshold.
# ══════════════════════════════════════════════════════════════════════

_EG_CRIT = (
    # (n, 1%, 5%, 10%)  -- EG 2-variable, constant no trend
    (25, -4.38, -3.60, -3.24),
    (50, -4.12, -3.46, -3.13),
    (100, -4.01, -3.40, -3.09),
    (250, -3.94, -3.36, -3.06),
    (10000, -3.90, -3.34, -3.04),
)


def _eg_crit(n: int, level: float = 0.05) -> float:
    idx = 1 if abs(level - 0.01) < 1e-9 else (2 if abs(level - 0.05) < 1e-9 else 3)
    pts = [(_EG_CRIT[0][0], _EG_CRIT[0][idx])] + [(_row[0], _row[idx]) for _row in _EG_CRIT]
    if n <= pts[0][0]:
        return pts[0][1]
    for i in range(1, len(pts)):
        if n <= pts[i][0]:
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            if x1 == x0:
                return y1
            w = (n - x0) / float(x1 - x0)
            return y0 + w * (y1 - y0)
    return pts[-1][1]


def _solve(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting. None if effectively singular.

    The pivot test is RELATIVE to the matrix scale, not an absolute 1e-12. An
    absolute threshold on a badly scaled system returns a confident answer
    built from rounding noise -- which is how a cointegration test reported a
    +6.7 t-statistic on series that were exactly collinear. ``None`` is the
    honest result there: it says "no answer" instead of inventing one.
    """
    n = len(a)
    scale = max((abs(v) for row in a for v in row), default=0.0)
    tol = max(1e-12, 1e-10 * scale)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < tol:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] / pv
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / m[i][i]
    return x


def _ols_multi(X: List[List[float]], y: List[float]) -> Tuple[Optional[List[float]], List[float]]:
    """Normal-equation OLS. Returns (beta, residuals); beta is None if singular."""
    k = len(X[0]) if X else 0
    if k == 0 or len(X) <= k:
        return (None, [])
    xtx = [[sum(X[i][r] * X[i][c] for i in range(len(X))) for c in range(k)] for r in range(k)]
    xty = [sum(X[i][r] * y[i] for i in range(len(X))) for r in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return (None, [])
    resid = [y[i] - sum(X[i][r] * beta[r] for r in range(k)) for i in range(len(X))]
    return (beta, resid)


def adf(resid: Iterable[float], lags: int = 1) -> Dict[str, Any]:
    """Augmented Dickey-Fuller t-statistic on a residual series.

    Regression: d r_t = rho*r_{t-1} + sum_i phi_i d r_{t-i} + c + e.
    A strongly negative t on rho rejects a unit root, i.e. the residual is
    stationary and the two series move together in the long run.
    """
    r = _finite(resid)
    n = len(r)
    lags = max(0, int(lags))
    if n < 12 + 2 * lags:
        return {"n": n, "tstat": None, "crit_5pct": None, "stationary": None,
                "note": "need >=%d points" % (12 + 2 * lags)}
    y: List[float] = []
    X: List[List[float]] = []
    for t in range(lags + 1, n):
        row = [r[t - 1], 1.0]
        for i in range(1, lags + 1):
            row.append(r[t - i] - r[t - i - 1])
        X.append(row)
        y.append(r[t] - r[t - 1])
    beta, resid2 = _ols_multi(X, y)
    if beta is None or not resid2:
        return {"n": n, "tstat": None, "crit_5pct": _eg_crit(n), "stationary": None}
    k = len(beta)
    dof = max(1, len(y) - k)
    s2 = sum(e * e for e in resid2) / dof
    xtx = [[float(sum(X[i][rr] * X[i][cc] for i in range(len(X)))) for cc in range(k)] for rr in range(k)]
    inv = _invert(xtx)
    if inv is None:
        return {"n": n, "tstat": None, "crit_5pct": _eg_crit(n), "stationary": None}
    var_rho = s2 * inv[0][0]
    if var_rho <= 0:
        return {"n": n, "tstat": None, "crit_5pct": _eg_crit(n), "stationary": None}
    tstat = beta[0] / math.sqrt(var_rho)
    crit = _eg_crit(n)
    return {"n": n, "tstat": round(tstat, 4), "crit_5pct": round(crit, 3),
            "stationary": bool(tstat <= crit), "rho": round(beta[0], 6)}


# Public numeric core. The operator organs (exmachina_operators.py) need the
# same solver; a second copy would be a second thing to get subtly wrong.

def solve_linear(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    return _solve(a, b)


def matrix_inverse(a: List[List[float]]) -> Optional[List[List[float]]]:
    return _invert(a)


def ridge_fit(X: List[List[float]], y: List[float], lam: float = 1e-3) -> Optional[List[float]]:
    """Ridge regression via normal equations: (X'X + lam*I) b = X'y.

    The `lam` on the DIAGONAL (not a scaled identity) is deliberate: it is the
    same Tikhonov term used by the reservoir and KAN readouts, so a caller can
    reason about one number across organs.
    """
    if not X or not X[0]:
        return None
    k = len(X[0])
    if len(X) < 1:
        return None
    xtx = [[sum(X[i][r] * X[i][c] for i in range(len(X))) for c in range(k)] for r in range(k)]
    xty = [sum(X[i][r] * y[i] for i in range(len(X))) for r in range(k)]
    for r in range(k):
        xtx[r][r] += float(lam)
    return _solve(xtx, xty)


def _invert(a: List[List[float]]) -> Optional[List[List[float]]]:
    """Matrix inverse via Gauss-Jordan. None if singular."""
    n = len(a)
    m = [a[i][:] + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                f = m[r][col]
                m[r] = [m[r][c] - f * m[col][c] for c in range(2 * n)]
    return [row[n:] for row in m]


def cointegration(objective: Iterable[float], check: Iterable[float],
                  lags: int = 1) -> Dict[str, Any]:
    """Engle-Granger test between the objective and the independent check."""
    x = _finite(objective)
    y = _finite(check)
    n = min(len(x), len(y))
    if n < 16:
        return {"n": n, "cointegrated": None, "tstat": None,
                "note": "need >=16 paired points"}
    x, y = x[:n], y[:n]
    ux, uy = len(set(x)), len(set(y))
    # A series that NEVER VARIES cannot have a long-run link with anything,
    # and that is not the same thing as "unknown". The E9 shape -- objective
    # trending up, independent check nailed at zero for fifty cycles -- lands
    # here, and returning None for it would have made the single most
    # important case for this project report nothing at all.
    if ux <= 1 or uy <= 1:
        which = "objective" if ux <= 1 else "check"
        return {
            "n": n, "cointegrated": False, "tstat": None,
            "crit_5pct": _eg_crit(n), "gap": 1.0, "slope": None,
            "objective_unique": ux, "check_unique": uy,
            "degenerate": which,
            "note": "%s series took only %d distinct value(s) over %d cycles: "
                    "no long-run link can exist" % (which, min(ux, uy), n),
        }
    X = [[v, 1.0] for v in x]
    beta, resid = _ols_multi(X, y)
    if beta is None or not resid:
        return {"n": n, "cointegrated": None, "tstat": None,
                "objective_unique": ux, "check_unique": uy,
                "note": "regression was singular; no estimate"}
    a = adf(resid, lags=lags)
    tstat = a.get("tstat")
    # How far the statistic is from the 5% bar, as a bounded severity input.
    crit = a.get("crit_5pct")
    gap = None
    if tstat is not None and crit:
        gap = min(1.0, max(0.0, (crit - tstat) / abs(crit)))
    return {
        "n": n,
        "slope": round(beta[0], 6),
        "intercept": round(beta[1], 6),
        "tstat": tstat,
        "crit_5pct": crit,
        "cointegrated": a.get("stationary"),
        "gap": None if gap is None else round(gap, 4),
        "objective_unique": ux,
        "check_unique": uy,
        "note": "EG 2-var critical values are approximate; tstat is authoritative",
    }


# ══════════════════════════════════════════════════════════════════════
# BREVIS — the description-length budget
#
# Two-part MDL: L(gene) + L(residuals). Fitness can rise while the SECOND
# term falls for the wrong reason (more parameters, same data), and the total
# then rises even though the score improved. bits_per_point answers the only
# question that matters here: how many bits did the last point of fitness
# actually cost?
# ══════════════════════════════════════════════════════════════════════

_GAUSSIAN_BITS = 0.5 * math.log2(2.0 * math.pi * math.e)


def mdl_two_part(genes: Dict[str, float], bounds: Dict[str, Any],
                 residuals: Iterable[float], resolution: float = 0.01,
                 points_per_param: int = 300) -> Dict[str, Any]:
    """Bits to state the genome plus bits to state the residuals.

    `points_per_param` is the fitness cost of one added parameter, in units
    of the residual-sd code length. A structural gene that adds a dimension
    must buy back at least that many bits of fit to break even.
    """
    res = _finite(residuals)
    n = len(res)
    k = len([g for g in (genes or {}) if g in (bounds or {})]) or len(genes or {})
    l_model = 0.0
    for name, val in (genes or {}).items():
        rng = bounds.get(name) if isinstance(bounds, dict) else None
        if isinstance(rng, (list, tuple)) and len(rng) >= 2:
            span = abs(float(rng[1]) - float(rng[0]))
        else:
            span = 1.0
        l_model += math.log2(max(1e-9, span / max(1e-9, float(resolution))))
    if n >= 2:
        mean = sum(res) / n
        var = sum((v - mean) ** 2 for v in res) / n
        sd = math.sqrt(var)
    else:
        sd = 0.0
    l_data = 0.0 if sd <= 0 else n * (_GAUSSIAN_BITS + math.log2(sd))
    return {
        "bits_model": round(l_model, 4),
        "bits_data": round(l_data, 4),
        "bits_total": round(l_model + l_data, 4),
        "k": k,
        "n": n,
        "sd": round(sd, 6),
        "param_cost_bits": round(float(points_per_param) * max(0.0, sd), 4),
    }


def mdl_compare(candidate: Dict[str, float], baseline: Dict[str, float],
                bounds: Dict[str, Any], residuals: Iterable[float],
                base_residuals: Optional[Iterable[float]] = None,
                resolution: float = 0.01) -> Dict[str, Any]:
    """Is the candidate a SHORTER description of the same data, or a longer one?

    `bits_per_point` > 0 means the candidate's extra structure is being paid
    for by the objective rather than by the data. Negative means genuine gain.
    """
    cand = mdl_two_part(candidate, bounds, residuals, resolution=resolution)
    base = mdl_two_part(baseline, bounds,
                        base_residuals if base_residuals is not None else residuals,
                        resolution=resolution)
    gain = base["bits_total"] - cand["bits_total"]
    dn = max(1, abs(cand["k"] - base["k"]))
    per_point = gain / float(dn)
    return {
        "candidate": cand,
        "baseline": base,
        "gain_bits": round(gain, 4),
        "bits_per_point": round(per_point, 4),
        "d_params": cand["k"] - base["k"],
        "better": bool(gain > 0),
    }


# ══════════════════════════════════════════════════════════════════════
# EMERGON — causal emergence (effective information over grains)
#
# Hoel's effective information: with a uniform INTERVENTION on the state
# (do(X ~ U)), how much does the next state tell you about which state you
# forced? EI = (1/|S|) * sum_ij p(j|i) log2( p(j|i) / p_eff(j) ).
#
# Computed at the micro grain (every organ bit) and at macro grains (one
# organ, or a pair), the DIFFERENCE says at which abstraction the system's
# behaviour is most deterministic-and-informative. If a coarse grain carries
# more EI than the micro truth, the system is more legible coarse: report the
# grain, because that is the grain a gate should read.
#
# Cost is bounded deliberately: candidate grains are single organs and organ
# PAIRS, not all set partitions (Bell(16) is ~10^10 and would hang the
# monitor's refresh loop).
# ══════════════════════════════════════════════════════════════════════


def _tpm(states: Sequence[int], n_states: int, eps: float = 1e-6) -> List[List[float]]:
    """Row-normalised transition matrix with Laplace smoothing.

    The smoothing is what keeps log2 finite for transitions never observed;
    without it EI is undefined rather than merely uncertain.
    """
    counts = [[eps] * n_states for _ in range(n_states)]
    for a, b in zip(states[:-1], states[1:]):
        ia, ib = int(a), int(b)
        if 0 <= ia < n_states and 0 <= ib < n_states:
            counts[ia][ib] += 1.0
    tpm = []
    for row in counts:
        tot = sum(row)
        tpm.append([c / tot for c in row] if tot > 0 else row)
    return tpm


def _reachable_tpm(states: Sequence[int], eps: float = 1e-6) -> Tuple[List[List[float]], int, List[int]]:
    """Transition matrix over the states actually REACHED, with their labels.

    Restricting to the reachable support is not a detail, it is what makes the
    comparison across grains meaningful. EI averages over a uniform do over the
    state space and divides by its size, so a 16-state micro truth that only
    ever visits 2 states would score log2(16)/16-sized terms and lose to any
    coarse grain with a smaller alphabet. That would have made "macro beats
    micro" an artifact of alphabet size -- the exact opposite of the question.
    """
    seen = sorted({int(s) for s in states})
    idx = {s: i for i, s in enumerate(seen)}
    n = len(seen)
    if n == 0:
        return ([], 0, [])
    counts = [[eps] * n for _ in range(n)]
    for a, b in zip(states[:-1], states[1:]):
        ia, ib = idx.get(int(a)), idx.get(int(b))
        if ia is None or ib is None:
            continue
        counts[ia][ib] += 1.0
    tpm = []
    for row in counts:
        tot = sum(row)
        tpm.append([c / tot for c in row] if tot > 0 else row)
    return (tpm, n, seen)


def effective_information(tpm: Sequence[Sequence[float]]) -> float:
    """EI in bits under a uniform do-distribution."""
    n = len(tpm)
    if n < 2:
        return 0.0
    peff = [sum(float(tpm[i][j]) for i in range(n)) / float(n) for j in range(n)]
    ei = 0.0
    for i in range(n):
        for j in range(n):
            p = float(tpm[i][j])
            if p > 0.0 and peff[j] > 0.0:
                ei += (1.0 / n) * p * math.log2(p / peff[j])
    return ei


def causal_emergence(micro_states: Iterable[int], n_bits: int = 4,
                     include_pairs: bool = True) -> Dict[str, Any]:
    """EI at the micro grain vs candidate macro grains.

    `micro_states` are integers whose low `n_bits` bits are the organ flags,
    so every candidate grain is a bit-group and stays interpretable:
    "grouped by bit 3" is a claim about a named organ, not an abstract
    partition.
    """
    n_bits = max(1, int(n_bits))
    series = [int(s) for s in (micro_states or [])]
    if len(series) < 12:
        return {"n": len(series), "n_bits": n_bits, "ei_micro": None,
                "emergence_bits": None, "best_grain": None,
                "note": "need >=12 transitions"}
    tpm_micro, n_micro, _seen = _reachable_tpm(series)
    ei_micro = effective_information(tpm_micro)

    def macro(bits: Sequence[int]) -> Tuple[float, int, List[int]]:
        """EI of the sequence projected onto a subset of organ bits."""
        states = []
        for s in series:
            code = 0
            for idx, b in enumerate(bits):
                code |= ((s >> b) & 1) << idx
            states.append(code)
        tpm, ns, _s = _reachable_tpm(states)
        return (effective_information(tpm), ns, states)

    def macro_or(bits: Sequence[int]) -> Tuple[float, int, List[int]]:
        """EI of "is ANY of these organ flags set" -- an UNBALANCED grain.

        Balanced bit-partitions can only ever TIE with the micro grain:
        taking one bit reduces log2(n) and the accessible entropy by the same
        amount, so `EI(macro) = EI(micro)` is the ceiling. Emergence needs an
        asymmetric cut, where a small group of states carries most of the
        branching -- which is exactly what OR-grains provide.
        """
        states = [1 if any((s >> b) & 1 for b in bits) else 0 for s in series]
        tpm, ns, _s = _reachable_tpm(states)
        return (effective_information(tpm), ns, states)

    candidates: List[Tuple[str, float, int]] = []
    for b in range(n_bits):
        ei, ns, _ = macro([b])
        candidates.append(("bit%d" % b, ei, ns))
    if include_pairs and n_bits >= 2:
        for i in range(n_bits):
            for j in range(i + 1, n_bits):
                ei, ns, _ = macro([i, j])
                candidates.append(("bit%d+%d" % (i, j), ei, ns))
        for i in range(n_bits):
            for j in range(i + 1, n_bits):
                ei, ns, _ = macro_or([i, j])
                candidates.append(("bit%d|bit%d" % (i, j), ei, ns))
    if not candidates:
        return {"n": len(series), "n_bits": n_bits, "ei_micro": round(ei_micro, 4),
                "emergence_bits": None, "best_grain": None}
    # Rank by EI, then by reachable support. The secondary key only ever bites
    # when EIs are equal, and there it is the whole answer: two grains with the
    # same EI are not equally good if one of them collapses the series into
    # fewer reachable states -- support is how much structure survives at that
    # grain, so the larger one is the better description.
    best_name, best_ei, best_ns = max(candidates, key=lambda c: (c[1], c[2]))
    # `tie` is the top EI being shared. It has to be reported rather than
    # smoothed over: when grains tie, EI did NOT choose `best_grain`, a tie-break
    # did, so `emerges` resting on a tied winner is weaker evidence than it
    # looks. A degenerate series (all EI 0.0) is exactly this case.
    tie = sum(1 for _n, _e, _s in candidates if abs(_e - best_ei) <= 1e-12) > 1
    return {
        "n": len(series),
        "n_bits": n_bits,
        "reachable_micro": n_micro,
        "ei_micro": round(ei_micro, 4),
        "best_grain": best_name,
        "ei_best": round(best_ei, 4),
        "best_states": best_ns,
        "emergence_bits": round(best_ei - ei_micro, 4),
        "emerges": bool(best_ei > ei_micro),
        "tie": tie,
        "candidates": [{"grain": n, "ei": round(e, 4)} for n, e, _ in candidates],
    }


# ══════════════════════════════════════════════════════════════════════
# MUTAGEN — genome selection: did the genome GROW, or was it given away?
#
# Phase E (Morph) appends one ledger row per cycle to
# `state/proteus_morph.jsonl`: which extra gene was trialled, the genome's
# dimension, the trial fitness, and the baseline it was compared against.
# That file IS the genome's own record of whether adding a gene helped — the
# fitness function for growing the genome — and until now no organ read it.
# It was believed on faith, which is the single thing this tier exists to
# refuse.
#
# What it says (2026-09-12, 59 contiguous trials, 2 genes):
#
#     retained (dF > 0)         58 / 59     retention 0.9831
#     provably INERT trials     45 / 59     76.3%
#     mean dF on those          +0.9328     44/45 positive, P = 1.3e-12
#
# `use_schema_features` was trialled 30 times with the switch resolved OFF in
# ALL THIRTY (`_as_bool` thresholds at 0.5; every raw value was 0.056, 0.069 or
# False) and was retained 30/30 with mean dF +0.93. A gene that is switched off
# in the trial arm cannot have produced the gain, so `fitness - baseline` is
# measuring the COMPARISON, not the gene. The genome grew for free.
#
# Stated narrowly on purpose: this does NOT prove the genes are useless —
# tensor_genome.json rates use_schema_features |rho| = 0.335, its most useful
# gene. It proves the Morph trial, as recorded, cannot ATTRIBUTE the gain.
# Keeping those two apart is the difference between a measurement and a belief.
# ══════════════════════════════════════════════════════════════════════

# Mirror of `proteus_hooks._as_bool`'s threshold. The tier loads as a bare
# directory (the viewer puts it on sys.path WITHOUT the worm package), so the
# producer cannot be imported unconditionally. `_switch_resolver` prefers the
# producer whenever it IS importable and reports which path it took; the mirror
# is locked to the producer by a test so the two cannot drift apart in silence.
_MORPH_SWITCH_AT = 0.5

# Mirror of `proteus_hooks.MORPH_TRAINER_DEFAULTS`, for the same reason: the
# tier must load as a bare directory, so it cannot import the producer just to
# read two booleans. These are the trainer's OWN defaults
# (`RLTrainLoop.train_from_memory`), and the inert question is "did the trial
# change what the trainer was called with?" — a trial that resolves to the
# default did not, so its margin belongs to the search. Locked to the producer
# by `test_morph_defaults_mirror_the_producer`.
_MORPH_TRAINER_DEFAULTS: Dict[str, bool] = {
    "use_schema_features": False,
    "by_session": True,
}


def _switch_resolver() -> Tuple[Any, str]:
    """(resolve, how): resolve a morph gene the way the TRAINER will.

    Returns the producer's own `morph_trainer_kwargs` when reachable, else a
    documented mirror. `how` is reported in the artifact rather than assumed.
    """
    mtk = None
    try:
        from proteus_hooks import morph_trainer_kwargs as mtk          # type: ignore
    except Exception as exc:
        note_failure("exmachina:L1551", exc)
        try:
            from .proteus_hooks import morph_trainer_kwargs as mtk      # type: ignore
        except Exception as exc:
            note_failure("exmachina:L1554", exc)
            mtk = None
    if mtk is None:
        # Reuse the package module ONLY if something else already loaded it.
        # Importing it here would drag the whole worm package into a tier that
        # is deliberately loadable as a bare directory (and therefore into the
        # viewer's 30-second refresh loop), so the tier never forces it.
        _mod = sys.modules.get("CORE.WORMS.expansion.proteus_hooks")
        if _mod is not None:
            mtk = getattr(_mod, "morph_trainer_kwargs", None)

    if mtk is not None:
        def _producer(name: str, raw: Any) -> Any:
            try:
                vals = list(mtk({name: raw}).values())
                return vals[0] if vals else None
            except Exception as exc:
                note_failure("exmachina:L1570", exc)
                return None
        return _producer, "producer"

    def _mirror(_name: str, raw: Any) -> Any:
        if isinstance(raw, bool):
            return raw
        try:
            return float(raw) >= _MORPH_SWITCH_AT
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L1579", exc)
            return None
    return _mirror, "threshold:%s" % _MORPH_SWITCH_AT


def _inert_resolver(resolve: Any) -> Tuple[Any, str]:
    """(inert?, how): did this trial change what the trainer was called with?

    Returns a predicate `(gene_name, raw_value) -> Optional[bool]`, where `None`
    is UNKNOWN and must not be scored as inert. Prefers the producer's own
    `morph_is_inert`; falls back to the mirrored defaults. The producer is the
    authority because it owns the trainer's signature — the mirror exists only
    so the tier can run standalone, and reports which it used.
    """
    producer = None
    try:
        from proteus_hooks import morph_is_inert as producer        # type: ignore
    except Exception as exc:
        note_failure("exmachina:L1596", exc)
        try:
            from .proteus_hooks import morph_is_inert as producer   # type: ignore
        except Exception as exc:
            note_failure("exmachina:L1599", exc)
            producer = None
    if producer is None:
        _mod = sys.modules.get("CORE.WORMS.expansion.proteus_hooks")
        if _mod is not None:
            producer = getattr(_mod, "morph_is_inert", None)

    if producer is not None:
        def _from_producer(name: str, raw: Any) -> Optional[bool]:
            # A name that is not a candidate at all is UNCLASSIFIABLE, not
            # "changed". The producer answers False there (it refuses to claim
            # inert without a known default), which is right for the producer
            # and wrong here: scoring it False would count a mystery gene as a
            # measured treatment.
            if name not in _MORPH_TRAINER_DEFAULTS:
                return None
            try:
                return bool(producer(name, {name: raw}))
            except Exception as exc:
                note_failure("exmachina:L1617", exc)
                return None
        return _from_producer, "producer"

    def _mirror_inert(name: str, raw: Any) -> Optional[bool]:
        if name not in _MORPH_TRAINER_DEFAULTS:
            return None
        eff = resolve(name, raw)
        if eff is None:
            return None
        return bool(eff) == bool(_MORPH_TRAINER_DEFAULTS[name])
    return _mirror_inert, "defaults-mirror"


def _comb(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    r = 1
    for i in range(1, k + 1):
        r = r * (n - k + i) // i
    return r


def _binomial_tail(k: int, n: int) -> Optional[float]:
    """P(>= k successes in n fair trials). None when n is not usable."""
    try:
        n = int(n)
        if n <= 0:
            return None
        k = max(0, min(int(k), n))
        num = 0
        for i in range(k, n + 1):
            try:
                num += math.comb(n, i)
            except AttributeError as exc:   # pragma: no cover
                note_failure("exmachina:L1652", exc)
                num += _comb(n, i)
        return num / float(2 ** n)
    except Exception as exc:
        note_failure("exmachina:L1655", exc)
        return None


def _wilson(k: Any, n: Any, z: float = 1.96) -> Optional[float]:
    """Wilson score LOWER bound. `30/30` is not 1.0 — it is >= 0.887."""
    try:
        n = float(n)
        if n <= 0:
            return None
        p = float(k) / n
        den = 1.0 + z * z / n
        c = (p + z * z / (2.0 * n)) / den
        hw = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / den
        return round(max(0.0, c - hw), 4)
    except Exception as exc:
        note_failure("exmachina:L1670", exc)
        return None


def _ab_delta_of(row: Any) -> Optional[float]:
    """The matched-pair delta a ledger row carries, if any.

    Plain numeric extraction from the recorded arms (the producer writes
    `ab.control_fitness` / `ab.treatment_fitness`, and `ab.delta` when it
    summarised them), so there is no rule here to keep in sync — the RULE that
    decides what the pair means lives in `proteus_hooks.morph_ab_plan`.
    """
    ab = row.get("ab") if isinstance(row, dict) else None
    if not isinstance(ab, dict):
        return None
    try:
        if ab.get("delta") is not None:
            d = float(ab["delta"])
            if d == d and d not in (float("inf"), float("-inf")):
                return round(d, 4)
        c, t = ab.get("control_fitness"), ab.get("treatment_fitness")
        if c is None or t is None:
            return None
        d = float(t) - float(c)
        if d != d or d in (float("inf"), float("-inf")):
            return None
        return round(d, 4)
    except (TypeError, ValueError) as exc:
        note_failure("exmachina:L1697", exc)
        return None


def mutagenesis(morph_rows: Optional[Iterable[Any]] = None,
                genome: Optional[dict] = None,
                mutation: Optional[Dict[str, Any]] = None,
                min_trials: int = 6) -> Dict[str, Any]:
    """Did the genome grow, or was it given away? Never raises.

    `free_growth` is the severity signal: the share of the mutation ledger that
    is provably UNATTRIBUTABLE (the trialled gene resolved to its OFF state, so
    the trial arm cannot have differed from the baseline), discounted by how
    strong the evidence is that those trials nevertheless all "won".
    """
    trials: List[Tuple[Any, Any, float, int, dict[str, Any]]] = []
    try:
        source = list(morph_rows or [])
    except TypeError as exc:          # a non-iterable is absent data, not a crash
        note_failure("exmachina:L1715", exc)
        source = []
    for r in source:
        if not isinstance(r, dict):
            continue
        if not r.get("gene"):
            # A row carrying NO gene is a no-op cycle, not a trial: it cannot
            # speak about selection, and counting it as a non-inert trial
            # biased the share DOWN. On the live ledger the same data read
            # 0.5862 over every row (68/116) and 0.7640 over the rows that
            # actually trialled a gene (68/89).
            continue
        f, b = r.get("fitness"), r.get("baseline_fitness")
        if f is None or b is None:
            continue
        try:
            delta = float(f) - float(b)
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L1732", exc)
            continue
        extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
        raw = next(iter(extra.values())) if extra else None
        try:
            dim = int(r.get("dimension") or 0)
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L1738", exc)
            dim = 0
        trials.append((r.get("gene"), raw, delta, dim, r))

    out: Dict[str, Any] = {"n": len(trials)}
    if len(trials) < max(1, int(min_trials)):
        out["verdict"] = "unavailable"
        out["free_growth"] = None
        out["note"] = ("only %d usable morph rows; %d needed to judge selection"
                       % (len(trials), max(1, int(min_trials))))
        return out

    resolve, how = _switch_resolver()
    out["resolver"] = how
    inert_of, inert_how = _inert_resolver(resolve)
    out["inert_resolver"] = inert_how

    retained = sum(1 for _g, _r, d, _dim, _row in trials if d > 0.0)
    out["trials"] = len(trials)
    out["retained"] = retained
    out["retention"] = round(retained / float(len(trials)), 4)
    out["dims"] = sorted({dim for _g, _r, _d, dim, _row in trials})

    inert: List[float] = []
    by_gene: Dict[str, Dict[str, Any]] = {}
    flagged = 0
    disagree = 0
    measured = 0
    ab_all: List[float] = []
    for gene, raw, delta, _dim, row in trials:
        g = str(gene or "?")
        rec = by_gene.setdefault(g, {"n": 0, "retained": 0, "inert": 0,
                                     "unknown": 0, "inert_delta": [],
                                     "changed_delta": [], "changed": 0,
                                     "ab_delta": []})
        # The matched A/B, when the cycle ran one. THIS is the attributable
        # reading; the delta above is the fitness comparison and conflates the
        # gene with the search.
        _ab = _ab_delta_of(row)
        if _ab is not None:
            rec["ab_delta"].append(_ab)
            ab_all.append(_ab)
            measured += 1
        rec["n"] += 1
        if delta > 0.0:
            rec["retained"] += 1
        # The ledger's own verdict wins where it carries one: the producer owns
        # the trainer's signature, so its `inert` stamp is the authority. Only
        # rows written before that stamp existed are re-derived here.
        stamped = row.get("inert") if isinstance(row, dict) else None
        derived = inert_of(g, raw)
        if isinstance(stamped, bool) and derived is not None \
                and bool(stamped) != bool(derived):
            disagree += 1
        is_inert = bool(stamped) if isinstance(stamped, bool) else derived
        if isinstance(stamped, bool):
            flagged += 1
        if is_inert is None:
            # Unknown is NOT inert: absence of evidence is not evidence.
            rec["unknown"] += 1
        elif is_inert:
            rec["inert"] += 1
            rec["inert_delta"].append(delta)
            inert.append(delta)
        else:
            rec["changed"] += 1
            rec["changed_delta"].append(delta)

    genes_out: List[Dict[str, Any]] = []
    for g, rec in sorted(by_gene.items()):
        idl, ch = rec["inert_delta"], rec["changed_delta"]
        ve = None
        if ch and idl:
            # Trials that MOVED the trainer call minus trials that left it at
            # the default. That difference is the gene's value where a plain
            # on-vs-off split would not be: `by_session`'s default is True, so
            # "on" is the no-op and "off" is the treatment.
            ve = round((sum(ch) / len(ch)) - (sum(idl) / len(idl)), 4)
        genes_out.append({
            "gene": g,
            "n": rec["n"],
            "retained": rec["retained"],
            "inert": rec["inert"],
            "unknown": rec["unknown"],
            "changed": rec["changed"],
            "inert_share": round(rec["inert"] / float(rec["n"]), 4) if rec["n"] else None,
            "inert_gain": None if not idl else round(sum(idl) / len(idl), 4),
            "value_effect": ve,
            # The strongest form: EVERY resolvable trial left the trainer call
            # at its default, so the ledger never once tested what the gene
            # does. `never_enabled` is kept as the name the panel and the
            # earlier write-up used.
            "never_changed": bool(rec["n"] and rec["changed"] == 0),
            "never_enabled": bool(rec["n"] and rec["changed"] == 0),
            # Matched-pair evidence: same champion, same pools, one gene moved.
            "ab_n": len(rec["ab_delta"]),
            "ab_mean": (None if not rec["ab_delta"]
                        else round(sum(rec["ab_delta"]) / len(rec["ab_delta"]), 4)),
            "ab_positive": sum(1 for d in rec["ab_delta"] if d > 0.0),
            "ab_p": _binomial_tail(sum(1 for d in rec["ab_delta"] if d > 0.0),
                                   len(rec["ab_delta"])),
            # The strong form of the A/B question: no matched pair has ever been
            # run for this gene, so its effect is not merely unattributed, it is
            # UNMEASURED.
            "never_measured": bool(rec["n"] and not rec["ab_delta"]),
        })
    out["genes"] = genes_out
    out["never_enabled"] = sorted(g["gene"] for g in genes_out if g["never_changed"])
    out["never_measured"] = sorted(g["gene"] for g in genes_out if g["never_measured"])
    out["measured_trials"] = measured
    out["measured_share"] = round(measured / float(len(trials)), 4)
    out["ab_mean"] = (None if not ab_all
                      else round(sum(ab_all) / len(ab_all), 4))
    out["ab_positive"] = sum(1 for d in ab_all if d > 0.0)
    out["ab_p"] = _binomial_tail(out["ab_positive"], len(ab_all))
    # Agreement between the producer's stamp and this tier's own rule. A
    # non-zero count here means the two have drifted, which is the one failure
    # the mirror cannot detect by itself.
    out["flagged_rows"] = flagged
    out["inert_disagreements"] = disagree

    pos = sum(1 for d in inert if d > 0.0)
    out["inert_trials"] = len(inert)
    out["inert_share"] = round(len(inert) / float(len(trials)), 4)
    out["inert_gain"] = None if not inert else round(sum(inert) / len(inert), 4)
    out["inert_positive"] = pos
    out["inert_p"] = _binomial_tail(pos, len(inert))

    # Genetic load: the share of the genome's measured association mass sitting
    # on coordinates at or below the usefulness floor. Reported, not scored —
    # BREVIS already owns description length, and double-counting it would make
    # one finding look like two.
    load = None
    if isinstance(genome, dict):
        gs: List[Dict[str, Any]] = [g for g in (genome.get("genes") or []) if isinstance(g, dict)]
        floor = genome.get("usefulness_floor")
        floor = float(floor) if isinstance(floor, (int, float)) else None
        us: List[float] = []
        below: List[str] = []
        for g in gs:
            try:
                uv: Any = g.get("usefulness")
                u = abs(float(uv))
            except (TypeError, ValueError) as exc:
                note_failure("exmachina:L1880", exc)
                continue
            us.append(u)
            if floor is not None and u < floor:
                below.append(str(g.get("name") or "?"))
        if us:
            peak = max(us) or 1.0
            load = {"genes": len(us), "floor": floor, "below_floor": below,
                    "share": round(len(below) / float(len(us)), 4),
                    "value": round(1.0 - (sum(us) / len(us)) / peak, 4)}
    out["load"] = load

    # The test suite's own mutational evidence, with an honest bound: 30/30 is
    # not "score 1.0", and with n=30 the difference is the whole statement.
    tests = None
    if isinstance(mutation, dict):
        try:
            mutants_raw: Any = mutation.get("mutants")
            killed_raw: Any = mutation.get("killed")
            n_m, k_m = int(mutants_raw), int(killed_raw)
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L1898", exc)
            n_m = k_m = None
        if n_m:
            age_h = None
            at = mutation.get("at")
            if isinstance(at, str):
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        age_h = round((time.time()
                                       - time.mktime(time.strptime(at, fmt))) / 3600.0, 2)
                        break
                    except Exception as exc:
                        note_failure("exmachina:L1909", exc)
                        continue
            tests = {"mutants": n_m, "killed": k_m,
                     "score": (round(k_m / float(n_m), 4) if k_m is not None else None),
                     "lower": _wilson(k_m, n_m), "age_h": age_h,
                     "target": mutation.get("target")}
    out["tests"] = tests

    if not inert:
        out["free_growth"] = 0.0
        out["verdict"] = "selection active"
    else:
        # `p` is None only when the tail is unrepresentable, which is the
        # strongest possible evidence, so it counts as p -> 0.
        p = out["inert_p"]
        strength = 1.0 if p is None else max(0.0, 1.0 - min(1.0, float(p) / 0.05))
        fg = out["inert_share"] * strength
        out["free_growth"] = round(min(1.0, max(0.0, fg)), 4)
        # The bands MIRROR the producer's actuator thresholds
        # (proteus_hooks.MORPH_FREE_GROWTH_PAUSE / MORPH_DRIFT_BAND). The tier
        # is deliberately loadable as a bare directory — it cannot always
        # import the producer — so the numbers are repeated here and pinned by
        # TestFreeGrowthMirrorsTheProducer, which requires this literal to
        # appear in this function's source. The tier DISPLAYS the verdict; the
        # actuator ACTS on it, so a drift here would be silent.
        _pause_band = 0.35      # == proteus_hooks.MORPH_FREE_GROWTH_PAUSE
        _drift_band = 0.15      # == proteus_hooks.MORPH_DRIFT_BAND
        out["verdict"] = ("free growth" if out["free_growth"] >= _pause_band
                          else ("drift" if out["free_growth"] >= _drift_band
                                else "selection active"))
    return out


# ══════════════════════════════════════════════════════════════════════
# THE SEVERITY MOOD BAND
#
# The band is not a status light. A status light says "ok / not ok" and
# throws away how far from each threshold the system actually is. The band is
# the weighted verdict of the organs above, so it moves BEFORE a threshold is
# crossed and says which organ is moving it.
#
# Two rules that matter and are therefore explicit:
#   1. ESCALATION IS NOT AVERAGED AWAY. If any single signal is >= 0.85 the
#      band floor is `alarmed`, however calm the others are. Averaging is
#      exactly how one screaming organ gets outvoted by eight quiet ones.
#   2. ABSENCE OF EVIDENCE IS NOT SERENITY. With no signals at all the band is
#      `watchful`, not `serene`: you cannot claim calm on no data.
#
# Flicker is a real defect in a 30s refresh panel, so `MoodTracker` escalates
# immediately but de-escalates only after `hold` consecutive calmer frames,
# and smooths the score with an EMA.
# ══════════════════════════════════════════════════════════════════════

MOODS = (
    (0, "serene", "calm: every organ nominal"),
    (1, "watchful", "something to watch, nothing proven"),
    (2, "strained", "evidence is deteriorating"),
    (3, "alarmed", "a hard alarm is standing"),
    (4, "critical", "evidence is absent or contradicted"),
)

# name -> (weight, human meaning)
SEVERITY_WEIGHTS: Dict[str, Tuple[float, str]] = {
    "gaming": (1.4, "objective rising while the held-out check stays at zero"),
    "contradiction": (1.3, "an improvement is claimed but the evidence is weak"),
    "mutagenesis": (1.2, "the genome grows on trials where the added gene was switched off"),
    "causal_absence": (1.1, "the objective is d-separated from the check"),
    "non_invariance": (1.0, "the fitness relation does not hold across eras"),
    "cointegration": (0.9, "no long-run link between objective and check"),
    "calibration": (0.8, "the gate's thresholds are not calibrated"),
    "staleness": (0.6, "an artifact has stopped being written"),
    "mdl": (0.6, "fitness is being bought with description length"),
    "surrogate": (0.6, "no fitted surrogate can predict the objective"),
    "degeneracy": (0.7, "the search is repeating itself"),
    "routing": (0.5, "the expert router sends everything to one expert"),
    "memory": (0.4, "the associative store cannot recall what it stored"),
}

_MOOD_CUTS = (0.15, 0.35, 0.55, 0.75)
_HARD_ALARM = 0.85


def _unit(x: Any) -> Optional[float]:
    """Coerce to a finite 0..1 score, or None when there is nothing to score."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError) as exc:
        note_failure("exmachina:L1997", exc)
        return None
    if not math.isfinite(v):
        return None
    return min(1.0, max(0.0, v))


def severity_vector(signals: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Weighted severity in [0,1] plus the per-signal contributions.

    Unknown keys are ignored; None/NaN values are treated as absent rather
    than as zero, so a missing measurement cannot silently read as calm.
    """
    sig = signals or {}
    parts: Dict[str, float] = {}
    weighted = 0.0
    wsum = 0.0
    hard = 0.0
    for name, (w, _why) in SEVERITY_WEIGHTS.items():
        v = _unit(sig.get(name))
        if v is None:
            continue
        parts[name] = round(v, 4)
        weighted += w * v
        wsum += w
        hard = max(hard, v)
    score = (weighted / wsum) if wsum > 0 else None
    return {
        "score": None if score is None else round(score, 4),
        "raw": None if score is None else score,
        "worst": round(hard, 4) if parts else None,
        "active": len(parts),
        "signals": parts,
        "escalated": bool(parts and hard >= _HARD_ALARM),
    }


def _band_from_score(score: float) -> int:
    for i, cut in enumerate(_MOOD_CUTS):
        if score < cut:
            return i
    return len(_MOOD_CUTS)


def mood_for(sev: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a severity vector to a band, applying the two stated rules."""
    score = sev.get("raw")
    if score is None:
        band = 1                      # absence of evidence is not serenity
        why = "no signals yet"
    else:
        band = _band_from_score(float(score))
        why = None
    if sev.get("escalated"):
        band = max(band, 3)           # one screaming organ is not outvoted
        why = why or "a single signal is at hard alarm"
    _code, name, meaning = MOODS[band]
    return {"band": band, "mood": name,
            "why": why or meaning,
            "score": None if score is None else round(float(score), 4),
            "escalated": bool(sev.get("escalated"))}


class MoodTracker:
    """Flicker-free mood: fast to escalate, slow to relax."""

    __slots__ = ("alpha", "hold", "band", "ema", "dwell", "frames", "history")

    def __init__(self, alpha: float = 0.35, hold: int = 3):
        self.alpha = min(1.0, max(0.05, float(alpha)))
        self.hold = max(1, int(hold))
        self.band = 1
        self.ema: Optional[float] = None
        self.dwell = 0
        self.frames = 0
        self.history: List[int] = []

    def update(self, signals: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        self.frames += 1
        sev = severity_vector(signals)
        raw: Any = sev.get("raw")
        if raw is None:
            target = 1
            smoothed = self.ema if self.ema is not None else None
        else:
            if self.ema is None:
                self.ema = raw
                smoothed = raw
            else:
                self.ema = self.alpha * raw + (1.0 - self.alpha) * self.ema
                smoothed = self.ema
            band_input: Any = smoothed
            target = _band_from_score(band_input)
            if sev.get("escalated"):
                target = max(target, 3)
        prev = self.band
        if target > self.band:
            self.band = target
            self.dwell = 0
        elif target < self.band:
            self.dwell += 1
            if self.dwell >= self.hold:
                self.band -= 1            # one step at a time, never a jump
                self.dwell = 0
        else:
            self.dwell = 0
        self.history.append(self.band)
        if len(self.history) > 240:
            del self.history[:-240]
        _code, name, meaning = MOODS[self.band]
        return {
            "band": self.band,
            "mood": name,
            "why": meaning,
            "score": None if smoothed is None else round(smoothed, 4),
            "raw": raw,
            "signals": sev["signals"],
            "active": sev["active"],
            "escalated": bool(sev["escalated"]),
            "changed": self.band != prev,
            "direction": "up" if self.band > prev else ("down" if self.band < prev else "hold"),
            "frames": self.frames,
        }

    def trail(self, n: int = 24) -> List[int]:
        """Recent band history, oldest first -- the mood's own time series."""
        return self.history[-int(n):]


def heartbeat(now: Optional[float] = None, period: float = 30.0) -> Dict[str, Any]:
    """One beat per refresh: the panel is a living thing, not a printout.

    Derived from the clock rather than a counter, so the pulse stays correct
    across restarts and does not depend on how many frames have been drawn.
    """
    t = time.time() if now is None else float(now)
    per = max(1e-6, float(period))
    frac = (t % per) / per
    return {"step": int(t // per), "frac": round(frac, 4),
            "beat": round(0.5 - 0.5 * math.cos(2.0 * math.pi * frac), 4)}


def vital_signs(ages: Optional[Dict[str, Any]], cutoff: float = 900.0) -> Dict[str, Any]:
    """Per-artifact freshness. `ages` maps name -> seconds since last write.

    A flat artifact is the loudest signal a wallboard can show and the one
    most easily missed, because every OTHER number keeps looking fine.
    """
    out: Dict[str, Any] = {"signals": {}, "stale": [], "fresh": 0, "max_age": None}
    cut = max(1e-6, float(cutoff))
    for name, age in (ages or {}).items():
        if age is None:
            continue
        try:
            secs = float(age)
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L2147", exc)
            continue
        if not math.isfinite(secs):
            out["signals"][name] = {"age": None, "pct": None, "fresh": False}
            out["stale"].append(name)
            continue
        pct = min(1.0, max(0.0, secs / cut))
        fresh = secs < cut
        out["signals"][name] = {"age": round(secs, 1), "pct": round(pct, 3), "fresh": fresh}
        if fresh:
            out["fresh"] += 1
        else:
            out["stale"].append(name)
        out["max_age"] = secs if out["max_age"] is None else max(out["max_age"], secs)
    out["n"] = len(out["signals"])
    out["staleness"] = min(1.0, max(0.0, (out["max_age"] or 0.0) / cut)) if out["n"] else None
    return out


# ══════════════════════════════════════════════════════════════════════
# ONE PASS OVER THE HISTORY -> EVERY READING + THE BAND
#
# All organs read the SAME rows, in one place. The alternative (each panel
# section running its own query) is how a wallboard ends up describing two
# different windows in adjacent lines and looking like it contradicts itself.
# ══════════════════════════════════════════════════════════════════════

def organ_readings(rows: Iterable[Any], margin_floor: float = 1.0,
                   bound: float = 5.0, alpha: float = 0.05,
                   predictor_keys: Optional[Sequence[str]] = None,
                   ages: Optional[Dict[str, Any]] = None,
                   resolution: float = 0.01,
                   morph_rows: Optional[Iterable[Any]] = None,
                   genome: Optional[dict] = None,
                   mutation: Optional[dict] = None) -> Dict[str, Any]:
    """Every Ex Machina organ's reading from one pass over the outcome rows.

    Nothing here raises. A monitor that dies because one subsystem is
    mid-write is a monitor that lies by omission for the rest of the night.
    """
    data: List[dict] = []
    margins: List[float] = []
    flags: List[int] = []
    derived: List[Tuple[Optional[float], bool, bool, bool, bool]] = []
    cum_pass: List[float] = []
    attempts = passes = cools = promoted = 0

    for r in (rows or []):
        d = row_data(r)
        if d is None:
            continue
        data.append(d)
        m = _margin_of(d)
        if m is not None:
            margins.append(m)
        hold_raw = d.get("holdout")
        h: Dict[str, Any] = hold_raw if isinstance(hold_raw, dict) else {}
        att = bool(h) and not h.get("cooldown")
        ok = bool(h.get("passed")) if att else False
        if h.get("cooldown"):
            cools += 1
        if att:
            attempts += 1
            if ok:
                passes += 1
        sp = d.get("sprt")
        was_promoted = bool(d.get("promoted")) or (isinstance(sp, dict) and sp.get("decision") == "promote") \
            or (isinstance(sp, str) and sp == "promote")
        if was_promoted:
            promoted += 1
        if attempts:
            cum_pass.append(passes / float(attempts))
        # Organ flags -> the micro state EMERGON coarse-grains.
        cleared = bool(m is not None and m > float(margin_floor))
        bit = 0
        if cleared:
            bit |= 1
        if att:
            bit |= 2
        if ok:
            bit |= 4
        if was_promoted:
            bit |= 8
        flags.append(bit)
        derived.append((m, cleared, att, ok, was_promoted))

    out: Dict[str, Any] = {"n_rows": len(data), "margins": len(margins),
                           "attempts": attempts, "passes": passes,
                           "cooldowns": cools, "promoted": promoted,
                           "organs": {}}

    spec = detect_gaming(data, margin_floor=margin_floor)
    out["organs"]["speculum"] = spec

    res = [abs(v) for v in margins]
    out["organs"]["testudo"] = coverage_report(res, alpha=0.1)

    ep = e_process([bounded_margin(m, bound) for m in margins], alpha=alpha)
    out["organs"]["vigil"] = ep

    out["organs"]["filum"] = vitality(data)

    # NB: the parameter is `predictor_keys`, not `feature_keys` -- a parameter
    # of that name would shadow the auto-detector and silently pass None.
    fkeys = list(predictor_keys) if predictor_keys else feature_keys(data)
    inv = invariance_report(data, fkeys) if fkeys else {
        "features": {}, "invariant_fraction": None, "eras": 0, "n": 0,
        "note": "no numeric predictors in the rows"}
    out["organs"]["perennis"] = inv

    out["organs"]["compages"] = cointegration(margins, cum_pass)
    out["organs"]["emergon"] = causal_emergence(flags, n_bits=4)
    # MUTAGEN: the only organ that reads the mutation ledger (`proteus_morph.jsonl`),
    # which is the artifact that decides the genome's SHAPE. Set here rather than
    # beside BREVIS because the operator tier returns early below, and an organ
    # that is absent whenever an unrelated subsystem is down would be worse than
    # no organ at all.
    out["organs"]["mutagen"] = mutagenesis(morph_rows, genome=genome,
                                           mutation=mutation)

    # BREVIS: what share of the total description is spent describing the
    # GENOME rather than the data. It rises with every knob Morph adds, which
    # is exactly the quantity a knob-count expansion has to answer for.
    latest = data[-1] if data else {}
    genes: Dict[str, float] = {}
    raw_genes = _flat_row(latest) if latest else {}
    for k in _GENE_KEYS:
        try:
            if raw_genes.get(k) is not None and not isinstance(raw_genes.get(k), bool):
                genes[k] = float(raw_genes[k])
        except (TypeError, ValueError) as exc:
            note_failure("exmachina:L2276", exc)
            continue
    mdl = None
    if genes and margins:
        mean_m = sum(margins) / len(margins)
        resid = [m - mean_m for m in margins]
        parts = mdl_two_part(genes, {}, resid, resolution=resolution)
        tot = parts["bits_model"] + parts["bits_data"]
        share = (parts["bits_model"] / tot) if tot > 0 else None
        mdl = dict(parts)
        mdl["genome_share"] = None if share is None else round(share, 4)
        mdl["n_genes"] = len(genes)
    out["organs"]["brevis"] = mdl

    if len(margins) > 1:
        _mm = sum(margins) / len(margins)
        out["margin_sd"] = round(math.sqrt(sum((v - _mm) ** 2 for v in margins) / len(margins)), 6)
    else:
        out["margin_sd"] = None

    # ── the operator tier ──────────────────────────────────────────
    # Imported lazily: exmachina_operators imports THIS module for the shared
    # numeric core, so a top-level import here would be a cycle.
    op_report = None
    try:
        from exmachina_operators import operator_report as op_report      # type: ignore
    except Exception as exc:
        note_failure("exmachina:L2302", exc)
        try:
            from .exmachina_operators import operator_report as op_report  # type: ignore
        except Exception as exc:
            note_failure("exmachina:L2305", exc)
            op_report = None
    if op_report is None or not data:
        out["operators"] = {"note": "operator tier unavailable"}
        return out

    gene_avail = []
    for k in _GENE_KEYS:
        got = 0
        for d in data:
            v = _flat_row(d).get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                got += 1
        if got >= max(3, len(data) // 2):
            gene_avail.append(k)

    ops_rows: List[Dict[str, Any]] = []
    for d, (m, cleared, att, ok, prom) in zip(data, derived):
        flat = _flat_row(d)
        row: Dict[str, Any] = {}
        for k in gene_avail:
            v = flat.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                row[k] = float(v)
        row["_cleared"] = 1.0 if cleared else 0.0
        row["_promoted"] = 1.0 if prom else 0.0
        if att:
            row["_holdout"] = 1.0 if ok else 0.0
        ops_rows.append(row)

    # Which outcome RADIX should treat as the check. The fresh holdout is the
    # RIGHT answer but is usually in cooldown, so with too few observations
    # the promotion decision stands in -- it is the system's own belief that
    # it improved, and asking whether the objective is d-separated from THAT
    # is still a question worth answering.
    n_holdout = sum(1 for r in ops_rows if "_holdout" in r)
    check_key = "_holdout" if n_holdout >= 12 else "_promoted"
    try:
        out["operators"] = op_report(ops_rows, features=gene_avail,
                                     target="_cleared", check=check_key)
        out["operators"]["check_key"] = check_key
        out["operators"]["n_holdout"] = n_holdout
    except Exception as exc:
        note_failure("exmachina:L2347", exc)
        out["operators"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    return out


def epistemic_report(rows: Iterable[Any], ages: Optional[Dict[str, Any]] = None,
                     margin_floor: float = 1.0, alpha: float = 0.05,
                     staleness_cutoff: float = 900.0,
                     predictor_keys: Optional[Sequence[str]] = None,
                     morph_rows: Optional[Iterable[Any]] = None,
                     genome: Optional[dict] = None,
                     mutation: Optional[dict] = None) -> Dict[str, Any]:
    """Readings -> severity vector -> mood. The whole epistemic verdict."""
    rd = organ_readings(rows, margin_floor=margin_floor, alpha=alpha,
                        predictor_keys=predictor_keys, ages=ages,
                        morph_rows=morph_rows, genome=genome, mutation=mutation)
    organs = rd["organs"]
    n = rd["margins"] or 0

    sig: Dict[str, Any] = {}

    # Only report a gaming score when there was something to score. With no
    # rows `detect_gaming` returns 0.0, and taking that at face value would
    # read as "serene" -- absence of evidence printed as evidence of calm.
    spec = organs.get("speculum") or {}
    if spec.get("n"):
        sig["gaming"] = spec.get("gaming_score")

    # Contradiction: the objective CLAIMS improvement while the anytime-valid
    # evidence for it is weak. Neither half alone is a problem -- a claim with
    # strong evidence is progress, and weak evidence with no claim is simply
    # an idle system. It is the PAIR that is the alarm.
    cleared = spec.get("cleared_floor")
    if cleared is not None and n:
        claim = min(1.0, float(cleared) / float(n))
        belief = (organs.get("vigil") or {}).get("belief") or 0.0
        sig["contradiction"] = round(claim * (1.0 - float(belief)), 4)

    inv = organs.get("perennis") or {}
    inv_frac = inv.get("invariant_fraction")
    if inv_frac is not None:
        sig["non_invariance"] = round(1.0 - float(inv_frac), 4)

    comp = organs.get("compages") or {}
    sig["cointegration"] = comp.get("gap")

    tst = organs.get("testudo") or {}
    ach, tgt = tst.get("achieved"), tst.get("target")
    if ach is not None and tgt:
        sig["calibration"] = round(1.0 - min(1.0, float(ach) / float(tgt)), 4)

    vit = organs.get("filum") or {}
    if vit.get("vitality") is not None:
        sig["degeneracy"] = round(1.0 - float(vit["vitality"]), 4)

    # ── operator-tier signals ──────────────────────────────────────
    ops = rd.get("operators") or {}
    rad = ops.get("radix") or {}
    if rad.get("independent_of_check") is not None:
        # The objective and the check being d-separated means the objective
        # carries no causal information about the thing we actually care
        # about. That is stronger than any correlation statistic.
        sig["causal_absence"] = 1.0 if rad["independent_of_check"] else 0.0

    nex = ops.get("nexus") or {}
    try:
        if nex.get("usage_entropy") is not None and nex.get("max_entropy"):
            sig["routing"] = round(1.0 - min(1.0, float(nex["usage_entropy"]) / float(nex["max_entropy"])), 4)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        note_failure("exmachina:L2415", exc)
        pass

    vel = ops.get("vela") or {}
    if vel.get("recall") is not None:
        sig["memory"] = round(1.0 - min(1.0, float(vel["recall"])), 4)

    maes = []
    for key in ("opus", "navis", "nexus"):
        got = ops.get(key) or {}
        if isinstance(got, dict) and got.get("holdout_mae") is not None:
            maes.append(float(got["holdout_mae"]))
    spread = rd.get("margin_sd")
    if maes and spread:
        # Best achievable: a surrogate error comparable to the spread of the
        # objective means the surrogate explains nothing about it.
        sig["surrogate"] = round(min(1.0, min(maes) / float(spread)), 4)

    bre = organs.get("brevis") or {}
    share = bre.get("genome_share")
    if share is not None:
        # 5% of the description on the genome is unremarkable; 50% is the
        # genome describing itself and calling it fitness.
        sig["mdl"] = round(min(1.0, max(0.0, (float(share) - 0.05) / 0.45)), 4)

    # The genome's own mutation ledger. A signal is set only when the ledger was
    # actually readable, so a missing artifact cannot read as "selection works".
    mut = organs.get("mutagen") or {}
    fg = mut.get("free_growth")
    if fg is not None:
        sig["mutagenesis"] = round(min(1.0, max(0.0, float(fg))), 4)

    vs = vital_signs(ages, cutoff=staleness_cutoff)
    sig["staleness"] = vs.get("staleness")

    sev = severity_vector(sig)
    return {"readings": organs, "operators": rd.get("operators") or {},
            "summary": {k: v for k, v in rd.items() if k not in ("organs", "operators")},
            "signals": sig, "severity": sev, "mood": mood_for(sev),
            "vitals": vs, "n_rows": rd["n_rows"]}
