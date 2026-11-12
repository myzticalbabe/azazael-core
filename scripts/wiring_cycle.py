"""L1 wiring cycle — the fleet's COMPOSITION as evolvable state.

WHY THIS EXISTS (2026-09-21)
----------------------------
`evolution_cycle.py` evolves four RL floats. The system's own policy file says
that search space is closed:

    state/morph_policy.json
      "search_space": {"addable": []}
      "rotation": {"search_space_closed": true, "retired_genes": ["use_schema_features"]}

Tuning harder cannot open it; the genotype has to change. This is rung L1 of the
ladder in `wiring_genome.py`: the genotype is which registered organs run, in what
order, with what budget — registry DATA, not Python, so a mutation is always
syntactically valid, reversible by restoring one dict, and checkable by
`audit_wiring.py` before it runs.

WHAT MAKES THIS A LOOP RATHER THAN ANOTHER METRIC
-------------------------------------------------
A learner with no consumer is the defect this project already found at the
hyperparameter layer (memory -> hyperparameters -> training -> memory, never
conditioned on what was learned). So the three links are explicit here:

    LEARN   credit from `spawn_credit` (post-hoc execution outcomes)
    DECIDE  paired_compare on ONE snapshot, streak-gated
    ACT     `state/wiring_promoted.json`, read by
            `worm_registry._load_promoted_wiring()` on every dispatch

`worm_registry.decide_execution_order` is the effector: the genome's `priority`
replaces the hand-authored registry priority, and an organ the genome deactivated
is demoted to the tail. The invariant that made that acceptable is preserved —
the engine still returns EVERY candidate, so L1 can reorder but never starve, and
"inactive" is a demotion rather than a deletion.

THE TWO ESTIMATOR DEFECTS THIS GATE IS BUILT AGAINST
----------------------------------------------------
Both were measured in this repo, not assumed:

1. **Gate smaller than the noise it resolves.** `evolution_cycle` promoted on a
   0.75 margin against per-gene noise of 2.4-30.2, and 159/400 rows (39.8%)
   cleared it. Here the comparison is PAIRED by construction — candidate and
   incumbent scored against the same credit snapshot — so snapshot drift cancels
   instead of accumulating, and the residue is handled by requiring the SAME
   candidate fingerprint to win on `L1_MIN_WINS` consecutive cycles rather than
   one.

2. **A verdict issued on too little evidence.** For the `alert` pool the
   per-state oracle EQUALLED the best constant, i.e. the measurement carried no
   information, yet it still produced promotions. So a candidate is unpromotable
   unless it is scored on at least `L1_MIN_OBSERVED` observed organs covering
   `L1_MIN_EVIDENCE` of its active set. Thin evidence is reported, never acted on.

SAFETY
------
* `validate()` runs on every mutation; an invalid genome is rejected, not scored.
* The modifier is not evolvable — `wiring_genome.INFRA_EXCLUDED` / `_INFRA_PATTERNS`
  keep the loop, its gates, its quarantines and its audits out of the genome.
* Promotion writes `.prev.json` first, so rollback is restoring one file.
* A canary compares the promoted genome's realised fitness on later snapshots
  against its fitness at promotion and auto-reverts on decay.
* Kill switches: `AZAZAEL_WIRING_L1=0` disables the effector,
  `AZAZAEL_WIRING_L1_APPLY=0` stops promotions while still recording trials.
* Fail-open throughout: any error returns a report with `ok: False`, never raises.

Run:
    python scripts/wiring_cycle.py --status     # incumbent, streak, lineage size
    python scripts/wiring_cycle.py              # propose + score (no promotion)
    python scripts/wiring_cycle.py --apply       # allow a promotion to be written
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

_AZAZAEL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_AZAZAEL_ROOT, "src"))

from CORE.WORMS.expansion import wiring_genome as wg  # noqa: E402
import hashlib

try:  # the outer loop may calibrate these; its absence is never fatal
    from CORE.WORMS.expansion import hyperopt as _hyp  # noqa: E402
except Exception:  # pragma: no cover - optional by design
    _hyp = None


def _tuned(name, default):
    """A gate threshold from the hyper-optimizer, or its literal default.

    Every reader falls back to the value it shipped with, so hyper-optimization
    cannot become a new way for the loop to fail to start: with `hyperopt`
    missing, unreadable, or switched off, this module behaves exactly as before.
    """
    if _hyp is None:
        return default
    try:
        return _hyp.get(name, default)
    except Exception:
        return default

_LOG_DIR = os.path.join(_AZAZAEL_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG = open(os.path.join(_LOG_DIR, "wiring_cycle.log"), "a", encoding="utf-8")

_STATE_DIR = os.environ.get("AZAZAEL_WIRING_STATE_DIR") or os.path.join(
    _AZAZAEL_ROOT, "state")
os.makedirs(_STATE_DIR, exist_ok=True)

PROMOTED_JSON = os.environ.get("AZAZAEL_WIRING_PROMOTED") or os.path.join(
    _STATE_DIR, "wiring_promoted.json")
PREV_JSON = PROMOTED_JSON + ".prev.json"
LINEAGE_JSONL = os.path.join(_STATE_DIR, "wiring_lineage.jsonl")
STREAK_JSON = os.path.join(_STATE_DIR, "wiring_streak.json")
CANARY_JSON = os.path.join(_STATE_DIR, "wiring_canary.json")

#: The carried-forward winner. THE FIX FOR AN UNREACHABLE GATE.
#:
#: The streak rule is "the SAME candidate must win L1_MIN_WINS consecutive
#: cycles". Every cycle used to generate `L1_CANDIDATES` FRESH random children
#: and discard them afterwards, so the identical winner had to be re-invented by
#: chance in each of three consecutive cycles. Measured on the live system: two
#: wins on `wg-bba8ae222080defd` (13:58, 14:00) and never a third, with a delta
#: of 0.001777 -- the gate was not strict, it was unreachable, which is why the
#: loop had run for hours without ever promoting anything.
#:
#: Carrying the winner forward turns the gate into a hill-climb: the elite is
#: re-scored against the CURRENT snapshot each cycle, so consecutive wins are
#: about the candidate still being good, not about it being regenerated.
#: It is dropped as soon as it stops winning, so the search re-explores instead
#: of pinning itself to one basin.
ELITE_JSON = os.path.join(_STATE_DIR, "wiring_elite.json")


def _flag(name, default="1"):
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "no", "")


#: Kill switch for the effector (the reader in `worm_registry`).
L1_ENABLED = _flag("AZAZAEL_WIRING_L1")
#: Kill switch for writing a promotion. Trials are still recorded when off.
L1_APPLY = _flag("AZAZAEL_WIRING_L1_APPLY")

# ── Gate ────────────────────────────────────────────────────────────────────
#: Minimum EFFECT SIZE a candidate must beat the incumbent by.
#:
#: RETRACTED, then re-derived. On 2026-09-21 this became an absolute 0.002 on the
#: reasoning that promotions on +0.000555, +0.000272 and +0.000111 against a
#: credit spread of 0.1228 were noise. The first half was the error: 0.1228 is the
#: spread of the CREDIT SIGNAL, and the objective is not the credit signal. It is
#: `fitness.value`, a priority-WEIGHTED MEAN over the observed active fleet, so a
#: single-organ mutation moves it by roughly `step / total_weight * (credit_i -
#: mean)`. MEASURED on the live promoted genome over 60 children per operator:
#:
#:     mutate_flip_active      best_delta +0.00008363   (24x BELOW the 0.002 floor)
#:     mutate_bump_priority    best_delta +0.00001666   (120x BELOW it)
#:     mutate_adjust_cooldown  best_delta +0.00000000
#:
#: So 0.002 was above the largest effect any operator can produce. The floor did
#: not filter noise; it made the gate UNREACHABLE, and the search ran for 33
#: recorded cycles producing no promotion at all while every report said `ok`.
#: The deltas it was meant to reject were, at ~1e-4, about two organ-units on an
#: objective whose resolution is ~5e-5 -- small differences, not re-readings of
#: one measurement. The honest conclusion is that with a paired, deterministic
#: comparison there is no measurement noise for an absolute constant to filter:
#: the meaningfulness test that belongs here is REPETITION (L1_MIN_WINS) and the
#: canary that reverts a promotion that does not hold. What remains is an epsilon
#: that rejects zero and floating-point dust.
#:
#: `margin_reachable` in every report says whether the floor is above the best
#: delta this cycle, so a floor set too high can never be silent again -- which
#: is the only reason the mistake lasted 33 cycles.
L1_MARGIN_FLOOR = 1e-9
L1_MARGIN = max(L1_MARGIN_FLOOR, float(_tuned("wiring.margin_floor", 0.0)))
#: The SAME candidate fingerprint must win this many consecutive cycles. One
#: winning draw is not evidence — that was the lesson of 0.75 vs 2.4-30.2 noise.
#: CALIBRATED by `hyperopt_cycle` (see the PARAM_SPACE entry); the literals here
#: are the fallback, not the truth.
L1_MIN_WINS = int(_tuned("wiring.min_wins", 3))
#: How many mutated candidates to propose per cycle.
L1_CANDIDATES = 12
#: Evidence floor. Below either of these a result is REPORTED, never acted on.
#: Floor-13 status (2026-10-01): evaluated in tests ONLY
#: (tests/test_wiring_cycle.py pins L1_MIN_OBSERVED=13 in-fixture and proves
#: the gate works there). Production follows hyperopt (`wiring.min_observed`,
#: gen 134 = 6); promoting 13 to production requires a hyperopt evaluation,
#: never a hand-edit — settled, not pending.
L1_MIN_OBSERVED = int(_tuned("wiring.min_observed", 3))
L1_MIN_EVIDENCE = float(_tuned("wiring.min_evidence", 0.25))
#: Elitist replacement. Measured live once the elite was carried forward: the
#: streak reached 2 repeatedly and then reset, because a FRESH child with a
#: barely-larger delta (0.000555 vs 0.000461, i.e. ~1e-4 apart) displaced the
#: carried winner every cycle. Those two deltas are the same measurement twice,
#: so treating the larger as "better" is reading noise as progress.
#: A fresh child must therefore beat the elite by this RELATIVE margin to take
#: its place; otherwise the elite keeps its streak. Calibrated by hyperopt.
L1_ELITE_REPLACE_RATIO = float(_tuned("wiring.elite_replace_ratio", 0.5))
#: Canary: revert if the promoted genome's realised fitness decays by more than
#: this against its fitness at promotion time.
L1_CANARY_TOLERANCE = 0.05


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        _LOG.write(line + "\n")
        _LOG.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path, payload):
    """Write via a temp file + replace.

    `os.replace` on Windows is atomic within a volume, so the effector's reader
    can never observe a half-written genome — the same failure the chain lock
    work hit, where a partial write was indistinguishable from "no data".
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _append_lineage(record):
    try:
        with open(LINEAGE_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except Exception:
        pass


def _lineage_size():
    try:
        with open(LINEAGE_JSONL, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def _worms_tree_fingerprint():
    """(file count, newest mtime_ns) over the worm source tree.

    The discovery it guards costs ~2.2s (measured 2026-10-01: 2,385 imports
    per cycle, 64% of a 3.46s run_cycle). A tree that did not change cannot
    register new worms, so re-discovery is pure overhead; any added, removed,
    or edited file changes the fingerprint and re-arms discovery. Fail-open:
    an unreadable tree returns None, which never matches, so discovery runs.
    """
    try:
        root = os.path.join(_AZAZAEL_ROOT, "src", "CORE", "WORMS")
        latest = 0
        count = 0
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if fn.endswith(".py"):
                    try:
                        latest = max(latest, os.stat(
                            os.path.join(dirpath, fn)).st_mtime_ns)
                    except OSError:
                        return None
                    count += 1
        return (count, latest) if count else None
    except Exception:
        return None


_REGISTRY_CACHE = {"fp": None, "registry": None}


def load_registry(refresh=False):
    """The registry, fully discovered.

    `WORM_REGISTRY` only holds whatever happened to be imported. Importing the
    module alone yields a PARTIAL registry (measured 2026-09-21: 111 organs
    against 232 discovered), and a genome scored against a partial registry would
    treat every undiscovered organ as absent - the same silent-shrinking defect as
    `search_space_closed`, one level down. So discovery runs first, fail-open: a
    discovery error logs and the cycle continues on whatever is registered.

    Discovery is cached per tree fingerprint (see `_worms_tree_fingerprint`):
    repeat cycles in one process skip the ~2.2s re-import. `refresh=True` or
    `AZAZAEL_WIRING_REFRESH=1` forces re-discovery.
    """
    from CORE.worm_registry import WORM_REGISTRY
    if _flag("AZAZAEL_WIRING_DISCOVER", "1"):
        if refresh or _flag("AZAZAEL_WIRING_REFRESH", "0"):
            _REGISTRY_CACHE.update(fp=None, registry=None)
        else:
            fp = _worms_tree_fingerprint()
            if (fp is not None and fp == _REGISTRY_CACHE["fp"]
                    and _REGISTRY_CACHE["registry"] is not None):
                return dict(_REGISTRY_CACHE["registry"])
        try:
            from CORE.worm_registry import discover_worms
            discover_worms()
        except Exception as e:
            log("discover_worms failed (%s) - using partial registry" % e)
        _REGISTRY_CACHE.update(fp=_worms_tree_fingerprint(),
                               registry=dict(WORM_REGISTRY))
        return dict(WORM_REGISTRY)
    return dict(WORM_REGISTRY)


def eligible_organs(registry):
    """Organs the genome may touch. Excludes the modifier itself."""
    return sorted(n for n in registry if wg.is_evolvable_organ(n))


def prime_caches():
    """Fill the discovery/credit caches now; first-cycle cost paid at startup.

    Best-effort, never raises — the cycle is correct (if slower) without it.
    Called by `startup_warm` after registry warm; also usable standalone.
    Returns (registry_cached, credit_cached).
    """
    reg_ok, cred_ok = False, False
    try:
        load_registry()
        reg_ok = _REGISTRY_CACHE.get("registry") is not None
    except Exception as e:
        log("prime registry failed (non-fatal): %s" % e)
    try:
        if load_credit() is not None:
            cred_ok = _CREDIT_CACHE.get("credit") is not None
    except Exception as e:
        log("prime credit failed (non-fatal): %s" % e)
    return reg_ok, cred_ok


def _store_fingerprint(store):
    """(path, mtime_ns, size) of the telemetry store behind a credit handle.

    Building a credit costs ~1.2s (measured 2026-10-01: 20k outcome + 5k
    choice rows, 35% of a 3.46s run_cycle). Telemetry only grows by writes,
    and every write changes mtime or size, so an unchanged fingerprint means
    an unchanged credit. Fail-open: no visible file returns None, which never
    matches, so the credit rebuilds exactly as before.
    """
    try:
        for attr in ("db_path", "path", "store_path", "filename", "db_file"):
            p = getattr(store, attr, None)
            if isinstance(p, str) and os.path.isfile(p):
                st = os.stat(p)
                return (p, st.st_mtime_ns, st.st_size)
        inner = getattr(store, "store", None)
        if inner is not None and inner is not store:
            return _store_fingerprint(inner)
    except Exception:
        pass
    return None


_CREDIT_CACHE = {"fp": None, "credit": None}


def load_credit(refresh=False):
    """Grounded per-organ reward from observed execution telemetry.

    Cached per store fingerprint (see `_store_fingerprint`): repeat cycles
    against an unwritten store skip the ~1.2s rebuild. The fingerprint is
    re-read from the store handle every call, so a write between cycles is a
    guaranteed miss — a hit can never serve stale telemetry. `refresh=True`
    forces a rebuild. Fail-open throughout: any fingerprint miss rebuilds.
    """
    try:
        from CORE.WORMS.expansion.spawn_credit import (
            build_spawn_credit, resolve_store)
        store = resolve_store()
        if not refresh and store is not None:
            fp = _store_fingerprint(store)
            if (fp is not None and fp == _CREDIT_CACHE.get("fp")
                    and _CREDIT_CACHE.get("credit") is not None):
                return _CREDIT_CACHE["credit"]
        from CORE.WORMS.expansion.spawn_credit import live_spawn_credit
        credit = live_spawn_credit()
        _CREDIT_CACHE.update(
            fp=_store_fingerprint(store), credit=credit)
        return credit
    except Exception as e:
        log("credit unavailable: %s" % e)
        return None


def credit_snapshot(credit):
    """(rewards map, snapshot fingerprint).

    The fingerprint is recorded so landscape drift between cycles is observable
    rather than silent — the same reason `pool_fingerprint` exists in
    `evolution_cycle`.
    """
    rewards = dict(credit.rewards)
    counts = {w: int(o.n) for w, o in credit.outcomes.items()}
    fp = "%s|%d|%d" % (
        ",".join("%s:%d" % (w, counts[w]) for w in sorted(counts)),
        credit.n_outcome_rows, credit.n_choice_rows)
    return rewards, "cs-" + hashlib.sha256(fp.encode("utf-8")).hexdigest()[:16], counts


def load_incumbent(registry):
    """The promoted genome, or the seed (every eligible organ active)."""
    data = _read_json(PROMOTED_JSON)
    if data and data.get("active"):
        try:
            return wg.WiringGenome.from_dict(data), "promoted"
        except Exception as e:
            log("promoted genome unreadable (%s) — falling back to seed" % e)
    return wg.seed_genome(registry, origin="init"), "seed"


# ---------------------------------------------------------------------------
# Canary — auto-revert a promotion that did not hold
# ---------------------------------------------------------------------------

def canary_check(genome, rewards, eligible, counts):
    """Compare realised fitness against fitness at promotion time.

    A canary that never fires is indistinguishable from no canary, so this
    records its decision every cycle (`evaluated`), and only the revert is
    conditional. Undersampled evidence is not a reason to revert — that would
    turn snapshot noise into a flip-flop — so the check is skipped, visibly,
    until the promoted genome has the evidence floor again.
    """
    state = _read_json(CANARY_JSON)
    if not state or state.get("fingerprint") != genome.fingerprint():
        return {"status": "not-promoted"}
    baseline = state.get("baseline")
    if baseline is None:
        return {"status": "no-baseline"}

    score = wg.fitness(genome, rewards, eligible)
    if not score.get("ok"):
        return {"status": "unscorable"}
    observed = score.get("observed", 0)
    if observed < L1_MIN_OBSERVED:
        return {"status": "undersampled", "observed": observed}

    delta = score["value"] - float(baseline)
    if delta < -L1_CANARY_TOLERANCE:
        prev = _read_json(PREV_JSON)
        if prev and prev.get("active"):
            _write_json(PROMOTED_JSON, prev)
            _append_lineage({"cycle_kind": "canary-revert", "at": _now(),
                             "fingerprint": genome.fingerprint(),
                             "delta": round(delta, 6),
                             "reverted_to": prev.get("fingerprint")})
            log("CANARY REVERT: %s decayed %.4f -> restored %s"
                % (genome.fingerprint(), delta, prev.get("fingerprint")))
            return {"status": "reverted", "delta": round(delta, 6)}
        return {"status": "would-revert-no-prev", "delta": round(delta, 6)}
    return {"status": "held", "delta": round(delta, 6), "observed": observed}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def promotion_quality(limit=20):
    """Held-rate over recent promotions. REPORT ONLY — never gates.

    Counts `promotion` vs `canary-revert` rows in the lineage tail: the
    fraction of promotions still standing. A gate built on this would need
    its own negative control first (R2); until then it is a readout, and a
    readout must not hide a bad number (R5).
    """
    try:
        with open(LINEAGE_JSONL, "r", encoding="utf-8") as fh:
            kinds = []
            for line in fh:
                try:
                    kinds.append(json.loads(line).get("cycle_kind"))
                except Exception:
                    continue
    except Exception:
        return {"promotions": 0, "reverts": 0, "held_rate": None}
    tail = [k for k in kinds if k in ("promotion", "canary-revert")][-limit:]
    promos = sum(1 for k in tail if k == "promotion")
    reverts = sum(1 for k in tail if k == "canary-revert")
    return {"promotions": promos, "reverts": reverts,
            "held_rate": (round((promos - reverts) / promos, 4)
                          if promos else None)}


def promotion_gate_ok(min_history: int = 3, min_held_rate: float = 0.5,
                      quality: dict | None = None) -> dict:
    """Advisory promotion-quality gate. ADVISORY ONLY — never blocks.

    R2 requires a negative control before any gate enforces, so this reports
    a verdict instead of acting on it. Its controls (in test_wiring_cycle):
    empty history does not block, all-held does not block, majority-reverted
    advises against. Enforcement is a separate, operator-gated change.
    """
    q = dict(quality) if quality is not None else promotion_quality()
    promos = int(q.get("promotions") or 0)
    if promos < max(1, int(min_history)):
        return {"block": False, "reason": "insufficient history",
                "quality": q}
    held = q.get("held_rate")
    if held is None or held >= float(min_held_rate):
        return {"block": False, "reason": "quality sufficient", "quality": q}
    return {"block": True, "reason": "held_rate %.3f below %.3f over %d" % (
        held, min_held_rate, promos), "quality": q}


# ---------------------------------------------------------------------------
# The cycle
# ---------------------------------------------------------------------------

def _load_elite(incumbent):
    """The carried-forward winner, or None when it does not belong here.

    Guarded by the parent fingerprint: an elite mutated from a genome that is no
    longer the incumbent (because a promotion or a canary revert moved it) is
    not a contender for this cycle, it is a memory of a different search.
    """
    data = _read_json(ELITE_JSON) or {}
    if not data.get("genome"):
        return None
    if data.get("parent") and data.get("parent") != incumbent.fingerprint():
        return None
    try:
        return wg.WiringGenome.from_dict(data["genome"])
    except Exception as e:
        log("elite unreadable (%s) — re-exploring" % str(e)[:100])
        return None


def _streak_state():
    return _read_json(STREAK_JSON) or {"fingerprint": None, "wins": 0}


def _update_streak(best_fp, accepted):
    """Same candidate must win consecutively; a different candidate restarts.

    This is what stops a cycle-to-cycle winner being crowned on one lucky draw —
    each win is a fresh snapshot, so `L1_MIN_WINS` consecutive wins by one genome
    is the accumulation that a single unpaired margin could not provide.
    """
    state = _streak_state()
    if not accepted or not best_fp:
        state = {"fingerprint": None, "wins": 0}
    elif state.get("fingerprint") == best_fp:
        state["wins"] = int(state.get("wins", 0)) + 1
    else:
        state = {"fingerprint": best_fp, "wins": 1}
    state["at"] = _now()
    _write_json(STREAK_JSON, state)
    return state


def promote(candidate, report, rewards, eligible, registry):
    """Write the accepted genome, keeping a one-step rollback.

    The FIRST promotion also writes a `.prev.json` — the seed genome, i.e. the
    state the system was in before anything was ever promoted. Without that, the
    very first promotion would have no rollback point and the canary's revert
    path would be unreachable exactly when the system has the least evidence
    behind the change. "Rollback is restoring one file" has to hold for the first
    promotion too, or it is a claim about the second promotion onward.
    """
    current = _read_json(PROMOTED_JSON)
    if current:
        _write_json(PREV_JSON, current)
    else:
        _write_json(PREV_JSON, wg.seed_genome(registry, origin="pre-first-promotion").to_dict())
    payload = candidate.to_dict()
    payload["promoted_at"] = _now()
    payload["promoted_by"] = "wiring_cycle"
    payload["paired_delta"] = report.get("delta")
    _write_json(PROMOTED_JSON, payload)

    score = wg.fitness(candidate, rewards, eligible)
    _write_json(CANARY_JSON, {
        "fingerprint": candidate.fingerprint(),
        "baseline": score.get("value"),
        "observed": score.get("observed"),
        "at": _now(),
    })
    _append_lineage({"cycle_kind": "promotion", "at": _now(),
                     "fingerprint": candidate.fingerprint(),
                     "delta": round(float(report.get("delta") or 0.0), 6)})
    log("PROMOTED %s delta=%+.6f observed=%d coverage=%.3f"
        % (candidate.fingerprint(), report.get("delta", 0.0),
           score.get("observed", 0), score.get("coverage", 0.0)))


def run_cycle(apply_promotion=False, candidates=None, seed=None):
    """One L1 cycle. Returns a report dict; never raises."""
    started = time.time()
    report = {"ok": False, "seed": seed, "at": _now(),
              "effector_enabled": L1_ENABLED, "apply": bool(apply_promotion),
              "promoted": False, "reason": None}
    try:
        registry = load_registry()
        eligible = eligible_organs(registry)
        report["registry_organs"] = len(registry)
        report["eligible_organs"] = len(eligible)
        if not eligible:
            report["reason"] = "no eligible organs"
            return report

        credit = load_credit()
        if credit is None:
            report["reason"] = "credit unavailable"
            return report
        rewards, snapshot_id, counts = credit_snapshot(credit)
        report["credit_snapshot"] = snapshot_id
        report["observed_organs"] = len(rewards)
        report["attribution_mode"] = credit.attribution_mode

        # SEARCH RESOLUTION, stated rather than implied. A mutation can only be
        # selected for if it touches an organ the objective can see, so the
        # fraction of eligible organs carrying any observation is the fraction of
        # the genotype the search can actually discriminate. Measured live
        # 2026-09-21: 15 of 211 organs, i.e. ~93% of mutations are provably inert
        # — which is why observation coverage, not search power, is the binding
        # constraint on this rung.
        visible = len(set(eligible) & set(rewards))
        report["visible_organs"] = visible
        report["search_resolution"] = (round(visible / len(eligible), 6)
                                       if eligible else 0.0)

        incumbent, origin = load_incumbent(registry)
        report["incumbent"] = incumbent.fingerprint()
        report["incumbent_origin"] = origin

        canary = canary_check(incumbent, rewards, eligible, counts)
        report["canary"] = canary
        if canary.get("status") == "reverted":
            incumbent, origin = load_incumbent(registry)
            report["incumbent"] = incumbent.fingerprint()
            report["incumbent_origin"] = "post-canary-revert"

        base_score = wg.fitness(incumbent, rewards, eligible)
        report["incumbent_score"] = base_score

        rng = random.Random(seed if seed is not None else int(time.time()))
        n = L1_CANDIDATES if candidates is None else int(candidates)

        # Only operators the objective and effector can act on. See
        # `wiring_genome.OBSERVABLE_MUTATIONS` for why: measured live, 40
        # mutations with all three operators enabled produced 0 accepted
        # candidates, because a cooldown/budget child scores identically to its
        # parent (delta exactly 0.0).
        operators = list(wg.OBSERVABLE_MUTATIONS)
        report["operators"] = [op.__name__ for op in operators]
        report["operators_excluded"] = [op.__name__
                                        for op in wg.NOT_YET_OBSERVABLE_MUTATIONS]

        # Contenders = the carried-forward elite (if it belongs to THIS
        # incumbent) plus `n` fresh children. The elite is what makes the streak
        # reachable; without it the gate demanded a randomly-regenerated winner.
        pool: List[tuple] = []
        trials: List[dict] = []
        elite = _load_elite(incumbent)
        if elite is not None:
            pool.append(("elite_carried", elite))
        for i in range(n):
            operator = operators[i % len(operators)]
            try:
                pool.append((operator.__name__, operator(incumbent, eligible, rng)))
            except Exception as e:
                trials.append({"i": i, "ok": False, "error": str(e)})
        report["elite_carried"] = elite is not None

        best = None
        elite_delta = None
        for i, (label, child) in enumerate(pool):
            # Lineage is recorded for every trial, not just the winner: a
            # proposal that loses is the evidence that the gate discriminates,
            # and discarding it is how "no data" becomes "no problem".
            # The operator returns a fully-formed child (generation+1, parent set,
            # descriptive origin) — re-wrapping it here would overwrite `origin`
            # and destroy per-operator attribution, which the first version did:
            # every trial reported as "l1-mutation" and the effect table could not
            # say WHICH operator was inert.
            problems = wg.validate(child, registry)
            if problems:
                trials.append({"i": i, "ok": False, "fingerprint":
                               child.fingerprint(), "invalid": problems[:3]})
                continue

            cmp = wg.paired_compare(child, incumbent, rewards, eligible,
                                    margin=L1_MARGIN)
            if not cmp.get("ok"):
                trials.append({"i": i, "ok": False, "reason": cmp.get("reason")})
                continue

            cand = cmp["candidate"]
            rec = {
                "i": i, "ok": True,
                "operator": label,
                "origin": child.origin,
                "fingerprint": child.fingerprint(),
                "delta": cmp["delta"],
                "accept": cmp["accept"],
                "observed": cand.get("observed"),
                "coverage": cand.get("coverage"),
                "evidence_ratio": cand.get("evidence_ratio"),
                "n_active": cand.get("n_active"),
            }
            trials.append(rec)
            if label == "elite_carried":
                elite_delta = cmp["delta"]
            if not cmp["accept"]:
                continue
            if best is None:
                best = (child, cmp, label)
            elif best[2] == "elite_carried":
                # The carried winner holds its place unless a fresh child beats
                # it by a real margin. Without this the streak never reaches
                # L1_MIN_WINS: the search hops between statistically identical
                # children and restarts the count every cycle.
                floor = float(best[1]["delta"]) * (1.0 + L1_ELITE_REPLACE_RATIO)
                if cmp["delta"] > floor:
                    best = (child, cmp, label)
            elif cmp["delta"] > best[1]["delta"]:
                best = (child, cmp, label)

        # Win-stay / lose-switch. The accepted winner becomes next cycle's
        # elite, which is what gives the streak something stable to count. When
        # nothing is accepted the elite is cleared, so the search re-explores
        # rather than re-scoring a genome the current snapshot already rejected.
        if best is not None:
            _write_json(ELITE_JSON, {
                "genome": best[0].to_dict(),
                "parent": incumbent.fingerprint(),
                "delta": best[1].get("delta"),
                "at": _now(),
            })
            report["elite_saved"] = best[0].fingerprint()
        else:
            if _read_json(ELITE_JSON):
                _write_json(ELITE_JSON, {})
            report["elite_saved"] = None

        report["trials"] = len(trials)
        report["accepted"] = sum(1 for t in trials if t.get("accept"))
        # A null operator is a search dimension that does not exist. Reported
        # per cycle, so an operator silently ceasing to matter is visible rather
        # than merely unmeasured.
        _by_op: Dict[str, List[bool]] = {}
        for t in trials:
            if t.get("ok"):
                _by_op.setdefault(str(t.get("operator")), []).append(
                    bool(t.get("accept") or abs(float(t.get("delta") or 0.0)) > 0))
        report["operator_effect"] = {
            op: {"n": len(v), "moved": sum(1 for x in v if x)}
            for op, v in sorted(_by_op.items())}
        report["best"] = ({"fingerprint": best[0].fingerprint(),
                           "delta": best[1]["delta"],
                           "label": best[2]} if best else None)
        report["elite_delta"] = elite_delta
        report["elite_replace_ratio"] = L1_ELITE_REPLACE_RATIO
        # Whether the floor CAN be cleared, and by how much. A floor above every
        # achievable delta makes the gate unreachable, and an unreachable gate
        # reports `ok`, `promoted: false` and `no candidate beat the incumbent`
        # for ever -- which is exactly what an absolute 0.002 did for 33 cycles.
        # Published per cycle so it cannot be silent again.
        _best_delta = float(best[1]["delta"]) if best else None
        report["margin"] = L1_MARGIN
        report["best_delta"] = _best_delta
        report["margin_reachable"] = (_best_delta is not None
                                     and _best_delta > L1_MARGIN)

        # ── Gate: evidence floor, then the streak ──────────────────────────
        best_fp = best[0].fingerprint() if best else None
        accepted = False
        if best is None:
            report["reason"] = "no candidate beat the incumbent"
        else:
            cand = best[1]["candidate"]
            # Evidence is measured against the pool of organs that HAVE any
            # observation, not against the candidate's own active set.
            #
            # The first version used wiring_genome's evidence_ratio (observed /
            # active), which asks "what share of the fleet has data" - a COVERAGE
            # question the fitness floor already answers - and so made promotion
            # arithmetically impossible for any genome activating the full fleet:
            # 8 observed organs over a 94-organ active set is 0.085, and the
            # coverage floor of 0.5 caps the best achievable ratio below the
            # threshold. The question that actually matters is "does this verdict
            # rest on a meaningful share of the evidence that EXISTS", so a genome
            # touching all 8 observed organs scores 1.0 rather than 0.085.
            # The evidence pool is organs that are BOTH evolvable and observed.
            # Using `len(rewards)` counts organs the genome is not allowed to
            # touch (infra worms like SpineCycleWorm carry credit but are
            # excluded by construction), which inflates the denominator and makes
            # a candidate look thinner than it is.
            available = visible
            covered = len(set(best[0].active) & set(eligible) & set(rewards))
            cover_ratio = (covered / available) if available else 0.0
            report["best_observed"] = covered
            report["best_evidence_available"] = available
            report["best_evidence_ratio"] = round(cover_ratio, 6)
            report["best_active"] = len(best[0].active)
            if covered < L1_MIN_OBSERVED or cover_ratio < L1_MIN_EVIDENCE:
                # Reported, not acted on. This is defect #2 above: the earlier
                # pipeline promoted on measurements that carried no information.
                report["reason"] = ("evidence too thin to promote "
                                    "(covered=%d/%d observed, ratio=%.3f)"
                                    % (covered, available, cover_ratio))
            else:
                accepted = True

        streak = _update_streak(best_fp if accepted else None, accepted)
        report["streak"] = streak
        report["streak_required"] = L1_MIN_WINS
        report["promotion_quality"] = promotion_quality()
        report["promotion_gate"] = promotion_gate_ok(
            quality=report["promotion_quality"])

        if accepted and streak.get("wins", 0) >= L1_MIN_WINS:
            if not L1_APPLY:
                report["reason"] = "gate passed but AZAZAEL_WIRING_L1_APPLY=0"
            elif not apply_promotion:
                report["reason"] = "gate passed but --apply not given"
            else:
                promote(best[0], best[1], rewards, eligible, registry)
                report["promoted"] = True
                report["reason"] = "promoted"
                _write_json(STREAK_JSON, {"fingerprint": None, "wins": 0,
                                          "at": _now()})
        elif accepted:
            report["reason"] = ("awaiting %d consecutive wins (have %d)"
                                % (L1_MIN_WINS, streak.get("wins", 0)))

        report["ok"] = True
    except Exception as e:
        report["reason"] = "cycle error: %s" % e
    finally:
        report["duration_ms"] = int((time.time() - started) * 1000)
        try:
            _append_lineage({
                "cycle_kind": "l1", "at": report["at"], "ok": report["ok"],
                # `apply` distinguishes a cycle that could promote from a dry
                # evaluation of the same candidates. This block is in a
                # `finally`, so it records a row whether or not promotion was
                # allowed -- correct for an evaluation ledger, but without this
                # field the ledger could not tell the two apart, and a reader
                # asking "how many cycles has this search had?" counts dry runs
                # as cycles. Rows written before this change have no `apply` key
                # and are reported as unknown rather than guessed at.
                "apply": report.get("apply"),
                "seed": report.get("seed"),
                "credit_snapshot": report.get("credit_snapshot"),
                "incumbent": report.get("incumbent"),
                "best": report.get("best"),
                "accepted": report.get("accepted"),
                "trials": report.get("trials"),
                "promoted": report.get("promoted"),
                "reason": report.get("reason"),
                # The floor and the best delta it was compared against. Without
                # them the ledger cannot distinguish "nothing beat the incumbent"
                # (a mature search) from "nothing CAN beat the incumbent" (an
                # unreachable gate) -- and the second read exactly like the first
                # for 33 cycles, because the only number recorded was the one that
                # said no.
                "margin": report.get("margin"),
                "best_delta": report.get("best_delta"),
                "margin_reachable": report.get("margin_reachable"),
                "canary": report.get("canary"),
                "duration_ms": report.get("duration_ms"),
            })
        except Exception:
            pass
    return report


def status():
    """Current state, for the CLI and for `--status` in the chain log."""
    registry = load_registry()
    eligible = eligible_organs(registry)
    incumbent, origin = load_incumbent(registry)
    credit = load_credit()
    out = {
        "registry_organs": len(registry),
        "eligible_organs": len(eligible),
        "incumbent": incumbent.fingerprint(),
        "incumbent_origin": origin,
        "incumbent_n_active": len(incumbent.active),
        "streak": _streak_state(),
        "streak_required": L1_MIN_WINS,
        "lineage_rows": _lineage_size(),
        "effector_enabled": L1_ENABLED,
        "apply_enabled": L1_APPLY,
        "promoted_path": PROMOTED_JSON,
    }
    if credit is not None:
        rewards, snapshot_id, _ = credit_snapshot(credit)
        out["credit_snapshot"] = snapshot_id
        out["observed_organs"] = len(rewards)
        out["attribution_mode"] = credit.attribution_mode
        out["incumbent_score"] = wg.fitness(incumbent, rewards, eligible)
    return out


def main():
    ap = argparse.ArgumentParser(description="L1 wiring cycle")
    ap.add_argument("--status", action="store_true",
                    help="print incumbent, streak and lineage state, then exit")
    ap.add_argument("--apply", action="store_true",
                    help="allow a promotion to be written when the gate passes")
    ap.add_argument("--candidates", type=int, default=None,
                    help="mutations to propose this cycle (default %d)"
                         % L1_CANDIDATES)
    ap.add_argument("--seed", type=int, default=None, help="RNG seed (replay)")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(status(), indent=2, sort_keys=True, default=str))
        return 0

    report = run_cycle(apply_promotion=args.apply, candidates=args.candidates,
                       seed=args.seed)
    log("L1 ok=%s promoted=%s trials=%s accepted=%s best=%s reason=%s"
        % (report.get("ok"), report.get("promoted"), report.get("trials"),
           report.get("accepted"), report.get("best"), report.get("reason")))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
