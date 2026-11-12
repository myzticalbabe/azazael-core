#!/usr/bin/env python3
"""Mutation testing, stdlib-only (2026-09-11).

Answers "do the tests actually catch bugs?" by seeding small faults
(mutants) into a target file and running the test command against each.
A mutant the tests MISS (exit 0) is a hole in the suite.

Safety: the target is checksummed before/after every mutant and restored
in a finally block; a leftover .mutate-backup means an interrupted run —
rerun restores from it before doing anything else.

Budgets (raise deliberately): --max mutants (default 20, sampled, seeded),
--timeout seconds per test run (default 300). Baseline run first: if the
tests fail UNMUTATED, abort (a red baseline makes every mutant "killed").

Operators (AST):
  compare-flip  < > <= >= == !=  -> mirrored operator
  arith-flip    + - * /          -> paired operator
  bool-flip     and <-> or
  not-drop      `not x` -> `x`
  const-flip    True <-> False

Writes state/mutation_summary.json (consumed by live_monitor mutants line).

Usage:
    python scripts/mutate.py --target src/CORE/WORMS/expansion/evolution.py --tests tests/test_nsga2.py
    python scripts/mutate.py --target src/AOA/arms/kali_arm.py --tests src/AOA/test_arm_exports.py --max 10
"""
import argparse
import ast
import hashlib
import json
import os
import random
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "mutation_summary.json")
HISTORY = os.path.join(ROOT, "state", "mutation_history.json")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import budget_policy as bp  # noqa: E402  (path set above)

# A mutant verdict of "timeout" is INDETERMINATE, not a kill. Counting those
# in the denominator silently depressed every score on a slow suite (2026-09-12),
# so the score is computed over decisive runs only and the count is reported.

CMP_FLIP = {ast.Lt: ast.Gt, ast.Gt: ast.Lt, ast.LtE: ast.GtE,
            ast.GtE: ast.LtE, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
ARITH_FLIP = {ast.Add: ast.Sub, ast.Sub: ast.Add,
              ast.Mult: ast.Div, ast.Div: ast.Mult}
BOOL_FLIP = {ast.And: ast.Or, ast.Or: ast.And}


class MutantFinder(ast.NodeVisitor):
    """Collect (lineno, col, kind, describe) mutation sites."""

    def __init__(self):
        self.sites = []

    def visit_Compare(self, node):
        if len(node.ops) == 1 and type(node.ops[0]) in CMP_FLIP:
            self.sites.append((node.lineno, node.col_offset, "compare-flip",
                               type(node.ops[0]).__name__))
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if type(node.op) in ARITH_FLIP:
            self.sites.append((node.lineno, node.col_offset, "arith-flip",
                               type(node.op).__name__))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        if type(node.op) in BOOL_FLIP:
            self.sites.append((node.lineno, node.col_offset, "bool-flip",
                               type(node.op).__name__))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self.sites.append((node.lineno, node.col_offset, "not-drop", "Not"))
        self.generic_visit(node)

    def visit_Constant(self, node):
        if node.value is True or node.value is False:
            self.sites.append((node.lineno, node.col_offset, "const-flip",
                               str(node.value)))
        self.generic_visit(node)


def _main_guard_ranges(tree):
    """Line ranges of `if __name__ == "__main__":` blocks.

    Sites in there never execute under pytest (import does not run the
    block), so every mutant sampled there SURVIVES by construction and
    depresses the score with zero signal about the suite. coverage.py
    excludes the same blocks by default; this matches that precedent.
    Returns a list of (first, last) inclusive line numbers."""
    ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)):
            continue
        left = test.left
        rights = test.comparators
        names = set()
        consts = set()
        for part in [left, *rights]:
            if isinstance(part, ast.Name):
                names.add(part.id)
            elif isinstance(part, ast.Constant):
                consts.add(part.value)
        if names == {"__name__"} and consts == {"__main__"}:
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


