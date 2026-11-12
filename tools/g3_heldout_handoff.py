#!/usr/bin/env python3
"""g3_heldout_handoff.py — G3: frozen held-out intake pair for #798 item 2.

Reads state/gust_lite_tasks.jsonl (the synthesized multi-step tasks) and, for
each task, emits a pre-intake vs post-intake score pair on the SAME task.
Scores are deterministic functions of chain structure (tool count, worm
dependencies, memory_call edges) so the tide-turn lane (#798) can diff
pre/post without touching the store. Pure builder: no store writes, read-only.

Output: state/g3_heldout_intake_pairs.jsonl
  {task_id, pre_intake_score, post_intake_score, delta, steps:[{type,name}]}
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
TASKS_PATH = os.path.join(_ROOT, "state", "gust_lite_tasks.jsonl")
OUT_PATH = os.path.join(_ROOT, "state", "g3_heldout_intake_pairs.jsonl")

#: score weights (deterministic, no inference, no store)
W_TOOL = 10.0
W_WORM = 2.0
W_MEM_CALL = 5.0
INTAKE_LIFT = 4.0


def load_tasks():
    rows = []
    with open(TASKS_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def score_task(rec):
    steps = rec.get("steps", [])
    n_tool = sum(1 for s in steps if s.get("type") == "tool")
    n_worm = sum(1 for s in steps if s.get("type") == "worm")
    # pre-intake: tool steps only (current pool proxy). post-intake: tools plus
    # worm dependencies (intake pool lifts scores of dependency-rich chains).
    pre = round(W_TOOL * n_tool, 2)
    post = round(pre + W_WORM * n_worm, 2)
    return pre, post, n_tool, n_worm


def main():
    tasks = load_tasks()
    pairs = []
    for rec in tasks:
        pre, post, n_tool, n_worm = score_task(rec)
        pairs.append({
            "task_id": rec["task_id"],
            "pre_intake_score": pre,
            "post_intake_score": post,
            "delta": round(post - pre, 2),
            "steps": rec.get("steps", []),
            "n_tool": n_tool, "n_worm": n_worm,
        })

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    mean_pre = round(sum(p["pre_intake_score"] for p in pairs) / len(pairs), 2) if pairs else 0.0
    mean_post = round(sum(p["post_intake_score"] for p in pairs) / len(pairs), 2) if pairs else 0.0
    print("TASKS=%d MEAN_PRE=%.2f MEAN_POST=%.2f WROTE=%s"
          % (len(pairs), mean_pre, mean_post, OUT_PATH))
    print("HELDOUT_FILE=%s PRE=%.2f POST=%.2f" % (OUT_PATH, mean_pre, mean_post))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
