#!/usr/bin/env python
# coding: utf-8
"""calibrate_floor.py -- propose and check the two-tier recall floors, standalone.

The two relevance floors (PMB_RECALL_MIN_BEST, the query floor; PMB_RECALL_FLOOR,
the row floor) are a property of the STORE, not of the software: they sit in the
measured gap between best-row scores of noise queries and real queries. They can
therefore be calibrated from the measured populations alone -- no daemon, no
MCP client, no embedding model -- which is what this tool does.

Standalone on purpose: it depends on nothing outside the standard library, so
any deployment that runs `apply_floor` can run this beside it to re-derive the
thresholds its proxy should use. (The live-corpus sweep is a separate tool that
probes the daemon itself; this file consumes its output.)

INPUT -- a scores JSON with measured populations:

    {
      "measured": "<when/surface>",
      "populations": {
        "noise_best": [0.836, 0.722, ...],          # best row per noise query
        "real_best":  [1.313, 1.298, ...],          # best row per real query
        "real_row_tail": [0.600, 0.640, ...]        # optional: real-query rows
      },
      "floors": {"PMB_RECALL_MIN_BEST": 0.89, "PMB_RECALL_FLOOR": 0.70}  # optional
    }

The committed fixture this repo ships is exactly that shape. Hand `--file` any
new sweep's populations to re-derive the placement for the changed corpus.

PLACEMENT RULES (the same derivation the original floors were set by)
* query floor  = midpoint of the measured gap (max noise_best .. min real_best).
  Measured 2026-10-30: gap [0.836 .. 0.949], midpoint 0.8925 -> shipped 0.89.
* row floor    = midpoint between the real-row p10 and p25 -- NOT inside the
  gap, because a genuine query's tail reaches below the noise ceiling (0.600
  < 0.836), so the row floor has to sit lower than the query floor. It only
  ever runs after tier 1 passed, so its job is trimming the weak tail, not
  holding noise out.
* Without real-row data the row floor is UNPROPOSABLE: a threshold needs a
  measured population to sit in. `--check` then reports the row floor as
  unverifiable (not a failure), never a guess quietly accepted.

--check compares the DEPLOYED floors (env, default the shipped 0.89/0.70)
against the derivation and, when the input also records `floors`, against
the values committed with the measurement. Exit codes:
    0  query floor inside the gap, row floor inside its measured p10..p25
       band (and matching committed, when present)
    1  a deployed floor violates one of the above
    2  the input file is missing or malformed
"""
import argparse
import json
import os
import sys

QUERY_FLOOR_ENV = "PMB_RECALL_MIN_BEST"
ROW_FLOOR_ENV = "PMB_RECALL_FLOOR"


def percentile(sorted_vals, frac):
    """Linear-interpolated percentile; empty input returns None."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = frac * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    weight = pos - lo
    return sorted_vals[lo] * (1 - weight) + sorted_vals[hi] * weight


def load_populations(path):
    """Read and validate a scores JSON. Returns (noise_best, real_best,
    real_row_tail, real_row_stats, recorded_floors) or raises ValueError."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("%r is missing or not JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("%r: top level must be an object" % path)
    populations = data.get("populations")
    if not isinstance(populations, dict):
        raise ValueError("%r: no 'populations' object" % path)

    def _numlist(key):
        vals = populations.get(key)
        if not isinstance(vals, list) or not vals:
            raise ValueError("%r: populations.%s must be a non-empty list"
                             % (path, key))
        if not all(isinstance(v, (int, float)) for v in vals):
            raise ValueError("%r: populations.%s contains a non-numeric value"
                             % (path, key))
        return [float(v) for v in vals]

    noise_best = _numlist("noise_best")
    real_best = _numlist("real_best")
    tail = populations.get("real_row_tail")
    if tail is not None:
        if not isinstance(tail, list) or not tail or \
                not all(isinstance(v, (int, float)) for v in tail):
            raise ValueError("%r: populations.real_row_tail must be a "
                             "non-empty numeric list" % path)
        tail = [float(v) for v in tail]
    stats = populations.get("real_row_stats")
    if stats is not None and not isinstance(stats, dict):
        raise ValueError("%r: populations.real_row_stats must be an object"
                         % path)

    recorded = data.get("floors")
    if recorded is not None and not isinstance(recorded, dict):
        raise ValueError("%r: 'floors' must be an object" % path)

    return noise_best, real_best, tail, stats, recorded


