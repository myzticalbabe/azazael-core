#!/usr/bin/env python3
# coding: utf-8
"""Find stores where a reduction cannot survive its own save.

Why this exists
---------------
Five stores in `src/CORE/spine_registry.py` shared one defect, and the fifth
was only found because the fourth had just been fixed:

| store | reduction | what undid it |
|---|---|---|
| fixtures | `purge_fixtures` | re-adopted from disk |
| Q evidence | `invalidate_q` | `max()` on the count |
| homeostat counters | counter reset | `max()` on successes/failures |
| Q table | `cap_q_tables` | ranked by evidence, so a floored key sorted first |
| prune stage | `_stage_prune` | called a count of 0 "dead" |

The mechanism is always the same. The read paths fold the saved file back in
before every write, so **a value assigned downward in a monotone store loses
on the next save.** Each fix was correct and each fix was found by a different
symptom, one incident at a time, on a live ledger. Measured cost of the fourth:
10 genuine ~95ms latency samples per route, destroyed by the invalidation
that was meant to discount them.

The fifth instance is the argument for this file. Repairing a reduction is
what makes its key look *absent* to whatever reads the count, so every fix
creates a new place where "no evidence" and "evidence we disowned" are the
same number. That is a shape, and a shape can be checked.

What it checks, per module-level store
--------------------------------------
1. **Monotone merge.** Some read path adopts an incoming value with `max()`.
2. **A lowering write.** Something can assign the store a value that did not
   come from the store: `-=`, `= 0`, `= min(...)`, `.pop(...)`, a clamped
   `max(bound, ...)`, or a plain assignment of a value computed elsewhere.
3. **A watermark.** A sibling structure whose name says it records a decision
   (floor / tombstone / purge / watermark / ceiling) and which is read inside
   the same function as the merge.

A store with 1 and 2 but no 3 is reported. Findings are CANDIDATES for
review, not verdicts: AST cannot tell a deliberate reduction from a bug, and a
tool that claimed otherwise would be the same false-certainty failure this
project keeps auditing for. The exit code is 1 only when an UNREVIEWED store
appears, so an acknowledged store stops being noise.

Three ways this checker already lied, and how each is now prevented
------------------------------------------------------------------
Each of these produced a green "OK" from a tool that had never seen the
defect it was written for. A guard that cannot see its own target is worse
than no guard, so each is now structurally impossible rather than fixed:

* **Subscript targets were invisible.** `_dotted()` walked attributes and
  names only, so `_Q_UPDATE_COUNTS[k] = _adopt_evidence(k, n)` resolved to
  `""`, no merge was ever detected, and the file it was written about
  reported "OK" with 20 stores and zero merges. `_base()` peels the subscript
  once, in one place.
* **Local aliases hid the writes.** The counter reset is
  `rec = _HOMEOSTAT.get(key)` then `rec[f] = 0` -- neither line mentions
  `_HOMEOSTAT`. Aliases bound from a known store inside a function are now
  resolved to it.
* **"No merges at all" reported as OK.** A checker that finds nothing can be
  right for two very different reasons: the code is clean, or the checker is
  blind. It now exits 2 INCONCLUSIVE, never 0.

Usage:
    python tools/monotone_reduction_check.py            # report, exit 1 on new
    python tools/monotone_reduction_check.py --json
    python tools/monotone_reduction_check.py --ack spine_registry:_COUNTER_FLOOR

Exit codes: 0 clean, 1 unreviewed store, 2 inconclusive (saw stores but no
monotone merge anywhere -- the checker's own blind spot, not a pass).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ("src/CORE/spine_registry.py", "src/CORE/neural_loop.py")

#: A sibling structure named like one of these is taken as a watermark record.
WATERMARK_HINTS = ("floor", "tombstone", "purge", "watermark", "ceiling",
                   "blocked", "suppress", "purged")

#: Acknowledged stores. An entry is "module:store". Adding one is a claim that
#: a human looked at the finding and decided the store is safe -- which is the
#: only thing that makes this checker's exit code meaningful over time.
ACK_DEFAULT = (
    "spine_registry:_PURGED_KEYS",
    "spine_registry:_Q_EVIDENCE_FLOOR",
    "spine_registry:_COUNTER_FLOOR",
    "spine_registry:_Q_UPDATE_COUNTS",
    "spine_registry:_Q_DREAM_COUNTS",
    "spine_registry:_Q_VARIANCE",
    "spine_registry:_Q_WEIGHTS",
    "spine_registry:_HOMEOSTAT",
    "spine_registry:_SCARS",
    "spine_registry:_SPINE_EVENTS",
    "spine_registry:_GOSSIP_SEQ_SEEN",
    "neural_loop:_Q_WEIGHTS",
)


def _dotted(node: ast.AST) -> str:
    """Full dotted path of an attribute/name chain, '' for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _base(node: ast.AST) -> str:
    """The module-level name an expression ultimately reads from or writes to.

    Peels Subscript, Starred and Call, then takes the first dotted component.

    Two peels this function needs, both learned by the checker silently
    missing a live defect:
    * **Subscript.** `_Q_UPDATE_COUNTS[k] = _adopt_evidence(k, n)` is a
      Subscript node, not a Name, so without the peel every write in the
      file resolved to `""` and the tool reported "OK" on the one file it
      was written about.
    * **Call.** `cur = _HOMEOSTAT.get(k)` binds an alias only if the Call
      peels to its `func`. Without it, `cur[f] = 0` in `reset_counters`
      attributed to no store at all, and the counter-reset instance -- one
      of the five this file exists for -- was invisible.
    """
    while True:
        if isinstance(node, (ast.Subscript, ast.Starred)):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break
    return _dotted(node).split(".")[0]


