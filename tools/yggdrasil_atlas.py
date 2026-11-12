#!/usr/bin/env python3
# coding: utf-8
"""YGGDRASIL atlas — every unit that could live as its own tree, measured.

The census (`tools/yggdrasil_census.py`) answers one question: which top-level
trees are cleave-clean. This answers the wider one: *what are all the units* —
trees, subpackages, module shards, worm categories, skills, tools — and for
each, how much of the mycelium it already speaks, so a standalone cut can be
planned from evidence instead of from a directory listing.

Levels, all from one AST pass over `src/`:

* **tree** — a directory directly under `src/` (19 today).
* **sub** — any deeper directory holding `.py` (the subdivisions:
  `CORE/WORMS`, `CORE/compliance`, `ORCHESTRATION/HYDRA`, …). Imports are
  resolved to the directory that actually owns the module, so `from
  CORE.WORMS.x import y` is an edge into `CORE/WORMS`, not into `CORE`.
* **module** — one `.py` file; the shard the 24-per-limb rule counts.

Per unit: modules, lines, classes, functions, methods, public callable surface,
inbound and outbound units (any-import), the mycelium hubs it references,
`except ImportError` guards, and whether it has a `__main__` entry point.

Inventories beyond `src/`: worm categories (from `@register_worm`), skills
(`skills/`, `.agents/skills/`), tools (`tools/`) and the MCP servers among them.

Usage::

    python tools/yggdrasil_atlas.py                 # human summary
    python tools/yggdrasil_atlas.py --json --out atlas.json
    python tools/yggdrasil_atlas.py --top 20
    python tools/yggdrasil_atlas.py --selftest
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCHEMA = 2
SHARDS_PER_LIMB = 24
HUBS = ("audit", "inference", "paths", "pams", "worm_registry", "memorybus")
TRUNK = "CORE"


# --------------------------------------------------------------------------
# AST helpers
# --------------------------------------------------------------------------

def _modules(node: ast.AST) -> List[str]:
    """Dotted module paths named by one import statement."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names if a.name]
    if isinstance(node, ast.ImportFrom):
        if getattr(node, "level", 0):
            return []
        return [(node.module or "")]
    return []


def _has_main(tree: ast.AST) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__" and node.body:
                return True
    return False


