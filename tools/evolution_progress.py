#!/usr/bin/env python3
# coding: utf-8
"""evolution_progress.py — is the evolution loop ALIVE, and is it IMPROVING?

Restored 2026-09-30 after a mis-anchored edit destroyed `adjudicate`,
`is_derived` and `effectiveness`. Rebuilt from the verified behaviour plus one
addition: a CURRENT window, so a condition that was fixed stops reporting as
DEGRADED forever.

WHY THIS EXISTS
---------------
`scripts/audit_loop_liveness.py` answers "does every scheduled loop still
run?". The evolution cycle passes that check comfortably: 10.3 min cadence,
lock held, state written every cycle. It is the healthiest loop in the tree.

It has also promoted nothing in 44 cycles. `audit_loop_liveness` cannot see
that, because a loop that runs and produces nothing looks exactly like a loop
that runs and produces something. This file asks the second question.

TWO VERDICTS, DELIBERATELY
---------------------------
`verdict` is all-time: what the log contains, which is true forever.
`current_verdict` is the recent tail: what the loop is doing NOW. Publishing
only the first is the stale-artifact failure — a schema thrash fixed twenty
cycles ago keeps reporting as DEGRADED, and a consumer cannot tell "still
broken" from "was broken". A gate must act on `current_*`.

THE DEFECT IT MEASURES
----------------------
Two subsystems with incompatible requirements, added at different times:

  PROTEUS morph   `morph_candidate_gene(cycle_index, ...)` rotates ONE extra
                  gene into the search space every cycle, and `morph_strip`
                  removes the previous one, so a per-gene A/B verdict is not
                  confounded. That intent REQUIRES alternation.

  Promotion gate  Compares this cycle's champion against the production
                  baseline and the previous champion over PROMOTE_WINS
                  consecutive cycles. That intent REQUIRES a stable schema.

Alternating schemas make the second subsystem's comparisons meaningless.
Worse, `genome_seed()` (evolution_cycle.py:89) hashes EVERY gene sorted, so
adding or stripping slot 5 changes the RNG stream for all genes: two genomes
differing only in slot 5 are evaluated by different trajectories and are not
the same measurement.

THE MEASUREMENT RULE THIS ENFORCES
----------------------------------
A measurement is only comparable if the thing measured did not change identity
between samples. This file therefore groups cycles BY SCHEMA and reports
comparability per arm, which is what tells you whether promotion is even
possible. If every arm has n=1, no arm can accumulate a streak, and the loop
is arithmetically incapable of promoting however long it runs.

EMPTY IS NOT HEALTHY
--------------------
A loop that has never been surfaced reports NO_DATA, not PASS. That must
never be reported as health.

Usage:
    python tools/evolution_progress.py
    python tools/evolution_progress.py --json
Exit: 0 IMPROVING / 1 DEGRADED / 2 NO_DATA / 3 NO_INPUT

NO_DATA MEANS IT READ THE LOG AND FOUND NOTHING TO REPORT. NO_INPUT MEANS IT
COULD NOT READ THE LOG AT ALL. Until 2026-10-11 those were one verdict with one
exit code, so "my input is missing" was indistinguishable from "the loop
produced nothing" -- an instrument reporting its own blindness as a measurement
(action registry item L-5).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "evolution_cycle.log")
OUT = os.path.join(ROOT, "state", "loop_integrity.json")

BASE_GENES = ("learning_rate", "epsilon", "discount", "epsilon_decay")
STALL_CYCLES = 6          # cycles with no promotion before calling it stalled
QD_ZERO_CYCLES = 4        # consecutive "+0 this cycle" before calling QD stalled
TREND_WINDOW = 8          # cycles used for the margin trend test
CURRENT_WINDOW = 12       # cycles defining "right now" for the current_* verdict

# The log's line vocabulary is defined once, in tools/evolution_log.py, so this
# reader cannot drift from the other three. RE_PROMO here is the full-depth
# standing line (sprt/llr/sigma/streak), not the short form the other analysts use.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evolution_log import (RE_BEST, RE_CONV, RE_CYCLE, RE_MORPH,  # noqa: E402
                           RE_PROMOTED, RE_QD, RE_STRIP,
                           RE_PROMO_SPRT as RE_PROMO, iter_lines)

STOPWORDS = {
    "which", "there", "their", "would", "about", "never", "always", "these",
    "those", "where", "while", "should", "could", "before", "after", "being",
    "because", "however", "instead", "already", "cannot", "through", "during",
    "without", "within", "between", "against", "though", "under", "other",
    "same", "then", "than", "when", "what", "with", "from", "into", "your",
}
TOOL_TOKEN = re.compile(r"\b([a-z][a-z0-9_]{4,})\b")
MEMORY_TOOLS = {"record_batch", "record_keyed_fact", "record_fact", "record_goal",
                "record_activity", "mark_lesson_followed", "prepare"}

#: BEST lines the parser could not read. This was a bare `pass`, so a dropped line
#: silently removed a cycle from the comparability data; it is counted and
#: reported instead. A non-empty count is a real hole in the measurement.
BEST_PARSE_FAILURES: list[str] = []


def _input_state(path: str) -> dict:
    """What the reader actually got, so NO_DATA cannot mean two things.

    MEASURED (action registry item L-5): this tool reported NO_DATA with the same
    message and the same exit code whether the log was absent or present-and-empty,
    which made "I could not read my input" and "my input says the loop produced
    nothing" the same report. This states which of the two actually happened.
    """
    if not os.path.exists(path):
        return {"path": path, "found": False, "bytes": 0, "unreadable": True,
                "reason": "log not found"}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return {"path": path, "found": True, "bytes": 0, "unreadable": True,
                "reason": "stat failed: %s" % exc}
    if size == 0:
        return {"path": path, "found": True, "bytes": 0, "unreadable": False,
                "reason": "log exists but is empty"}
    return {"path": path, "found": True, "bytes": size, "unreadable": False,
            "reason": "read"}


def parse(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    cycles, cur = [], None
    for line in iter_lines(path):
        line = line.rstrip("\n")
        m = RE_CYCLE.match(line)
        if m:
            cur = {"ts": m.group(1), "seed": int(m.group(2)), "schema": None,
                   "best": None, "margin": None, "beats": None, "sprt": None,
                   "sprt_n": 0, "streak": None, "qd_gain": None,
                   "converged_gen": None, "stripped": 0, "promoted": False}
            cycles.append(cur)
            continue
        if cur is None:
            continue
        m = RE_MORPH.match(line)
        if m:
            cur["schema"] = m.group(2)
            continue
        m = RE_STRIP.match(line)
        if m:
            cur["stripped"] = int(m.group(2))
            continue
        m = RE_BEST.match(line)
        if m:
            try:
                genes = _parse_best(m.group(2))
                cur["best"] = genes
                cur["schema"] = cur["schema"] or _schema_of(genes)
            except Exception as exc:
                # Recorded rather than swallowed. A BEST line that will not parse
                # silently drops a cycle from the per-schema arm counts, and a bare
                # `pass` here was also a single-statement handler (a scanner finding).
                BEST_PARSE_FAILURES.append("%s: %s" % (m.group(1), exc))
                continue
            continue
        m = RE_PROMO.match(line)
        if m:
            cur["beats"] = m.group(2) == "True"
            cur["margin"] = float(m.group(3))
            cur["sprt"] = m.group(4)
            cur["sprt_n"] = int(m.group(5))
            cur["streak"] = f"{m.group(8)}/{m.group(9)}"
            continue
        m = RE_QD.match(line)
        if m:
            cur["qd_gain"] = int(m.group(2))
            continue
        m = RE_CONV.match(line)
        if m:
            cur["converged_gen"] = int(m.group(2))
            continue
        if RE_PROMOTED.match(line):
            cur["promoted"] = True
    return {"cycles": cycles}


def _parse_best(text: str) -> dict:
    """Parse a `BEST:` payload, which is a Python repr -- not JSON.

    MEASURED (engram #569): the previous reader was
    `json.loads(text.replace("'", '"'))` and it failed on **all 18** `BEST:`
    lines in the live log, because the trial gene is a Python bool
    (`'by_session': False`) and `False` is not valid JSON. Every cycle therefore
    dropped out of the per-schema arm counts with `cur["best"] is None`.

    The producer is `log('BEST: %s' % (best.genes if best else None))`
    (`scripts/evolution_cycle.py`), i.e. `repr(dict)`, so the payload is parsed
    as the Python literal it actually is; `json.loads` stays first so a producer
    that switches to `json.dumps` keeps working.
    """
    try:
        value = json.loads(text)
    except ValueError:
        value = ast.literal_eval(text)
    if not isinstance(value, dict):
        raise ValueError("BEST payload is %s, not a dict" % type(value).__name__)
    return value


def _schema_of(genes: dict) -> str:
    extra = sorted(set(genes) - set(BASE_GENES))
    return "+".join(extra) if extra else "(base4)"


def _window_analysis(win) -> dict:
    """Verdict over the recent tail only — what the loop is doing NOW."""
    n = len(win)
    if not n:
        return {"verdict": "NO_DATA", "findings": [], "schemas": [], "sprt_zero": 0}
    seq = [c["schema"] or "?" for c in win]
    schemas = sorted(set(seq))
    alt = (sum(1 for a, b in zip(seq, seq[1:]) if a != b) / max(1, len(seq) - 1))
    sprt_zero = sum(1 for c in win if c["sprt_n"] == 0)
    qd_zero = 0
    for c in reversed(win):
        if c["qd_gain"]:
            break
        qd_zero += 1
    promoted = sum(1 for c in win if c["promoted"])
    findings = []
    if alt > 0.5 and len(schemas) > 1:
        findings.append(
            f"CURRENT SCHEMA THRASH: {alt:.0%} alternation over the last {n} "
            f"cycles across {len(schemas)} arm(s) ({', '.join(schemas)}) — still "
            "happening now, not historical.")
    if sprt_zero == n and n >= 4:
        findings.append(
            f"CURRENT SPRT BLIND: n=0 on all {n} of the last {n} cycles — the "
            "gate is not gating right now.")
    if qd_zero == n and n >= QD_ZERO_CYCLES:
        findings.append(
            f"CURRENT QD STAGNANT: +0 on all {n} of the last {n} cycles.")
    if not promoted and n >= STALL_CYCLES:
        findings.append(f"CURRENT NO PROMOTION: 0 promotions in the last {n} cycles.")
    return {"verdict": "DEGRADED" if findings else "IMPROVING",
            "findings": findings, "schemas": schemas, "sprt_zero": sprt_zero,
            "alternation": round(alt, 3), "qd_zero": qd_zero,
            "promoted": promoted}


def analyse(data: dict) -> dict:
    cy = data.get("cycles") or []
    if not cy:
        return {"verdict": "NO_DATA", "current_verdict": "NO_DATA",
                "current_findings": [], "cycles": 0}

    promotions = sum(1 for c in cy if c["promoted"])
    beats = sum(1 for c in cy if c["beats"])

    arms = defaultdict(list)
    for c in cy:
        arms[c["schema"] or "(unknown)"].append(c)
    arm_rows = []
    for name, rows in sorted(arms.items(), key=lambda kv: -len(kv[1])):
        margins = [r["margin"] for r in rows if r["margin"] is not None]
        arm_rows.append({
            "schema": name, "cycles": len(rows),
            "comparable_pairs": max(0, len(rows) - 1),
            "can_accumulate_streak": len(rows) >= 2,
            "beats": sum(1 for r in rows if r["beats"]),
            "max_margin": round(max(margins), 4) if margins else None,
            "min_margin": round(min(margins), 4) if margins else None,
            "mean_margin": round(statistics.fmean(margins), 4) if margins else None,
        })

    seq = [c["schema"] or "(unknown)" for c in cy]
    alternations = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    alternation_rate = alternations / max(1, len(seq) - 1)

    tail = [c["margin"] for c in cy[-TREND_WINDOW:] if c["margin"] is not None]
    trend = None
    if len(tail) >= 4:
        mean_tail = statistics.fmean(tail)
        head = [c["margin"] for c in cy[:-TREND_WINDOW] if c["margin"] is not None]
        if head:
            trend = round(mean_tail - statistics.fmean(head), 4)

    since_promo = 0
    for c in reversed(cy):
        if c["promoted"]:
            break
        since_promo += 1
    qd_zero = 0
    for c in reversed(cy):
        if c["qd_gain"]:
            break
        qd_zero += 1
    sprt_zero = sum(1 for c in cy if c["sprt_n"] == 0)

    findings = []
    if promotions == 0 and len(cy) >= STALL_CYCLES:
        findings.append(f"NO PROMOTION in {len(cy)} cycles (>= {STALL_CYCLES} "
                        "threshold). The streak gate cannot advance.")
    if alternation_rate > 0.5 and len(arms) > 1:
        findings.append(
            f"SCHEMA THRASH: schema changed on {alternation_rate:.0%} of "
            f"consecutive cycles across {len(arms)} arms "
            f"({', '.join(a['schema'] for a in arm_rows)}). genome_seed() hashes "
            "every gene, so arms are not the same measurement.")
    stuck = [a["schema"] for a in arm_rows if not a["can_accumulate_streak"]]
    if stuck:
        findings.append(f"ARMS CANNOT ACCUMULATE A STREAK: {', '.join(stuck)} "
                        "(fewer than 2 cycles each). Promotion is arithmetically "
                        "impossible regardless of fitness.")
    if qd_zero >= QD_ZERO_CYCLES:
        findings.append(f"QD ARCHIVE STAGNANT: +0 for {qd_zero} consecutive "
                        "cycles. The multi-objective mechanism is full and "
                        "admitting nothing.")
    if sprt_zero >= STALL_CYCLES:
        findings.append(f"SPRT GATE BLIND: n=0 on {sprt_zero}/{len(cy)} cycles. "
                        "It has no comparable consecutive pairs to test.")
    if trend is not None and abs(trend) < 0.05:
        findings.append(f"MARGIN TREND FLAT: {trend:+.4f} over the last "
                        f"{TREND_WINDOW} cycles.")

    window = cy[-CURRENT_WINDOW:] if len(cy) > CURRENT_WINDOW else cy
    current = _window_analysis(window)

    return {
        "verdict": "DEGRADED" if findings else "IMPROVING",
        "current_verdict": current["verdict"],
        "current_findings": current["findings"],
        "current_window_cycles": len(window),
        "current_schemas": current["schemas"],
        "current_sprt_zero": current["sprt_zero"],
        "current_alternation": current["alternation"],
        "current_qd_zero": current["qd_zero"],
        "current_promoted": current["promoted"],
        "cycles": len(cy),
        "promotions": promotions,
        "cycles_beating_baseline": beats,
        "cycles_since_promotion": since_promo,
        "schema_alternation_rate": round(alternation_rate, 3),
        "distinct_schemas": len(arms),
        "qd_zero_streak": qd_zero,
        "sprt_zero_cycles": sprt_zero,
        "margin_trend": trend,
        "arms": arm_rows,
        "findings": findings,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    state = _input_state(LOG)
    rep = analyse(parse(LOG))
    rep["input"] = state
    rep["best_parse_failures"] = len(BEST_PARSE_FAILURES)
    if not a.no_write and rep.get("verdict") != "NO_DATA":
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        prev = None
        if os.path.exists(OUT):
            try:
                prev = json.load(open(OUT, encoding="utf-8"))
            except Exception:
                prev = None
        rep["previous_verdict"] = (prev or {}).get("verdict")
        rep["reoriented"] = rep.get("previous_verdict") != rep["verdict"]
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, default=str)

    if a.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        if rep["verdict"] == "NO_DATA":
            if not state["found"] or state["unreadable"]:
                print("evolution_progress: NO_INPUT - could not read %s (%s)"
                      % (LOG, state["reason"]))
                return 3
            print("evolution_progress: NO_DATA - read %s (%d bytes) but parsed "
                  "no cycles" % (LOG, state["bytes"]))
            return 2
        print(f"evolution_progress: all-time={rep['verdict']}  "
              f"CURRENT={rep['current_verdict']}  "
              f"(last {rep['current_window_cycles']} cycles)")
        print(f"  cycles                 : {rep['cycles']}")
        print(f"  promotions             : {rep['promotions']} "
              f"(cycles since: {rep['cycles_since_promotion']})")
        print(f"  current schemas        : {rep['current_schemas']} "
              f"alternation={rep['current_alternation']}")
        print(f"  current SPRT n=0       : {rep['current_sprt_zero']}"
              f"/{rep['current_window_cycles']}")
        print(f"  current QD +0 streak   : {rep['current_qd_zero']}")
        print(f"  margin trend           : {rep['margin_trend']}")
        if rep["current_findings"]:
            print("\n  CURRENT FINDINGS (act on these):")
            for f in rep["current_findings"]:
                print(f"   - {f}")
        if rep["findings"]:
            print(f"\n  historical findings: {len(rep['findings'])} "
                  f"(true of the log, not necessarily of now)")
        print("\n  arms (comparability per search space):")
        for r in rep["arms"]:
            ok = "yes" if r["can_accumulate_streak"] else "NO"
            print(f"    {r['schema']:<22} n={r['cycles']:<4} "
                  f"pairs={r['comparable_pairs']:<3} streak_possible={ok:<4} "
                  f"margin[{r['min_margin']}, {r['max_margin']}]")
    return 0 if rep["current_verdict"] == "IMPROVING" else 1


if __name__ == "__main__":
    sys.exit(main())
