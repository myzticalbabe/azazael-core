# coding: utf-8
"""Smoke-check the new Ex Machina organs on synthetic + real data."""
import sys
import os
import json
import math

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src", "CORE", "WORMS", "expansion"))
import exmachina as ex

fails = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + "%-26s %s" % (name, detail))
    if not cond:
        fails.append(name)


print("organs: %d (%d implemented, %d registered)" % (
    len(ex.ORGANS),
    sum(1 for o in ex.ORGANS if o["tier"] == "implemented"),
    sum(1 for o in ex.ORGANS if o["tier"] == "registered")))

# ---- VIGIL ------------------------------------------------------------
print("\n[VIGIL]")
h0 = [0.5 + (0.1 if i % 2 else -0.1) for i in range(60)]      # mean 0.5 = H0
h1 = [0.8] * 40                                               # clear improvement
r0 = ex.e_process(h0, mu0=0.5, alpha=0.05)
r1 = ex.e_process(h1, mu0=0.5, alpha=0.05)
print("  H0 wealth=%.4f bits=%s rejected=%s" % (r0["wealth"], r0["belief_bits"], r0["rejected"]))
print("  H1 wealth=%.4f bits=%s rejected=%s" % (r1["wealth"], r1["belief_bits"], r1["rejected"]))
check("vigil reject H1", r1["rejected"] is True)
check("vigil keeps H0", r0["rejected"] is False, "wealth=%.3f" % r0["wealth"])
check("vigil bounded map", ex.bounded_margin(0.0) == 0.5 and ex.bounded_margin(5.0) == 1.0)

# false-positive rate under H0 (Ville: should be <= alpha)
bad = 0
for seed in range(400):
    s = 12345 + seed * 7919
    ys = []
    for _ in range(50):
        s = (1103515245 * s + 12345) % (1 << 31)
        ys.append(0.8 if (s / float(1 << 31)) > 0.625 else 0.2)   # mean 0.5
    if ex.e_process(ys, mu0=0.5, alpha=0.05)["rejected"]:
        bad += 1
print("  H0 false-positive rate over 400 trials: %.3f (alpha=0.05)" % (bad / 400.0))
check("vigil type-I controlled", bad / 400.0 <= 0.05, "%d/400" % bad)

# ---- FILUM ------------------------------------------------------------
print("\n[FILUM]")
loop = [1.0, 2.0, 1.0, 2.0] * 20
pe_loop = ex.permutation_entropy(loop, order=3)
s = 7
rand = []
for _ in range(200):
    s = (1103515245 * s + 12345) % (1 << 31)
    rand.append(s / float(1 << 31))
pe_rand = ex.permutation_entropy(rand, order=3)
print("  loop pe_norm=%s patterns=%s" % (pe_loop["pe_norm"], pe_loop["patterns"]))
print("  noise pe_norm=%s patterns=%s" % (pe_rand["pe_norm"], pe_rand["patterns"]))
check("loop degenerate", pe_loop["pe_norm"] < 0.5, str(pe_loop["pe_norm"]))
check("noise varied", pe_rand["pe_norm"] > 0.9, str(pe_rand["pe_norm"]))
check("loop flagged", pe_loop["degenerate"] is True)
white = ex.dfa(rand)
# A random WALK is the drift case: integrated noise, alpha ~1.5. (A square
# wave is periodic, and DFA of a periodic signal is ~0 -- that was my first
# test's error, not the implementation's.)
walk, acc2 = [], 0.0
s2 = 99
for _ in range(300):
    s2 = (1103515245 * s2 + 12345) % (1 << 31)
    acc2 += (s2 / float(1 << 31)) - 0.5
    walk.append(acc2)
w = ex.dfa(walk)
print("  dfa white=%.3f  random-walk=%.3f" % (white["alpha"] or -1, w["alpha"] or -1))
check("dfa white ~0.5", 0.3 <= (white["alpha"] or 0) <= 0.7, str(white["alpha"]))
check("dfa walk high", (w["alpha"] or 0) > 1.0, str(w["alpha"]))
check("vitality states", ex.vitality([{"margin": m} for m in loop])["state"] == "looping")

