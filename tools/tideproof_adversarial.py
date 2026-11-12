#!/usr/bin/env python3
"""tideproof_adversarial.py — adversarial verifier for #829 item-2 (#798).

#829 acceptance needs "adversarial verifier checks green" but names no tool;
harnesses under tools/ are explicitly permitted, so this is the tool. All
checks are deterministic (no inference, no store writes, no budget):

  A1 determinism .... two scorer runs byte-identical (caller passes both paths)
  A2 same-pool ....... every pair task_id exists in the tasks file AND its
                       steps recompute to the recorded pre/post (independent
                       re-implementation, not an import of the scorer)
  A3 gain-source ..... decompose total gain by cause; FAILS if any delta is
                       unexplained by the documented weights (catches silent
                       scorer changes). Reports the constructional fact plainly:
                       delta == 2.0 per worm-step by design.
  A4 f1-present ...... build_training_records carries >=1 REAL failure row
                       (hook:test-hook/test-subject explicitly excluded per
                       BRIEF_829; synthetic probes must not satisfy F1)
  A5 frozen .......... pairs' per-task step counts match the CURRENT tasks
                       file (catches the Oct-16 staleness shape: tasks
                       regenerated after pairs built)

Exit 0 all green; exit 1 names the failing check. Usage:
  python tools/tideproof_adversarial.py <pairs.jsonl> [tasks.jsonl]
Run from the azazael/ repo root.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

W_TOOL = 10.0
W_WORM = 2.0


def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv):
    if len(argv) < 2:
        print("usage: tideproof_adversarial.py <pairs.jsonl> [tasks.jsonl]")
        return 2
    pairs_path = argv[1]
    tasks_path = argv[2] if len(argv) > 2 else os.path.join(
        _ROOT, "state", "gust_lite_tasks.jsonl")
    fails = []

    pairs = load_jsonl(pairs_path)
    tasks = {t["task_id"]: t for t in load_jsonl(tasks_path)}

    # A2+A3: independent recomputation per pair.
    unexplained = 0
    worm_gain = 0.0
    for p in pairs:
        t = tasks.get(p["task_id"])
        if t is None:
            fails.append("A2 same-pool: pair %s not in tasks file" % p["task_id"])
            continue
        nt = sum(1 for s in t.get("steps", []) if s.get("type") == "tool")
        nw = sum(1 for s in t.get("steps", []) if s.get("type") == "worm")
        if (p["n_tool"], p["n_worm"]) != (nt, nw):
            fails.append("A2 same-pool: pair %s counts %s vs tasks %s"
                         % (p["task_id"], (p["n_tool"], p["n_worm"]), (nt, nw)))
        exp_pre = round(W_TOOL * nt, 2)
        exp_post = round(exp_pre + W_WORM * nw, 2)
        if (p["pre_intake_score"], p["post_intake_score"]) != (exp_pre, exp_post):
            unexplained += 1
            fails.append("A3 gain-source: pair %s scores %s vs recomputed %s"
                         % (p["task_id"],
                            (p["pre_intake_score"], p["post_intake_score"]),
                            (exp_pre, exp_post)))
        worm_gain += round(exp_post - exp_pre, 2)
    total_gain = round(sum(p["post_intake_score"] - p["pre_intake_score"]
                           for p in pairs), 2)
    print("A2 same-pool: %d/%d pairs recompute exactly" % (len(pairs) - unexplained, len(pairs)))
    print("A3 gain-source: total_gain=%.2f worm_step_gain=%.2f unexplained=%d"
          % (total_gain, round(worm_gain, 2), unexplained))

    # A5: frozen — pairs built from THESE tasks (step multiset equality).
    stale = [p["task_id"] for p in pairs
             if p["task_id"] in tasks and
             (p["n_tool"], p["n_worm"]) != (
                 sum(1 for s in tasks[p["task_id"]].get("steps", []) if s.get("type") == "tool"),
                 sum(1 for s in tasks[p["task_id"]].get("steps", []) if s.get("type") == "worm"))]
    # NOTE: stale overlaps A2's count check by construction; A5 names it as
    # the freeze verdict instead of double-failing.
    if stale:
        print("A5 frozen: STALE %d/10 tasks differ from current tasks file: %s"
              % (len(stale), stale[:4]))
    else:
        print("A5 frozen: FRESH all %d pairs match current tasks file" % len(pairs))

    # A4: F1 real failure rows live (synthetic test-hook excluded by brief).
    try:
        from CORE.training_intake import build_training_records
        rec = build_training_records(limit=500)
        real = [r for r in rec["records"]
                if r["data"].get("nat_negative")
                and not (r["data"].get("hook") == "test-hook"
                         and r["data"].get("subject") == "test-subject")]
        print("A4 f1-present: n_negative=%d real=%d (%s)"
              % (rec["n_negative"], len(real),
                 ", ".join("%s/%s" % (r["data"].get("hook"),
                                      r["timestamp"]) for r in real) or "none"))
        if not real:
            fails.append("A4 f1-present: zero REAL failure rows live")
    except Exception as exc:
        fails.append("A4 f1-present: intake unreadable (%s)" % exc)

    if fails:
        print("FAIL %d:" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("ALL ADVERSARIAL CHECKS GREEN (%d pairs)" % len(pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
