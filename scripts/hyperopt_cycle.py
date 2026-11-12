#!/usr/bin/env python3
# coding: utf-8
"""HYPEROPT CYCLE -- one calibration step per run, evaluated in time.

The outer loop of the self-improvement stack. `evolution_cycle` evolves gene
values, `wiring_cycle` evolves dispatch ORDER, `morphogenesis` generates
components; this one evolves the THRESHOLDS all three are judged against. Those
thresholds were typed by hand and nothing had ever revised them, so the
system's sensitivity was fixed while everything it governed moved.

THE GATE, AND WHY IT IS NOT THE L1 GATE

`wiring_cycle` can score candidates against a *single snapshot* before choosing:
both genomes are evaluated on the same reward table, so a paired comparison is
possible and a streak-of-wins rule makes sense.

A hyper-parameter cannot be scored that way. Its effect only exists once it is
installed and the system has run under it. So this loop is deliberately
different, and each difference is load-bearing:

  * **one dimension per cycle** -- attribution. A candidate that moved three
    thresholds could not be credited to any of them.
  * **MIN_DWELL_CYCLES between proposals** -- an effect that has not had time
    to appear cannot be measured, so proposing again immediately would replace
    a calibration with noise.
  * **baseline recorded at promotion, re-measured next cycle** -- the score of
    a candidate is what the *running system* then produced, never a model of it.
  * **revert on measured decay** -- the same canary shape as L1, with the same
    tolerance, so "this made things worse" is acted on rather than argued about.
  * **an evidence floor that REFUSES rather than promotes** -- below the floor a
    step is reported and skipped. Promoting on thin evidence is how a loop
    ratchets itself into a corner its own measurements cannot see.

KILL SWITCHES
    AZAZAEL_HYPEROPT=0        the readers fall back to their literal defaults
    AZAZAEL_HYPEROPT_APPLY=0  steps are still recorded, nothing is written

Usage:
  python scripts/hyperopt_cycle.py            # one step (dry: proposes, does not write)
  python scripts/hyperopt_cycle.py --apply    # one step, promotions land
  python scripts/hyperopt_cycle.py --status   # current params, objective, canary
  python scripts/hyperopt_cycle.py --revert   # restore the previous parameter set
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AZAZAEL_HYPEROPT_DIR",
                      os.path.join(_ROOT, "state", "hyperopt"))

from CORE.WORMS.expansion import hyperopt as hyp  # noqa: E402

STATE_DIR = hyp._STATE_DIR
PARAMS_JSON = hyp.PARAMS_JSON
PREV_JSON = str(PARAMS_JSON) + ".prev.json"
LINEAGE_JSONL = os.path.join(str(STATE_DIR), "hyperopt_lineage.jsonl")
STREAK_JSON = os.path.join(str(STATE_DIR), "hyperopt_dwell.json")

LOG_PATH = os.path.join(_ROOT, "logs", "hyperopt_cycle.log")


def _flag(name, default="1"):
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "no", "")


#: Reader kill switch. Off => every reader uses its own literal default.
HYPEROPT_ENABLED = _flag("AZAZAEL_HYPEROPT")
#: Write kill switch. Off => steps are recorded but never promoted.
HYPEROPT_APPLY = _flag("AZAZAEL_HYPEROPT_APPLY")

#: Cycles a promotion must be left alone before another proposal is allowed.
#: The chain runs this hourly, so three cycles is roughly three hours of the
#: system running under one calibration -- enough for the outcome taps to move.
MIN_DWELL_CYCLES = 3

#: Revert a promotion whose objective fell by more than the tolerance below its
#: baseline. The tolerance is DERIVED every cycle from the objective's own
#: resolution (`hyp.canary_tolerance`), not a constant: the value this replaces
#: was 0.05, measured at 12x larger than the whole achievable shift in the
#: objective, so it could not fire and reported `held` for every promotion.
#: This name is kept only for readers that referenced it.
CANARY_TOLERANCE = hyp.CANARY_TOLERANCE_FLOOR


def log(msg):
    line = "%s %s" % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path, payload):
    """Temp file + replace, so a reader never sees a half-written parameter set.

    `os.fspath` at the top because a caller may pass either a str or a Path, and
    this function used to assume str: `hyperopt.PARAMS_JSON` is a `Path` while
    `PREV_JSON` is built as `str(PARAMS_JSON) + ".prev.json"`, so the promoted
    set took the Path branch and died on `path + ".tmp"` with
    `unsupported operand type(s) for +: 'WindowsPath' and 'str'`. Caught and
    logged, so it never raised -- and the consequence was that L2 proposed
    calibrations every cycle, reported ``promote-write-failed``, and the durable
    parameter file was never written once. Same class as the learning cycle that
    moved 264 edges in memory and left 56 on disk. The read path already
    coerce-to-Path for the same reason; the write path now matches it.
    """
    try:
        path = os.fspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log("write failed %s (%s)" % (os.path.basename(path), str(exc)[:100]))
        return False


def _append_lineage(record):
    try:
        os.makedirs(os.path.dirname(LINEAGE_JSONL), exist_ok=True)
        with open(LINEAGE_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def _dwell_state():
    data = _read_json(STREAK_JSON) or {}
    return {"fingerprint": data.get("fingerprint", ""),
            "baseline": data.get("baseline"),
            "cycle": int(data.get("cycle", 0) or 0),
            "applied_at": data.get("applied_at", "")}


def _promote(params, obj):
    """Install `params`, remember the previous set, and set this one's baseline."""
    current = hyp.load()
    _write_json(PREV_JSON, {"params": current,
                            "fingerprint": hyp.fingerprint(current),
                            "superseded_at": _now()})
    ok = _write_json(PARAMS_JSON, {
            "params": params,
            "fingerprint": hyp.fingerprint(params),
            "gen": (_read_json(PARAMS_JSON) or {}).get("gen", 0) + 1,
            "promoted_at": _now(),
            "baseline_objective": obj.get("value"),
            "objective_evidence": obj.get("evidence"),
            # The full measurement, not just its average: a baseline whose
            # term states are not recorded cannot be told apart later from a
            # baseline that was pinned and therefore meaningless.
            "objective_terms": obj.get("terms"),
            "objective_term_states": obj.get("term_states"),
            "objective_term_weights": obj.get("term_weights"),
    })
    if ok:
        # Baseline is the objective measured BEFORE this step installed the
        # candidate, so the next cycle compares like with like.
        _write_json(STREAK_JSON, {"fingerprint": hyp.fingerprint(params),
                                  "baseline": obj.get("value"), "cycle": 0,
                                  "applied_at": _now()})
    return ok


