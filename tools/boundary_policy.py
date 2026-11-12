#!/usr/bin/env python3
"""boundary_policy -- turn the census's tiers into constraints, not descriptions.

WHY
The ledger already computes which trees are cleave-clean (`cleave`), which are
imported (`imported`), which are data stubs (`stub`), which is the trunk, and
which directories outside src/ are reached (`periphery`). Those are descriptions:
nothing stops a boundary from being crossed, it is only reported after the fact.
Nx's depConstraints do the other thing -- declare which tagged projects may depend
on which, then fail on a violation -- and that is what this is.

The rules shipped here are deliberately few and currently all satisfied. That is
their role: a guard on today's tree, not a backlog. What proves the checker works
is `--selftest`, which runs synthetic graphs through it and fails unless each rule
fires when it should AND stays silent on a healthy fixture.

USAGE
  python tools/boundary_policy.py --census C:/Users/clint/yggdrasil_forest/yggdrasil_census.json
  python tools/boundary_policy.py --selftest
  python tools/boundary_policy.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

HERE = Path(__file__).resolve().parent
POLICY = HERE / "boundary_policy.json"


def _names(rows: Iterable[Any]) -> List[str]:
    """Tier rows are names in some tiers and {tree: ...} dicts in others."""
    out = []
    for r in rows or []:
        out.append(r["tree"] if isinstance(r, dict) else r)
    return out


def tags_of(ledger: Dict[str, Any]) -> Dict[str, Set[str]]:
    tiers = ledger.get("tiers") or {}
    tags: Dict[str, Set[str]] = defaultdict(set)
    for name in _names(tiers.get("A_cleave_today")):
        tags[name].add("cleave")
    for name in _names(tiers.get("A_mixed_unaudited")):
        tags[name].add("mixed")
    for name in _names(tiers.get("B_needs_shim")):
        tags[name].add("imported")
    for name in _names(tiers.get("D_stub")):
        tags[name].add("stub")
    for name in _names(tiers.get("C_cycle_blocked")):
        tags[name].add("cycle")
    tags["CORE"].add("trunk")
    # A periphery directory only exists because an edge named it.
    for row in (ledger.get("cross_language") or {}).get("periphery_pairs") or []:
        for side in (row["from"], row["to"]):
            if side.startswith("@"):
                tags[side].add("periphery")
    return tags


def edges_of(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every tree->tree reference, from every graph, with its evidence.

    Zero-ref rows are not edges (a tier table can carry a placeholder pair) and
    the same pair reached through two summaries is counted once -- a policy that
    double-reports one crossing teaches people to ignore it.
    """
    out: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, bool]] = set()

    def add(row: Dict[str, Any], gname: str, weak: bool) -> None:
        if row["from"] == row["to"] or int(row.get("refs") or 0) <= 0:
            return
        key = (row["from"], row["to"], gname, weak)
        if key in seen:
            return
        seen.add(key)
        out.append({"from": row["from"], "to": row["to"], "refs": row["refs"],
                    "graph": gname, "weak": weak})

    for gname, g in [("python", ledger.get("python_graph") or {})] + \
                    list((ledger.get("graphs") or {}).items()):
        for row in g.get("tree_pairs") or []:
            add(row, gname, False)
        for row in g.get("tree_pairs_weak") or []:
            add(row, gname, True)
        for row in g.get("periphery_pairs") or []:
            add(row, gname, False)
    # A census built without an in-process graph (a fixture, or a ledger written
    # before periphery_pairs existed) still has to be checkable.
    for row in (ledger.get("cross_language") or {}).get("periphery_pairs") or []:
        add(row, "cross_language", False)
    return out


def _matches(selector: str, name: str, tags: Dict[str, Set[str]]) -> bool:
    if selector == "*":
        return True
    if selector.startswith("tag:"):
        return selector[4:] in tags.get(name, set())
    return selector == name