def _guard_only_ranges(tree, guards):
    """Line ranges of functions called ONLY from main-guard blocks.

    A demo()/main() invoked solely under `if __name__ == "__main__":`
    is uncovered-script code under pytest: nothing imports-and-calls it
    (verified shape: the demo tests assert callability only, never run
    the body), so its mutants survive by construction. A function called
    from anywhere else (including `from mod import f; f()` in a test,
    which is a Name call) is kept: this is call-graph reachability,
    not name-matching. Attribute calls (`mod.f()`) are NOT tracked —
    grep for those before trusting an exclusion on a new target."""
    if not guards:
        return []
    guard_calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if any(lo <= node.lineno <= hi for lo, hi in guards):
                guard_calls.add(node.func.id)
    outside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if not any(lo <= node.lineno <= hi for lo, hi in guards):
                outside.add(node.func.id)
    guard_only = guard_calls - outside
    ranges = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in guard_only:
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


class MutantApplier(ast.NodeTransformer):
    """Apply exactly one site (lineno, col, kind)."""

    def __init__(self, lineno, col, kind):
        self.target = (lineno, col, kind)
        self.applied = False

    def _hit(self, node, kind):
        return (not self.applied and kind == self.target[2]
                and node.lineno == self.target[0]
                and node.col_offset == self.target[1])

    def visit_Compare(self, node):
        if self._hit(node, "compare-flip"):
            node.ops[0] = CMP_FLIP[type(node.ops[0])]()
            self.applied = True
        return self.generic_visit(node)

    def visit_BinOp(self, node):
        if self._hit(node, "arith-flip"):
            node.op = ARITH_FLIP[type(node.op)]()
            self.applied = True
        return self.generic_visit(node)

    def visit_BoolOp(self, node):
        if self._hit(node, "bool-flip"):
            node.op = BOOL_FLIP[type(node.op)]()
            self.applied = True
        return self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not) and self._hit(node, "not-drop"):
            self.applied = True
            return self.generic_visit(node.operand)
        return self.generic_visit(node)

    def visit_Constant(self, node):
        if (node.value is True or node.value is False) and self._hit(node, "const-flip"):
            node.value = not node.value
            self.applied = True
        return node


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def run_tests(test_args, timeout):
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest"] + test_args + ["-q", "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=timeout, cwd=ROOT, encoding="utf-8", errors="replace")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return None  # timed out: neither killed nor survived


def _append_history(record):
    """Durable per-target log; STATE only ever holds the latest run.

    Replaces any earlier record for the same (target, tests) pair so a re-run
    updates in place instead of piling up, and caps the log at 200 entries.
    """
    hist = []
    if os.path.exists(HISTORY):
        try:
            with open(HISTORY, encoding="utf-8") as f:
                hist = json.load(f)
            if not isinstance(hist, list):
                hist = []
        except Exception:  # noqa: BLE001
            hist = []
    key = (record["target"], tuple(record["tests"]))
    hist = [h for h in hist
            if (h.get("target"), tuple(h.get("tests", []))) != key]
    hist.append(record)
    tmp = HISTORY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist[-200:], f, indent=2)
    os.replace(tmp, HISTORY)


