#!/usr/bin/env python
# coding: utf-8
"""floor_measure.py - re-measure the recall floor thresholds against the corpus.

WHY. PMB_RECALL_MIN_BEST (0.89) and PMB_RECALL_FLOOR (0.70) are a property of
the STORE, not of the software: they sit in the gap between what a noise query
and a real query score. The canon says re-measure after a large purge, because
the numbers move with the corpus. Recorded 2026-10-30: noise best 0.836 max /
0.645 mean, real best 0.949 min, a 0.113 gap.

WHAT IT MEASURES. Only the daemon (:8765) - the unfloored store. Measuring
through the proxy would measure the thresholds themselves. Each query's BEST
row score is the statistic the query floor compares, so that is what is
reported: max/mean of noise bests, min/mean of real bests, and the gap between
them, with a verdict on whether both thresholds still sit inside it.

READ-ONLY: recall calls only, no writes.

Usage:
    python tools/floor_measure.py            # 12 noise + 12 real probes
    python tools/floor_measure.py --noise 20 --real 20
    python tools/floor_measure.py --adjacent 8
    python tools/floor_measure.py --json
    python tools/floor_measure.py --self-test   # no daemon needed
Exit codes: 0 = both thresholds inside the gap, 1 = a threshold is outside it
(the floor no longer separates noise from real on this corpus), 2 = the daemon
could not be measured. Under --self-test: 0 = the verdict behaves correctly on
both fixtures, 1 = it does not.
"""
import argparse
import json
import random
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mcp_startup_gate as gate  # noqa: E402

#: The query floor and the row floor as shipped (azazael/tools/pmb_floor_proxy.py
#: and supervisor_mcp.py). Read from the environment when set, so a changed
#: deployment is measured rather than described.
import os  # noqa: E402
QUERY_FLOOR = float(os.environ.get("PMB_RECALL_MIN_BEST", "0.89"))
ROW_FLOOR = float(os.environ.get("PMB_RECALL_FLOOR", "0.70"))

#: REAL probes: questions this corpus must answer, in the vocabulary of the
#: project. Deliberately NOT copied from stored memory verbatim - a verbatim
#: copy measures the copy, not retrieval.
REAL = [
    "how does the pmb floor proxy filter recall rows",
    "what does the lane queue claim protocol guarantee about two lanes polling",
    "why did the kilo lane see an empty task board",
    "what is the session order gate and when must it exit non-zero",
    "how are agent order blocks generated into lane instruction files",
    "what happens when the opencode mcp client cannot reach the endpoint",
    "which interpreter is the production python for this project",
    "how do I record a lesson that must survive the session",
    "what does the anti-pattern ratchet compare against",
    "how is the memory store backed up without stopping the daemon",
    "what is the difference between a claim lease and a retired row",
    "how do domain trees avoid forking the renderer",
]

#: ADJACENT probes: the project's own vocabulary, asked about something the
#: store does NOT contain. This is the realistic false-positive class - an
#: off-topic question shares no terms with the corpus and a nonce shares none
#: at all, so both understate recall. An adjacent probe is what a user types
#: when they half-remember a subsystem name, and lexical overlap alone can
#: lift it into the real band.
ADJACENT = [
    "how does the pmb floor proxy shard its redis cache across regions",
    "what does the kilo lane scheduler do with gpu kernel fusion",
    "which kubernetes operator manages the worm registry autoscaler",
    "how does the lane queue protocol handle byzantine lane faults",
    "what grpc streaming mode does the memorybus use for video frames",
    "how do domain trees compile their descriptors to wasm",
    "what is the opencode plugin's oauth refresh strategy for mcp",
    "how does the anti-pattern ratchet sign its baselines with ed25519",
]

#: NOISE probes: off-topic, plus random nonces. Both classes matter - a nonce
#: can score high by accident (measured 2026-10-30: 1 nonce in 15 reached
#: 0.836), and an ordinary off-topic question is the realistic failure.
NOISE = [
    "how to bake sourdough bread at altitude",
    "who won the 1998 world cup final",
    "best chess opening against the sicilian defence",
    "what is the boiling point of nitrogen at sea level",
    "how do migratory birds navigate using magnetic fields",
    "recommend a hiking trail near reading pennsylvania",
    "what causes the northern lights",
    "how to change a bicycle chain",
    "history of the ottoman empire in the 16th century",
    "what is the airspeed velocity of an unladen swallow",
]