# ---- PERENNIS ---------------------------------------------------------
print("\n[PERENNIS]")
rows = []
for era in ("e1", "e2", "e3"):
    for i in range(10):
        x = i * 0.1
        rows.append({"pool_id": era, "lr": x, "margin": 1.0 + 2.0 * x})   # invariant
rows_bad = []
for era, sign in (("e1", 1.0), ("e2", -1.0), ("e3", 1.0)):
    for i in range(10):
        x = i * 0.1
        rows_bad.append({"pool_id": era, "lr": x, "margin": 1.0 + sign * 2.0 * x})
inv = ex.invariance_report(rows, ["lr"])
invbad = ex.invariance_report(rows_bad, ["lr"])
print("  invariant  slope=%s instability=%s inv=%s" % (
    inv["features"]["lr"]["slope"], inv["features"]["lr"]["instability"], inv["features"]["lr"]["invariant"]))
print("  sign-flip  instability=%s inv=%s" % (
    invbad["features"]["lr"]["instability"], invbad["features"]["lr"]["invariant"]))
check("perennis invariant", inv["features"]["lr"]["invariant"] is True)
check("perennis catches flip", invbad["features"]["lr"]["invariant"] is False)
check("perennis theil-sen", abs(ex.theil_sen([0, 1, 2, 3], [0, 2, 4, 6]) - 2.0) < 1e-9)

# ---- COMPAGES ---------------------------------------------------------
print("\n[COMPAGES]")
n = 60
s = 4242
noise = []
for _ in range(n):
    s = (1103515245 * s + 12345) % (1 << 31)
    noise.append((s / float(1 << 31)) - 0.5)
obj = [1.0 + 0.05 * i for i in range(n)]
chk = [obj[i] + 0.2 * noise[i] for i in range(n)]          # cointegrated
co = ex.cointegration(obj, chk)
# Two independent random walks: no long-run link exists to find.
sa, sb, wa, wb = 11, 22, [], []
for _ in range(n):
    sa = (1103515245 * sa + 12345) % (1 << 31)
    sb = (1103515245 * sb + 12345) % (1 << 31)
    wa.append((sa / float(1 << 31)) - 0.5)
    wb.append((sb / float(1 << 31)) - 0.5)
noco = ex.cointegration(wa, wb)
print("  linked tstat=%s coint=%s gap=%s" % (co["tstat"], co["cointegrated"], co["gap"]))
check("compages finds link", co["cointegrated"] is True, str(co["tstat"]))
check("compages gap smaller when linked", (co["gap"] or 0) < 0.5, str(co["gap"]))
check("compages enough data", ex.cointegration([1, 2, 3], [1, 2, 3])["n"] == 3)

# A single draw proves nothing here: two independent walks reject ~5% of the
# time BY DESIGN, and my first version of this check drew exactly that 5%.
# The claim that is actually testable is the SIZE of the test.
rej = 0
trials = 120
for k in range(trials):
    ra, rb = 7 + k * 13, 101 + k * 29
    wa, wb = [], []
    aa = ab = 0.0
    for _ in range(60):
        ra = (1103515245 * ra + 12345) % (1 << 31)
        rb = (1103515245 * rb + 12345) % (1 << 31)
        aa += (ra / float(1 << 31)) - 0.5
        ab += (rb / float(1 << 31)) - 0.5
        wa.append(aa)
        wb.append(ab)
    if ex.cointegration(wa, wb).get("cointegrated"):
        rej += 1
rate = rej / float(trials)
print("  independent walks: false cointegration %.3f over %d trials (nominal 0.05)"
      % (rate, trials))
check("compages size near nominal", rate <= 0.15, "%.3f" % rate)

# The E9 shape must produce a VERDICT, not silence: the check never moves.
margin_up = [0.2 + 0.03 * i for i in range(60)]
e9 = ex.cointegration(margin_up, [0.0] * 60)
print("  E9 shape (check flat at 0): coint=%s gap=%s degenerate=%s"
      % (e9["cointegrated"], e9["gap"], e9.get("degenerate")))
check("compages rules on E9 shape", e9["cointegrated"] is False and e9["gap"] == 1.0,
      str(e9.get("note")))

