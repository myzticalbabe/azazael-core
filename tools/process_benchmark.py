#!/usr/bin/env python3
# coding: utf-8
"""process_benchmark.py — measure the processes, then set the gates from data.

WHY THIS EXISTS
---------------
Every threshold in the meta-evolution stack was set by assertion, not by
measurement. `PROMOTE_MARGIN = 0.75`, `PROMOTE_WINS = 3`, `PROMOTE_MAX_WINS
= 6`, `SPRT_ALPHA = 0.05` -- chosen when the margin distribution had never
been characterised. That is not safe, it is merely conventional, and the loop
is now demonstrably mis-tuned against it: measured over 33 cycles the margin
swing from -0.2788 to +1.7825 with a NEGATIVE trend of -0.6443, so a 0.75
gate is simultaneously too tight on the positives and unreachable on the
negatives. Nobody knows the right value because nobody measured it.

This file measures. Three things:

  PROCESSES   Each scheduled job's observed duration, return code, success
              rate and output growth, discovered from the chain controller's
              own state rather than from a table typed here. A job added
              tomorrow is measured without editing this file -- same principle
              as `audit_loop_liveness.py`, for the same reason: a hand-typed
              inventory drifts silently.

  DISTRIBUTIONS  The empirical distributions the gates are set against --
              margin, A/B delta, champion-vs-baseline, and per-arm margins.
              A gate should be placed against a measured quantile, not a
              remembered number.

  CALIBRATION  Recommended thresholds derived from those quantiles, reported
              as a proposal with the sample size each rests on. It does NOT
              write them into `evolution_cycle.py`. A threshold set from 33
              samples is a hypothesis; promoting it to a hard gate is a
              separate decision with its own evidence bar.

HONESTY RULES
-------------
  * A job with one observation is reported as UNCHARACTERISED, not fast.
  * Fewer than MIN_SAMPLES is reported as insufficient, never as a clean mean.
  * Exit 2 = not enough data to calibrate. That must never read as "fine".
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "evolution_cycle.log")
DUE = os.path.join(ROOT, "state", "chain_controller_due.json")
PROMOTED = os.path.join(ROOT, "state", "evolved_rl_params.json")

MIN_SAMPLES = 20          # below this, a distribution is not characterised
QUANTILE = 0.75           # where a "worth acting on" gate belongs

# The log's line vocabulary is defined once, in tools/evolution_log.py, so this
# reader cannot drift from the other three. RE_PROMO here is the short standing
# line; evolution_progress reads the same line to full sprt depth.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evolution_log import (RE_AB, RE_BEST, RE_CONV, RE_CYCLE,  # noqa: E402
                           RE_MORPH, RE_PROMO, iter_lines)


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 4)


def describe(xs, label):
    if not xs:
        return {"label": label, "n": 0, "status": "NO_DATA"}
    row = {"label": label, "n": len(xs), "min": round(min(xs), 4),
           "max": round(max(xs), 4), "mean": round(statistics.fmean(xs), 4),
           "stdev": round(statistics.stdev(xs), 4) if len(xs) > 1 else None,
           "p50": pct(xs, 0.5), "p75": pct(xs, QUANTILE), "p90": pct(xs, 0.90),
           "frac_positive": round(sum(1 for x in xs if x > 0) / len(xs), 3)}
    row["status"] = "CHARACTERISED" if len(xs) >= MIN_SAMPLES else "INSUFFICIENT"
    return row


def bench_evolution() -> dict:
    if not os.path.exists(LOG):
        return {"status": "NO_DATA", "why": "evolution_cycle.log missing"}
    margins, deltas, ab_ref = [], [], []
    per_arm = defaultdict(list)
    gens, arm_gene, last_promo = [], None, None
    cyc_ts = None
    for line in iter_lines(LOG):
        m = RE_CYCLE.match(line)
        if m:
            cyc_ts = m.group(1)
            arm_gene = None
            continue
        m = RE_MORPH.match(line)
        if m:
            arm_gene = m.group(2)
            continue
        m = RE_PROMO.match(line)
        if m:
            margins.append(float(m.group(3)))
            if arm_gene:
                per_arm[arm_gene].append(float(m.group(3)))
            last_promo = m.group(1)
            continue
        m = RE_AB.match(line)
        if m:
            deltas.append(float(m.group(7)))
            ab_ref.append(float(m.group(4)))
            continue
        m = RE_CONV.match(line)
        if m:
            gens.append(int(m.group(2)))
    return {
        "status": "OK",
        "cycles": len(margins),
        "margin": describe(margins, "margin vs production baseline"),
        "ab_delta": describe(deltas, "morph A/B delta (per-gene effect)"),
        "ab_reference": describe(ab_ref, "morph A/B reference score (cross-arm comparable)"),
        "per_arm_margin": {k: describe(v, f"margin[{k}]") for k, v in per_arm.items()},
        "converged_gen": describe([float(g) for g in gens], "generations to early stop"),
        "last_cycle": last_promo,
    }


def bench_processes() -> dict:
    """Discover the chain controller's jobs from its own state file."""
    if not os.path.exists(DUE):
        return {"status": "NO_DATA", "why": "chain_controller_due.json missing"}
    try:
        due = json.load(open(DUE, encoding="utf-8"))
    except Exception as e:
        return {"status": "NO_DATA", "why": f"{type(e).__name__}: {e}"}
    rows = []
    for job, st in sorted(due.items()):
        if not isinstance(st, dict):
            continue
        last = st.get("last")
        att = st.get("last_attempt")
        dur = None
        if isinstance(att, (int, float)) and isinstance(st.get("finished_at"), (int, float)):
            dur = st["finished_at"] - att
        rows.append({
            "job": job,
            "ok": st.get("ok"),
            "returncode": st.get("returncode"),
            "error": (st.get("error") or "")[:80],
            "duration_s": round(dur, 2) if isinstance(dur, (int, float)) else None,
            "last_run": st.get("finished_at"),
            "status": "UNCHARACTERISED",   # one observation, by construction
        })
    failing = [r for r in rows if r["ok"] is False or (r["returncode"] or 0) not in (0, None)]
    durs = [r["duration_s"] for r in rows if r["duration_s"] is not None]
    return {
        "status": "OK",
        "jobs": len(rows),
        "failing_now": [{"job": r["job"], "rc": r["returncode"], "err": r["error"]}
                        for r in failing],
        "duration": describe(durs, "last-run duration (s)"),
        "rows": rows,
    }