def derive(noise_best, real_best, real_row_tail=None, real_row_stats=None):
    """Derive the measured gap and the two proposed floors from populations."""
    gap_lo = max(noise_best)
    gap_hi = min(real_best)
    query_floor = (gap_lo + gap_hi) / 2.0
    if real_row_stats and real_row_stats.get("p10") is not None \
            and real_row_stats.get("p25") is not None:
        # The committed stats are computed on the FULL tail distribution;
        # real_row_tail in a fixture is usually a handful of representative
        # points, so percentile-computing from it would fabricate a different
        # (too narrow) band. The recorded stats win: they are the measurement.
        p10 = float(real_row_stats["p10"])
        p25 = float(real_row_stats["p25"])
        row_floor = (p10 + p25) / 2.0
        row_source = "midpoint of recorded real-row p10 and p25"
        row_bounds = [p10, p25]
    elif real_row_tail and len(real_row_tail) >= 10:
        # Enough raw rows to compute the percentiles honestly.
        sorted_tail = sorted(real_row_tail)
        p10 = percentile(sorted_tail, 0.10)
        p25 = percentile(sorted_tail, 0.25)
        row_floor = (p10 + p25) / 2.0
        row_source = "midpoint of real-row p10 and p25 (computed from %d rows)" \
            % len(sorted_tail)
        row_bounds = [p10, p25]
    else:
        # No tail population to sit in -> UNPROPOSABLE. A threshold without a
        # measured population is a guess; guessing the gap midpoint here would
        # trim real rows with a threshold that never touched them when it was
        # measured. --check then reports the row floor as unverifiable, which
        # is an honest state, not a failure.
        row_floor = None
        row_source = "unset -- requires populations.real_row_stats or a " \
            "real_row_tail of >=10 rows"
        row_bounds = None
    return {
        "measured_gap": [gap_lo, gap_hi],
        "gap_width": gap_hi - gap_lo,
        "noise_best_max": gap_lo,
        "real_best_min": gap_hi,
        "row_bounds": row_bounds,
        "proposed_pmb_recall_min_best": round(query_floor, 4),
        "proposed_pmb_recall_floor": None if row_floor is None
            else round(row_floor, 4),
        "row_floor_source": row_source,
    }


def deployed_floors():
    return {
        QUERY_FLOOR_ENV: float(os.environ.get(QUERY_FLOOR_ENV, "0.89")),
        ROW_FLOOR_ENV: float(os.environ.get(ROW_FLOOR_ENV, "0.70")),
    }