def _revert(reason, delta=None):
    prev = _read_json(PREV_JSON)
    if not prev or not isinstance(prev.get("params"), dict):
        return {"status": "no-previous-set"}
    if not _write_json(PARAMS_JSON, {"params": prev["params"],
                                     "fingerprint": prev.get("fingerprint", ""),
                                     "gen": (_read_json(PARAMS_JSON) or {}).get("gen", 0) + 1,
                                     "reverted_at": _now(),
                                     "revert_reason": reason}):
        return {"status": "write-failed"}
    _write_json(STREAK_JSON, {"fingerprint": prev.get("fingerprint", ""),
                              "baseline": None, "cycle": 0,
                              "applied_at": _now()})
    _append_lineage({"at": _now(), "kind": "revert", "reason": reason,
                     "delta": delta, "restored": prev.get("fingerprint")})
    log("REVERT (%s%s) -> restored %s"
        % (reason, "" if delta is None else " delta=%.4f" % delta,
           prev.get("fingerprint")))
    return {"status": "reverted", "restored": prev.get("fingerprint"),
            "delta": delta}


# ---------------------------------------------------------------------------
# One step
# ---------------------------------------------------------------------------

def step(apply_promotion=False, seed=None):
    rng = random.Random(seed)
    incumbent = hyp.load()
    fp = hyp.fingerprint(incumbent)
    metrics = hyp.measure()
    obj = hyp.objective(metrics)
    health = hyp.objective_health(metrics)
    tolerance = float(health.get("canary_tolerance") or CANARY_TOLERANCE)
    dwell = _dwell_state()

    report = {"at": _now(), "fingerprint": fp, "incumbent": incumbent,
              "metrics": metrics, "objective": obj, "dwell": dwell,
              "health": health, "canary_tolerance": tolerance,
              "candidate": None, "promoted": False, "canary": {"status": "not-promoted"}}

    # --- 1. canary: did the incumbent hold its baseline? ---
    if dwell["fingerprint"] == fp and dwell["baseline"] is not None:
        if not obj["ok"]:
            report["canary"] = {"status": "unscorable", "reasons": obj.get("reasons")}
        else:
            delta = float(obj["value"]) - float(dwell["baseline"])
            if delta < -tolerance:
                report["canary"] = _revert("canary-decay", round(delta, 6))
                _append_lineage({"at": _now(), "kind": "canary-revert",
                                 "fingerprint": fp, "delta": round(delta, 6),
                                 "baseline": dwell["baseline"],
                                 "tolerance": tolerance,
                                 "value": obj["value"]})
                return report
            report["canary"] = {"status": "held", "delta": round(delta, 6),
                                "baseline": dwell["baseline"], "value": obj["value"],
                                "tolerance": tolerance}

    # --- 2. dwell: has the incumbent been observable long enough? ---
    if dwell["fingerprint"] == fp:
        dwell["cycle"] += 1
    else:
        dwell["cycle"] = 0
    if dwell["cycle"] < MIN_DWELL_CYCLES:
        report["candidate"] = {"status": "dwell", "cycle": dwell["cycle"],
                               "required": MIN_DWELL_CYCLES}
        _write_json(STREAK_JSON, {**dwell, "fingerprint": fp,
                                  "baseline": dwell["baseline"]})
        log("dwell %d/%d on %s -- no proposal this cycle"
            % (dwell["cycle"], MIN_DWELL_CYCLES, fp))
        _append_lineage({"at": _now(), "kind": "dwell", "fingerprint": fp,
                         "cycle": dwell["cycle"]})
        return report

    # --- 3. evidence floor: refuse to calibrate on noise ---
    if not obj["ok"]:
        report["candidate"] = {"status": "refused", "reasons": obj.get("reasons"),
                               "evidence": obj.get("evidence")}
        log("refused: %s" % "; ".join(obj.get("reasons") or ["no evidence"]))
        _append_lineage({"at": _now(), "kind": "refused", "fingerprint": fp,
                         "reasons": obj.get("reasons")})
        return report

    # --- 3b. can the objective select at all? ---
    # Measured 2026-09-21: it could not, and nothing said so. Every cycle
    # reported `objective=0.65`, every canary reported `held`, and every
    # promotion installed a random walk that no measurement could ever have
    # reverted -- because two of the three terms were pinned at a ceiling and
    # the third was read through a non-existent attribute. A promotion that no
    # measurement can rank is not a calibration, so it is refused and named.
    if not health.get("reachable"):
        report["candidate"] = {"status": "unreachable",
                               "reason": health.get("reason"),
                               "pinned_terms": health.get("pinned_terms"),
                               "movable_terms": health.get("movable_terms")}
        log("refused: objective cannot select -- %s" % health.get("reason"))
        _append_lineage({"at": _now(), "kind": "objective-unreachable",
                         "fingerprint": fp, "reason": health.get("reason"),
                         "pinned_terms": health.get("pinned_terms"),
                         "unmeasured_terms": health.get("unmeasured_terms")})
        return report

    # --- 4. propose exactly one dimension ---
    cand, mutation = hyp.propose(rng, base=incumbent)
    report["candidate"] = {"status": "proposed", "mutation": mutation,
                           "params": cand, "fingerprint": hyp.fingerprint(cand),
                           "objective": obj["value"]}
    if not apply_promotion:
        log("proposed (not applied): %s  objective=%.4f" % (mutation, obj["value"]))
        _append_lineage({"at": _now(), "kind": "proposed", "fingerprint": fp,
                         "mutation": mutation, "value": obj["value"]})
        return report

    if _promote(cand, obj):
        report["promoted"] = True
        log("PROMOTED %s  baseline_objective=%.4f  gen now %s"
            % (mutation, obj["value"], (_read_json(PARAMS_JSON) or {}).get("gen")))
        _append_lineage({"at": _now(), "kind": "promote", "fingerprint": fp,
                         "candidate": hyp.fingerprint(cand), "mutation": mutation,
                         "baseline": obj["value"], "evidence": obj.get("evidence")})
    else:
        report["candidate"]["status"] = "promote-write-failed"
        log("promote FAILED to write %s" % PARAMS_JSON)
    return report


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status():
    params = hyp.load()
    metrics = hyp.measure()
    obj = hyp.objective(metrics)
    health = hyp.objective_health(metrics)
    dwell = _dwell_state()
    data = _read_json(PARAMS_JSON) or {}
    n_lineage = 0
    try:
        with open(LINEAGE_JSONL, encoding="utf-8") as fh:
            n_lineage = sum(1 for ln in fh if ln.strip())
    except Exception:
        pass

    print("HYPEROPT -- outer loop over this system's own thresholds")
    print("enabled=%s apply=%s  params=%s  gen=%s  promoted_at=%s"
          % (HYPEROPT_ENABLED, HYPEROPT_APPLY, PARAMS_JSON,
             data.get("gen", 0), data.get("promoted_at", "-")))
    print("objective=%s  (ok=%s)  canary_tolerance=%s"
          % (obj.get("value"), obj.get("ok"), health.get("canary_tolerance")))
    if not obj["ok"]:
        print("  refused: %s" % "; ".join(obj.get("reasons") or ["no measurement"]))
    # Every term, its state, and whether the objective can select at all. An
    # average with no terms listed is the reading that looked healthy for the
    # entire life of this loop while the objective was a constant.
    print("terms: %s" % ", ".join(
        "%s=%s[%s]" % (k, v, (obj.get("term_states") or {}).get(k, "?"))
        for k, v in sorted((obj.get("terms") or {}).items())) or "terms: none")
    if obj.get("unmeasured_terms"):
        print("  unmeasured: %s" % ", ".join(obj["unmeasured_terms"]))
    print("can_select=%s  %s" % (health.get("reachable"), health.get("reason")))
    for w in (health.get("warnings") or []):
        print("  warning: %s" % w)
    print("evidence: outcomes=%s coverage=%.3f observed=%s/%s chosen_never_run=%s"
          % (metrics.get("n_outcome_rows"), metrics.get("coverage", 0.0),
             metrics.get("observed"), metrics.get("registered"),
             metrics.get("chosen_never_run")))
    print("dwell: fingerprint=%s cycle=%d/%d baseline=%s"
          % (dwell["fingerprint"] or "-", dwell["cycle"], MIN_DWELL_CYCLES,
             dwell["baseline"]))
    print("lineage rows: %d" % n_lineage)
    print()
    print("%-28s %-10s %-10s %-10s %s" % ("PARAMETER", "VALUE", "DEFAULT", "RANGE", "READER EFFECT"))
    for name in sorted(hyp.PARAM_SPACE):
        spec = hyp.PARAM_SPACE[name]
        print("%-28s %-10s %-10s %-10s %s"
              % (name, params[name], spec["default"],
                 "%s..%s" % (spec["low"], spec["high"]), spec["desc"][:44]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="promotions land")
    ap.add_argument("--status", action="store_true", help="report only")
    ap.add_argument("--revert", action="store_true", help="restore the previous set")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed (replay)")
    args = ap.parse_args()

    if args.status:
        return status()
    if args.revert:
        res = _revert("manual")
        print(json.dumps(res, indent=1))
        return 0 if res.get("status") == "reverted" else 1

    apply_promotion = bool(args.apply and HYPEROPT_APPLY and HYPEROPT_ENABLED)
    if args.apply and not apply_promotion:
        log("--apply ignored: AZAZAEL_HYPEROPT=%s AZAZAEL_HYPEROPT_APPLY=%s"
            % (HYPEROPT_ENABLED, HYPEROPT_APPLY))
    report = step(apply_promotion=apply_promotion, seed=args.seed)
    obj = report.get("objective") or {}
    cand = report.get("candidate") or {}
    log("step: objective=%s ok=%s canary=%s candidate=%s"
        % (obj.get("value"), obj.get("ok"),
           (report.get("canary") or {}).get("status"),
           cand.get("status")))
    # Exit non-zero when the loop could not do its job, so a scheduler shows it
    # as red instead of printing a success-shaped line.
    if not obj.get("ok") and cand.get("status") != "dwell":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
