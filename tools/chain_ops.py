#!/usr/bin/env python3
# coding: utf-8
"""chain_ops.py — the three operational gaps that keep failure invisible.

WHY THIS EXISTS
---------------
Three separate blind spots, all found on 2026-09-30, all with the same shape:
something the operator needs to decide something, measured nowhere.

  1. DURATION. `chain_controller_due.json` records FIRE TIME and return code
     but not RUNTIME, so `last_attempt` and `finished_at` are effectively the
     same instant and every job reads ~0s. You cannot choose a cadence for a
     process whose duration you have never measured.

  2. CONSUMPTION. The chain writes ~20 `state/*.json` artifacts. Nothing
     reports which are read by anything. A file with zero readers is a dead
     store: the same class Intermesh found in the natures, where `monitor`
     declared it emits cpu_pct/mem_pct/disk_io and nothing in production named
     any of them. This tool greps the source for each artifact's name.

  3. SILENT FAILURE. 4 of 20 jobs return non-zero with EMPTY error strings.
     `audit_loop_liveness` checks for SILENCE, so a job that runs and fails
     reads as alive -- the health of the fleet and the truth about it are
     different things, and only one of them is currently measured.

Empty results report NO_DATA, never OK. An artifact with no readers is a
finding, not a clean bill.

Usage:
    python tools/chain_ops.py
    python tools/chain_ops.py --json
Exit: 0 nothing wrong / 1 findings / 2 no state file
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from collections import defaultdict

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUE = os.path.join(AZ, "state", "chain_controller_due.json")
REPORT = os.path.join(AZ, "state", "chain_ops.json")
SRC_DIRS = ("src", "scripts", "tools", "packages")
SKIP = {"node_modules", ".git", "__pycache__", ".venv-3.14t", "venv",
        "site-packages", "dist", "build", ".pytest_cache", "archive",
        "archived", "archived_unified", "backup", "output", "temp"}


def load_due():
    if not os.path.exists(DUE):
        return None
    try:
        return json.load(open(DUE, encoding="utf-8"))
    except Exception:
        return None


# ── 1. duration ───────────────────────────────────────────────────────────
def durations(due):
    """Read the duration the controller now records.

    Until 2026-09-30 the controller wrote `finished_at` as an ISO string while
    `last_attempt` was an epoch float, so `fin - att` was a type mismatch and
    every job read as unmeasurable. The controller now records `duration_s`
    directly and keeps both epochs, so read `duration_s` and fall back to the
    epoch pair. A job with neither is reported as unmeasurable, not as 0.0s --
    0.0s is a claim that something ran instantly.
    """
    rows = []
    for job, st in sorted(due.items()):
        if not isinstance(st, dict):
            continue
        dur = st.get("duration_s")
        if not isinstance(dur, (int, float)):
            fe, la = st.get("finished_epoch"), st.get("last_attempt")
            if isinstance(fe, (int, float)) and isinstance(la, (int, float)):
                dur = fe - la
            if not isinstance(dur, (int, float)):
                # last resort: both ISO
                for a, b in (("finished_at", "started_at"),):
                    x, y = st.get(a), st.get(b)
                    if isinstance(x, str) and isinstance(y, str):
                        try:
                            dur = (datetime.fromisoformat(x)
                                    - datetime.fromisoformat(y)).total_seconds()
                        except Exception:
                            dur = None
        rows.append({"job": job, "ok": st.get("ok"),
                     "returncode": st.get("returncode"),
                     "error": (st.get("error") or "")[:80],
                     "duration_s": round(float(dur), 3) if isinstance(dur, (int, float)) else None,
                     "timed_out": st.get("timed_out"),
                     "duration_measurable": isinstance(dur, (int, float)) and dur > 0.01})
    return rows


# ── 2. consumption ────────────────────────────────────────────────────────
_ART = re.compile(r"[\"'\s(=\[\]]state[/\\]([\w.\-/]+\.json)")
_CODE = re.compile(r"(?<![\w.])(\w+\.json)")


def consumption():
    state = os.path.join(AZ, "state")
    if not os.path.isdir(state):
        return None, None
    arts = sorted(f for f in os.listdir(state) if f.endswith(".json"))
    # one pass over the source, collecting every *.json token mentioned
    mentions = defaultdict(set)
    for d in SRC_DIRS:
        base = os.path.join(AZ, d)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in SKIP]
            for f in fn:
                if not f.endswith((".py", ".toml", ".json", ".yaml", ".yml")):
                    continue
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, AZ)
                # the artifact's own writer does not count as a consumer
                if rel.endswith(os.path.join("state", f)):
                    continue
                try:
                    txt = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for m in _CODE.findall(txt):
                    if m in arts:
                        mentions[m].add(rel)
    dead = [a for a in arts if not mentions.get(a)]
    return arts, {"readers": {a: sorted(mentions.get(a, ()))[:4] for a in arts},
                  "no_readers": dead,
                  "total": len(arts),
                  "note": "a state/*.json with zero readers is a dead store: "
                          "written every cycle, consumed by nothing"}


# ── 3. silent failure ─────────────────────────────────────────────────────
def silent_failures(rows):
    out = []
    for r in rows:
        if r["ok"] is False or (r["returncode"] not in (0, None)):
            out.append({"job": r["job"], "returncode": r["returncode"],
                        "error": r["error"],
                        "error_is_empty": not bool((r["error"] or "").strip()),
                        "detected_by": "chain_ops, NOT audit_loop_liveness",
                        "why_invisible": "audit_loop_liveness checks for "
                                         "silence, and this job is not silent"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    due = load_due()
    if due is None:
        print("NO_DATA: chain_controller_due.json missing or unreadable")
        return 2
    rows = durations(due)
    arts, cons = consumption()
    fails = silent_failures(rows)
    unmeasurable = [r["job"] for r in rows if not r["duration_measurable"]]

    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "jobs": len(rows),
           "duration": {"measurable": len(rows) - len(unmeasurable),
                        "unmeasurable": unmeasurable,
                        "total_unmeasurable": len(unmeasurable),
                        "why": "the state file records fire time, not runtime, "
                               "so duration is not measurable for ANY job until "
                               "the controller records wall time"},
           "consumption": cons,
           "silent_failures": fails}
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)

    if a.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print("=" * 72)
        print(f"CHAIN OPS  jobs={rep['jobs']}")
        print(f"\n  1. DURATION")
        print(f"     measurable        : {rep['duration']['measurable']}/{rep['jobs']}")
        print(f"     unmeasurable      : {len(unmeasurable)}")
        print(f"     reason            : {rep['duration']['why'][:100]}")
        print(f"\n  2. CONSUMPTION (state/*.json readers)")
        if cons is None:
            print("     NO_DATA: no state/ directory")
        else:
            print(f"     artifacts        : {cons['total']}")
            print(f"     with NO readers  : {len(cons['no_readers'])}")
            for d in cons["no_readers"][:12]:
                print(f"        {d}")
        print(f"\n  3. SILENT FAILURES (non-zero return, invisible to liveness)")
        if not fails:
            print("     none")
        for f in fails:
            print(f"     {f['job']:<14} rc={f['returncode']} "
                  f"error={'<EMPTY>' if f['error_is_empty'] else repr(f['error'][:40])}")
        print(f"\n  report: {REPORT}")
    return 1 if (fails or (cons and cons["no_readers"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())