def check(ledger: Dict[str, Any], policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    tags = tags_of(ledger)
    violations: List[Dict[str, Any]] = []
    for rule in policy.get("constraints") or []:
        source = rule.get("source", "*")
        forbid: Sequence[str] = rule.get("forbid") or []
        # `allow_source` names the callers permitted to reach the forbidden
        # target: the rule is "only drivers reach the periphery", so the
        # exception belongs on the source side. (Written as a target whitelist
        # once, which made the one real edge look like a violation.)
        allow_source: Sequence[str] = rule.get("allow_source") or []
        for edge in edges_of(ledger):
            if not _matches(source, edge["from"], tags):
                continue
            if not any(_matches(sel, edge["to"], tags) for sel in forbid):
                continue
            if any(_matches(sel, edge["from"], tags) for sel in allow_source):
                continue
            violations.append({
                "rule": rule.get("name", "(unnamed)"),
                "from": edge["from"], "to": edge["to"], "refs": edge["refs"],
                "graph": edge["graph"], "weak": edge["weak"],
                "why": rule.get("why", ""),
            })
    return violations


def selftest() -> int:
    """Positive AND negative controls: an oracle that never fires is unvalidated."""
    policy = {"constraints": [
        {"name": "nothing depends on a stub", "source": "*", "forbid": ["tag:stub"]},
        {"name": "only drivers reach the periphery", "source": "*",
         "forbid": ["tag:periphery"], "allow_source": ["TOOLS"]},
    ]}
    healthy = {
        "tiers": {"D_stub": ["DATA"]},
        "python_graph": {"tree_pairs": [{"from": "CORE", "to": "WORMZ", "refs": 3},
                                         {"from": "CORE", "to": "DATA", "refs": 0}]},
        "cross_language": {"periphery_pairs": [{"from": "TOOLS", "to": "@scripts",
                                                "refs": 4}]},
    }
    broken = {
        "tiers": {"D_stub": ["DATA"]},
        "python_graph": {"tree_pairs": [{"from": "AOA", "to": "DATA", "refs": 2}]},
        "cross_language": {"periphery_pairs": [{"from": "REPORT",
                                                "to": "@scripts", "refs": 1}]},
    }
    failures: List[str] = []
    if check(healthy, policy):
        failures.append("healthy fixture fired: %r" % (check(healthy, policy),))
    fired = check(broken, policy)
    if len(fired) != 2:
        failures.append("broken fixture gave %d violation(s), expected 2: %r"
                        % (len(fired), fired))
    if not any(v["rule"].startswith("nothing depends") for v in fired):
        failures.append("stub rule did not fire on AOA -> DATA")
    if not any(v["rule"].startswith("only drivers") for v in fired):
        failures.append("periphery rule did not fire on REPORT -> @scripts")
    if failures:
        print("SELFTEST FAILED", file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 3
    print("SELFTEST OK — 4 controls (healthy silent, stub rule fires, periphery "
          "rule fires, violation count exact)")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--census", default=None,
                    help="census JSON (default: ~/yggdrasil_forest/yggdrasil_census.json)")
    ap.add_argument("--policy", default=str(POLICY))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    census_path = Path(args.census) if args.census else \
        Path.home() / "yggdrasil_forest" / "yggdrasil_census.json"
    if not census_path.is_file():
        print("no census at %s -- run yggdrasil_census.py --out first"
              % census_path, file=sys.stderr)
        return 2
    ledger = json.loads(census_path.read_text(encoding="utf-8"))
    violations = check(ledger, policy)
    if args.json:
        print(json.dumps({"violations": violations,
                          "rules": len(policy.get("constraints") or [])}, indent=2))
    else:
        print("boundary policy -- %d rule(s) over %d edge(s)"
              % (len(policy.get("constraints") or []), len(edges_of(ledger))))
        for rule in policy.get("constraints") or []:
            hits = [v for v in violations if v["rule"] == rule.get("name")]
            print("  %-40s %s" % (rule.get("name"), "OK" if not hits else
                                  "%d VIOLATION(S)" % len(hits)))
        for v in violations[:20]:
            print("    %s -> %s (%s, %d ref%s) -- %s"
                  % (v["from"], v["to"], v["graph"], v["refs"],
                     "" if v["refs"] == 1 else "s", v["rule"]))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