# Exactly collinear regressors must yield "no answer", never a t-statistic.
check("solver refuses singular", ex._solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]) is None)
check("solver solves invertible", ex._solve([[1.0, 0.0], [0.0, 1.0]], [1.0, 2.0]) == [1.0, 2.0])
alt = [0.05 if i % 2 else -0.05 for i in range(60)]
check("adf refuses degenerate", ex.adf(alt, lags=1)["tstat"] is None,
      str(ex.adf(alt, lags=1)["tstat"]))

# ---- BREVIS -----------------------------------------------------------
print("\n[BREVIS]")
resid = [0.1, -0.2, 0.05, -0.05, 0.1, -0.1]
small = ex.mdl_two_part({"epsilon": 0.3}, {"epsilon": (0, 0.5)}, resid)
big = ex.mdl_two_part({"epsilon": 0.3, "lr": 0.5, "discount": 0.9, "d": 0.1},
                      {"epsilon": (0, 0.5), "lr": (0, 0.1), "discount": (0, 1), "d": (0, 1)}, resid)
print("  1 gene bits=%s   4 genes bits=%s" % (small["bits_model"], big["bits_model"]))
cmp_ = ex.mdl_compare({"a": 1.0}, {"a": 1.0}, {"a": (0, 2)}, resid)
print("  gain=%s bits_per_point=%s better=%s" % (cmp_["gain_bits"], cmp_["bits_per_point"], cmp_["better"]))
check("brevis penalises params", big["bits_model"] > small["bits_model"])
check("brevis two-part", small["bits_total"] == round(small["bits_model"] + small["bits_data"], 4))

# ---- EMERGON ----------------------------------------------------------
print("\n[EMERGON]")
det = [1, 3] * 10
ce = ex.causal_emergence(det, n_bits=4)
print("  deterministic  ei_micro=%s reachable=%s best=%s emergence=%s" % (
    ce["ei_micro"], ce["reachable_micro"], ce["best_grain"], ce["emergence_bits"]))
check("emergon computes", ce["ei_micro"] is not None and ce["best_grain"] is not None)
check("emergon deterministic high", ce["ei_micro"] > 0.9, str(ce["ei_micro"]))
check("emergon no false emergence", (ce["emergence_bits"] or 0) <= 0.05, str(ce["emergence_bits"]))
check("emergon needs data", ex.causal_emergence([1, 0], n_bits=4)["ei_micro"] is None)

# Genuine emergence needs an UNBALANCED grain. Here state 0 splits three ways
# (high micro entropy) while "any flag set" collapses it to a deterministic
# two-state cycle -- so the macro grain must carry strictly more EI.
emg = []
state = 0
for i in range(80):
    if state == 0:
        seq = (i, 2 * i + 1, 3 * i + 2)
        state = seq[i % 3] % 3 + 1        # uniformly one of 1, 2, 3
    else:
        state = 0
    emg.append(state)
ce2 = ex.causal_emergence(emg, n_bits=4)
print("  unbalanced     ei_micro=%s best=%s ei_best=%s emergence=%+0.4f" % (
    ce2["ei_micro"], ce2["best_grain"], ce2["ei_best"], ce2["emergence_bits"]))
check("emergon micro noisy", ce2["ei_micro"] < 1.0, str(ce2["ei_micro"]))
check("emergon finds emergence", ce2["emerges"] is True and ce2["emergence_bits"] > 0.05,
      "%s via %s" % (ce2["emergence_bits"], ce2["best_grain"]))

# ---- SEVERITY BAND ----------------------------------------------------
print("\n[SEVERITY BAND]")
sev_calm = ex.severity_vector({"gaming": 0.0, "calibration": 0.0, "degeneracy": 0.0})
m_calm = ex.mood_for(sev_calm)
print("  calm     score=%s band=%d mood=%s" % (sev_calm["score"], m_calm["band"], m_calm["mood"]))
sev_bad = ex.severity_vector({"gaming": 1.0})
m_bad = ex.mood_for(sev_bad)
print("  gaming   score=%s band=%d mood=%s escalated=%s" % (
    sev_bad["score"], m_bad["band"], m_bad["mood"], sev_bad["escalated"]))
m_none = ex.mood_for(ex.severity_vector({}))
print("  no data  band=%d mood=%s why=%s" % (m_none["band"], m_none["mood"], m_none["why"]))
check("calm is serene", m_calm["band"] == 0, m_calm["mood"])
check("hard alarm escalates", m_bad["band"] >= 3, m_bad["mood"])
check("no evidence is not serene", m_none["band"] == 1, m_none["mood"])
check("nan ignored not zero",
      ex.severity_vector({"gaming": float("nan"), "calibration": 0.0})["score"] == 0.0)