def check(derived, deployed, recorded):
    """Verify the deployed floors against a derivation. Returns (ok, reasons,
    notes). Notes are honest unverified states, never failures."""
    ok, reasons, notes = True, [], []
    gap_lo, gap_hi = derived["measured_gap"]
    deployed_q = deployed[QUERY_FLOOR_ENV]
    deployed_r = deployed[ROW_FLOOR_ENV]
    if not gap_lo < deployed_q < gap_hi:
        ok = False
        reasons.append("query floor %.2f is OUTSIDE the measured gap "
                       "[%.3f .. %.3f] -- it no longer separates noise from "
                       "real on this corpus" % (deployed_q, gap_lo, gap_hi))
    bounds = derived.get("row_bounds")
    if bounds is not None:
        if not bounds[0] <= deployed_r < bounds[1]:
            ok = False
            reasons.append("row floor %.2f is OUTSIDE its measured band "
                           "[%.3f .. %.3f] (real-row p10 .. p25) -- %s"
                           % (deployed_r, bounds[0], bounds[1],
                              "it would cut real rows at or above the p25 "
                              "tail" if deployed_r >= bounds[1] else
                              "it would keep real rows the p10 tail already "
                              "measured as weak"))
    else:
        notes.append("row floor cannot be verified: the input carries no "
                     "real_row_tail population, so no measured band exists")
    if recorded is not None:
        committed_q = recorded.get(QUERY_FLOOR_ENV)
        committed_r = recorded.get(ROW_FLOOR_ENV)
        if committed_q is not None and abs(deployed_q - float(committed_q)) > 1e-9:
            ok = False
            reasons.append("deployed query floor %.2f disagrees with the value "
                           "committed with the measurement (%s: %.2f)"
                           % (deployed_q, QUERY_FLOOR_ENV, committed_q))
        if committed_r is not None and abs(deployed_r - float(committed_r)) > 1e-9:
            ok = False
            reasons.append("deployed row floor %.2f disagrees with the value "
                           "committed with the measurement (%s: %.2f)"
                           % (deployed_r, ROW_FLOOR_ENV, committed_r))
    return ok, reasons, notes


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Propose and check the two-tier recall floors from "
                    "measured score populations. Standalone: stdlib only.")
    ap.add_argument("--file", default=None, metavar="JSON",
                    help="scores JSON (default: committed fixture next to this "
                         "repo's tests/fixtures/pmb_floor_fixture.json when "
                         "present)")
    ap.add_argument("--check", action="store_true",
                    help="verify the deployed floors (env or the 0.89/0.70 "
                         "defaults) against the measurement instead of "
                         "printing only proposals")
    args = ap.parse_args(argv)

    path = args.file
    if path is None:
        # Default input: the committed fixture, when this checkout carries it.
        default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "tests", "fixtures",
                               "pmb_floor_fixture.json")
        path = os.path.abspath(default)

    try:
        noise_best, real_best, tail, stats, recorded = load_populations(path)
    except ValueError as exc:
        print("cannot calibrate: %s" % exc, file=sys.stderr)
        return 2

    derived = derive(noise_best, real_best, tail, stats)
    print("measured on        : %s" % path)
    print("measured gap       : [%.3f .. %.3f]  (width %.3f)"
          % (derived["noise_best_max"],
             derived["real_best_min"], derived["gap_width"]))
    print("noise best (max)   : %.3f" % derived["noise_best_max"])
    print("real best  (min)   : %.3f" % derived["real_best_min"])
    print("proposed %s : %.4f" % (QUERY_FLOOR_ENV,
                                   derived["proposed_pmb_recall_min_best"]))
    print("proposed %s     : %.4f  (%s)"
          % (ROW_FLOOR_ENV, derived["proposed_pmb_recall_floor"],
             derived["row_floor_source"]))

    if not args.check:
        return 0

    deployed = deployed_floors()
    print()
    print("deployed %s   : %.2f (env %s)"
          % (QUERY_FLOOR_ENV, deployed[QUERY_FLOOR_ENV],
             "set" if os.environ.get(QUERY_FLOOR_ENV) else "default"))
    print("deployed %s       : %.2f (env %s)"
          % (ROW_FLOOR_ENV, deployed[ROW_FLOOR_ENV],
             "set" if os.environ.get(ROW_FLOOR_ENV) else "default"))
    ok, reasons, notes = check(derived, deployed, recorded)
    for reason in reasons:
        print("FAIL: " + reason)
    for note in notes:
        print("NOTE: " + note)
    if ok:
        print("CHECK: PASS -- deployed floors sit inside their measured "
              "placements%s"
              % (" and match the committed values"
                 if recorded is not None else ""))
    else:
        print("CHECK: FAIL -- %d reason(s), see above" % len(reasons))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