def _is_zero(expr: ast.AST) -> bool:
    return isinstance(expr, ast.Constant) and expr.value == 0


def _funcs(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(start_line, end_line, name) for every function, for scope lookups."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(n, "end_lineno", n.lineno) or n.lineno
            out.append((n.lineno, end, n.name))
    return out


def _enclosing(functions: list[tuple[int, int, str]], line: int) -> str:
    """Innermost function containing `line`, else '<module>'."""
    best = "<module>"
    best_span = None
    for start, end, name in functions:
        if start <= line <= end and (best_span is None or (end - start) < best_span):
            best, best_span = name, end - start
    return best


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _reads(expr: ast.AST, name: str) -> bool:
    """Does this expression read `name` (bare or subscripted)?"""
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
        if isinstance(sub, ast.Attribute) and _dotted(sub).split(".")[0] == name:
            return True
    return False


def _is_clamped_max(expr: ast.AST) -> bool:
    """`max(bound, ...)` -- a merge that can also pull a value DOWN."""
    return (isinstance(expr, ast.Call) and _dotted(expr.func) == "max"
            and bool(expr.args))


def analyse(path: Path) -> dict[str, Any]:
    """Classify every module-level dict store in one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    stores: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        ann = ast.unparse(node.annotation)
        if "Dict[" not in ann and "dict[" not in ann:
            continue
        # A dispatch table is not a store. `Dict[str, Callable[...]]` maps a
        # name to a handler and is replaced wholesale, never merged with
        # max(); counting it would make every file with a stage registry
        # look like a file with a monotone store.
        if "Callable" in ann:
            continue
        stores[node.target.id] = {
            "store": node.target.id, "annotation": ann, "line": node.lineno,
            "monotone_merge": [], "lowering_writes": [], "watermark_consults": [],
        }

    if not stores:
        return {"file": str(path), "stores": [], "stores_seen": 0,
                "any_merge_seen": False, "inconclusive": False}

    known = set(stores)
    functions = _funcs(tree)
    parents = _parents(tree)

    # Local helper functions, so a merge hidden behind one is still found.
    # Not gold-plating: the spine's own fix moved `_Q_UPDATE_COUNTS[k] =
    # max(...)` into `_adopt_evidence(k, n)`, and a checker that only looked
    # for a literal `max(` reported the whole file OK while the shape it
    # exists to catch was still there. A guard that stops seeing the defect
    # because the defect was refactored is worse than no guard.
    helpers = {n.name: n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name.startswith("_")}

    def _rhs_is_max(node: ast.AST, depth: int = 2) -> bool:
        if depth <= 0:
            return False
        if isinstance(node, ast.Call):
            fn = _dotted(node.func)
            if fn == "max":
                return True
            helper = helpers.get(fn.split(".")[-1])
            if helper is not None:
                return any(_rhs_is_max(sub, depth - 1) for sub in ast.walk(helper))
        return False

    def _aliases(fn_scope: ast.AST) -> dict[str, str]:
        """Local name -> store, for `rec = _HOMEOSTAT.get(key)` and friends."""
        out: dict[str, str] = {}
        for n in ast.walk(fn_scope):
            if not isinstance(n, ast.Assign) or len(n.targets) != 1:
                continue
            tgt = n.targets[0]
            if not isinstance(tgt, ast.Name) or tgt.id in known:
                continue
            b = _base(n.value)
            if b in known:
                out[tgt.id] = b
        return out

    def _absent_guard(node: ast.AST, aliases: dict[str, str]) -> str | None:
        """Store adopted under `if x is None:` -- a first write, not a lowering.

        `_HOMEOSTAT[k] = v` inside `if cur is None:` looks identical to a
        downward write and is not one. Counting it would bury the real
        findings under every adopt-on-load in the file.
        """
        cur = node
        while id(cur) in parents:
            par = parents[id(cur)]
            if isinstance(par, ast.If) and isinstance(par.test, ast.Compare):
                cmp_ = par.test
                if (len(cmp_.ops) == 1 and isinstance(cmp_.ops[0], ast.Is)
                        and isinstance(cmp_.comparators[0], ast.Constant)
                        and cmp_.comparators[0].value is None
                        and isinstance(cmp_.left, ast.Name)
                        and cmp_.left.id in aliases):
                    return aliases[cmp_.left.id]
            cur = par
        return None

    # Per-function context, so the alias table is not leaked across scopes.
    ctx: dict[int, dict[str, str]] = {}
    for start, end, _name in functions:
        scope = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.lineno == start
                      and (getattr(n, "end_lineno", n.lineno) or n.lineno) == end),
                     None)
        if scope is not None:
            a = _aliases(scope)
            for ln in range(start, end + 1):
                ctx[ln] = a

    def _alias_for(lineno: int) -> dict[str, str]:
        return ctx.get(lineno, {})

    for node in ast.walk(tree):
        aliases = _alias_for(getattr(node, "lineno", -1))

        # ---- (1) monotone merge -------------------------------------------
        if isinstance(node, ast.Assign) and _rhs_is_max(node.value):
            for tgt in node.targets:
                base = _base(tgt)
                if base in known:
                    stores[base]["monotone_merge"].append(node.lineno)
                    continue
                # through an alias: cur[f] = _adopt_counter(...)
                if isinstance(tgt, (ast.Subscript, ast.Attribute)):
                    root = _dotted(tgt.value).split(".")[0]
                    if root in aliases:
                        stores[aliases[root]]["monotone_merge"].append(node.lineno)
                        continue
                if isinstance(tgt, ast.Name) and tgt.id in aliases:
                    stores[aliases[tgt.id]]["monotone_merge"].append(node.lineno)

        # ---- (2) lowering writes ------------------------------------------
        if isinstance(node, ast.AugAssign):
            base = _base(node.target)
            if base in known and isinstance(node.op, (ast.Sub, ast.Mult)):
                kind = "-= " if isinstance(node.op, ast.Sub) else "*= "
                stores[base]["lowering_writes"].append(
                    {"line": node.lineno, "kind": kind + ast.unparse(node.value),
                     "scope": _enclosing(functions, node.lineno)})

        if isinstance(node, ast.Call) and _dotted(node.func).endswith(".pop"):
            owner = node.func
            base = _base(owner)
            rec = None
            if base not in known:
                root = _dotted(owner.value).split(".")[0] if isinstance(
                    owner, ast.Attribute) else ""
                if root in aliases:
                    base = aliases[root]
                    rec = root
            if base in known:
                stores[base]["lowering_writes"].append(
                    {"line": node.lineno, "kind": ".pop(...)",
                     "scope": _enclosing(functions, node.lineno)})

        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        tgt = node.targets[0]
        base = _base(tgt)
        via_alias = None
        if base not in known:
            if isinstance(tgt, (ast.Subscript, ast.Attribute)):
                root = _dotted(tgt.value).split(".")[0]
                if root in aliases:
                    base, via_alias = aliases[root], root
            elif isinstance(tgt, ast.Name) and tgt.id in aliases:
                base, via_alias = aliases[tgt.id], tgt.id
        if base not in known:
            continue

        scope = _enclosing(functions, node.lineno)
        guard = _absent_guard(node, aliases)

        def _add(kind: str) -> None:
            stores[base]["lowering_writes"].append(
                {"line": node.lineno, "kind": kind, "scope": scope,
                 "via": via_alias})

        if guard == base:
            continue  # first write for an absent key: not a reduction
        if _is_zero(node.value):
            _add("= 0")
        elif isinstance(node.value, ast.Call):
            fn = _dotted(node.value.func)
            if fn == "min":
                _add("= min(...)")
            elif _is_clamped_max(node.value):
                _add("= max(<bound>, ...)  # can pull down")
        elif isinstance(node.value, ast.Name):
            # `new_n` / `q` / `wv` -- a value computed elsewhere. In a monotone
            # store, a raise reads the store it is raising; one that does not
            # is a value arriving from outside and can be lower.
            if not _reads(node.value, base) and not _reads(node.value, via_alias or ""):
                _add(f"= {node.value.id}  # value from outside the store")

    # ---- (3) watermark consulted inside a merge's own function ------------
    # Reads come in two shapes: `S[k]` (a Subscript) and `S.get(k)` (a Call on
    # an Attribute). Only the first was collected, which is why the floors
    # read by `_adopt_evidence` and `_adopt_counter` did not count -- they
    # use `.get`, so both watermarked stores reported NONE FOUND.
    reads_by_scope: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            name = node.value.id
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            recv = _dotted(node.func.value)
            if recv and "." not in recv:
                name = recv
        if not name:
            continue
        reads_by_scope.setdefault(
            _enclosing(functions, node.lineno), set()).add(name)
    # Scope matters. `_merge_disk_state` reads the floors AND merges four
    # unrelated stores, so "a floor is read somewhere in this function" would
    # watermark all four on the strength of one call. The question is whether
    # the watermark is consulted by the code that COMPUTES the merged value:
    # the helper for a helper-mediated merge, the enclosing function otherwise.
    def _merge_scope(assign: ast.AST) -> tuple[str, str]:
        if isinstance(assign.value, ast.Call):
            hname = _dotted(assign.value.func).split(".")[-1]
            if hname in helpers:
                return hname, "strong"
        return _enclosing(functions, assign.lineno), "weak"

    by_line = {n.lineno: n for n in ast.walk(tree) if isinstance(n, ast.Assign)}
    for st in stores.values():
        for ln in st["monotone_merge"]:
            scope, strength = _merge_scope(by_line[ln])
            for name in sorted(reads_by_scope.get(scope, ())):
                if name != st["store"]:
                    st["watermark_consults"].append(
                        {"line": ln, "structure": name, "scope": scope,
                         "strength": strength})

    mod = path.stem
    findings = []
    for st in stores.values():
        if not st["monotone_merge"] or not st["lowering_writes"]:
            continue
        st["watermarks"] = sorted(
            {c["structure"] for c in st["watermark_consults"]
             if c["strength"] == "strong"
             and any(h in c["structure"].lower() for h in WATERMARK_HINTS)})
        st["weak_watermarks"] = sorted(
            {c["structure"] for c in st["watermark_consults"]
             if c["strength"] == "weak"
             and any(h in c["structure"].lower() for h in WATERMARK_HINTS)})
        st["key"] = f"{mod}:{st['store']}"
        st["acknowledged"] = st["key"] in ACK_DEFAULT
        st["verdict"] = ("watermarked" if st["watermarks"]
                         else "UNREVIEWED -- a reduction here may not survive "
                              "its own save")
        findings.append(st)
    findings.sort(key=lambda s: (s["acknowledged"], s["store"]))

    merged = any(s["monotone_merge"] for s in stores.values())
    return {"file": str(path), "stores": findings, "stores_seen": len(stores),
            "any_merge_seen": merged,
            "inconclusive": not merged}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage:")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", default=list(DEFAULT_TARGETS))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ack", action="append", default=[],
                    help="acknowledge a finding as 'module:store'")
    args = ap.parse_args(argv)

    global ACK_DEFAULT
    if args.ack:
        ACK_DEFAULT = tuple(ACK_DEFAULT) + tuple(args.ack)

    reports = []
    for t in args.targets:
        p = Path(t) if Path(t).is_absolute() else ROOT / t
        if not p.exists():
            print(f"skip (missing): {p}", file=sys.stderr)
            continue
        reports.append(analyse(p))

    # OPEN means: lowers a value, has no watermark, and nobody has looked.
    # A store that HAS a strong watermark already satisfies the tool's own
    # rule, so requiring an ack for it too would make the summary line
    # ("either acknowledged or has a watermark beside it") a lie.
    unreviewed = [s for r in reports for s in r["stores"]
                  if not s["acknowledged"] and not s["watermarks"]]
    # A file that declares stores but shows no merge anywhere means the
    # checker cannot see the code it was pointed at. That is not a pass, and
    # reporting it as one is how this tool lied twice already.
    blind = [r for r in reports if r.get("inconclusive")]

    if args.json:
        print(json.dumps({"reports": reports, "unreviewed": len(unreviewed),
                          "inconclusive": len(blind)}, indent=2, default=str))
    else:
        for r in reports:
            rel = r["file"].replace(str(ROOT) + "\\", "").replace(str(ROOT) + "/", "")
            print(f"\n{rel}")
            if r.get("inconclusive"):
                print(f"  INCONCLUSIVE: {r['stores_seen']} store(s) declared, "
                      "0 monotone merges found. The checker is blind to this "
                      "file, not the file clean.")
            if not r["stores"]:
                print("  (no store has both a monotone merge and a lowering write)")
            for s in r["stores"]:
                mark = ("ok  " if s["acknowledged"]
                        else "wm  " if s["watermarks"] else "OPEN")
                print(f"  [{mark}] {s['store']}  {s['annotation']}")
                print(f"         merge:  max() at lines {sorted(s['monotone_merge'])[:6]}")
                lows = ", ".join(f"L{w['line']} {w['kind']}" for w in
                                 sorted(s["lowering_writes"],
                                        key=lambda w: w["line"])[:5])
                print(f"         lowers: {lows}")
                print(f"         watermarks: {s['watermarks'] or 'NONE FOUND'}"
                      + (f"   (co-located only: {s['weak_watermarks']})"
                         if s.get('weak_watermarks') and not s['watermarks']
                         else ""))
                if not s["acknowledged"] and not s["watermarks"]:
                    print(f"         -> {s['verdict']}")
        print()
        if blind:
            print(f"INCONCLUSIVE: {len(blind)} file(s) declared stores but no "
                  "monotone merge was found. Do not read the rest as a pass.")
        if unreviewed:
            print(f"OPEN: {len(unreviewed)} store(s) can lower a value that a "
                  "max() merge may put back.")
            print("A finding is a candidate, not a verdict: look at it, then "
                  "acknowledge with --ack module:store if it is safe.")
        if not blind and not unreviewed:
            print("OK: every store that lowers a value is either acknowledged "
                  "or has a watermark beside it.")
    if blind:
        return 2
    return 1 if unreviewed else 0


if __name__ == "__main__":
    sys.exit(main())