tracker = ex.MoodTracker()
alarm = {"gaming": 1.0}
calm = {"gaming": 0.0}
tracker.update(alarm)
up = tracker.update(alarm)
f1 = tracker.update(calm)["band"]
f2 = tracker.update(calm)["band"]
f3 = tracker.update(calm)["band"]
print("  hysteresis: after alarm=%d, calm frames -> %d %d %d" % (up["band"], f1, f2, f3))
check("escalates immediately", up["band"] >= 3)
check("de-escalates slowly", f1 == up["band"] and f3 < f1, "%d -> %d" % (f1, f3))

hb = ex.heartbeat(now=0.0, period=30.0)
print("  heartbeat step=%s frac=%s beat=%s" % (hb["step"], hb["frac"], hb["beat"]))
check("heartbeat in range", 0.0 <= hb["beat"] <= 1.0)
vs = ex.vital_signs({"oracle": 10.0, "monitor": 5000.0}, cutoff=900.0)
print("  vitals n=%s stale=%s staleness=%s" % (vs["n"], vs["stale"], vs["staleness"]))
check("vitals flags stale", vs["stale"] == ["monitor"])

# ---- END TO END -------------------------------------------------------
print("\n[EPISTEMIC REPORT]")
# The real rows live in the memory store (event_type='evolution_result'),
# exactly as the monitor reads them -- NOT in the run logs, which are prose.
DB = os.environ.get("AZAZAEL_MEMORY_DIR", r"C:\Users\clint\.azazael")
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")
real = []
if os.path.exists(DB):
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        cur = con.execute("SELECT timestamp, data FROM memories "
                          "WHERE event_type='evolution_result' "
                          "ORDER BY id DESC LIMIT 120")
        for ts, data in cur.fetchall():
            try:
                d = json.loads(data)
            except Exception:
                continue
            if isinstance(d, dict):
                real.append((ts, d))
        con.close()
    except Exception as exc:
        print("  db read failed: %s" % exc)
print("  real rows loaded: %d" % len(real))
if real:
    print("  features auto-detected: %s" % ex.feature_keys(real))
rep = ex.epistemic_report(real, ages={"oracle": 120.0})
sm = rep["summary"]
print("  rows=%s margins=%s attempts=%s passes=%s cooldowns=%s promoted=%s" % (
    sm.get("n_rows"), sm.get("margins"), sm.get("attempts"), sm.get("passes"),
    sm.get("cooldowns"), sm.get("promoted")))
ops = rep.get("operators") or {}
print("  operator check column: %s (%d holdout observations)" % (
    ops.get("check_key"), ops.get("n_holdout") or 0))
for name in ("radix", "catena", "navis", "vela", "nexus", "opus", "limen"):
    got = ops.get(name)
    if isinstance(got, dict):
        keys = [k for k in ("nodes", "edges", "linked", "independent_of_check",
                            "blanket_size", "radius", "log_ml", "holdout_mae",
                            "recall", "usage_entropy", "max_entropy", "trained",
                            "params", "note", "error") if k in got]
        print("    %-8s %s" % (name, {k: got[k] for k in keys}))
print("  signals : %s" % rep["signals"])
print("  severity: %s (worst=%s, active=%s)" % (
    rep["severity"]["score"], rep["severity"]["worst"], rep["severity"]["active"]))
print("  mood    : %s (band %d) %s" % (rep["mood"]["mood"], rep["mood"]["band"], rep["mood"]["why"]))
for k, v in rep["readings"].items():
    if isinstance(v, dict):
        keys = [kk for kk in ("n", "gaming_score", "verdict", "vitality", "state",
                              "invariant_fraction", "cointegrated", "gap",
                              "emergence_bits", "best_grain", "genome_share",
                              "achieved", "belief", "belief_bits") if kk in v]
        print("    %-10s %s" % (k, {kk: v[kk] for kk in keys}))
