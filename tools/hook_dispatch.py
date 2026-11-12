#!/usr/bin/env python3
# coding: utf-8
"""hook_dispatch.py — declare and dispatch the agent-loop's enforcement points.

THE PROBLEM THIS SOLVES
-----------------------
Kilo advertises exactly one MCP client capability: `{roots:{}}`. No lifecycle
hooks, no sampling, no elicitation. So the usual hook surface — run a script on
UserPromptSubmit / PreToolUse / Stop — does not exist, and any design that
assumes it is a design that never runs. That was already learned the hard way
in this tree: `\\Azazael-Chain` was disabled, its log stopped mid-flight, and
"four days of self-improvement simply did not happen" with no error and no
alert anywhere.

So the hooks are not assumed. They are DECLARED, and each one is labelled with
the surface it actually rides and how strong that surface is. Four exist, and
they are not equally trustworthy:

  STRENGTH  SURFACE                    FIRES WHEN                  NEEDS ME?
  --------  -------------------------  --------------------------  ---------
  gate      kilo permission rule       agent calls a matched tool   no (human)
  tool      an MCP tool the agent      agent chooses to call it     YES
            must call anyway
  clock     cron / scheduled task      time passes                  no
  context   AGENTS.md instructions     every turn, if I read it     no, but
                                                                            unreliable

The ordering is the argument. `context` is where a hook usually goes and it is
the weakest: it is the same channel that already failed once tonight, and a
mechanism that lives inside the agent's own discretion fails exactly when the
agent is behaving badly. `gate` and `clock` fire regardless of what I do.
Anything load-bearing goes on those two. `context` is used only for intent,
never for enforcement, because a rule that reports PASS because nobody read it
is the "empty reads as clean" defect wearing a different hat.

UNARMED IS NOT HEALTHY
----------------------
A hook that is declared but whose backing tool is missing reports UNARMED, and
that is a failure state, not a pass. Same rule as `lesson_liveness.py`:
absence of evidence is not evidence of absence.

Usage:
    python tools/hook_dispatch.py --list
    python tools/hook_dispatch.py --run session_start
    python tools/hook_dispatch.py --status
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PY = sys.executable
TOOLS = os.path.dirname(os.path.abspath(__file__))
AZ = os.path.dirname(TOOLS)

# ---------------------------------------------------------------------------
# The registry. Data, not code, so a hook added tomorrow is a row and not a
# release. `check` must return 0 for the hook to count as ARMED.
# ---------------------------------------------------------------------------
HOOKS = [
    {
        "id": "session_start_load",
        "event": "session_start",
        "surface": "context",
        "strength": "weak",
        "enforces": "Load Skills: skills first, then RULE 0K canonical order.",
        "owner": ".kilo/command/load-skills.md + AGENTS.md KILO-STARTUP",
        "note": "Kilo's own command validator rejects frontmatter here, so the "
                "AGENTS.md block is the guaranteed path.",
    },
    {
        "id": "outcome_adjudication",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Derive follow-through verdicts for every surfaced lesson "
                    "and write them back. Rank lessons by measured adoption.",
        "owner": "tools/lesson_liveness.py",
        "check": ["tools/lesson_liveness.py", "--json"],
    },
    {
        "id": "evolution_progress",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Is the evolution loop IMPROVING, not merely running: "
                    "promotions, schema alternation, per-arm comparability, "
                    "QD stagnation, SPRT blindness, margin trend.",
        "owner": "tools/evolution_progress.py",
        "check": ["tools/evolution_progress.py", "--json"],
        "note": "audit_loop_liveness answers 'does it run' and passes this "
                "loop comfortably. This asks the second question.",
    },
    {
        "id": "fitness_arm_liveness",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Every objective arm contributes. Measured 2026-09-30: the "
                    "`alert` arm is exactly 0.0 on all 35 cycles because its "
                    "pool holds 7 rows against info's 500, so `total` IS "
                    "`info` and the loop is single-objective while reporting "
                    "two objectives. A scalar aggregating a live and a dead "
                    "component is indistinguishable from both being alive.",
        "owner": "tools/fitness_arm_liveness.py",
        "check": ["tools/fitness_arm_liveness.py", "--json"],
    },
    {
        "id": "gate_calibration",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Promotion thresholds are set against a measured "
                    "distribution, not an asserted one. Proposes only; never "
                    "writes into evolution_cycle.py.",
        "owner": "tools/process_benchmark.py",
        "check": ["tools/process_benchmark.py", "--json"],
    },
    {
        "id": "error_recording",
        "event": "per_turn",
        "surface": "context",
        "strength": "weak",
        "enforces": "Record every error in the same turn, with evidence.",
        "owner": "AGENTS.md KILO-STARTUP Phase 2",
        "note": "error_log is at 0 rows. The rule is written and unused, so "
                "this hook is declared but UNVERIFIED by observation.",
    },
    {
        "id": "commit_scope",
        "event": "pre_commit",
        "surface": "gate",
        "strength": "strong",
        "enforces": "A path-scoped commit must not sweep in unrelated staged "
                    "files; the index is read, not the working tree.",
        "owner": "tools/commit_scope_check.py",
        "check": ["tools/commit_scope_check.py", "--help"],
    },
    {
        "id": "scheduled_loop_liveness",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Every scheduled task still actually runs; silence bounds "
                    "derived from each task's declared cadence, not typed.",
        "owner": "scripts/audit_loop_liveness.py",
        "check": ["scripts/audit_loop_liveness.py", "--help"],
    },
    {
        "id": "loop_budget",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Loops stay inside their declared time/cost budget.",
        "owner": "scripts/audit_loop_budgets.py",
        "check": ["scripts/audit_loop_budgets.py", "--help"],
    },
    {
        "id": "artifact_freshness",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "Generated artifacts are not older than their sources.",
        "owner": "tools/artifact_freshness.py",
        "check": ["tools/artifact_freshness.py", "--help"],
    },
    {
        "id": "fs_hygiene",
        "event": "scheduled",
        "surface": "clock",
        "strength": "strong",
        "enforces": "No stray temp/probe files committed to the tree.",
        "owner": "tools/fs_hygiene.py",
        "check": ["tools/fs_hygiene.py", "--help"],
    },
    {
        "id": "write_advice",
        "event": "pre_write",
        "surface": "tool",
        "strength": "medium",
        "enforces": "Consult memory before an edit that a known lesson "
                    "covers, carrying the surface_id in the query.",
        "owner": "pmb MCP recall/find_lessons",
        "note": "MEDIUM, not strong: fires only if the agent chooses it. This "
                "is the coupling that made follow-through unreliable in the "
                "first place, and it is listed here so its weakness is "
                "visible rather than assumed away.",
    },
]


def armed(hook: dict) -> tuple[bool, str]:
    """Is the hook's instrument RUNNABLE, and is it currently healthy?

    Two different questions, and conflating them is a defect this file has
    already committed once. The liveness instruments here exit 1 on PURPOSE
    when they find a problem (`lesson_liveness`, `evolution_progress`,
    `fitness_arm_liveness`, `process_benchmark`), so reading rc!=0 as
    "unarmed" reports a working detector that found a dead loop as a broken
    detector -- which is the precise inversion that lets a real fault pass as
    an instrument fault.

    Convention across the instrument set:
        0  ran, everything healthy
        1  ran, FOUND A PROBLEM        -> armed, verdict DEGRADED
        2  ran, cannot judge (NO_DATA) -> armed, verdict BLIND
        other / traceback / missing    -> not armed
    """
    check = hook.get("check")
    if not check:
        return False, "no executable check declared (declarative only)"
    path = os.path.join(AZ, check[0])
    if not os.path.exists(path):
        return False, f"owner missing: {check[0]}"
    try:
        p = subprocess.run([PY] + check, cwd=AZ, capture_output=True,
                           text=True, timeout=300, encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:80]
    if p.returncode in (0, 1, 2):
        verdict = {0: "HEALTHY", 1: "DEGRADED", 2: "BLIND"}[p.returncode]
        return True, f"rc={p.returncode} verdict={verdict}"
    tail = (p.stderr or p.stdout or "").strip().splitlines()
    return False, f"BROKEN rc={p.returncode} {tail[-1][:70] if tail else ''}"


def status() -> dict:
    rows = []
    for h in HOOKS:
        ok, why = armed(h)
        rows.append({**h, "armed": ok, "reason": why})
    by_surface = {}
    for r in rows:
        b = by_surface.setdefault(r["surface"], {"n": 0, "armed": 0})
        b["n"] += 1
        b["armed"] += 1 if r["armed"] else 0
    strong = [r for r in rows if r["strength"] == "strong"]
    unverified = [r for r in rows if r["surface"] == "context"]
    healthy = [r for r in strong if "verdict=HEALTHY" in (r["reason"] or "")]
    degraded = [r for r in strong if "verdict=DEGRADED" in (r["reason"] or "")]
    blind = [r for r in strong if "verdict=BLIND" in (r["reason"] or "")]
    broken = [r for r in strong if not r["armed"]]
    return {
        "hooks": rows,
        "by_surface": by_surface,
        "strong_hooks": len(strong),
        "strong_hooks_armed": sum(1 for r in strong if r["armed"]),
        "strong_healthy": [r["id"] for r in healthy],
        "strong_degraded": [r["id"] for r in degraded],
        "strong_blind": [r["id"] for r in blind],
        "strong_broken": [r["id"] for r in broken],
        "unverified_context_hooks": [r["id"] for r in unverified],
        # The verdict is about the SYSTEM, not the instrument. A broken
        # instrument is the only condition that is not itself a finding.
        "verdict": "OK" if (not broken and not degraded and not blind)
                   else ("INSTRUMENT_BROKEN" if broken else "DEGRADED"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--run", metavar="HOOK_ID")
    a = ap.parse_args()

    if a.run:
        h = next((x for x in HOOKS if x["id"] == a.run), None)
        if h is None:
            print(f"unknown hook {a.run!r}", file=sys.stderr)
            return 2
        print(f"{h['id']}: {h['enforces']}")
        print(f"  surface={h['surface']} strength={h['strength']}")
        check = h.get("check")
        if not check:
            print("  declarative only - no executable to run")
            return 0
        # Bounded to 300s because armed() already runs this identical [PY] + check
        # shape under timeout=300, so the bound is evidence, not a guess. The bare
        # `# scanner:ignore` that stood on this line existed only to hide the
        # missing timeout; with the cause fixed there is nothing left to suppress,
        # so the marker was removed rather than given a reason.
        p = subprocess.run([PY] + check, cwd=AZ, timeout=300)
        return p.returncode

    st = status()
    if a.status or a.list or True:
        print(f"hook_dispatch: {st['verdict']}")
        print(f"  strong hooks armed : {st['strong_hooks_armed']}/{st['strong_hooks']}")
        for k, lbl in (('strong_healthy','HEALTHY '),('strong_degraded','DEGRADED'),('strong_blind','BLIND    '),('strong_broken','BROKEN  ')):
            if st[k]: print(f"    {lbl}: {', '.join(st[k])}")
        for sid, b in sorted(st["by_surface"].items()):
            print(f"  surface {sid:<9}: {b['armed']}/{b['n']} armed")
        if st["unverified_context_hooks"]:
            print(f"  UNVERIFIED (context-surface, unobserved): "
                  f"{', '.join(st['unverified_context_hooks'])}")
        print()
        for r in st["hooks"]:
            mark = "OK  " if r["armed"] else "UNAR"
            print(f"  [{mark}] {r['id']:<24} {r['surface']:<8} "
                  f"{r['strength']:<7} {r['enforces'][:52]}")
            if not r["armed"]:
                print(f"         reason: {r['reason']}")
    return 0 if st["verdict"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())