def _best(client, query):
    payload = client.call("recall", {"query": query, "top_k": 10})
    if payload.get("_error"):
        return None, str(payload["_error"])[:160]
    rows = [r for r in (payload.get("results") or []) if isinstance(r, dict)]
    if not rows:
        return 0.0, ""
    return max([r.get("score") or 0.0 for r in rows]), ""


def _probe(client, queries, label):
    scores = []
    for q in queries:
        best, err = _best(client, q)
        if best is None:
            print("  !! %s probe failed: %s" % (label, err))
            return None
        scores.append((q, best))
    return scores


def _stats(scores):
    vals = [s for _q, s in scores]
    return {"n": len(vals), "max": max(vals), "mean": sum(vals) / len(vals),
            "min": min(vals)}


def _evaluate(nscores, ascores, rscores):
    """The verdict, in ONE place, so --self-test exercises the shipped logic
    rather than a copy of it that can drift away from what production runs."""
    n, r = _stats(nscores), _stats(rscores)
    # The gap is measured against the WORST noise class: a floor that separates
    # off-topic questions but not domain-adjacent ones is not separating.
    noise_max = max([n["max"]] + ([max(s for _q, s in ascores)] if ascores else []))
    return {
        "noise": n,
        "adjacent": _stats(ascores) if ascores else None,
        "real": r,
        "gap": r["min"] - noise_max,
        "noise_max": noise_max,
        "query_floor": QUERY_FLOOR,
        "row_floor": ROW_FLOOR,
        "query_floor_inside_gap": noise_max < QUERY_FLOOR <= r["min"],
        "row_floor_inside_gap": noise_max < ROW_FLOOR <= r["min"],
        "noise_scores": sorted(nscores, key=lambda x: -x[1]),
        "adjacent_scores": sorted(ascores, key=lambda x: -x[1]),
        "real_scores": sorted(rscores, key=lambda x: x[1]),
    }


def _run(client, noise, adjacent, real):
    """Probe any client and evaluate it. Returns (result, exit_code); a probe
    failure is (None, 2) and never a verdict."""
    nscores = _probe(client, noise, "noise")
    ascores = _probe(client, adjacent, "adjacent") if adjacent else []
    rscores = _probe(client, real, "real")
    if nscores is None or ascores is None or rscores is None:
        return None, 2
    res = _evaluate(nscores, ascores, rscores)
    ok = res["query_floor_inside_gap"] and res["row_floor_inside_gap"]
    return res, (0 if ok else 1)


class _FixtureClient:
    """Answers recall() from a fixed score table, so the whole pipeline -
    probe, best-row selection, stats, verdict - runs with no daemon."""

    def __init__(self, scores):
        self.scores = scores

    def initialize(self):
        return True, "fixture"

    def call(self, _tool, args):
        return {"results": [{"score": self.scores.get(args.get("query", ""), 0.0),
                             "content": "fixture row"}]}