# The judge-tier organs that MUST have a reading entry. Named rather than
# counted: this check used to assert `len(readings) == 8`, so adding MUTAGEN
# made the gate fail on a number instead of on a missing organ -- and a gate
# that cries wolf when nothing is wrong is one people learn to ignore. Testing
# for ABSENCE catches the failure that actually matters.
# NB: FULCRUM is exported by the module but has no reading entry by design, so
# it is deliberately not in this list.
_JUDGE_READINGS = ("speculum", "testudo", "vigil", "filum", "perennis",
                   "compages", "brevis", "emergon", "mutagen")
_missing = [k for k in _JUDGE_READINGS if k not in rep["readings"]]
check("report has all organs", not _missing,
      "%d/%d readings %s" % (len(_JUDGE_READINGS) - len(_missing),
                             len(_JUDGE_READINGS), _missing or "ok"))
check("report mood resolves", rep["mood"]["band"] in (0, 1, 2, 3, 4))
check("empty report is watchful", ex.epistemic_report([])["mood"]["band"] == 1)
check("no gaming score without data", "gaming" not in ex.epistemic_report([])["signals"])
if real:
    check("real rows produce readings", rep["summary"]["margins"] > 0,
          "%d margins" % rep["summary"]["margins"])
    check("features found", len(ex.feature_keys(real)) >= 2,
          str(ex.feature_keys(real)))

# ---- MUTAGEN ----------------------------------------------------------
# The one organ that reads `proteus_morph.jsonl`. Its whole claim is that a
# trial whose gene resolved to its OFF state cannot have produced the fitness
# delta, so the delta is measuring the comparison rather than the gene.
print("\n[MUTAGEN]")


def _mrow(gene, value, delta, i):
    return {"cycle_index": i, "gene": gene, "dimension": 5,
            "fitness": 20.0 + float(delta), "baseline_fitness": 20.0,
            "extra": {gene: value}}


_off = [_mrow("use_schema_features", False, 1.0, i) for i in range(8)]
_mg = ex.mutagenesis(_off)
check("inert trials detected", _mg.get("inert_trials") == 8, str(_mg.get("inert_share")))
check("never-enabled gene flagged", _mg.get("never_enabled") == ["use_schema_features"])
check("free growth flagged", _mg.get("verdict") == "free growth", str(_mg.get("free_growth")))

# A switch whose ON is a real change: `use_schema_features` defaults to False
# in the trainer, so True is a treatment.
_on = [_mrow("use_schema_features", True, (1.0 if i % 2 else -1.0), i)
       for i in range(8)]
_mo = ex.mutagenesis(_on)
check("no false alarm with the gene on", _mo.get("verdict") == "selection active",
      str(_mo.get("free_growth")))

# Default-aware inertness (2026-09-12, producer fix). `by_session` defaults to
# **True**: a `by_session=True` trial calls the trainer exactly as production
# does, so its margin belongs to the search. A naive on/off reading calls that
# trial a real treatment and credits the gene for it.
_dflt = [_mrow("by_session", True, 1.0, i) for i in range(8)]
_md = ex.mutagenesis(_dflt)
check("default-valued trial is inert", _md.get("inert_trials") == 8,
      "inert_share=%s resolver=%s" % (_md.get("inert_share"),
                                      _md.get("inert_resolver")))
check("default-valued gene flagged", _md.get("never_enabled") == ["by_session"])
# The tier must say which rule it used: the producer's, or its own mirror. A
# silent fallback to the mirror is how a tier and its producer drift apart.
check("inert resolver reports its path",
      _md.get("inert_resolver") in ("producer", "defaults-mirror"),
      str(_md.get("inert_resolver")))
# The ledger's own verdict outranks the mirror: the producer owns the trainer's
# signature, and this is the one case the mirror cannot check by itself.
_stamped = [dict(_mrow("by_session", True, 1.0, i), inert=True) for i in range(8)]
check("recorded verdict is authoritative",
      ex.mutagenesis(_stamped).get("inert_trials") == 8)
check("stamp and mirror agree on real rows",
      ex.mutagenesis(_dflt).get("inert_disagreements") == 0)
check("absent ledger is not calm", ex.mutagenesis(None).get("free_growth") is None)
check("mutagenesis not a signal without a ledger",
      "mutagenesis" not in rep["signals"])

print("\n%s  (%d failures)" % ("ALL OK" if not fails else "FAILURES: " + ", ".join(fails), len(fails)))
sys.exit(1 if fails else 0)