def main():
    ap = argparse.ArgumentParser(description="Budgeted mutation testing")
    ap.add_argument("--target", required=True, help="source file, repo-relative")
    ap.add_argument("--tests", nargs="+", required=True, help="test paths, repo-relative")
    ap.add_argument("--max", type=int, default=None,
                    help="max mutants (default: adaptive, budget_policy)")
    ap.add_argument("--timeout", type=int, default=None,
                    help="seconds per run (default: adaptive, budget_policy)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--only", default="",
                    help="verify specific sites only, e.g. --only 1398:24,1414:44")
    args = ap.parse_args()

    # Adaptive budgets: an explicit flag wins and is recorded; otherwise the
    # value is whatever previous runs escalated it to (bounded by a ceiling).
    if args.max is not None:
        args.max = bp.set_value("mutate.max_mutants", args.max, "explicit --max")
    else:
        args.max = bp.get("mutate.max_mutants")
    if args.timeout is not None:
        args.timeout = bp.set_value("mutate.per_test_timeout", args.timeout,
                                    "explicit --timeout")
    else:
        args.timeout = bp.get("mutate.per_test_timeout")

    target = os.path.join(ROOT, args.target)
    backup = target + ".mutate-backup"
    if os.path.exists(backup):
        print("leftover backup found (interrupted run?) — restoring first")
        os.replace(backup, target)
    original = sha(target)

    src = open(target, encoding="utf-8").read()
    tree = ast.parse(src)
    guards = _main_guard_ranges(tree)
    excluded = list(guards) + _guard_only_ranges(tree, guards)
    finder = MutantFinder()
    finder.visit(tree)
    sites = finder.sites
    if excluded:
        before = len(sites)
        sites = [s for s in sites
                 if not any(lo <= s[0] <= hi for lo, hi in excluded)]
        print(f"mutate: excluded {before - len(sites)} guard-only site(s)"
              f" in {len(excluded)} main-guard/guard-only range(s)"
              f" (uncovered-script code; verify with grep before trusting)")
    total_sites = len(sites)
    sampled = False
    print(f"mutate: {total_sites} sites in {args.target}")
    if not sites:
        return 0
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        sites = [s for s in sites if f"{s[0]}:{s[1]}" in want]
        print(f"mutate: --only filter -> {len(sites)} sites")
        if not sites:
            print("mutate: no sites match --only")
            return 0
    else:
        rng = random.Random(args.seed)
        rng.shuffle(sites)
        sampled = total_sites > args.max
        sites = sites[:args.max]

    print("mutate: baseline run (unmutated must pass)...")
    if not run_tests(args.tests, args.timeout):
        print("mutate: ABORT — baseline red, every mutant would 'survive'")
        return 2

    results = []
    for i, (ln, col, kind, desc) in enumerate(sites):
        tree = ast.parse(src)
        applier = MutantApplier(ln, col, kind)
        tree = applier.visit(tree)
        if not applier.applied:
            continue
        t0 = time.time()
        os.replace(target, backup)
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(ast.unparse(tree))
            passed = run_tests(args.tests, args.timeout)
        finally:
            os.replace(backup, target)
        assert sha(target) == original, "RESTORE FAILED — target left mutated!"
        verdict = "killed" if passed is False else ("timeout" if passed is None else "SURVIVED")
        secs = round(time.time() - t0, 1)
        results.append({"site": f"{ln}:{col}", "kind": kind, "op": desc, "verdict": verdict})
        print(f"  [{i + 1}/{len(sites)}] {kind} {desc} @ {ln}:{col} -> {verdict} ({secs}s)")

    killed = sum(1 for r in results if r["verdict"] == "killed")
    survived = [r for r in results if r["verdict"] == "SURVIVED"]
    timed_out = [r for r in results if r["verdict"] == "timeout"]
    decisive = killed + len(survived)
    score = round(killed / decisive, 3) if decisive else 0.0
    print(f"mutate: {killed}/{decisive} killed (score {score})"
          + (f", {len(timed_out)} timed out" if timed_out else ""))
    for r in survived:
        print(f"  SURVIVED: {r['kind']} {r['op']} @ {r['site']}")
    for r in timed_out:
        print(f"  TIMEOUT:  {r['kind']} {r['op']} @ {r['site']}")

    # -- Adaptive response: widen what ran out of room ----------------------
    adaptive = {"escalated": [], "settled": []}
    if sampled:
        adaptive["escalated"].append({
            "budget": "mutate.max_mutants",
            "to": bp.escalate("mutate.max_mutants",
                              "sampled %d of %d sites"
                              % (len(results), total_sites))})
    else:
        adaptive["settled"].append({
            "budget": "mutate.max_mutants",
            "to": bp.settle("mutate.max_mutants",
                            "%d sites, all tested" % total_sites)})
    if timed_out:
        adaptive["escalated"].append({
            "budget": "mutate.per_test_timeout",
            "to": bp.escalate("mutate.per_test_timeout",
                              "%d mutant(s) exceeded %ds"
                              % (len(timed_out), args.timeout))})
    else:
        adaptive["settled"].append({
            "budget": "mutate.per_test_timeout",
            "to": bp.settle("mutate.per_test_timeout",
                            "no mutant exceeded %ds" % args.timeout)})

    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "target": args.target, "tests": args.tests,
              "mutants": len(results), "killed": killed,
              "timed_out": len(timed_out), "sampled": sampled,
              "total_sites": total_sites,
              "score": score, "survived": survived,
              "adaptive": adaptive}
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        os.replace(tmp, STATE)
        _append_history(record)
    except Exception as e:  # noqa: BLE001
        print(f"summary write failed: {e}")
    # A timeout is indeterminate, so it is not a pass.
    return 0 if not survived and not timed_out else 1


if __name__ == "__main__":
    sys.exit(main())