def _selftest():
    """RULE 0B-C negative control, pinned in BOTH directions.

    A self-test that only asserts "separated" is passed by a tool that always
    says separated - which is exactly the silent pass this exists to prevent.
    So the fixtures pin both verdicts: a known-separable set the oracle must NOT
    fire on, and a known-overlapping set it MUST fire on.
    """
    noise_q = ["fixture noise one", "fixture noise two"]
    adjacent_q = ["fixture adjacent one"]
    real_q = ["fixture real one", "fixture real two"]

    # Every noise row below both floors, every real row above them. The correct
    # verdict is "both floors inside the gap" (exit 0). Reporting the floor
    # broken here means false-failing a healthy stack.
    separable = {**{q: 0.40 for q in noise_q},
                 **{q: 0.55 for q in adjacent_q},
                 **{q: 1.30 for q in real_q}}
    # The noise maximum sits above the real minimum, as measured on the real
    # corpus (1.584 vs 1.030). The correct verdict is "outside" (exit 1);
    # reporting a gap here is the silent pass.
    overlapping = {**{q: 1.60 for q in noise_q},
                   **{q: 1.10 for q in adjacent_q},
                   **{q: 1.03 for q in real_q}}

    cases = [("known-separable fixture", separable, 0, True, True),
             ("known-overlapping fixture", overlapping, 1, False, False)]
    failures = []
    for name, table, want_code, want_query, want_row in cases:
        res, code = _run(_FixtureClient(table), noise_q, adjacent_q, real_q)
        if res is None:
            print("[FAIL] %-26s could not be measured (exit %d)" % (name, code))
            failures.append("%s: not measurable" % name)
            continue
        problems = []
        if code != want_code:
            problems.append("exit %d, expected %d" % (code, want_code))
        if res["query_floor_inside_gap"] is not want_query:
            problems.append("query floor verdict %s, expected %s"
                            % (res["query_floor_inside_gap"], want_query))
        if res["row_floor_inside_gap"] is not want_row:
            problems.append("row floor verdict %s, expected %s"
                            % (res["row_floor_inside_gap"], want_row))
        print("[%s] %-26s gap %+.3f  exit %d"
              % ("PASS" if not problems else "FAIL", name, res["gap"], code))
        for p in problems:
            print("        %s" % p)
            failures.append("%s: %s" % (name, p))

    print()
    if failures:
        print("SELFTEST FAIL - %d problem(s); the verdict cannot be trusted"
              % len(failures))
        return 1
    print("SELFTEST PASS - the verdict fires on overlapping input and stays "
          "silent on a separable one")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--noise", type=int, default=len(NOISE))
    ap.add_argument("--real", type=int, default=len(REAL))
    ap.add_argument("--adjacent", type=int, default=len(ADJACENT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="run the known-separable / known-overlapping control; "
                         "needs no daemon")
    args = ap.parse_args(argv)

    if args.self_test:
        return _selftest()

    noise = list(NOISE)
    extra = max(0, args.noise - len(noise))
    noise += ["zzq" + secrets.token_hex(6) for _ in range(extra)]
    noise = noise[:args.noise]
    real = (REAL * 2)[:args.real]
    adjacent = ADJACENT[:args.adjacent]

    client = gate.Mcp("http://127.0.0.1:%d/mcp" % gate.DAEMON_PORT)
    ok, info = client.initialize()
    if not ok:
        print("daemon at :%d did not initialize: %s" % (gate.DAEMON_PORT, info))
        return 2

    res, code = _run(client, noise, adjacent, real)
    if res is None:
        return code

    n, r = res["noise"], res["real"]
    gap, noise_max = res["gap"], res["noise_max"]
    query_ok, row_ok = res["query_floor_inside_gap"], res["row_floor_inside_gap"]
    nscores, ascores = res["noise_scores"], res["adjacent_scores"]
    rscores = res["real_scores"]

    if args.json:
        print(json.dumps({"noise": n,
                          "adjacent": _stats(ascores) if ascores else None,
                          "real": r, "gap": gap, "noise_max": noise_max,
                          "query_floor": QUERY_FLOOR, "row_floor": ROW_FLOOR,
                          "query_floor_inside_gap": query_ok,
                          "row_floor_inside_gap": row_ok,
                          "noise_scores": sorted(nscores, key=lambda x: -x[1]),
                          "adjacent_scores": sorted(ascores, key=lambda x: -x[1]),
                          "real_scores": sorted(rscores, key=lambda x: x[1])},
                         indent=2, ensure_ascii=False))
    else:
        print("noise  n=%d  max %.3f  mean %.3f  min %.3f"
              % (n["n"], n["max"], n["mean"], n["min"]))
        if ascores:
            a = _stats(ascores)
            print("adjc   n=%d  max %.3f  mean %.3f  min %.3f"
                  % (a["n"], a["max"], a["mean"], a["min"]))
        print("real   n=%d  max %.3f  mean %.3f  min %.3f"
              % (r["n"], r["max"], r["mean"], r["min"]))
        print("gap (real min - noise max): %+.3f  [noise max = %.3f]"
              % (gap, noise_max))
        print()
        print("highest-scoring noise probes:")
        for q, s in sorted(nscores, key=lambda x: -x[1])[:3]:
            print("  %.3f  %s" % (s, q[:64]))
        if ascores:
            print("highest-scoring ADJACENT probes:")
            for q, s in sorted(ascores, key=lambda x: -x[1])[:3]:
                print("  %.3f  %s" % (s, q[:64]))
        print("lowest-scoring real probes:")
        for q, s in sorted(rscores, key=lambda x: x[1])[:3]:
            print("  %.3f  %s" % (s, q[:64]))
        print()
        print("query floor %.2f inside the gap: %s"
              % (QUERY_FLOOR, "YES" if query_ok else "NO"))
        print("row   floor %.2f inside the gap: %s"
              % (ROW_FLOOR, "YES" if row_ok else "NO"))
    return code


if __name__ == "__main__":
    sys.exit(main())
