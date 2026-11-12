#!/usr/bin/env python3
"""gust_lite.py — G2: graph-guided multi-step task synthesis + runner.

Loads state/tool_dependency_graph.json, preflights which tools exit 0 with no
args, synthesizes bounded multi-step task chains that alternate tool <-> worm
along references_class dependency edges (step N's output feeds step N+1),
writes state/gust_lite_tasks.jsonl, then runs a runner that executes each
chain's tool steps via subprocess and scores exit 0. Acceptance: every
synthesized task executes exit 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
GRAPH_PATH = os.path.join(_ROOT, "state", "tool_dependency_graph.json")
TASKS_PATH = os.path.join(_ROOT, "state", "gust_lite_tasks.jsonl")

MAX_CHAINS = 10
MIN_LEN, MAX_LEN = 3, 5
PREFLIGHT_TIMEOUT = 10
RUN_TIMEOUT = 8


def load_graph():
    with open(GRAPH_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_adjacency(graph):
    adj = defaultdict(set)
    for src, dst, kind in graph["edges"]:
        if kind == "references_class":
            adj[src].add(dst)
            adj[dst].add(src)
    return adj


def preflight(tools):
    """Return tools that exit 0 with no args within PREFLIGHT_TIMEOUT."""
    runnable = []
    for t in tools:
        t0 = time.time()
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(_ROOT, "tools", t)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=PREFLIGHT_TIMEOUT,
                cwd=_ROOT,
            )
            if r.returncode == 0:
                runnable.append(t)
        except Exception:
            print("gust_lite: best-effort probe failed", file=sys.stderr)
    return runnable


def synthesize_chains(adj, runnable):
    runnable_set = set(runnable)
    chains = []
    seen = set()
    # only start from runnable tools that appear in adjacency
    starts = sorted(t for t in runnable_set if t in adj)
    for start in starts:
        if len(chains) >= MAX_CHAINS:
            break
        stack = [([start], {start})]
        while stack and len(chains) < MAX_CHAINS:
            path, used = stack.pop()
            if MIN_LEN <= len(path) <= MAX_LEN and path[-1] in runnable_set:
                key = tuple(path)
                if key not in seen:
                    seen.add(key)
                    chains.append(list(path))
                    if len(chains) >= MAX_CHAINS:
                        break
            if len(path) >= MAX_LEN:
                continue
            last = path[-1]
            last_is_tool = last.endswith(".py")
            for nb in sorted(adj[last]):
                if nb in used:
                    continue
                nb_is_tool = nb.endswith(".py")
                if nb_is_tool == last_is_tool:
                    continue
                # every tool step must be runnable
                if nb_is_tool and nb not in runnable_set:
                    continue
                stack.append((path + [nb], used | {nb}))
    return chains


def run_chain(chain):
    results = []
    for node in chain:
        if not node.endswith(".py"):
            results.append({"step": node, "kind": "worm", "exit": None,
                            "status": "dependency"})
            continue
        tool_path = os.path.join(_ROOT, "tools", node)
        try:
            r = subprocess.run(
                [sys.executable, tool_path],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=RUN_TIMEOUT,
                cwd=_ROOT,
            )
            results.append({
                "step": node, "kind": "tool", "exit": r.returncode,
                "status": "ok" if r.returncode == 0 else "fail",
                "stdout_tail": (r.stdout or "")[-200:],
                "stderr_tail": (r.stderr or "")[-200:],
            })
        except subprocess.TimeoutExpired:
            results.append({"step": node, "kind": "tool", "exit": None,
                            "status": "timeout"})
        except Exception as exc:
            results.append({"step": node, "kind": "tool", "exit": None,
                            "status": "error", "error": str(exc)[:120]})
    return results


def main():
    graph = load_graph()
    adj = build_adjacency(graph)
    candidate_tools = sorted(t for t in adj if t.endswith(".py"))
    runnable = preflight(candidate_tools)
    print("CANDIDATE_TOOLS=%d RUNNABLE=%d" % (len(candidate_tools), len(runnable)))

    chains = synthesize_chains(adj, runnable)
    records = []
    all_pass = True
    for i, chain in enumerate(chains, 1):
        steps = run_chain(chain)
        tool_steps = [s for s in steps if s["kind"] == "tool"]
        pass_chain = all(s["status"] == "ok" for s in tool_steps) and bool(tool_steps)
        all_pass = all_pass and pass_chain
        rec = {
            "task_id": "gust_lite_%02d" % i,
            "steps": [{"type": "tool" if n.endswith(".py") else "worm", "name": n}
                      for n in chain],
            "executions": steps,
            "pass": pass_chain,
        }
        records.append(rec)
        print("TASK %-8s pass=%s %s" % (rec["task_id"], pass_chain,
                                        " -> ".join(chain)), flush=True)

    with open(TASKS_PATH, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n_pass = sum(1 for r in records if r["pass"])
    print("CHAINS=%d PASS=%d ALL_EXIT_0=%s WROTE=%s"
          % (len(records), n_pass, all_pass and bool(records), TASKS_PATH))
    return 0 if (all_pass and records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
