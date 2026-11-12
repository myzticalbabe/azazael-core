#!/usr/bin/env python3
"""Bounded repo-wide audit with ADAPTIVE budgets (2026-09-12).

Every phase runs under an explicit budget, and a budget that is hit now
EXPANDS for the next run instead of silently returning less. The budgets live
in ``scripts/budget_policy.py`` / ``state/budget_policy.json``:

  files:          default 2000 .py files, doubles when the scan stops early
  pattern hits:   default 25 shown per pattern, doubles when a pattern hits it
  phase timeout:  default 120s per phase, doubles when a phase times out
  uncovered shown: display truncation only - the FULL list is always written
                  to state/test_backlog.json, so the 262-module backlog can no
                  longer hide behind a [:50] slice

A run that does NOT hit a budget shrinks it back toward its floor, so one busy
run cannot inflate the budgets permanently.

Phases:
  1. compile   - py_compile every .py (syntax health repo-wide)
  2. patterns  - risk grep: bare except, TODO/FIXME, shell=True,
                 hardcoded C:/Users/clint, print( in src (noise audit)
  3. testmap    - src modules with no test file naming them
  4. livecheck  - honesty spot-checks: plateau-skip honesty, promo path,
                 registry count (cheap, read-only)

Writes state/audit_summary.json (consumed by live_monitor AUDIT section) and
state/test_backlog.json (full uncovered-module list for triage).

Usage:
    python scripts/repo_audit.py
    python scripts/repo_audit.py --max-files 5000 --pattern-cap 50
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "audit_summary.json")
BACKLOG = os.path.join(ROOT, "state", "test_backlog.json")
SCAN_ROOTS = ["src", "scripts", "tests"]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import budget_policy as bp  # noqa: E402  (path set above)
import import_map as imap  # noqa: E402  (path set above)

PATTERNS = {
    "bare_except": re.compile(r"^\s*except\s*:"),
    "todo": re.compile(r"\b(TODO|FIXME|XXX|HACK|HACKFIX)\b"),
    "shell_true": re.compile(r"shell\s*=\s*True"),
    "hardcoded_home": re.compile(r"C:/Users/clint"),
    "print_in_src": re.compile(r"(?<![\w.])print\s*\("),
}


def _files(max_files):
    out = []
    for root in SCAN_ROOTS:
        base = os.path.join(ROOT, root)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", ".pytest_cache")]
            for f in fn:
                if f.endswith(".py"):
                    out.append(os.path.join(dp, f))
                    if len(out) >= max_files:
                        return out, True
    return out, False


def phase_compile(files, deadline):
    bad, ok = [], 0
    import py_compile
    for p in files:
        if time.time() > deadline:
            return {"ok": ok, "bad": bad, "timed_out": True}
        try:
            py_compile.compile(p, doraise=True)
            ok += 1
        except Exception as e:  # noqa: BLE001 — collected, not silent
            bad.append({"file": os.path.relpath(p, ROOT), "error": str(e)[:160]})
    return {"ok": ok, "bad": bad, "timed_out": False}


def phase_patterns(files, caps, deadline):
    """Grep the risk patterns. `caps` maps each pattern to its own budget.

    Per-pattern caps matter: `print_in_src` has thousands of hits, and one
    shared cap let that noise drive the budget for every risk pattern with it.
    """
    hits = {k: {"count": 0, "shown": [], "cap": caps.get(k, 0)}
            for k in PATTERNS}
    for p in files:
        if time.time() > deadline:
            return hits, True
        rel = os.path.relpath(p, ROOT)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    for name, rx in PATTERNS.items():
                        if name == "print_in_src" and not rel.startswith("src"):
                            continue
                        if rx.search(line):
                            h = hits[name]
                            h["count"] += 1
                            if len(h["shown"]) < h["cap"]:
                                h["shown"].append(f"{rel}:{i}")
        except Exception:
            pass
    return hits, False


def testmap_coverage():
    """Coverage from the real import graph, or (None, err) on failure.

    Replaces the name-substring check, which counted a module as covered if its
    NAME appeared anywhere in a test file (a docstring, a variable, an
    unrelated word) and collapsed same-named modules into one entry. It also
    reported basenames, so the "262-module backlog" was 262 *names*, not 262
    files. See scripts/import_map.py.
    """
    try:
        return imap.get_map().coverage(), ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:160]


def phase_livecheck(deadline):
    """Cheap read-only honesty spot-checks against the live store/code."""
    out = {}
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        rows = store.query_memories(event_type="training_run_complete", limit=2)
        vacuous = [r for r in rows
                   if (r.get("data", {}) or {}).get("records") == 0]
        out["recent_training_rows"] = len(rows)
        out["vacuous_rows_in_last2"] = len(vacuous)
        evo = store.query_memories(event_type="evolution_result", limit=1)
        out["evolution_alive"] = bool(evo)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
    out["timed_out"] = time.time() > deadline
    return out


def _budget(name, cli_value, flag):
    """Explicit flags win (and are recorded); otherwise the adaptive value."""
    if cli_value is not None:
        return bp.set_value(name, cli_value, "explicit --%s" % flag)
    return bp.get(name)


def main():
    ap = argparse.ArgumentParser(description="Bounded repo-wide audit")
    ap.add_argument("--max-files", type=int, default=None,
                    help="override the adaptive file budget")
    ap.add_argument("--pattern-cap", type=int, default=None,
                    help="override the adaptive pattern-hit cap")
    ap.add_argument("--phase-timeout", type=int, default=None,
                    help="override the adaptive per-phase timeout")
    args = ap.parse_args()

    max_files = _budget("audit.max_files", args.max_files, "max-files")
    # One budget PER pattern: `print_in_src` (thousands of hits) used to share
    # a cap with the risk patterns and drag it to the ceiling on its own.
    # The seed is the FLOOR of the shared budget, never its current value —
    # reading the value would inherit whatever noise-driven escalation had
    # pushed the old shared cap to (it handed every pattern a cap of 1000).
    if args.pattern_cap is not None:
        pattern_seed = bp.set_value("audit.pattern_cap", args.pattern_cap,
                                    "explicit --pattern-cap")
    else:
        pattern_seed = bp.bounds("audit.pattern_cap")[0]
        bp.set_value("audit.pattern_cap", pattern_seed,
                     "seed only; per-pattern budgets do the adapting")
    caps = {name: bp.ensure("audit.pattern." + name, pattern_seed,
                            pattern_seed, 1000)
            for name in PATTERNS}
    phase_timeout = _budget("audit.phase_timeout", args.phase_timeout,
                            "phase-timeout")
    uncovered_shown = bp.get("audit.uncovered_shown")
    bad_shown = bp.get("audit.bad_shown")

    summary = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "budgets": {"max_files": max_files,
                           "pattern_cap_seed": pattern_seed,
                           "pattern_caps": caps,
                           "phase_timeout": phase_timeout,
                           "uncovered_shown": uncovered_shown,
                           "bad_shown": bad_shown}}
    files, capped = _files(max_files)
    summary["files_scanned"] = len(files)
    summary["file_budget_hit"] = capped
    print(f"audit: {len(files)} files (budget {max_files})")

    t = time.time() + phase_timeout
    c = phase_compile(files, t)
    summary["compile"] = {"ok": c["ok"], "bad_count": len(c["bad"]),
                          "bad": c["bad"][:bad_shown],
                          "timed_out": c["timed_out"]}
    print(f"  compile: {c['ok']} ok, {len(c['bad'])} bad")

    t = time.time() + phase_timeout
    pats, pat_to = phase_patterns(files, caps, t)
    summary["patterns"] = {k: {"count": v["count"], "cap": v["cap"],
                               "shown": v["shown"]}
                           for k, v in pats.items()}
    summary["patterns_timed_out"] = pat_to
    capped_patterns = [k for k, v in pats.items() if v["count"] > v["cap"]]
    for k, v in pats.items():
        print(f"  {k}: {v['count']} (cap {v['cap']})")

    cov, cov_err = testmap_coverage()
    testmap_timed_out = cov is None
    if cov is None:
        summary["testmap"] = {"error": cov_err}
        uncovered = None
        print(f"  testmap: FAILED - {cov_err}")
    else:
        uncovered = cov["uncovered"]
        summary["testmap"] = {
            "method": "import-graph",
            "src_modules": cov["src_modules"],
            "covered": cov["covered"],
            "covered_by_import": len(cov["via_import"]),
            "covered_by_path_literal": len(cov["via_path_literal"]),
            "uncovered_count": len(uncovered),
            "uncovered": uncovered[:uncovered_shown],
            "uncovered_shown": min(len(uncovered), uncovered_shown),
            "parse_errors": len(cov["parse_errors"]),
        }
        print(f"  testmap: {cov['src_modules']} src modules, "
              f"{cov['covered']} reached by tests, {len(uncovered)} not "
              f"(import-graph)")
    summary["testmap_timed_out"] = testmap_timed_out

    t = time.time() + phase_timeout
    summary["livecheck"] = phase_livecheck(t)
    print(f"  livecheck: {summary['livecheck']}")

    # -- Adaptive response: expand what was hit, relax what was not ---------
    adapt = {"escalated": [], "settled": []}

    def _grow(name, reason, needed=None):
        adapt["escalated"].append({"budget": name,
                                   "to": bp.escalate(name, reason,
                                                     needed=needed)})

    def _relax(name, reason):
        adapt["settled"].append({"budget": name, "to": bp.settle(name, reason)})

    # Hysteresis on the way down: only shrink when there is clear headroom
    # (half the budget unused). Settling merely because count <= cap made a
    # budget oscillate: 165 hits at cap 165 settled to 82, which the next run
    # escalated straight back to 165 (2026-09-12).
    if capped:
        _grow("audit.max_files",
              "scan stopped at %d files with roots unscanned" % len(files))
    elif len(files) * 2 <= max_files:
        _relax("audit.max_files", "scanned %d, under half the cap" % len(files))
    if c["timed_out"] or pat_to or testmap_timed_out:
        _grow("audit.phase_timeout",
              "phase timed out at %ds (compile=%s patterns=%s testmap=%s)"
              % (phase_timeout, c["timed_out"], pat_to, testmap_timed_out))
    else:
        _relax("audit.phase_timeout",
               "all phases finished inside %ds" % phase_timeout)
    for name, v in pats.items():
        budget = "audit.pattern." + name
        if v["count"] > v["cap"]:
            _grow(budget, "%s: %d hits against cap %d"
                  % (name, v["count"], v["cap"]), needed=v["count"])
        elif v["count"] * 2 <= v["cap"]:
            _relax(budget, "%s: %d hits, under half of cap %d"
                   % (name, v["count"], v["cap"]))
        # else: within the band - hold, so the budget does not oscillate
    # The seed is only the starter value for new patterns now, so it stays put.
    _relax("audit.pattern_cap",
           "seed value only; per-pattern budgets do the adapting")

    summary["adaptive"] = adapt
    summary["budget_policy"] = bp.snapshot()

    if uncovered is not None:
        try:
            payload = {"at": summary["at"], "method": "import-graph",
                       "src_modules": cov["src_modules"],
                       "covered": cov["covered"],
                       "uncovered_count": len(uncovered),
                       "uncovered": uncovered,
                       "parse_errors": cov["parse_errors"]}
            tmp = BACKLOG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, BACKLOG)
            print(f"wrote {BACKLOG} ({len(uncovered)} modules)")
        except Exception as e:  # noqa: BLE001
            print(f"backlog write failed: {e}")

    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        os.replace(tmp, STATE)
        print(f"wrote {STATE}")
    except Exception as e:  # noqa: BLE001
        print(f"summary write failed: {e}")
        return 1

    if adapt["escalated"]:
        print("budgets escalated: " + ", ".join(
            "%s->%d" % (e["budget"], e["to"]) for e in adapt["escalated"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
