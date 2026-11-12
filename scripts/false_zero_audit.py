"""Find the MemoryBus get_stats class of defect across a codebase.

THE DEFECT
    A status/stats getter reports a metric from store A, but the code that keeps
    that metric current writes to store B. Nothing raises. The getter returns a
    confident number -- typically A's initial value, often 0 -- forever.

    MemoryBus was the instance: subscribe()/unsubscribe() wrote `_stats`, while
    get_stats() read `_stats_data`. `get_stats()['subscriptions']` was a permanent 0.

WHY THE FIRST CRITERION WAS WRONG
    The obvious rule "a key that is read but never written" does not detect this,
    because __init__ DOES write the key (`self._stats_data = {"subscriptions": 0}`).
    A first draft using that rule reported "no findings" on a tree containing the
    known bug -- a false negative in the auditor. So the criterion is:

      key k is read from attribute A
      AND k is initialised in A (so A plausibly owns it)
      AND A is never updated with k outside __init__
      AND some OTHER attribute B IS updated with k outside __init__

    i.e. "reported from A, maintained in B". That is the whole shape.

UNDECIDABLE BY DESIGN
    If a store is written with a non-constant key (`self._x[key] = ...`, as
    MemoryBus._increment does), static analysis cannot say which keys are covered.
    Those attributes are reported separately as undecidable rather than silently
    passed -- passing them is how a tool lies.
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

GETTER_HINTS = ("get_stats", "get_status", "get_metrics", "get_info", "stats",
                "status", "metrics", "health", "snapshot")


def self_attr(node: ast.AST) -> str | None:
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return node.attr
    return None


def const_key(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def is_numeric(node: ast.AST | None) -> bool:
    """True for a plain numeric literal (int/float, not bool)."""
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


# A store initialised with numeric literals is a metric store. A store initialised
# with objects is a registry, and calling its keys "metrics that never update" is
# wrong. Without this discriminator the single confirmed hit on the real tree was
# SystemManager.crawlers = {"python": PythonCrawler(), ...}, where crawlers["python"]
# is entirely correct -- precision 0/1. The discriminator is initial value TYPE.


class ClassAudit:
    def __init__(self, cls: ast.ClassDef, path: str):
        self.cls = cls
        self.path = path
        self.methods = {n.name for n in ast.walk(cls)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.init_writes: dict[str, set[str]] = defaultdict(set)
        self.live_writes: dict[str, set[str]] = defaultdict(set)
        self.reads: dict[str, set[str]] = defaultdict(set)
        self.read_at: dict[tuple[str, str], int] = {}
        self.dynamic_writes: set[str] = set()
        self.broad_writes: set[str] = set()
        self.aliases: dict[str, str] = {}
        # method name -> attribute it writes dynamically, e.g. _increment -> _stats_data
        self.dynamic_writer_for: dict[str, str] = {}
        # keys observed as literal arguments to those writers
        self.dynamic_keys: dict[str, set[str]] = defaultdict(set)

    # -- alias + write/read collection -------------------------------------

    def _resolve(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        a = self_attr(node)
        if a:
            return None if a in self.methods else a
        if isinstance(node, ast.Name) and node.id in self.aliases:
            return self.aliases[node.id]
        for sub in ast.walk(node):
            sa = self_attr(sub)
            if sa and sa not in self.methods:
                return sa
            if isinstance(sub, ast.Name) and sub.id in self.aliases:
                return self.aliases[sub.id]
        return None

    def run(self) -> None:
        # pass 1: aliases (`snap = dict(self._stats_data)`)
        for n in ast.walk(self.cls):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)):
                a = self._resolve(n.value)
                if a:
                    self.aliases[n.targets[0].id] = a

        # pass 2: writes, split init vs live
        for fn in ast.walk(self.cls):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bucket = self.init_writes if fn.name == "__init__" else self.live_writes
            for n in ast.walk(fn):
                if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = getattr(n, "targets", None) or [getattr(n, "target", None)]
                    value = getattr(n, "value", None)
                    for t in targets:
                        if t is None:
                            continue
                        if isinstance(t, ast.Subscript):
                            a = self._resolve(t.value)
                            k = const_key(t.slice)
                            if a and k:
                                bucket[a].add(k)
                            elif a:
                                self.dynamic_writes.add(a)
                        else:
                            a = self_attr(t)
                            if a:
                                self.broad_writes.add(a)
                                if isinstance(value, ast.Dict):
                                    for kk, vv in zip(value.keys, value.values):
                                        ck = const_key(kk)
                                        if ck and is_numeric(vv):
                                            bucket[a].add(ck)

        # pass 2b: WHICH method performs the dynamic write, and which literal keys
        # are handed to it. Without this the whole store is undecidable -- which is
        # exactly what real code looks like (MemoryBus._increment), so the auditor
        # would return "undecidable" for every case that matters.
        for fn in ast.walk(self.cls):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Subscript):
                            a = self._resolve(t.value)
                            if a and const_key(t.slice) is None:
                                self.dynamic_writer_for[fn.name] = a
        for n in ast.walk(self.cls):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and self_attr(n.func.value) is None
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "self"
                    and n.func.attr in self.dynamic_writer_for):
                a = self.dynamic_writer_for[n.func.attr]
                for arg in n.args:
                    ck = const_key(arg)
                    if ck:
                        self.dynamic_keys[a].add(ck)

        # pass 3: reads inside getters
        for fn in ast.walk(self.cls):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(h in fn.name.lower() for h in GETTER_HINTS):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Subscript):
                    a, k = self._resolve(n.value), const_key(n.slice)
                elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "get" and n.args):
                    a, k = self._resolve(n.func.value), const_key(n.args[0])
                else:
                    continue
                if a and k:
                    self.reads[a].add(k)
                    self.read_at.setdefault((a, k), getattr(n, "lineno", 0))

    def findings(self) -> tuple[list[tuple], list[tuple]]:
        """Return (confirmed, undecidable), decided PER KEY rather than per store.

        Store-level verdicts were what made the output contradictory: one store can
        hold a key confirmed stale while another is merely unknown. Deciding per key
        lets "covered by a dynamic writer" resolve to OK rather than to a hedge.
        """
        confirmed, undecidable = [], []
        for a, keys in self.reads.items():
            dyn = self.dynamic_keys.get(a, set())
            for k in sorted(keys):
                initialised = k in self.init_writes[a] or k in self.live_writes[a]
                covered = k in self.live_writes[a] or k in dyn
                if covered:
                    continue
                others = sorted(b for b, ks in self.live_writes.items()
                                if b != a and k in ks)
                if not initialised:
                    # no baseline is declared anywhere, so no honest claim is possible
                    undecidable.append((a, [k]))
                elif others:
                    confirmed.append((k, a, others, self.read_at.get((a, k), 0)))
                elif a in self.dynamic_writes:
                    confirmed.append((k, a, ["<no writer receives this key>"],
                                      self.read_at.get((a, k), 0)))
                else:
                    confirmed.append((k, a, ["<initialised once, never updated>"],
                                      self.read_at.get((a, k), 0)))
        return confirmed, undecidable


def main(root: str) -> int:
    files = sorted(Path(root).rglob("*.py"))
    conf_total = und_total = 0
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            ca = ClassAudit(node, str(p))
            try:
                ca.run()
            except Exception:
                continue
            conf, und = ca.findings()
            for k, a, others, line in conf:
                rel = str(p).replace("\\", "/")
                print(f"  CONFIRMED  {rel}:{line}  class {node.name}")
                print(f"             reports '{k}' from self.{a}, "
                      f"but it is maintained in self.{others[0]}")
                conf_total += 1
            for a, keys in und:
                und_total += 1
                rel = str(p).replace("\\", "/")
                print(f"  UNDECIDABLE {rel}  class {node.name}  self.{a} "
                      f"written with dynamic keys; reports {keys[:4]}")
    print(f"\nscanned {len(files)} files  |  confirmed={conf_total}  undecidable={und_total}")
    return 0 if conf_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "src"))