def _silence_guards(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            names: List[str] = []
            if isinstance(node.type, ast.Name):
                names = [node.type.id]
            elif isinstance(node.type, ast.Tuple):
                names = [e.id for e in node.type.elts if isinstance(e, ast.Name)]
            if "ImportError" in names or "ModuleNotFoundError" in names:
                n += 1
    return n


def _defs(tree: ast.AST) -> Tuple[List[str], List[str], int]:
    classes: List[str] = []
    funcs: List[str] = []
    methods = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                classes.append(node.name)
            methods += sum(1 for b in node.body
                           if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                funcs.append(node.name)
    return classes, funcs, methods


def _class_records(tree: ast.AST) -> List[Dict[str, Any]]:
    """Every top-level class with the fields needed to SCOPE a cleave.

    A count is not a scope: "HYDRA has 40 classes" cannot be handed to a worker,
    a list of (name, bases, methods, public) can. Public means the name and at
    least one method are not underscore-prefixed — a class whose only methods
    are `_x` is an implementation detail even if the name is bare.
    """
    out: List[Dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        meths = [b.name for b in node.body
                 if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]
        pub_meths = [m for m in meths if not m.startswith("_")]
        try:
            bases = [ast.unparse(b) for b in node.bases][:4]
        except Exception:                      # pragma: no cover - py<3.9 shapes
            bases = []
        out.append({
            "name": node.name,
            "bases": bases,
            "methods": len(meths),
            "public_methods": len(pub_meths),
            "public": (not node.name.startswith("_")) and bool(pub_meths),
        })
    return out


def _worm_decorators(tree: ast.AST) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            fn = call.func if call else dec
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "register_worm" or call is None:
                continue
            worm = ""
            if call.args and isinstance(call.args[0], ast.Constant):
                worm = str(call.args[0].value)
            cat = ""
            for kw in call.keywords:
                if kw.arg == "category" and isinstance(kw.value, ast.Constant):
                    cat = str(kw.value.value)
            out.append({"class": node.name, "name": worm or node.name,
                        "category": cat})
    return out


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def _make_resolver(src: Path, files: List[Path]):
    """dotted module path -> (owning directory, tree)."""
    mods: Set[str] = set()
    dirs: Set[str] = set()
    for p in src.rglob("*"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(src)
        if p.is_dir():
            dirs.add("/".join(rel.parts))
    for f in files:
        mods.add("/".join(f.relative_to(src).with_suffix("").parts))
    mods = mods  # posix separators on every host

    def resolve(dotted: str) -> Optional[str]:
        if not dotted:
            return None
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            cand = "/".join(parts[:i])
            if cand in mods or cand in dirs or (cand + "/__init__") in mods:
                return cand
        return None

    return resolve, dirs, mods


def _owner(resolved: str, dirs: Set[str], is_module: bool) -> str:
    if is_module:
        return resolved.rpartition("/")[0] or "(root)"
    return resolved



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

def scan(src: Path) -> Dict[str, Any]:
    src = Path(src)
    if not src.is_dir():
        raise FileNotFoundError(f"not a directory: {src}")
    files = sorted(p for p in src.rglob("*.py") if "__pycache__" not in p.parts)
    trees = sorted({p.relative_to(src).parts[0] for p in files
                    if len(p.relative_to(src).parts) > 1
                    and (src / p.relative_to(src).parts[0]).is_dir()})
    tree_set = set(trees)
    resolve, dirs, mods = _make_resolver(src, files)

    mod_units: Dict[str, Dict[str, Any]] = {}
    units: Dict[str, Dict[str, Any]] = {}     # key: "tree/..." owner path
    edges: Counter[Tuple[str, str]] = Counter()   # owner -> owner
    worms: List[Dict[str, str]] = []
    parse_failures: List[Dict[str, Any]] = []
    class_index: Dict[str, List[Dict[str, Any]]] = {}
    # (source tree, target tree, declared symbol) -> statements. Only names that
    # are written down somewhere are counted: `from A.B import f` gives f, and
    # `import A.B as ab` + `ab.g()` gives g. A bare `import A.B` names no symbol
    # and is deliberately not turned into one — inventing '*' would make the
    # interop map look complete when it is only the nameable part.
    symbol_edges: Counter[Tuple[str, str, str]] = Counter()

    for f in files:
        rel = f.relative_to(src)
        parts = rel.parts
        tree = parts[0] if len(parts) > 1 else "(root)"
        owner = "/".join(parts[:-1]) if len(parts) > 1 else "(root)"
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            parse_failures.append({"file": str(f), "error": f"read: {exc}"})
            continue
        lines = text.count("\n")
        try:
            tree_ast = ast.parse(text)
        except SyntaxError as exc:
            parse_failures.append({"file": str(f), "line": exc.lineno,
                                   "error": "SyntaxError"})
            continue

        classes, funcs, methods = _defs(tree_ast)
        class_recs = _class_records(tree_ast)
        silence = _silence_guards(tree_ast)
        main = _has_main(tree_ast)
        spoken = sorted({h for h in HUBS if h in text.lower()})
        rel_posix = rel.as_posix()
        class_index[rel_posix] = class_recs
        for w in _worm_decorators(tree_ast):
            w["module"] = rel_posix
            w["tree"] = tree
            worms.append(w)

        mod_units[rel_posix] = {
            "tree": tree, "owner": owner, "lines": lines,
            # ALL top-level classes, not just publicly-named ones: `classes`
            # used to mean "classes whose name has no leading underscore", which
            # made totals.classes (1364) contradict classes.total (1395) in the
            # same JSON (measured 2026-10-30). Public-name count is kept
            # separately as `public_named_classes`.
            "classes": len(class_recs), "public_named_classes": len(classes),
            "funcs": len(funcs), "methods": methods,
            "public_api": len(classes) + len(funcs), "silence": silence,
            "main": main, "hubs": spoken,
            "top_classes": classes[:6], "top_funcs": funcs[:6],
        }

        b = units.setdefault(owner, {
            "modules": 0, "lines": 0, "classes": 0, "funcs": 0, "methods": 0,
            "public_api": 0, "silence": 0, "mains": 0, "hubs": set(),
            "top_classes": [], "top_funcs": []})
        b["modules"] += 1
        b["lines"] += lines
        b["classes"] += len(class_recs)
        b["public_named_classes"] = b.get("public_named_classes", 0) + len(classes)
        b["funcs"] += len(funcs)
        b["methods"] += methods
        b["public_api"] += len(classes) + len(funcs)
        b["silence"] += silence
        b["mains"] += 1 if main else 0
        b["hubs"].update(spoken)
        if len(b["top_classes"]) < 8:
            b["top_classes"].extend(classes[:8 - len(b["top_classes"])])

        # symbol-level interop: declared names and alias attribute use
        src_tree = owner.split("/")[0]
        alias_map: Dict[str, str] = {}
        for node in ast.walk(tree_ast):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname and a.name:
                        alias_map[a.asname] = a.name
        for node in ast.walk(tree_ast):
            target = ""
            syms: List[str] = []
            if (isinstance(node, ast.ImportFrom)
                    and not getattr(node, "level", 0) and node.module):
                hit = resolve(node.module)
                if hit:
                    is_module = hit in mods and hit not in dirs
                    target = ((hit.rpartition("/")[0] or "(root)")
                              if is_module else hit)
                    syms = [a.name for a in node.names if a.name]
            elif (isinstance(node, ast.Attribute)
                  and isinstance(node.value, ast.Name)
                  and node.value.id in alias_map):
                hit = resolve(alias_map[node.value.id])
                if hit:
                    is_module = hit in mods and hit not in dirs
                    target = ((hit.rpartition("/")[0] or "(root)")
                              if is_module else hit)
                    syms = [node.attr]
            if not target:
                continue
            dst_tree = target.split("/")[0]
            if target == owner or dst_tree not in tree_set or dst_tree == src_tree:
                continue
            for sym in syms:
                symbol_edges[(src_tree, dst_tree, sym)] += 1

        # edges resolved to the owning directory
        for node in ast.walk(tree_ast):
            for dotted in _modules(node):
                hit = resolve(dotted)
                if not hit:
                    continue
                # A package dir and a module can share a name; the module set wins.
                is_module = hit in mods and hit not in dirs
                target = (hit.rpartition("/")[0] or "(root)") if is_module else hit
                if target in ("(root)", "", owner):
                    continue
                if target.split("/")[0] not in tree_set:
                    continue
                edges[(owner, target)] += 1

    inbound: Dict[str, Dict[str, int]] = defaultdict(dict)
    outbound: Dict[str, Dict[str, int]] = defaultdict(dict)
    for (a, b), n in edges.items():
        inbound[b][a] = n
        outbound[a][b] = n

    # roll up to trees
    tree_units: Dict[str, Dict[str, Any]] = {}
    for t in trees:
        agg = {"modules": 0, "lines": 0, "classes": 0, "funcs": 0,
               "methods": 0, "public_api": 0, "silence": 0, "mains": 0,
               "hubs": set(), "subs": 0}
        for k, u in units.items():
            if k == t or k.startswith(t + "/"):
                for f2 in ("modules", "lines", "classes", "funcs", "methods",
                           "public_api", "silence", "mains"):
                    agg[f2] += u[f2]
                agg["hubs"].update(u["hubs"])
                if k != t:
                    agg["subs"] += 1
        agg["hubs"] = sorted(agg["hubs"])
        # A tree's inbound set is the union over the tree itself and every one of
        # its subpackages: an import of CORE/WORMS/x is an import of CORE.
        keys = [k for k in units if k == t or k.startswith(t + "/")]
        src_trees = {a.split("/")[0] for k in keys for a in inbound.get(k, {})}
        dst_trees = {b.split("/")[0] for k in keys for b in outbound.get(k, {})}
        src_trees.discard(t)
        dst_trees.discard(t)
        agg["inbound_trees"] = sorted(src_trees)
        agg["outbound_trees"] = sorted(dst_trees)
        agg["standalone"] = not agg["inbound_trees"]
        agg["is_trunk"] = t == TRUNK
        agg["saplings"] = agg["modules"] // SHARDS_PER_LIMB
        agg["shard_remainder"] = agg["modules"] % SHARDS_PER_LIMB
        tree_units[t] = agg

    # per-unit standalone flags
    for k, u in units.items():
        u["hubs"] = sorted(u["hubs"])
        u["inbound"] = dict(sorted(inbound.get(k, {}).items()))
        u["outbound"] = dict(sorted(outbound.get(k, {}).items()))
        u["standalone"] = not u["inbound"]
        u["tree"] = k.split("/")[0] if k != "(root)" else "(root)"
        u["depth"] = k.count("/") + 1 if k != "(root)" else 0

    subs = {k: u for k, u in units.items() if u["depth"] >= 2}

    # ---- leaf cleave units -------------------------------------------------
    # The smallest unit that can be cleaved is a directory that holds modules
    # (its owner). `needs` is what it imports from OUTSIDE itself — the shim
    # surface if it is taken alone. `needed_by` is who would break.
    leaf_units: Dict[str, Dict[str, Any]] = {}
    for k, u in units.items():
        deps = sorted(u["outbound"])
        dependents = sorted(u["inbound"])
        leaf_units[k] = {
            "tree": u["tree"],
            "depth": u["depth"],
            "modules": u["modules"],
            "classes": u["classes"],
            "lines": u["lines"],
            "saplings": u["modules"] // SHARDS_PER_LIMB,
            "needs": len(deps),
            "needed_by": len(dependents),
            "shim_list": deps[:40],
            "dependents": dependents[:40],
            "cleavable_now": not dependents,
            "self_contained": not deps,
        }

    # ---- class index rolled up per tree -----------------------------------
    classes_by_tree: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for mod, recs in class_index.items():
        t = mod.split("/")[0] if "/" in mod else "(root)"
        for r in recs:
            classes_by_tree[t].append({**r, "module": mod})

    # ---- symbol interop, ranked -------------------------------------------
    sym_by_pair: Counter[Tuple[str, str]] = Counter()
    for (a, b, _sym), n in symbol_edges.items():
        sym_by_pair[(a, b)] += n

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(src),
        "totals": {
            "trees": len(trees),
            "subpackages": len(subs),
            "modules": len(mod_units),
            "lines": sum(v["lines"] for v in mod_units.values()),
            "classes": sum(v["classes"] for v in mod_units.values()),
            "public_named_classes": sum(v["public_named_classes"]
                                        for v in mod_units.values()),
            "functions": sum(v["funcs"] for v in mod_units.values()),
            "methods": sum(v["methods"] for v in mod_units.values()),
            "public_api": sum(v["public_api"] for v in mod_units.values()),
            "silence_guards": sum(v["silence"] for v in mod_units.values()),
            "entry_points": sum(1 for v in mod_units.values() if v["main"]),
            "parse_failures": len(parse_failures),
            "standalone_trees": sum(1 for v in tree_units.values() if v["standalone"]),
            "standalone_subpackages": sum(1 for v in subs.values() if v["standalone"]),
        },
        "trees": tree_units,
        "subpackages": subs,
        "modules": mod_units,
        "edges": {
            "pairs": len(edges),
            "statements": sum(edges.values()),
            "tree_pairs": len({(a.split("/")[0], b.split("/")[0]) for a, b in edges
                               if a.split("/")[0] != b.split("/")[0]}),
        },
        "worms": {
            "total": len(worms),
            "categories_named": len({w["category"] for w in worms if w["category"]}),
            "by_category": dict(sorted(Counter(w["category"] or "(none)"
                                               for w in worms).items(),
                                       key=lambda kv: (-kv[1], kv[0]))),
            "by_tree": dict(sorted(Counter(w["tree"] for w in worms).items(),
                                   key=lambda kv: (-kv[1], kv[0]))),
            "by_owner": dict(sorted(Counter(w["module"].rpartition("/")[0]
                                            for w in worms).items(),
                                    key=lambda kv: (-kv[1], kv[0]))),
            "items": worms,
        },
        "saplings": {
            "shards_per_limb": SHARDS_PER_LIMB,
            "from_all_modules": len(mod_units) // SHARDS_PER_LIMB,
            "remainder": len(mod_units) % SHARDS_PER_LIMB,
        },
        "classes": {
            "total": sum(len(v) for v in class_index.values()),
            "public": sum(1 for recs in class_index.values()
                          for r in recs if r["public"]),
            "by_tree": {t: sorted(recs, key=lambda r: (-r["methods"], r["name"]))
                        for t, recs in sorted(classes_by_tree.items())},
        },
        "leaf_units": {
            "total": len(leaf_units),
            "cleavable_now": sum(1 for u in leaf_units.values() if u["cleavable_now"]),
            "self_contained": sum(1 for u in leaf_units.values() if u["self_contained"]),
            "deepest": max((u["depth"] for u in leaf_units.values()), default=0),
            "units": dict(sorted(leaf_units.items())),
        },
        "symbol_interop": {
            "symbols": len({s for (_a, _b, s) in symbol_edges}),
            "statements": sum(symbol_edges.values()),
            "pairs": len(sym_by_pair),
            "top": [{"from": a, "to": b, "symbol": s, "n": n}
                    for (a, b, s), n in symbol_edges.most_common(300)],
            "by_pair": [{"from": a, "to": b, "n": n, "distinct": distinct}
                        for (a, b), n in sym_by_pair.most_common()
                        for distinct in [len({s for (x, y, s) in symbol_edges
                                              if (x, y) == (a, b)})]],
        },
        "parse_failure_detail": parse_failures[:20],
    }


def inventory(repo: Path) -> Dict[str, Any]:
    repo = Path(repo)
    out: Dict[str, Any] = {}

    def count_skills(d: Path) -> Dict[str, Any]:
        """A skill is a SHARD: a directory holding SKILL.md, or a loose `.md`.

        Counting only `*.md` files reads a sharded tree as empty — `.agents/skills`
        reported 0 markdown while holding 1244 shard dirs (measured 2026-10-30).
        Count both shapes, and report them separately so a change of shape is
        visible instead of silently moving the number.
        """
        if not d.is_dir():
            return {"present": False}
        shard_dirs = sorted((p for p in d.iterdir()
                             if p.is_dir() and (p / "SKILL.md").is_file()),
                            key=lambda p: p.name)
        loose = sorted(d.glob("*.md"))
        names = [p.name for p in shard_dirs] + [p.stem for p in loose]
        groups = Counter()
        for name in names:
            if name.startswith("azazael-pkg-"):
                groups["azazael-pkg-*"] += 1
            elif name.startswith("azazael-bin-"):
                groups["azazael-bin-*"] += 1
            elif name.startswith("azazael-"):
                groups["azazael-*"] += 1
            else:
                groups["named"] += 1
        return {"present": True, "shards": len(names),
                "shard_dirs": len(shard_dirs), "loose_markdown": len(loose),
                "groups": dict(groups), "sample": names[:10],
                "duplicate_names": [n for n, c in Counter(names).items()
                                    if c > 1][:10]}

    out["skills_repo"] = count_skills(repo / "skills")
    out["skills_agents"] = count_skills(repo / ".agents" / "skills")
    # The lane mirror is a DIFFERENT tree from the repo copy and is the larger
    # one (1247 vs 241 shards, measured 2026-10-30). Counting only the repo copy
    # under-reports the shard population by five times, so both are recorded.
    home_skills = Path.home() / ".agents" / "skills"
    repo_skills = (repo / ".agents" / "skills")
    try:
        distinct = home_skills.resolve() != repo_skills.resolve()
    except OSError:
        distinct = True
    if home_skills.is_dir() and distinct:
        out["skills_agents_home"] = count_skills(home_skills)
        out["skills_agents_home"]["path"] = str(home_skills)

    tools = repo / "tools"
    if tools.is_dir():
        py = sorted(tools.glob("*.py"))
        js = sorted(tools.glob("*.js"))
        mains = []
        for p in py:
            try:
                if "__main__" in p.read_text(encoding="utf-8", errors="replace"):
                    mains.append(p.name)
            except OSError as exc:
                note_failure("tools/yggdrasil_atlas.py", exc)
                pass
        out["tools"] = {
            "python": len(py), "javascript": len(js),
            "with_main": len(mains),
            "mcp_servers": [p.name for p in py if "mcp" in p.name.lower()],
            "with_main_names": mains[:60],
        }
    return out


POOL_CLASS_NAMES = ("FreeThreadPool", "WorkStealingPool", "WorkStealingQueue",
                    "ThreadPoolExecutor", "ThreadPool")


def _pool_definitions(src: Path) -> Dict[str, List[Dict[str, str]]]:
    """Every definition of a pool class — TOP-LEVEL **and** NESTED.

    The nested copies live inside `try:` / `if` blocks as fallbacks. A
    top-level-only scan reported "duplicated: none" while six modules were
    carrying a copy (measured 2026-10-30), so the census records the scope of
    each one: a nested shim and a real implementation are not the same claim.
    """
    found: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for p in sorted(Path(src).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            note_failure("tools/yggdrasil_atlas.py", exc)
            continue
        if not any(n in text for n in POOL_CLASS_NAMES):
            continue                      # cheap pre-filter: most files never match
        try:
            f_ast = ast.parse(text)
        except SyntaxError as exc:
            note_failure("tools/yggdrasil_atlas.py", exc)
            continue
        rel = p.relative_to(src).as_posix()
        top_ids = {id(b) for b in f_ast.body}
        for node in ast.walk(f_ast):
            if isinstance(node, ast.ClassDef) and node.name in POOL_CLASS_NAMES:
                found[node.name].append({
                    "module": rel,
                    "scope": "top" if id(node) in top_ids else "nested",
                })
    return {k: sorted(v, key=lambda d: (d["scope"], d["module"]))
            for k, v in sorted(found.items())}


def swarms(root: Path, registered_names: Set[str],
           classes_by_tree: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Swarm topology: clusters, membership overlap, orchestrator, pool classes.

    Read from `CLUSTER_REGISTRY` in `CORE/WORMS/CLUSTERWORMS.py` with
    `ast.literal_eval` — that is the registry the dispatcher actually consults.
    If the file or the name has moved, `present` is False and the section says
    so; no cluster graph is inferred from module names, because an invented
    topology is worse than a reported absence.
    """
    reg_rel = "CORE/WORMS/CLUSTERWORMS.py"
    out: Dict[str, Any] = {"present": False, "source": reg_rel,
                           "clusters": {}, "cluster_count": 0}
    registry: Dict[str, Any] = {}
    reg_path = Path(root) / reg_rel
    if reg_path.is_file():
        try:
            reg_ast = ast.parse(reg_path.read_text(encoding="utf-8", errors="replace"))
            for node in reg_ast.body:
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, ast.AnnAssign) else [])
                for tgt in targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "CLUSTER_REGISTRY":
                        try:
                            lit = ast.literal_eval(node.value)
                        except (ValueError, SyntaxError, TypeError):
                            lit = None
                        if isinstance(lit, dict):
                            registry = lit
        except (OSError, SyntaxError):
            registry = {}

    if registry:
        clusters: Dict[str, Dict[str, Any]] = {}
        member_where: Dict[str, List[str]] = defaultdict(list)
        for name, spec in sorted(registry.items()):
            spec = spec if isinstance(spec, dict) else {}
            members = [str(w) for w in (spec.get("worms") or [])]
            clusters[str(name)] = {
                "description": str(spec.get("description", "")),
                "worms": members,
                "fanout": len(members),
                "tree": "CORE",
                "source": reg_rel,
            }
            for w in members:
                member_where[w].append(str(name))
        slots = [c["fanout"] for c in clusters.values()]
        overlap = {w: cl for w, cl in sorted(member_where.items()) if len(cl) > 1}
        distinct = sorted(member_where)
        unclustered = sorted(registered_names - set(distinct))
        out.update({
            "present": True,
            "clusters": clusters,
            "cluster_count": len(clusters),
            "member_slots": sum(slots),
            "distinct_worms": len(distinct),
            "max_fanout": max(slots) if slots else 0,
            "mean_fanout": round(sum(slots) / len(slots), 2) if slots else 0.0,
            "overlapping": overlap,
            "overlap_count": len(overlap),
            "registered_worms": len(registered_names),
            "unclustered": {"count": len(unclustered), "sample": unclustered[:25]},
            # Clusters may name a worm that no module registers. That is the
            # opposite direction from `unclustered` and is reported separately,
            # because a dispatch table pointing at a missing worker fails at run
            # time while a registered-but-unclustered worm merely never runs.
            "members_not_registered": sorted(set(distinct) - registered_names),
            "coverage_pct": (round(100.0 * len({w for w in distinct if w in registered_names})
                                   / len(registered_names), 1)
                             if registered_names else None),
        })

    # Orchestrator: MetaCluster declares flat dispatch (no routes).
    meta = Path(root) / "CORE" / "WORMS" / "METACLUSTER.py"
    orch: Dict[str, Any] = {"module": "CORE/WORMS/METACLUSTER.py", "present": meta.is_file()}
    if meta.is_file():
        try:
            text = meta.read_text(encoding="utf-8", errors="replace")
            orch["flat_dispatch"] = "flat dispatch" in text.lower()
            orch["coordinator"] = "class MetaCluster" in text
        except OSError:
            orch["flat_dispatch"] = None
    out["orchestrator"] = orch

    # Pool classes: the same class name defined in several modules is real
    # drift — a "swarm" whose workers are not all the same worker.
    defs = _pool_definitions(Path(root))
    out["pools"] = {
        "definitions": defs,
        "count": sum(len(v) for v in defs.values()),
        "top_level": {k: [d["module"] for d in v if d["scope"] == "top"]
                      for k, v in defs.items()},
        "nested_fallbacks": {k: [d["module"] for d in v if d["scope"] == "nested"]
                             for k, v in defs.items()},
        "duplicated": {k: v for k, v in defs.items() if len(v) > 1},
    }
    return out


def render(a: Dict[str, Any], inv: Optional[Dict[str, Any]], top: int) -> str:
    t = a["totals"]
    o: List[str] = []
    add = o.append
    add(f"YGGDRASIL atlas — {a['root']}")
    add(f"generated {a['generated_at']}")
    add("")
    add(f"units : {t['trees']} trees · {t['subpackages']} subpackages · "
        f"{t['modules']} module shards")
    add(f"code  : {t['lines']:,} lines · {t['classes']:,} classes · "
        f"{t['functions']:,} functions · {t['methods']:,} methods · "
        f"{t['public_api']:,} public symbols")
    add(f"risk  : {t['silence_guards']:,} `except ImportError` guards · "
        f"{t['entry_points']} `__main__` entry points · "
        f"{t['parse_failures']} parse failures")
    add(f"cleave: {t['standalone_trees']}/{t['trees']} trees standalone · "
        f"{t['standalone_subpackages']}/{t['subpackages']} subpackages standalone")
    add("")
    add("STANDALONE TREES — nothing outside imports them")
    for k, v in sorted(a["trees"].items(), key=lambda kv: -kv[1]["modules"]):
        if not v["standalone"]:
            continue
        add(f"  {k:<16}{v['modules']:>4} py {v['lines']:>7,} ln "
            f"{v['public_api']:>4} api  {v['mains']:>3} mains  hubs {len(v['hubs'])}")
    add("")
    add("TIER B TREES — imported by a sibling")
    for k, v in sorted(a["trees"].items(), key=lambda kv: -kv[1]["modules"]):
        if v["standalone"]:
            continue
        add(f"  {k:<16}{v['modules']:>4} py  <- {', '.join(v['inbound_trees']) or '-'}")
    add("")
    subs = sorted(((k, v) for k, v in a["subpackages"].items() if v["modules"] >= 2),
                  key=lambda kv: -kv[1]["public_api"])
    add(f"SUBPACKAGES — {len(subs)} with >=2 modules; "
        f"{t['standalone_subpackages']} standalone")
    for k, v in subs[:top]:
        flag = "CLEAN" if v["standalone"] else "dep"
        add(f"  {k:<32}{v['modules']:>4} py {v['public_api']:>4} api "
            f"{v['methods']:>5} m  silence {v['silence']:>3}  {flag}")
    add("")
    w = a["worms"]
    add(f"WORM REGISTRY — {w['total']} registered across {w['categories_named']} categories")
    add(f"  by tree: " + ", ".join(f"{k}:{v}" for k, v in w["by_tree"].items()))
    add("")
    s = a["saplings"]
    add(f"SAPLINGS — {s['from_all_modules']} at {s['shards_per_limb']} shards/limb "
        f"(remainder {s['remainder']})")
    # ---- swarms ---------------------------------------------------------
    sw = a.get("swarms") or {}
    if sw.get("present"):
        add(f"SWARMS — {sw['cluster_count']} clusters, {sw['member_slots']} member "
            f"slots over {sw['distinct_worms']} distinct worms "
            f"(fan-out max {sw['max_fanout']}, mean {sw['mean_fanout']})")
        add(f"  registered worms not in any cluster: {sw['unclustered']['count']} "
            f"of {sw['registered_worms']} -> coverage {sw['coverage_pct']}%")
        add(f"  shared across clusters: {sw['overlap_count']} "
            + ("(" + ", ".join(f"{w}x{len(c)}" for w, c in
                               list(sw["overlapping"].items())[:8]) + ")"
               if sw["overlap_count"] else ""))
        for name, c in list(sw["clusters"].items())[:top]:
            add(f"  {name:<14}{c['fanout']:>3} worms  {c['description'][:52]}")
        orch = sw.get("orchestrator", {})
        add(f"  orchestrator: {orch.get('module')} present={orch.get('present')} "
            f"flat_dispatch={orch.get('flat_dispatch')}")
        p = sw["pools"]
        add(f"  pool classes: {p['count']} definitions in {len(p['definitions'])} "
            f"names; top-level "
            + (", ".join(f"{k}@{v[0]}" for k, v in p["top_level"].items() if v)
               or "none"))
        dup = p["duplicated"]
        add("  duplicated names: " + ((", ".join(
            f"{k}x{len(v)}" for k, v in dup.items())) or "none"))
    else:
        add(f"SWARMS — registry not found at {sw.get('source')} (reported absent, "
            f"not guessed)")
    add("")

    # ---- classes --------------------------------------------------------
    cl = a.get("classes") or {}
    if cl:
        add(f"CLASSES — {cl['total']} defined, {cl['public']} public "
            f"({round(100.0 * cl['public'] / cl['total'], 1) if cl['total'] else 0}%)")
        for t, recs in list(cl["by_tree"].items())[:top]:
            pub = sum(1 for r in recs if r["public"])
            add(f"  {t:<12}{len(recs):>4} classes {pub:>4} public")
        add("  (full index in JSON: classes.by_tree -> module, bases, methods)")
    add("")

    # ---- leaf units -----------------------------------------------------
    lu = a.get("leaf_units") or {}
    if lu:
        add(f"LEAF UNITS — {lu['total']} directories hold modules; "
            f"{lu['cleavable_now']} have zero dependents, "
            f"{lu['self_contained']} need no shim at all, deepest={lu['deepest']}")
        worst = sorted(lu["units"].items(), key=lambda kv: (-kv[1]["needed_by"], kv[0]))
        for k, u in worst[:top]:
            add(f"  {k:<34}{u['modules']:>4} py {u['classes']:>4} cls  "
                f"needs {u['needs']:>2}  needed_by {u['needed_by']:>2}  "
                f"{'CLEAVABLE' if u['cleavable_now'] else 'held'}")
    add("")

    # ---- symbol interop -------------------------------------------------
    si = a.get("symbol_interop") or {}
    if si:
        add(f"SYMBOL INTEROP — {si['symbols']} distinct names across "
            f"{si['pairs']} tree pairs ({si['statements']} statements)")
        for row in si["by_pair"][:top]:
            add(f"  {row['from']:<12}-> {row['to']:<12}{row['n']:>5} uses "
                f"{row['distinct']:>4} distinct")
        add("  (top names in JSON: symbol_interop.top)")
    add("")

    if inv:
        sk, sa, sh, tl = (inv.get("skills_repo", {}), inv.get("skills_agents", {}),
                          inv.get("skills_agents_home", {}), inv.get("tools", {}))
        if sk.get("present"):
            add(f"SKILLS — skills/ {sk.get('shards', 0)} shards {sk.get('groups')}; "
                f"repo .agents/skills {sa.get('shards', 0)}; "
                f"lane {sh.get('shards', 0)} {sh.get('groups')}")
        if tl:
            add(f"TOOLS — {tl['python']} py + {tl['javascript']} js, "
                f"{tl['with_main']} runnable; MCP: {', '.join(tl['mcp_servers']) or 'none'}")
    return "\n".join(o)


def selftest() -> int:
    import tempfile

    fails: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        (src / "AAA" / "sub").mkdir(parents=True)
        (src / "BBB").mkdir()
        (src / "AAA" / "a.py").write_text(
            "import BBB.b\n\n"
            "class Public:\n    def m(self): pass\n"
            "class _Private: pass\n"
            "def run(): pass\n"
            "def _hidden(): pass\n"
            "if __name__ == '__main__':\n    run()\n", encoding="utf-8")
        (src / "AAA" / "sub" / "s.py").write_text(
            "try:\n    import nope\nexcept ImportError:\n    nope = None\n"
            "from CORE.worm_registry import register_worm\n"
            "from BBB.b import x\n"
            "@register_worm('W', category='mesh')\n"
            "class W:\n    pass\n", encoding="utf-8")
        (src / "BBB" / "b.py").write_text("x = 1\n", encoding="utf-8")
        (src / "BBB" / "pool.py").write_text(
            "class FreeThreadPool:\n    pass\n", encoding="utf-8")
        # Swarm topology: a literal CLUSTER_REGISTRY and a flat-dispatch
        # orchestrator, plus a pool class defined twice (drift the census must
        # name rather than count).
        (src / "CORE" / "WORMS").mkdir(parents=True)
        (src / "CORE" / "WORMS" / "CLUSTERWORMS.py").write_text(
            "CLUSTER_REGISTRY = {\n"
            "    'one': {'description': 'a', 'worms': ['W', 'V']},\n"
            "    'two': {'description': 'b', 'worms': ['V']},\n"
            "}\n"
            "class FreeThreadPool:\n    pass\n", encoding="utf-8")
        (src / "CORE" / "WORMS" / "METACLUSTER.py").write_text(
            '"""MetaCluster - flat dispatch."""\nclass MetaCluster:\n    pass\n',
            encoding="utf-8")

        a = scan(src)
        t = a["totals"]
        a["swarms"] = swarms(src, {w["name"] for w in a["worms"]["items"]},
                             a["classes"]["by_tree"])
        checks = [
            ("trees", t["trees"], 3),
            ("subpackages", t["subpackages"], 2),
            ("modules", t["modules"], 6),
            # 6 top-level classes: Public, _Private, W, 2x FreeThreadPool, MetaCluster
            ("classes", t["classes"], 6),
            ("public-named classes", t["public_named_classes"], 5),
            ("public classes", a["classes"]["public"], 1),
            ("entry_points", t["entry_points"], 1),
            ("silence_guards", t["silence_guards"], 1),
            ("worms", a["worms"]["total"], 1),
            ("worm categories", a["worms"]["by_category"], {"mesh": 1}),
            # CORE holds no .py directly, so its only unit is CORE/WORMS.
            ("leaf units", a["leaf_units"]["total"], 4),
            ("cleavable units", a["leaf_units"]["cleavable_now"], 3),
            # `from BBB.b import x` and `from CORE.worm_registry import
            # register_worm` (CORE resolves as the nearest known package).
            ("symbol pairs", a["symbol_interop"]["pairs"], 2),
            ("symbol names", a["symbol_interop"]["symbols"], 2),
            ("clusters", a["swarms"]["cluster_count"], 2),
            ("cluster member slots", a["swarms"]["member_slots"], 3),
            ("distinct cluster worms", a["swarms"]["distinct_worms"], 2),
            ("overlapping worms", a["swarms"]["overlap_count"], 1),
            # Coverage is measured against the worms that ARE registered: both
            # cluster members must appear, so 1 of 1 registered = 100%.
            ("cluster coverage %", a["swarms"]["coverage_pct"], 100.0),
            ("flat dispatch", a["swarms"]["orchestrator"]["flat_dispatch"], True),
            ("duplicated pool class modules",
             sorted(d["module"] for d in
                    a["swarms"]["pools"]["duplicated"].get("FreeThreadPool", [])),
             ["BBB/pool.py", "CORE/WORMS/CLUSTERWORMS.py"]),
        ]
        for name, got, want in checks:
            if got != want:
                fails.append(f"{name} = {got!r}, expected {want!r}")
        # AAA imports BBB; nobody imports AAA -> AAA standalone, BBB not.
        if not a["trees"]["AAA"]["standalone"]:
            fails.append("AAA not standalone, but nothing imports it")
        if a["trees"]["BBB"]["standalone"]:
            fails.append("BBB reported standalone although AAA imports it")
        # BBB.b is imported from AAA root -> BBB has an inbound edge from AAA
        if "AAA" not in a["trees"]["BBB"]["inbound_trees"]:
            fails.append(f"BBB inbound trees = {a['trees']['BBB']['inbound_trees']}")
        if a["saplings"]["from_all_modules"] != 0:
            fails.append("saplings should be 0 below one shard")

    if fails:
        print("SELFTEST FAILED", file=sys.stderr)
        for f in fails:
            print("  - " + f, file=sys.stderr)
        return 3
    print(f"SELFTEST OK — {len(checks) + 4} controls (trees, subpackages, modules, "
          "classes, public classes, entry points, silence guards, worms, categories, "
          "standalone direction, inbound edges, saplings, leaf units, cleavable "
          "units, symbol pairs, symbol names, clusters, member slots, distinct "
          "worms, overlap, coverage, flat dispatch, pool duplication)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="src dir (default: ../src)")
    ap.add_argument("--repo", default=None, help="repo root for inventories")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--top", type=int, default=14)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    here = Path(__file__).resolve().parent
    root = Path(args.root) if args.root else here.parent / "src"
    repo = Path(args.repo) if args.repo else here.parent
    try:
        atlas = scan(root)
    except FileNotFoundError as exc:
        print(f"yggdrasil_atlas: {exc}", file=sys.stderr)
        return 2
    inv = inventory(repo)
    atlas["inventory"] = inv
    atlas["swarms"] = swarms(root, {w["name"] for w in atlas["worms"]["items"]},
                             atlas["classes"]["by_tree"])
    text = (json.dumps(atlas, indent=2, default=list) if args.json
            else render(atlas, inv, args.top))
    print(text)
    if args.out:
        Path(args.out).write_text(json.dumps(atlas, indent=2, default=list),
                                  encoding="utf-8")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