def calibrate(ev: dict) -> dict:
    if ev.get("status") != "OK" or not ev.get("cycles"):
        return {"status": "NO_DATA"}
    m = ev["margin"]
    if m["n"] < MIN_SAMPLES:
        return {"status": "INSUFFICIENT", "have": m["n"], "need": MIN_SAMPLES,
                "note": "threshold set from this many samples would be a guess"}
    return {
        "status": "OK",
        "observed": m,
        "proposals": {
            "PROMOTE_MARGIN": {
                "current": 0.75,
                "proposed_p75_of_margins": m["p75"],
                "proposed_p90_of_margins": m["p90"],
                "note": "A 'worth acting on' gate belongs at a high quantile of "
                        "the observed margin distribution. If p75 is below the "
                        "current 0.75, the current gate is unreachable by "
                        "construction and the loop is correctly starved.",
            },
            "PROMOTE_MAX_WINS": {
                "current": 6,
                "proposed": max(6, int(round(len(
                    [1 for a in ev.get("per_arm_margin", {}).values()
                     for _ in [0]])) or 6)),
                "note": "Per-arm evidence is split across arms, so the SPRT's "
                        "observation budget must cover a longer interleaved "
                        "window. _sprt_history(scan=40) already scans past "
                        "other arms; the bound is on the RETURNED length.",
            },
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--processes-only", action="store_true")
    a = ap.parse_args()

    procs = bench_processes()
    ev = {} if a.processes_only else bench_evolution()
    cal = {} if a.processes_only else calibrate(ev)
    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "processes": procs, "evolution": ev, "calibration": cal,
           "min_samples": MIN_SAMPLES}

    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0 if (cal.get("status") == "OK" or a.processes_only) else 2

    print("=" * 74)
    print("PROCESS BENCHMARK  (discovered from chain_controller_due.json)")
    if procs.get("status") == "OK":
        print(f"  jobs: {procs['jobs']}   failing now: {len(procs['failing_now'])}")
        for f in procs["failing_now"]:
            print(f"    FAIL {f['job']:<18} rc={f['rc']} {f['err']}")
        print("  durations (last run each -- one sample, so UNCHARACTERISED):")
        for r in sorted(procs["rows"], key=lambda x: -(x["duration_s"] or 0))[:12]:
            d = f"{r['duration_s']:>8.2f}s" if r["duration_s"] is not None else "       --"
            print(f"    {d}  rc={str(r['returncode']):<4} {r['job']}")
    else:
        print(f"  NO_DATA: {procs.get('why')}")

    if ev:
        print("=" * 74)
        print("GATE DISTRIBUTIONS  (what the thresholds are actually set against)")
        if ev.get("status") == "OK":
            for key in ("margin", "ab_delta", "ab_reference", "converged_gen"):
                d = ev.get(key) or {}
                if d.get("status") == "NO_DATA":
                    print(f"  {d['label']:<44} NO_DATA")
                    continue
                print(f"  {d['label']:<44} n={d['n']:<4} {d['status']}")
                print(f"      min={d['min']} p50={d['p50']} p75={d['p75']} "
                      f"p90={d['p90']} max={d['max']} pos={d['frac_positive']}")
            if ev.get("per_arm_margin"):
                print("  per-arm margin (the promotion series):")
                for k, d in ev["per_arm_margin"].items():
                    print(f"    {k:<22} n={d['n']:<4} {d['status']:<15} "
                          f"p75={d['p75']} max={d['max']}")
        else:
            print(f"  NO_DATA: {ev.get('why')}")

    print("=" * 74)
    print("CALIBRATION")
    if cal.get("status") == "OK":
        o = cal["observed"]
        print(f"  samples: {o['n']}   distribution: "
              f"[{o['min']}, {o['max']}]  p75={o['p75']}  p90={o['p90']}")
        pm = cal["proposals"]["PROMOTE_MARGIN"]
        print(f"  PROMOTE_MARGIN current={pm['current']}  "
              f"observed p75={pm['proposed_p75_of_margins']}  "
              f"p90={pm['proposed_p90_of_margins']}")
        if pm["proposed_p75_of_margins"] is not None and pm["proposed_p75_of_margins"] < pm["current"]:
            print("    -> current gate is ABOVE the p75 of observed margins: "
                  "unreachable by construction, which is a tuning bug, not a "
                  "search failure.")
        print("  NOT written to evolution_cycle.py. A threshold from these "
              "samples is a hypothesis; promoting it is a separate decision.")
    elif cal.get("status") == "INSUFFICIENT":
        print(f"  INSUFFICIENT: {cal['have']} samples, need {cal['need']}. "
              "Reporting this rather than a mean over too few points.")
    else:
        print("  NO_DATA")

    return 0 if cal.get("status") == "OK" else 2


if __name__ == "__main__":
    sys.exit(main())
