"""Per-function pre-flight auditor for the Azazael neural mesh architecture.

Walks the mesh modules, runs a fixed battery of static pre-flight probes against
every function, and emits a checkbox TODO so the audit can be worked through one
function at a time instead of by module.

The battery is deliberately mechanical: each probe answers a question that can
be decided from the source alone, so a PASS is evidence rather than opinion, and
every FAIL is actionable.  Probes that need judgement are listed as the manual
half of the checklist and are never auto-ticked.

Usage:
    python tools/mesh_function_preflight.py                # write the TODO
    python tools/mesh_function_preflight.py --stdout        # print, do not write
    python tools/mesh_function_preflight.py --summary       # counts only
    python tools/mesh_function_preflight.py --module spine  # one module
    python tools/mesh_function_preflight.py --verdict       # PF16 decisions

`--verdict` writes NEURAL_MESH_WIRE_OR_CUT.json: every function nothing in the
repo reaches, with a declared disposition. See `wire_or_cut`.

`--check` judges two things and says which: the KEY SET (a caller added or
removed makes the ledger stale) and the STATUS MAP against `git show
HEAD:` (a decision moved). The HEAD comparison is the one that can fail: the
in-memory prior `wire_or_cut(funcs, committed)` uses cannot, because it takes
the committed map as input and can only read it back. A status move whose
justification moved with it -- `reason`, `decider`, `decider_evidence`,
`disposition` or `triage` -- is a recorded decision and passes with a note; a
bare move leaves the row arguing for a disposition it no longer declares and
fails. When HEAD is unreadable the run says the comparison was SKIPPED and why;
it never claims the statuses are unchanged.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
MESH_DIR = SRC / "CORE" / "WORMS" / "expansion"
DEFAULT_OUT = REPO / "NEURAL_MESH_FUNCTION_TODO.md"

# ---------------------------------------------------------------------------
# Scope: what counts as "the neural mesh architecture"
# ---------------------------------------------------------------------------
#
# The fabric proper (transport, membership, consensus, immunity), the neural
# primitives it is built from, the learning/plasticity loop, and the operational
# subsystems that keep it alive.  Anything outside this list is a different
# architecture and is out of scope for this TODO.

FABRIC = [
    "mesh", "envmesh", "fabric_state", "servemesh", "communication",
    "spine", "consensus", "federated", "immune", "synapse", "engram",
]

NEURAL_PRIMITIVES = [
    "nerve", "axon", "dendrite", "glia", "thalamus", "loom", "chorus",
    "arena", "allocation", "cppn", "tensorgene", "xenomorph",
]

LEARNING = [
    "learning", "learning_ab", "learning_features", "learning_grpo",
    "learning_pipeline", "learning_replay", "learning_rl", "learning_versioner",
    "plasticity", "consolidation", "dreaming", "meta_cognition", "metabolic",
    "evolution", "morph", "oracle",
]

OPERATIONAL = [
    "driver", "scheduling", "container", "config", "security",
    "error_recovery", "evidence", "migration", "race_detector", "code_audit",
    "profiling", "backup", "chaos", "berks", "db_monitor", "threatmon",
    "visualization", "proteus_hooks", "exmachina", "exmachina_operators",
    "session_state", "telemetry",
]

SCOPE_GROUPS: list[tuple[str, list[str]]] = [
    ("Fabric", FABRIC),
    ("Neural primitives", NEURAL_PRIMITIVES),
    ("Learning and plasticity", LEARNING),
    ("Operational", OPERATIONAL),
]

# Modules that live outside the expansion package but are part of the mesh.
EXTRA_MODULES = [
    SRC / "CORE" / "spine_registry.py",
    SRC / "CORE" / "NeuralPipeline.py",
]

DEFAULT_EXCLUDES = ("__pycache__", ".venv", "venv", "generated")

#: Probe-owned artifacts: the probe must never read its own report as a wire.
PROBE_ARTIFACTS = frozenset(("neural_mesh_integration_map.json",
                              "neural_mesh_wire_or_cut.json",
                              "neural_mesh_entrenchment.json"))

# ---------------------------------------------------------------------------
# Pre-flight battery
# ---------------------------------------------------------------------------
#
# The battery is intentionally cheap and deterministic.  It is a *pre*-flight:
# it is meant to be run before anyone invests time reading a function, so that
# the reading time goes to the functions the probes cannot already clear.

EMOJI_RANGES = (
    (0x1F300, 0x1FAFF), (0x1F000, 0x1F2FF), (0x2600, 0x27BF),
    (0x2B00, 0x2BFF), (0xFE00, 0xFE0F), (0x1F1E6, 0x1F1FF),
)

# Built from parts so this file does not itself contain a hardcoded drive path.
DRIVE_RE = re.compile(r"\b[A-Za-z]:" + re.escape(chr(92)) )

BUILTIN_NAMES = frozenset(dir(builtins))

PROBE_HELP: list[tuple[str, str]] = [
    ("PF01", "parses as valid Python and the span is inside the file"),
    ("PF02", "has a docstring"),
    ("PF03", "has a return annotation"),
    ("PF04", "every parameter is annotated"),
    ("PF05", "no bare `except:`"),
    ("PF06", "no mutable default argument"),
    ("PF07", "no `os.system` / `shell=True` subprocess"),
    ("PF08", "no emoji in the source span"),
    ("PF09", "no hardcoded drive path in the source span"),
    ("PF10", "no backslash inside an f-string expression"),
    ("PF11", "does not shadow a builtin or import a wildcard"),
    ("PF12", "not an unimplemented stub"),
    ("PF13", "no constant-true `while` with no break, return or raise"),
    ("PF14", "catches a specific exception, not `Exception` alone"),
    ("PF15", "no bare `pass` in an except body"),
    ("PF16", "is referenced somewhere in the repo (reachability)"),
    ("PF17", "is referenced by a test (test coverage signal)"),
    ("PF18", "writes no module-level global (concurrency signal)"),
    ("PF19", "opens files with an explicit encoding"),
    ("PF20", "recursion is depth-guarded"),
    ("PF21", "progress-dependent loop body always advances the condition"),
]

MANUAL_CHECKS: list[tuple[str, str]] = [
    ("M1", "signature matches what callers actually pass"),
    ("M2", "return contract holds on every path (including early returns)"),
    ("M3", "error paths leave shared state consistent"),
    ("M4", "safe under free-threaded concurrent calls"),
    ("M5", "no silent data loss on partial failure"),
    ("M6", "the docstring is true"),
]

# Methods the interpreter invokes implicitly.  PF16 asks "does the bare name
# appear as a Name or Attribute anywhere?", which no correct explicit reference
# can satisfy for these: `__repr__` is reached by `repr()` and the debugger,
# `__bool__` by `bool()`, `__getattr__` by any attribute miss.  Flagging them is
# a *measured* false positive rather than dead code.  The evidence that forced
# this calibration: `nerve._LazyTorch.__bool__` was reported unreachable while
# `bool(_torch)` is called at four sites in the same file, and `_torch.xpu` /
# `_torch.Generator` / `_torch.nn` all dispatch through `_LazyTorch.__getattr__`.
#
# Calibrated here rather than silenced by adding a caller, because inventing a
# call site to satisfy a probe is exactly the anti-pattern the wire-or-cut table
# exists to prevent.  PF17 (test reference) shares this blind spot but is left
# alone deliberately: it is a coverage *signal*, not a disposition, and changing
# two probes at once would make the census diff unattributable.
PROTOCOL_METHODS = frozenset({
    "__repr__", "__str__", "__format__", "__bytes__",
    "__bool__", "__len__", "__length_hint__", "__index__",
    "__int__", "__float__", "__complex__", "__hash__",
    "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__",
    "__iter__", "__next__", "__reversed__", "__contains__",
    "__getattr__", "__getattribute__", "__setattr__", "__delattr__",
    "__enter__", "__exit__", "__await__", "__aiter__", "__anext__",
    "__call__", "__init_subclass__", "__class_getitem__",
    "__getnewargs__", "__reduce__", "__reduce_ex__",
})


@dataclass
class Func:
    """One function found in the mesh tree."""

    module: str
    qualname: str
    lineno: int
    end_lineno: int
    complexity: int
    params: int
    is_async: bool
    source: str
    seg: str
    node: ast.AST = field(repr=False, default=None)
    failures: list[str] = field(default_factory=list)
    prior: str = "P3"
    # Repo-relative posix path of the defining file (set by build()).
    # Empty for hand-built stubs in contract tests -- the PF16 predicate
    # treats an empty path as "unknown file", never as a match.
    path: str = ""

    @property
    def label(self) -> str:
        return f"{self.module}:{self.qualname}"

    @property
    def lines(self) -> str:
        return f"L{self.lineno}-{self.end_lineno}"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for _, names in SCOPE_GROUPS:
        for name in names:
            p = MESH_DIR / f"{name}.py"
            if p.exists():
                files.append(p)
    for p in EXTRA_MODULES:
        if p.exists():
            files.append(p)
    return sorted(set(files))


def _complexity(node: ast.AST) -> int:
    branches = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
                ast.With, ast.AsyncWith, ast.BoolOp, ast.ExceptHandler,
                ast.IfExp, ast.Match)
    return sum(1 for n in ast.walk(node) if isinstance(n, branches))


def _collect(tree: ast.AST, module: str, source: str) -> list[Func]:
    out: list[Func] = []

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(source, node) or ""
                out.append(Func(
                    module=module,
                    qualname=f"{prefix}{node.name}",
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno),
                    complexity=_complexity(node),
                    params=len(node.args.args) + len(node.args.kwonlyargs),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    source=source,
                    seg=seg,
                    node=node,
                ))
                walk(node.body, f"{prefix}{node.name}.")

    walk(tree.body, "")
    return out


def _reference_index() -> tuple[set[str], set[str], set[str],
                               dict[str, set[str]], dict[str, set[str]]]:
    """(used anywhere, used by tests, used OUTSIDE tests, refs by file, imports by file).

    The last two maps exist because of the generic-token defect class
    (Error #472 / engram #682, then #719: the bare token ``merge`` in an
    unrelated tool made ``ContextConcatWorm.merge`` look referenced and
    silently dropped its ledger row). A bare name is only evidence of a
    caller in a file that IMPORTS the defining module (or symbol), or in
    the defining file itself -- see _module_qualified_hit(). The flat
    sets are kept for PF17 and for callers that only need membership.

    residual (documented, not fixed): ``from package import name`` where
    ``name`` collides with an unrelated same-named symbol still matches,
    because static reading cannot resolve which binding a bare name took.
    What this rule DOES kill is the proven class: a foreign token with no
    import of any kind (e.g. a local ``def merge`` in tools/ satisfying a
    method named ``merge`` in expansion/).

    The third set is the one PF16 must use, and the docstring has promised it
    since the probe was written without ever returning it -- so PF16 was reading
    a set that included ``tests/``. That made a test-only caller look like
    reachability, which contradicts the wire-or-cut table's own definition of
    `wired`: "a production caller exists (so PF16 must NOT fire)". A function
    called only from a test would have satisfied `wired` with no production
    caller at all.

    Measured, on this probe: adding a regression test that calls
    `MoEDispatcherWorm.dispatch_cascade` silently removed it from the PF16
    findings without any production caller existing. PF17 already covers test
    reference, so the two probes now measure different things instead of
    overlapping on the weaker one.
    """
    used: set[str] = set()
    test_used: set[str] = set()
    prod_used: set[str] = set()
    refs_by_file: dict[str, set[str]] = {}
    imports_by_file: dict[str, set[str]] = {}
    for base in ("src", "tests", "scripts", "tools"):
        root = REPO / base
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(x in path.parts for x in DEFAULT_EXCLUDES):
                continue
            try:
                tree = ast.parse(_read(path))
            except SyntaxError:
                continue
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            allnames = names | attrs
            rel = path.relative_to(REPO).as_posix()
            used |= allnames
            if base == "tests":
                test_used |= allnames
                # Tests are PF17's jurisdiction, never PF16 evidence: the
                # MoEDispatcherWorm lesson in this docstring. A test-only
                # caller must not satisfy "a production caller exists".
                continue
            refs_by_file[rel] = allnames
            imports_by_file[rel] = _file_imports(tree)
            prod_used |= allnames
    return used, test_used, prod_used, refs_by_file, imports_by_file


def _file_imports(tree: ast.AST) -> set[str]:
    """Every module/symbol name a file binds via import, by stem and symbol.

    Records full dotted paths plus first/last components (``import a.b`` ->
    ``a.b``/``a``/``b``; ``from a.b import c`` -> ``a.b``/``a``/``b``/``c``)
    so a defining module stem matches however the importer spelled it.
    Star imports bind nothing nameable and are skipped.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                out.add(alias.name)
                out.add(parts[0])
                out.add(parts[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                parts = node.module.split(".")
                out.add(node.module)
                out.add(parts[0])
                out.add(parts[-1])
            for alias in node.names:
                if alias.name != "*":
                    out.add(alias.name)
    return out


def _module_qualified_hit(fn: "Func", refs_by_file: dict[str, set[str]],
                          imports_by_file: dict[str, set[str]]) -> bool:
    """True when some file genuinely references fn's bare name.

    A bare token counts only in the defining file itself or in a file
    that imports the defining module stem (or the bare symbol, covering
    ``from package import name`` re-exports). Constructor calls count for
    ``__init__``: ``ClassName(...)`` reaches ``ClassName.__init__`` without
    ever naming ``__init__`` (the bare-token rule would otherwise flag
    every constructor in the tree, since only ``super().__init__`` -- a
    different class's init -- mentions the token). An empty fn.path
    (contract stubs) never matches -- unknown file, not evidence.
    """
    head, _, name = fn.qualname.rpartition(".")
    stem = fn.module
    for fpath, names in refs_by_file.items():
        imps = imports_by_file.get(fpath, set())
        same_file = bool(fn.path) and fpath == fn.path
        linked = same_file or stem in imps or name in imps
        if name in names and linked:
            return True
        # Constructor reachability: ClassName(...) -> ClassName.__init__.
        # The class token is distinctive (no generic-token problem), but the
        # import link is still required -- an unrelated same-named class in
        # another package must not satisfy it.
        if name == "__init__" and head:
            classname = head.rsplit(".", 1)[-1]
            if classname in names and (
                    same_file or stem in imps or classname in imps):
                return True
    return False


def _plain_true(test: ast.expr) -> bool:
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.Name) and test.id in {"True", "true"}:
        return True
    return False


def _while_exits(loop: ast.While) -> list[ast.AST]:
    return [x for x in ast.walk(loop)
            if isinstance(x, (ast.Break, ast.Return, ast.Raise))]


def _has_unbounded_while(node: ast.AST) -> bool:
    """True only for the classic spin: `while True` with no way out.

    A `while <condition>` is bounded by that condition, so flagging every
    `while` produced nothing but noise -- `while i < n`, `while not stop.is_set()`
    and `while not stop.wait(1)` are all finite and all correct. Only a constant
    truthy test with no break, return or raise can actually spin.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.While) and _plain_true(n.test) \
                and not _while_exits(n):
            return True
    return False


def _has_progress_loop(node: ast.AST) -> bool:
    """A loop that only ends when its body makes the condition false.

    `while len(buf) < target:` is correct and common, but it hangs if the body
    has any path that fails to extend the buffer. That is a real risk the pure
    bounded-loop rule cannot see, so it gets its own probe and stays advisory.
    """
    for n in ast.walk(node):
        if not isinstance(n, ast.While) or _plain_true(n.test):
            continue
        if _while_exits(n):
            continue
        for sub in ast.walk(n.test):
            # `ast.dump` prints `Call(func=Name(id='len'))`, so match the node,
            # not the rendered text.
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id == "len":
                return True
            if isinstance(sub, ast.Name) and sub.id in {
                    "count", "cursor", "offset", "attempts", "retries",
                    "remaining", "tries"}:
                return True
    return False


def _run_probes(fn: Func, used: set[str], test_used: set[str]) -> list[str]:
    fail: list[str] = []
    node = fn.node
    assert node is not None

    if not fn.seg:
        fail.append("PF01")

    if not ast.get_docstring(node):
        fail.append("PF02")

    if getattr(node, "returns", None) is None:
        fail.append("PF03")

    args = list(node.args.args) + list(node.args.kwonlyargs)
    if not all(a.annotation is not None for a in args):
        fail.append("PF04")

    for n in ast.walk(node):
        if isinstance(n, ast.ExceptHandler) and n.type is None:
            fail.append("PF05")
            break

    defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
    for d in defaults:
        if isinstance(d, (ast.List, ast.Dict, ast.Set)):
            fail.append("PF06")
            break

    if "os.system(" in fn.seg or "shell=True" in fn.seg:
        fail.append("PF07")

    for ch in fn.seg:
        cp = ord(ch)
        if any(a <= cp <= b for a, b in EMOJI_RANGES):
            fail.append("PF08")
            break

    if DRIVE_RE.search(fn.seg):
        fail.append("PF09")

    # f-string with a backslash inside the replacement field
    for n in ast.walk(node):
        if isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.FormattedValue):
                    piece = ast.get_source_segment(fn.source, v) or ""
                    if chr(92) in piece:
                        fail.append("PF10")
                        break
            else:
                continue
            break

    name = getattr(node, "name", "")
    if name in BUILTIN_NAMES:
        fail.append("PF11")
    for n in ast.walk(node):
        if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names):
            fail.append("PF11")
            break

    body = [b for b in node.body
            if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))]
    if not body:
        fail.append("PF12")
    elif len(body) == 1:
        only = body[0]
        if isinstance(only, ast.Pass):
            fail.append("PF12")
        elif isinstance(only, ast.Raise):
            exc = only.exc
            if isinstance(exc, ast.Call) and getattr(exc.func, "id", "") == "NotImplementedError":
                fail.append("PF12")
        elif isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant) \
                and only.value.value is Ellipsis:
            fail.append("PF12")

    if _has_unbounded_while(node):
        fail.append("PF13")
    elif _has_progress_loop(node):
        fail.append("PF21")

    handlers = [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]
    for h in handlers:
        if isinstance(h.type, ast.Name) and h.type.id == "Exception":
            fail.append("PF14")
            break

    for h in handlers:
        stmts = [s for s in h.body
                 if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if stmts and all(isinstance(s, ast.Pass) for s in stmts):
            fail.append("PF15")
            break
        if not stmts:
            fail.append("PF15")
            break

    if name and name not in PROTOCOL_METHODS and name not in used:
        fail.append("PF16")
    if name and name not in test_used:
        fail.append("PF17")

    if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(node)):
        fail.append("PF18")

    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "open":
            if not any(k.arg == "encoding" for k in n.keywords):
                fail.append("PF19")
                break


    # Recursion without a depth guard is the other way a function stops
    # returning. A self-call is only safe when it carries the depth down.
    fname = getattr(node, "name", "")
    if fname:
        self_calls = [n for n in ast.walk(node)
                      if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name)
                      and n.func.id == fname]
        guards = {"depth", "_depth", "level", "limit", "max_depth",
                  "seen", "visited", "memo"}
        if self_calls:
            # A guard can be passed positionally and derived (`depth - 1`), so
            # read the call's own source rather than inspecting one AST node.
            carried = False
            for call in self_calls:
                piece = ast.get_source_segment(fn.source, call) or ""
                if any(g in piece for g in guards):
                    carried = True
                    break
            if not carried:
                fail.append("PF20")

    # de-duplicate, keep battery order
    seen: set[str] = set()
    ordered: list[str] = []
    for f in fail:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


def _prioritise(fn: Func) -> str:
    hard = {"PF05", "PF07", "PF12", "PF13", "PF15", "PF18", "PF20"}
    if hard & set(fn.failures):
        return "P0"
    if {"PF09", "PF10", "PF14", "PF19", "PF16"} & set(fn.failures):
        return "P1"
    if fn.complexity >= 12 and fn.failures:
        return "P1"
    if fn.failures:
        return "P2"
    return "CLEAN"


def build(module_filter: str | None = None) -> tuple[list[Func], Counter]:
    files = _iter_files()
    _used, test_used, prod_used, _file_refs, _file_imports = _reference_index()
    funcs: list[Func] = []
    for path in files:
        module = path.stem
        if module_filter and module_filter.lower() not in module.lower():
            continue
        source = _read(path)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        found = _collect(tree, module, source)
        rel = path.relative_to(REPO).as_posix()
        for fn in found:
            fn.path = rel
            # PF16 = production reachability; PF17 = test reachability. Passing
            # `prod_used` here is what makes `wired` mean "a production caller
            # exists" rather than "something somewhere mentions it".
            fn.failures = _run_probes(fn, prod_used, test_used)
            # ...but `prod_used` is a FLAT name set, so a generic token anywhere
            # in src/tools/scripts counts as a caller. That is the documented
            # Error #472/#682/#719 class, and it produced a live contradiction:
            # this same module's carry path requires an import link
            # (_module_qualified_hit) while the finding path did not, so
            # `config:FeatureFlagCtrl.is_enabled` was "reached" by Selenium's
            # WebElement.is_enabled() and 135 ledger rows claimed `wired` on
            # bare tokens ('__init__' in 25 files, 'execute' in 117).
            # A name that is only mentioned in files which do not import the
            # defining module is not a caller, so it stays a PF16 finding.
            # Protocol methods are exempt BY NAME in `_run_probes` because no
            # explicit reference can satisfy them (`bool(_torch)` calls
            # `__bool__`; `_LazyTorch.__getattr__` serves every attribute). This
            # post-filter must inherit that exemption, not route around it --
            # otherwise the calibration tests find `__bool__`/`__getattr__`
            # reported again, which is the false positive the exemption exists
            # to stop.
            _bare = fn.qualname.rsplit(".", 1)[-1]
            if ("PF16" not in fn.failures
                    and _bare not in PROTOCOL_METHODS
                    and not _module_qualified_hit(fn, _file_refs, _file_imports)):
                fn.failures = [*fn.failures, "PF16"]
            fn.prior = _prioritise(fn)
            funcs.append(fn)
    counts = Counter()
    for fn in funcs:
        for f in fn.failures:
            counts[f] += 1
        counts[fn.prior] += 1
    return funcs, counts


def render(funcs: list[Func], counts: Counter, module_filter: str | None) -> str:
    total = len(funcs)
    clean = counts.get("CLEAN", 0)
    p0 = counts.get("P0", 0)
    p1 = counts.get("P1", 0)
    p2 = counts.get("P2", 0)

    out: list[str] = []
    out.append("# Neural Mesh Architecture - Per-Function Pre-Flight TODO")
    out.append("")
    out.append("Generated by `tools/mesh_function_preflight.py`. Re-run it after")
    out.append("changing a mesh function; the checklist is derived from the source,")
    out.append("so it cannot drift from the code it describes.")
    out.append("")
    out.append("Companion analysis - the reasoning behind every change made from this")
    out.append("list, plus the insights inferred from the census - lives in")
    out.append("`NEURAL_MESH_FUNCTION_AUDIT_ANALYSIS_2026-09-16.md`.")
    out.append("")
    if module_filter:
        out.append(f"Filter: modules matching `{module_filter}`")
        out.append("")
    out.append("## Scope")
    out.append("")
    out.append(f"- {total} functions across the fabric, neural primitives, learning")
    out.append("  and plasticity, and operational subsystems")
    out.append(f"- {clean} pass every automated probe; {total - clean} have at least one failure")
    out.append(f"- P0 {p0} / P1 {p1} / P2 {p2}")
    out.append("")
    out.append("## How to work this list")
    out.append("")
    out.append("For each function, in this order:")
    out.append("")
    out.append("1. Run the automated battery (below). A PASS is evidence, not a verdict.")
    out.append("2. Walk the manual checklist. This is the part that needs reading the")
    out.append("   callers, not just the body.")
    out.append("3. Only then tick the boxes.")
    out.append("4. Any probe you clear by *changing* the function gets recorded in")
    out.append("   `NEURAL_MESH_FUNCTION_AUDIT_ANALYSIS_2026-09-16.md`, with the")
    out.append("   reasoning, so the fix is reviewable later.")
    out.append("")
    out.append("## Automated pre-flight battery")
    out.append("")
    for pid, desc in PROBE_HELP:
        out.append(f"- **{pid}** {desc}")
    out.append("")
    out.append("## Manual pre-flight checklist")
    out.append("")
    for mid, desc in MANUAL_CHECKS:
        out.append(f"- **{mid}** {desc}")
    out.append("")

    out.append("## Failure census")
    out.append("")
    out.append("| probe | functions failing |")
    out.append("|---|---|")
    for pid, _ in PROBE_HELP:
        out.append(f"| {pid} | {counts.get(pid, 0)} |")
    out.append("")

    # Highest-value work first: P0, then P1 by complexity.
    out.append("## Triage - P0")
    out.append("")
    out.append("Probes in the P0 set (PF05, PF07, PF12, PF13, PF15, PF18, PF20) are the")
    out.append("ones that can lose data, hide an error, or spin forever. Clear these")
    out.append("first.")
    out.append("")
    p0_funcs = sorted((f for f in funcs if f.prior == "P0"),
                      key=lambda f: -f.complexity)
    if not p0_funcs:
        out.append("_None._")
        out.append("")
    for fn in p0_funcs:
        out.append(f"- [ ] `{fn.label}` {fn.lines} c={fn.complexity} "
                   f"fail={','.join(fn.failures)}")
        out.append(f"  - [ ] {'M1-M6 manual pass'}")
    out.append("")

    out.append("## Triage - P1")
    out.append("")
    p1_funcs = sorted((f for f in funcs if f.prior == "P1"),
                      key=lambda f: -f.complexity)
    for fn in p1_funcs:
        out.append(f"- [ ] `{fn.label}` {fn.lines} c={fn.complexity} "
                   f"fail={','.join(fn.failures)}")
    out.append("")

    out.append("## Full inventory")
    out.append("")
    out.append("Every function in scope, grouped by concern and module. Each row is")
    out.append("`- [ ]` battery pass, `- [ ]` manual pass.")
    out.append("")

    by_module: dict[str, list[Func]] = defaultdict(list)
    for fn in funcs:
        by_module[fn.module].append(fn)

    for group, names in SCOPE_GROUPS:
        out.append(f"### {group}")
        out.append("")
        for name in names:
            mods = by_module.get(name)
            if not mods:
                continue
            out.append(f"#### `{name}.py` - {len(mods)} functions")
            out.append("")
            for fn in sorted(mods, key=lambda f: f.lineno):
                flags = ",".join(fn.failures) if fn.failures else "OK"
                out.append(f"- [ ] `{fn.qualname}` {fn.lines} "
                           f"c={fn.complexity} p={fn.params} "
                           f"{'async ' if fn.is_async else ''}auto={flags}")
                out.append(f"  - [ ] manual M1-M6")
            out.append("")

    extras = [p.stem for p in EXTRA_MODULES if p.exists()]
    if extras:
        out.append("### Outside the expansion package")
        out.append("")
        for name in extras:
            mods = by_module.get(name)
            if not mods:
                continue
            out.append(f"#### `{name}.py` - {len(mods)} functions")
            out.append("")
            for fn in sorted(mods, key=lambda f: f.lineno):
                flags = ",".join(fn.failures) if fn.failures else "OK"
                out.append(f"- [ ] `{fn.qualname}` {fn.lines} "
                           f"c={fn.complexity} p={fn.params} "
                           f"{'async ' if fn.is_async else ''}auto={flags}")
                out.append(f"  - [ ] manual M1-M6")
            out.append("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Wire-or-cut verdict (PF16), as data
# ---------------------------------------------------------------------------
#
# PF16 is the probe that answers "does anything reach this function?" and it is
# the one whose findings cannot be fixed by editing the function: a function
# nothing calls cannot regress visibly, so it can neither be trusted nor repaired
# by observation. There are only two honest resolutions -- wire it to a caller,
# or retire it -- and that is a DECISION, not a patch.
#
# `evolution.py` already encodes exactly this for the v2 engines as
# `SERVED_ENGINES` / `UNSERVED_ENGINES`, with a test asserting no UNSERVED name
# is referenced. This generalises that pattern to every PF16 finding in the mesh
# so the decision is reviewable data instead of a silent 114-item blind spot.
#
# `pending` is a first-class status on purpose: an unreviewed function is not the
# same as a decided one, and collapsing the two is how a backlog becomes a lie.

VERDICT_OUT = REPO / "NEURAL_MESH_WIRE_OR_CUT.json"

#: Declared dispositions.
#:
#: * `wired`      -- a STATIC production caller exists (so PF16 must NOT fire)
#: * `retired`    -- it should be deleted (`reason` required)
#: * `dispatched` -- its only production reference is a STRING (name-based
#:                   lookup: `getattr`, a registry key, a dispatch table), so an
#:                   explicit reference cannot exist and PF16 cannot decide it.
#:                   Verified against `string_references()`, not asserted.
#: * `pending`    -- the decision has not been made
#:
#: `dispatched` exists because a triage measured it: of 164 findings, 62 are
#: named by a string literal in production code while PF16 (which reads
#: `ast.Name` / `ast.Attribute`) can see none of them. Before this status the
#: table could only say `pending` about a fact it had already measured, or lie --
#: `wired` fails its own contract test while PF16 still fires. This is the same
#: distinction as `PROTOCOL_METHODS` ("no explicit reference can ever satisfy
#: it"), kept as data rather than folded into the probe's filter.
VERDICT_STATUSES = ("wired", "retired", "dispatched", "accepted_gap", "rejected", "pending")

#: Evidence classes for the `pending` backlog -- see `triage()`.
#: Where the `--worklist` artifact is written. Its own file rather than a
#: section of the TODO, because that doc is REWRITTEN by every preflight run: a
#: section appended by one flag and dropped by the next is the exact field-loss
#: this probe has already produced twice (Error #199, Error #202).
WORKLIST_OUT = REPO / "NEURAL_MESH_WIRE_OR_CUT_WORKLIST.md"

#: The classes, in the order a reviewer should take them, with what the class
#: means for the DECISION (not for the status).
TRIAGE_CLASSES = ("test_reached_only", "no_reference_anywhere")

#: The fact that DECIDES a pending row, as opposed to the class that records how
#: it was measured. A class is what the probe saw; a decider is which fact the
#: operator has to act on. Still not a disposition -- `dead_candidate` is a
#: candidate, and Error #190 is what happens when a probe writes the status.
#: Added 2026-09-22: 101 rows sat with a class and no decider, which reads as a
#: backlog but is really a list of questions nobody has been handed.
DECIDERS = ("protocol_or_override", "declared_in_data", "dynamic_call",
            "test_is_the_wire", "dead_candidate")

DECIDER_MEANING = {
    "protocol_or_override": (
        "the same name is defined by another class in the tree, so a caller can "
        "reach it through a base or an interface -- keep or retire, but a "
        "name-based reference search can never settle it"),
    "declared_in_data": (
        "a non-Python artifact names it -- a registry cache, capability map, "
        "stage manifest or config. The reference index reads Python source "
        "only, so this is a wire the probe structurally cannot see, and the "
        "row must not be cut on its evidence"),
    "dynamic_call": (
        "the defining module builds a call target at runtime (`getattr` with a "
        "computed name), so 'no reference' is the probe's limit, not proof of "
        "death -- wire explicitly or leave, but do not cut on this evidence"),
    "test_is_the_wire": (
        "a test is the only caller: accept the test as the wire, or give "
        "production a caller -- either way it is a decision, not a defect"),
    "dead_candidate": (
        "no caller of any kind, no override, no computed dispatch anywhere in "
        "the defining module -- the retirement pool"),
}

#: How each class reads as an action, so the worklist says what to do rather
#: than only how the row was measured. A class is still not a disposition.
TRIAGE_MEANING = {
    "test_reached_only": (
        "a test names it, so it is exercised -- decide whether the test is the "
        "wire or the function should also be reachable from production"),
    "no_reference_anywhere": (
        "nothing in the tree names it, not even a test -- the candidate pool "
        "for retirement, or for explicit wiring"),
}

#: Where a name-based reference can come from: production only, because a string
#: in a test is test reachability and PF17 already measures that.
STRING_SCOPES = ("src", "scripts", "tools")

_STRING_REFS: dict[str, list[str]] | None = None

#: A literal that could name something: `deliver`, `Worm.deliver`. A sentence
#: (`"we deliver the mail"`) is not a dispatch site and is not counted.
_IDENTIFIER_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """`id()` of every docstring constant, so prose is not read as a reference."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def string_references() -> dict[str, list[str]]:
    """name -> `["src/.../file.py:123"]` for every production string literal.

    Cached, because the wire-or-cut tests ask about one name at a time and the
    walk covers several thousand files.

    Only IDENTIFIER-LIKE literals count -- `"deliver"`, `"Worm.deliver"`, and
    docstrings are skipped -- because a sentence that happens to contain the
    word is not a dispatch site, and a docstring that names a function is a
    description of code, not a reference to it from code. Being conservative is
    the safe direction here: a miss leaves the entry `pending` (the truth being
    "not measured") whereas a false hit would claim reachability that does not
    exist.
    """
    global _STRING_REFS
    if _STRING_REFS is not None:
        return _STRING_REFS
    found: dict[str, list[str]] = defaultdict(list)
    for base in STRING_SCOPES:
        root = REPO / base
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(x in path.parts for x in DEFAULT_EXCLUDES):
                continue
            try:
                tree = ast.parse(_read(path))
            except SyntaxError:
                continue
            rel = path.relative_to(REPO).as_posix()
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)):
                    continue
                if id(node) in docstrings:
                    continue
                literal = node.value
                if not _IDENTIFIER_LIKE.match(literal):
                    continue
                for token in {literal} | set(re.split(r"\.", literal)):
                    if token.isidentifier():
                        where = "%s:%d" % (rel, node.lineno)
                        if where not in found[token]:
                            found[token].append(where)
    _STRING_REFS = {k: sorted(v) for k, v in found.items()}
    return _STRING_REFS


def wire_or_cut(funcs: list[Func], declared: dict | None = None) -> dict:
    """The PF16 findings as a declaration table, preserving prior decisions.

    Passing the existing table in means a re-run never discards a decision
    someone already made and wrote down -- the same reason the TODO is
    regenerated rather than rewritten.
    """
    prior = dict(declared or {})
    entries: dict[str, dict] = {}
    for fn in funcs:
        if "PF16" not in fn.failures:
            continue
        key = fn.label
        old = prior.get(key) or {}
        # Carry the WHOLE prior disposition forward, not a hand-picked subset of
        # it. This used to rebuild each entry with exactly four keys, so a
        # `--verdict` re-run discarded every other recorded field -- including
        # `evidence`, which `--propose` had filled. Measured 2026-09-22: the
        # refresh path (`--verdict` without `--propose`) rewrote a table of 62
        # `dispatched` rows with 0 of them carrying locations, and the contract
        # test that requires them failed in the aggregate run against an
        # artifact no test had touched (Error #202 -- the same field-loss as
        # #199, one layer out: not inside a transition, but inside a rebuild).
        entry = dict(old)
        entry["module"] = fn.module
        entry["qualname"] = fn.qualname
        entry["line"] = fn.lineno
        # The path segment disambiguates two modules with the same stem, which
        # is what a name-based reference index cannot do (there are several
        # `db_monitor`/`spine`-style stems across the tree). Read defensively:
        # the contract tests drive this with a stub Func, and a rebuild that
        # demands more of its caller than the previous one did is the same
        # shape as the field-loss this method already produced twice.
        entry["seg"] = getattr(fn, "seg", "")
        entry["status"] = str(entry.get("status") or "pending")
        entry["reason"] = str(entry.get("reason") or "")
        entries[key] = entry
    # Prior-carry (engram #719): a committed row with no PF16 finding on
    # regenerate is CARRIED with its disposition intact, never silently
    # dropped. Dropping on a newly-appeared bare token baked a false
    # "wired" claim (generic-token class: an unrelated tools/ file's
    # ``merge()`` call erased dendrite:ContextConcatWorm.merge). The carry
    # is marked so the next --verdict reviewer rules it instead of
    # inheriting it blindly; genuine death still exits only via an explicit
    # `retired` disposition, never via disappearance.
    missing = [k for k in prior if k not in entries]
    if missing:
        refs_by_file, imports_by_file = _module_evidence()
        for key in missing:
            old = prior.get(key)
            entry = dict(old) if isinstance(old, dict) else {}
            old_status = str(entry.get("status") or "pending")
            # A carried row has no PF16 finding on regenerate, which means it is
            # now referenced (a caller was added). `pending`/`dispatched` entries
            # therefore become `wired` -- BUT only on a module-qualified link.
            # The bare-token form of this rule baked a false wire twice:
            # ContextConcatWorm.merge via an unrelated tools/ merge() (#472/
            # #682/#719), and config:FeatureFlagCtrl.is_enabled via Selenium's
            # WebElement.is_enabled() (2026-10-28, module-linked: no). An
            # unlinked carry keeps its prior status with the evidence stating
            # why; genuine death still exits only via an explicit `retired`
            # disposition, never via disappearance.
            module, _, qualname = key.partition(":")
            stub = Func(module=module or key, qualname=qualname or key,
                        lineno=0, end_lineno=0, complexity=0, params=0,
                        is_async=False, source="", seg="")
            linked = _module_qualified_hit(stub, refs_by_file,
                                           imports_by_file)
            if old_status in ("pending", "dispatched") and linked:
                entry["status"] = "wired"
            entry["carried"] = True
            entry["carry_evidence"] = _carry_evidence(key, entry,
                                                     refs_by_file,
                                                     imports_by_file)
            entries[key] = entry
    tally = Counter(e["status"] for e in entries.values())
    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "probe": "PF16",
        "rule": ("Every unreferenced mesh function must be declared wired, "
                 "retired or pending. tests/test_mesh_wire_or_cut.py asserts "
                 "that no finding is undeclared, that no declaration is "
                 "stale, and that a `wired` declaration is actually wired."),
        "counts": {s: tally.get(s, 0) for s in VERDICT_STATUSES},
        "total": len(entries),
        "declared": dict(sorted(entries.items())),
    }


def _module_evidence() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Per-file reference/import maps for carry evidence (see wire_or_cut)."""
    _, _, _, refs_by_file, imports_by_file = _reference_index()
    return refs_by_file, imports_by_file


def _carry_evidence(key: str, entry: dict,
                    refs_by_file: dict[str, set[str]],
                    imports_by_file: dict[str, set[str]]) -> str:
    """Where the bare token of a carried row now appears, and whether linked.

    ``key`` is ``module:qualname``. A stub Func (empty path) drives the
    shared module-qualification predicate: same-file can never match, so
    only import-linked files count as linked.
    """
    module, _, qualname = key.partition(":")
    name = (qualname or key).rsplit(".", 1)[-1]
    stub = Func(module=module or key, qualname=qualname or key, lineno=0,
                end_lineno=0, complexity=0, params=0, is_async=False,
                source="", seg="")
    hit_files = sorted(f for f, names in refs_by_file.items() if name in names)
    linked = _module_qualified_hit(stub, refs_by_file, imports_by_file)
    return ("no PF16 finding on regenerate; prior status %r carried; bare "
            "token %r in %d file(s)%s; module-linked: %s" % (
                entry.get("status", "pending"), name, len(hit_files),
                (" [" + ", ".join(hit_files[:5]) + "]") if hit_files else "",
                "yes" if linked else "no"))


def propose(table: dict) -> dict:
    """Fill in the statuses the EVIDENCE decides, and leave the rest pending.

    Deliberately not a classifier that guesses: it sets exactly one status
    (`dispatched`, when the only production trace of a name is a string) and
    writes the measurement into `reason` for everything else, so the remaining
    backlog states what is missing instead of being a bare count. An existing
    `reason` is never overwritten -- a human's note outranks a probe's.

    Returns the table unchanged in shape, with `counts` recomputed.
    """
    refs = string_references()
    entries = table.get("declared") or {}
    for entry in entries.values():
        qualname = str(entry.get("qualname") or "")
        name = qualname.rsplit(".", 1)[-1]
        hits = refs.get(name) or refs.get(qualname) or []
        status = str(entry.get("status") or "pending")
        reason = str(entry.get("reason") or "")
        if hits and status == "pending":
            entry["status"] = "dispatched"
            if not reason:
                entry["reason"] = (
                    "only production reference is a string literal (%s), so a "
                    "static caller cannot exist; wire it explicitly or delete "
                    "it" % ", ".join(hits[:3]))
        # Evidence is re-derived for EVERY dispatched entry, not only the one
        # being flipped. `--propose` is meant to be re-runnable, and the first
        # version recorded the locations only on the transition: a second run
        # left every already-dispatched row with a reason and no evidence, which
        # `tests/test_mesh_wire_or_cut.py::
        # test_a_dispatched_declaration_states_where_and_why` correctly rejects.
        if hits and str(entry.get("status")) == "dispatched":
            entry["evidence"] = hits[:3]
        elif not hits and status == "pending" and not reason:
            entry["reason"] = (
                "no production reference of any kind -- not as a Name, an "
                "attribute, or a string; wire it or delete it (PF16)")
    tally = Counter(str(e.get("status")) for e in entries.values())
    table["counts"] = {s: tally.get(s, 0) for s in VERDICT_STATUSES}
    return table


# ---------------------------------------------------------------------------
# Triage of the `pending` backlog -- a class, not a disposition
# ---------------------------------------------------------------------------
# 102 findings have no production reference of any KIND: not a name, not an
# attribute, not a string literal. `status` cannot be set from that (a class is
# not a decision -- see VERDICT_STATUSES), but the NEXT ACTION can be, and the
# evidence for it is one cheap question: **does the test suite reach it?**
#
#  * `test_reached_only` -- a test calls it and production does not. The
#    decision is about intent: public API (wire a production caller) or
#    scaffolding (retire it). PF17 already measures test reachability, so this
#    is the same fact stated where the decision is made.
#  * `no_reference_anywhere` -- nothing in this repository reaches it at all.
#    That is the deletion queue, and it is a claim the probe can check.
#
# The label is stored in its own field (`triage`, `triage_evidence`) and never
# in `status`, so no review can mistake a measurement for a disposition.
TEST_SCOPES = ("tests",)

_test_refs: dict[str, list[str]] | None = None


def test_references() -> dict[str, list[str]]:
    """name -> `["tests/test_x.py:123"]` for every TEST-side mention.

    Counts whatever a test can use to reach a name: an `ast.Name`, an
    `ast.Attribute` (``profiler.profile_function()``), or an identifier-like
    string literal. Cached for the same reason as `string_references`.
    """
    global _test_refs
    if _test_refs is not None:
        return _test_refs

    found: dict[str, list[str]] = {}

    def note(name: str, where: str) -> None:
        if not name or name.startswith("__"):
            return
        bucket = found.setdefault(name, [])
        if len(bucket) < 8 and where not in bucket:
            bucket.append(where)

    for scope in TEST_SCOPES:
        root = REPO / scope
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            docstrings = _docstring_nodes(tree)
            rel = path.relative_to(REPO).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    note(node.id, "%s:%d" % (rel, node.lineno))
                elif isinstance(node, ast.Attribute):
                    note(node.attr, "%s:%d" % (rel, node.lineno))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    text = node.value.strip()
                    if _IDENTIFIER_LIKE.match(text):
                        note(text.rsplit(".", 1)[-1], "%s:%d" % (rel, node.lineno))
    _test_refs = found
    return found


def triage(table: dict) -> dict:
    """Label the still-`pending` findings with the class that decides them.

    Never changes `status`: this is the ordering a reviewer needs, not a
    verdict, and collapsing the two is how a backlog becomes a lie.
    """
    refs = test_references()
    entries = table.get("declared") or {}
    tally: Counter = Counter()
    for entry in entries.values():
        if str(entry.get("status")) != "pending":
            # A class is not a disposition: triage labels only belong on pending
            # entries. Strip any stale label carried over from a prior run so a
            # non-pending row never carries triage state.
            entry.pop("triage", None)
            entry.pop("triage_evidence", None)
            continue
        qualname = str(entry.get("qualname") or "")
        hits = refs.get(qualname.rsplit(".", 1)[-1]) or refs.get(qualname) or []
        klass = "test_reached_only" if hits else "no_reference_anywhere"
        entry["triage"] = klass
        entry["triage_evidence"] = hits[:3]
        tally[klass] += 1
    table["triage_counts"] = {k: tally.get(k, 0) for k in TRIAGE_CLASSES}
    decide(table)
    return table


_METHOD_INDEX: dict[str, set[str]] | None = None
_DYNAMIC_CALL_FILES: set[str] | None = None
_FILES_BY_STEM: dict[str, list[str]] | None = None

#: Where a name can be wired WITHOUT appearing in any `.py` file: a registry
#: cache, a capability map, a stage manifest, a YAML config. `_reference_index`
#: and `string_references` both read Python source only -- `Name`, `Attribute`,
#: and identifier-like string constants -- so a function named in
#: `state/*.json` is invisible to them and reads as dead. Measured 2026-09-22:
#: 1,157 files / 33 MB under the scopes below at the size cap, which is cheap
#: enough to read on every run and is why the cap matters.
DATA_SCOPES = ("state", "src", "tools", "scripts", "docs")
DATA_EXTS = (".json", ".yaml", ".yml", ".toml")
DATA_MAX_BYTES = 1_000_000
_IDENT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

#: A data file that names a large SHARE of the candidates is an inventory of
#: them, not a wire for them. Without this rule the first run classified
#: 101 of 101 rows as `declared_in_data`, every one of them on the strength of
#: `NEURAL_MESH_INTEGRATION_MAP.json` and `NEURAL_MESH_ENTRENCHMENT.json` --
#: artifacts generated by this probe family. The probe read its own report back
#: as evidence, which would make every function in the mesh immortal and retire
#: the whole backlog in a single afternoon. A capability map names a handful of
#: entry points; an inventory names all of them, and the count is what tells
#: them apart.
DATA_INVENTORY_SHARE = 0.20
DATA_INVENTORY_MIN = 4


def data_inventory_threshold(n_names: int) -> int:
    """How many candidates in one data file make it an inventory, not a wire."""
    return max(DATA_INVENTORY_MIN, int(n_names * DATA_INVENTORY_SHARE))


#: A data file only wires something if PRODUCTION CODE READS IT. Without this,
#: the second run of the rule above still classified 101 of 101, now on the
#: strength of `src/graphify-out/cache/ast/<sha>.json` -- graphify's own AST
#: cache, one file per source file, each naming whatever that source names. A
#: cache of the tree is not a dispatcher for the tree. The test is exact rather
#: than heuristic: if no `.py` under production scopes loads the file, nothing
#: in the system can be reached through it, whatever its contents say.
_DATA_FILES_READ: set[str] | None = None


def data_files_read_by_code() -> set[str]:
    """Basenames of data files named in a string constant under production code."""
    global _DATA_FILES_READ
    if _DATA_FILES_READ is not None:
        return _DATA_FILES_READ
    out: set[str] = set()
    for base in STRING_SCOPES:
        root = REPO / base
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(x in path.parts for x in DEFAULT_EXCLUDES):
                continue
            try:
                tree = ast.parse(_read(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    low = node.value.strip().lower()
                    if low.endswith(DATA_EXTS):
                        out.add(low.replace("\\", "/").rsplit("/", 1)[-1])
    _DATA_FILES_READ = out
    return out


#: Cache keyed by the requested name set, so a second call for different names
#: cannot be answered from the first call's results -- the bug that would make
#: this index report "nothing found" for every name it was not first asked
#: about.
_DATA_INDEX: dict[frozenset, tuple[dict[str, list[str]], list[str]]] = {}


def data_inventory_exclusions(names: set[str]) -> list[str]:
    """The data files rejected as inventories rather than wires, as strings.

    Read from the same cache `data_index` fills, so the exclusions a run
    reported are the exclusions it actually applied.
    """
    data_index(names)
    return list(_DATA_INDEX[frozenset(names)][1])


def data_index(names: set[str]) -> dict[str, list[str]]:
    """Where a wire BY DATA names each of `names`, as {name: ["path:line"]}.

    Targeted rather than general: only the names the caller asks about are
    kept, so the scan costs no more than the tokens it must answer for.
    """
    key = frozenset(names)
    cached = _DATA_INDEX.get(key)
    if cached is not None:
        return cached[0]
    files: list[Path] = []
    try:
        for p in REPO.iterdir():
            if p.is_file() and p.suffix.lower() in DATA_EXTS:
                files.append(p)
    except OSError:
        print("mesh_function_preflight: data scan failed", file=sys.stderr)
    for base in DATA_SCOPES:
        root = REPO / base
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if (p.is_file() and p.suffix.lower() in DATA_EXTS
                    and not any(x in p.parts for x in DEFAULT_EXCLUDES)):
                files.append(p)
    # Per-FILE first, because the inventory test is a property of the file (how
    # many candidates it names at once), not of any single name.
    per_file: dict[str, dict[str, list[str]]] = {}
    for path in sorted(set(files)):
        try:
            if path.stat().st_size > DATA_MAX_BYTES:
                continue
            text = _read(path)
        except OSError:
            continue
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        here: dict[str, list[str]] = {}
        for i, line in enumerate(text.splitlines(), 1):
            for tok in set(_IDENT_TOKEN.findall(line)):
                if tok in names:
                    here.setdefault(tok, []).append("%s:%d" % (rel, i))
        if here:
            per_file[rel] = here

    limit = data_inventory_threshold(len(names))
    read_by_code = data_files_read_by_code()
    found: dict[str, list[str]] = {}
    excluded: list[str] = []
    for rel, here in sorted(per_file.items()):
        base = rel.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in PROBE_ARTIFACTS:
            excluded.append("%s (probe's own artifact)" % rel)
            continue
        if base not in read_by_code and rel.lower() not in read_by_code:
            excluded.append("%s (not loaded by any production code)" % rel)
            continue
        if len(here) >= limit:
            excluded.append("%s (%d of %d candidates)" % (rel, len(here), len(names)))
            continue
        for tok, where in here.items():
            hits = found.setdefault(tok, [])
            for w in where:
                if len(hits) < 3 and w not in hits:
                    hits.append(w)
    _DATA_INDEX[key] = (found, excluded)
    return found


def _decider_index() -> tuple[dict[str, set[str]], set[str], dict[str, list[str]]]:
    """(method name -> classes defining it, files with a computed call, files).

    One cached pass over the same bases `_reference_index` reads. The method map
    is the point: a name defined by two classes is reached through a base, and a
    reference search is the wrong instrument for it. `PROTOCOL_METHODS` already
    admits that for the known hooks; this extends the same admission to whatever
    the tree actually overrides, which is the difference between a question the
    operator can answer and a verdict the probe cannot support.
    """
    global _METHOD_INDEX, _DYNAMIC_CALL_FILES, _FILES_BY_STEM
    if (_METHOD_INDEX is not None and _DYNAMIC_CALL_FILES is not None
            and _FILES_BY_STEM is not None):
        return _METHOD_INDEX, _DYNAMIC_CALL_FILES, _FILES_BY_STEM
    methods: dict[str, set[str]] = {}
    dynamic: set[str] = set()
    by_stem: dict[str, list[str]] = {}
    for base in ("src", "scripts", "tools"):
        root = REPO / base
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(x in path.parts for x in DEFAULT_EXCLUDES):
                continue
            try:
                tree = ast.parse(_read(path))
            except SyntaxError:
                continue
            rel = path.relative_to(REPO).as_posix()
            by_stem.setdefault(path.stem, []).append(rel)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "getattr"
                        and len(node.args) >= 2
                        and not (isinstance(node.args[1], ast.Constant)
                                 and isinstance(node.args[1].value, str))):
                    dynamic.add(rel)
                if not isinstance(node, ast.ClassDef):
                    continue
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.setdefault(sub.name, set()).add(node.name)
    _METHOD_INDEX = methods
    _DYNAMIC_CALL_FILES = dynamic
    _FILES_BY_STEM = by_stem
    return methods, dynamic, by_stem


def _resolve_module(module: str, seg: str,
                    by_stem: dict[str, list[str]]) -> str | None:
    """The one file a stem names, or None when the stem is genuinely ambiguous.

    `seg` (the path segment the discovery walked) breaks the tie. If more than
    one candidate survives, the honest answer is None: picking the first one
    would attach another module's `getattr` to this row, which is the same class
    of error as reading a test caller as a production one.
    """
    cands = by_stem.get(module) or []
    if seg:
        narrowed = [c for c in cands if seg in c.split("/")]
        if narrowed:
            cands = narrowed
    return cands[0] if len(cands) == 1 else None


def decide(table: dict) -> dict:
    """Attach the fact that decides each pending row. Writes no status.

    Order matters, and it is deliberately the order that protects life over
    tidiness: an override, a data-declared wire and a computed dispatch are all
    checked BEFORE the row is allowed to be called a retirement candidate,
    because 'the probe found no name' is the weakest of the available facts and
    the only destructive one.
    """
    methods, dynamic, by_stem = _decider_index()
    pending = [e for e in (table.get("declared") or {}).values()
               if str(e.get("status")) == "pending"]
    # One scan for the whole pending set, not one per row.
    data = data_index({str(e.get("qualname") or "").rsplit(".", 1)[-1]
                       for e in pending})
    for entry in pending:
        qualname = str(entry.get("qualname") or "")
        name = qualname.rsplit(".", 1)[-1]
        cls = qualname.rsplit(".", 1)[0] if "." in qualname else ""
        rel = _resolve_module(str(entry.get("module") or ""),
                              str(entry.get("seg") or ""), by_stem)
        evidence: list[str] = []
        owners = sorted(methods.get(name, set()) - {cls})
        if owners:
            klass = "protocol_or_override"
            evidence = ["also defined by: %s" % ", ".join(owners)]
        elif data.get(name):
            klass = "declared_in_data"
            evidence = ["named by %s" % h for h in data[name]]
        elif rel and rel in dynamic:
            klass = "dynamic_call"
            evidence = [rel]
        elif str(entry.get("triage")) == "test_reached_only":
            klass = "test_is_the_wire"
            evidence = [str(h) for h in (entry.get("triage_evidence") or [])]
        else:
            klass = "dead_candidate"
        entry["decider"] = klass
        # Full provenance, not a window: the recorded evidence is what the
        # decider actually acted on (2026-10-22 sweep D1 site). Decision
        # logic is untouched -- decide() still writes no status.
        entry["decider_evidence"] = evidence
    counts: Counter = Counter()
    for entry in (table.get("declared") or {}).values():
        if str(entry.get("status")) == "pending":
            counts[str(entry.get("decider"))] += 1
    table["decider_counts"] = {k: counts.get(k, 0) for k in DECIDERS}
    return table


def worklist_markdown(table: dict) -> str:
    """The pending backlog as an ordered, evidence-carrying action list.

    `--triage` already writes a class and its evidence onto every pending row,
    but only into the JSON, where 101 rows of `{"triage": ...}` are a count with
    a shape. This renders the same measurement as work: one section per class in
    the order they should be taken, one checkbox per row, and -- for the rows a
    test reaches -- the test location that reaches them, so checking a row does
    not begin with a search.

    It deliberately does NOT write a status. A class is a measurement of what is
    known; turning it into `retired` or `wired` here would make this an inferred
    disposition presented as a decision (Error #190) and the table's own
    contract test would then be asserting a guess.
    """
    entries = table.get("declared") or {}
    counts = table.get("triage_counts") or {}
    lines = [
        "# Wire-or-cut worklist -- the pending backlog, in the order to take it",
        "",
        "Generated by `tools/mesh_function_preflight.py --verdict --triage "
        "--worklist`.",
        "Every row below is `pending` in `NEURAL_MESH_WIRE_OR_CUT.json`: nothing "
        "here is a decision,",
        "and running this does not change a status. Decide, then set the status "
        "in the table.",
        "",
        "| Class | Pending | What it means |",
        "|---|---|---|",
    ]
    for klass in TRIAGE_CLASSES:
        lines.append("| `%s` | %d | %s |"
                     % (klass, counts.get(klass, 0), TRIAGE_MEANING[klass]))
    lines.append("")
    deciders = table.get("decider_counts") or {}
    if any(deciders.get(k) for k in DECIDERS):
        lines.append("The same rows, by the fact that decides them. A class is "
                     "how the row was measured; a decider is what to act on:")
        lines.append("")
        lines.append("| Decider | Pending | What it means |")
        lines.append("|---|---|---|")
        for klass in DECIDERS:
            lines.append("| `%s` | %d | %s |"
                         % (klass, deciders.get(klass, 0),
                            DECIDER_MEANING[klass]))
        lines.append("")
    for klass in TRIAGE_CLASSES:
        rows = [e for e in entries.values()
                if str(e.get("status")) == "pending"
                and str(e.get("triage")) == klass]
        rows.sort(key=lambda e: (str(e.get("module") or ""),
                                 str(e.get("qualname") or "")))
        lines.append("## %s (%d)" % (klass, len(rows)))
        lines.append("")
        if not rows:
            lines.append("Nothing pending in this class.")
            lines.append("")
            continue
        for entry in rows:
            evidence = [str(h) for h in (entry.get("triage_evidence") or [])]
            where = " -- reached by %s" % ", ".join(evidence) if evidence else ""
            decider = str(entry.get("decider") or "")
            why = [str(h) for h in (entry.get("decider_evidence") or [])]
            note = " [%s%s]" % (decider, ": " + "; ".join(why) if why else "")
            lines.append("- [ ] `%s:%s` (`%s:%s`)%s%s"
                         % (entry.get("module"), entry.get("qualname"),
                            entry.get("module"), entry.get("line"), where, note))
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# --check: what a freshness gate can actually judge
# --------------------------------------------------------------------------- #
# The status map has to be compared against GIT, not against the in-memory
# prior. `wire_or_cut(funcs, committed)` takes the committed map as prior so
# hand-set decisions survive a regeneration, which makes every comparison
# THROUGH that prior self-fulfilling: it reads whatever the file already says
# and therefore cannot disagree (negative control 2026-10-10 flipped a status
# pending->retired and the old check still printed "statuses unchanged"). HEAD
# is fixed, so a difference against it is a real edit.
#
# A move is DOCUMENTED when the row's justification moved with it. That split is
# what keeps the gate usable: adjudicating a row (backlog #739) rewrites its
# reason, and a bare flip -- the ledger now says `retired` while its reasons
# still argue for `wired` -- is exactly the unrecorded decision worth failing.

#: Fields that carry a row's decision. A status move with any of these changed
#: was recorded; `carry_evidence` is excluded on purpose -- it is recomputed on
#: every regeneration (token counts), so it moves without any decision moving.
JUSTIFICATION_FIELDS = ("reason", "decider", "decider_evidence",
                        "disposition", "triage")


def head_ledger(path: Path | None = None,
                timeout: int = 20) -> "tuple[dict | None, str]":
    """The declaration map as committed at HEAD, plus why it is unavailable.

    Returns ``(declared, "")`` on success and ``(None, reason)`` when the
    comparison cannot run. The caller must report the reason rather than
    assume freshness: an unreadable HEAD is missing evidence, never a pass.
    """
    target = path or VERDICT_OUT
    try:
        rel = target.resolve().relative_to(REPO.resolve()).as_posix()
    except (ValueError, OSError) as exc:
        return None, f"ledger path is outside the repository ({type(exc).__name__})"
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=str(REPO),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git unavailable ({type(exc).__name__}: {exc})"
    if proc.returncode != 0:
        first = (proc.stderr or "").strip().splitlines()
        detail = first[0] if first else "no stderr"
        return None, f"git show exited {proc.returncode} ({detail})"
    try:
        doc = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return None, f"HEAD ledger is not valid JSON ({exc})"
    if not isinstance(doc, dict):
        return None, "HEAD ledger is not a JSON object"
    declared = doc.get("declared")
    if not isinstance(declared, dict):
        return None, "HEAD ledger has no `declared` map"
    return declared, ""


def _justification(row: object) -> tuple:
    """The fields that state WHY a row holds its status, comparable by value."""
    if not isinstance(row, dict):
        return ()
    return tuple(repr(row.get(name)) for name in JUSTIFICATION_FIELDS)


def status_drift(current: dict,
                 head: dict) -> "tuple[list[dict], list[dict]]":
    """Compare statuses between the worktree ledger and HEAD.

    Returns ``(documented, undocumented)``, each a list of
    ``{"id", "old", "new"}``. ``documented`` moved with its justification;
    ``undocumented`` moved alone, which is the move a reader would be misled by.
    """
    documented: "list[dict]" = []
    undocumented: "list[dict]" = []
    for key in sorted(set(current) & set(head)):
        new_row, old_row = current.get(key), head.get(key)
        old = old_row.get("status") if isinstance(old_row, dict) else None
        new = new_row.get("status") if isinstance(new_row, dict) else None
        if old == new:
            continue
        move = {"id": key, "old": old, "new": new}
        if _justification(new_row) != _justification(old_row):
            documented.append(move)
        else:
            undocumented.append(move)
    return documented, undocumented


def assess_ledger(committed: dict, current: dict,
                  head: "dict | None") -> dict:
    """What ``--check`` decides, as a value instead of printed prose.

    Pure: no filesystem, no git. ``head=None`` means the comparison could not
    run, and the verdict then says so in ``head_available`` so no caller can
    print "unchanged" over a comparison that never happened.
    """
    added = sorted(set(current) - set(committed))
    removed = sorted(set(committed) - set(current))
    documented: "list[dict]" = []
    undocumented: "list[dict]" = []
    if head is not None:
        documented, undocumented = status_drift(current, head)
    return {
        "added": added,
        "removed": removed,
        "documented": documented,
        "undocumented": undocumented,
        "head_available": head is not None,
        "ok": not (added or removed or undocumented),
    }


def report_ledger(verdict: dict, current: dict,
                  head_reason: str = "", limit: int = 10) -> "list[str]":
    """The lines ``--check`` prints. Pure, so the wording is testable."""
    lines: "list[str]" = []
    added, removed = verdict["added"], verdict["removed"]
    documented, undocumented = verdict["documented"], verdict["undocumented"]
    if not verdict["head_available"]:
        lines.append(f"[Mesh] status comparison SKIPPED -- {head_reason or 'no reason given'}")
    if verdict["ok"]:
        if verdict["head_available"]:
            if documented:
                lines.append(
                    f"[Mesh] ledger fresh: {len(current)} declaration(s), "
                    f"key set unchanged, {len(documented)} documented "
                    f"status move(s)")
            else:
                lines.append(
                    f"[Mesh] ledger fresh: {len(current)} declaration(s), "
                    f"statuses unchanged against HEAD")
        else:
            lines.append(
                f"[Mesh] ledger fresh: {len(current)} declaration(s), key set "
                f"unchanged -- statuses NOT compared")
        for key, row in sorted(current.items()):
            if isinstance(row, dict) and row.get("carried"):
                lines.append(f"   = {key}  (carried: "
                             f"{str(row.get('carry_evidence', ''))[:160]})")
                if len([x for x in lines if x.startswith("   = ")]) >= limit:
                    break
        for move in documented:
            lines.append(f"   ~ {move['id']}: {move['old']} -> {move['new']}"
                         f"  (justification updated)")
        return lines
    lines.append(f"[Mesh] ledger STALE -- {len(added)} added, {len(removed)} "
                 f"removed, {len(undocumented)} undocumented status "
                 f"move(s)")
    lines.append("[Mesh] staleness mode: the set of unreferenced functions "
                 "moved. Status legality is enforced by "
                 "tests/test_mesh_wire_or_cut.py.")
    for key in added[:limit]:
        row = current.get(key)
        status = row.get("status") if isinstance(row, dict) else None
        lines.append(f"   + {key}  -> {status}")
    for key in removed[:limit]:
        lines.append(f"   - {key}")
    for move in documented[:limit]:
        lines.append(f"   ~ {move['id']}: {move['old']} -> {move['new']}"
                     f"  (justification updated)")
    for move in undocumented[:limit]:
        lines.append(f"   ! {move['id']}: {move['old']} -> {move['new']}"
                     f"  -- reason unchanged, so the row still argues for "
                     f"'{move['old']}'")
    lines.append("[Mesh] refresh: python tools/mesh_function_preflight.py "
                 "--verdict --triage")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--module", default=None, help="filter to modules matching this")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    ap.add_argument("--summary", action="store_true", help="counts only")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--verdict", action="store_true",
                    help="write the PF16 wire-or-cut declaration table instead")
    ap.add_argument("--propose", action="store_true",
                    help="with --verdict: fill the statuses the evidence "
                         "decides (string-only references become `dispatched`) "
                         "and record the measurement for the rest")
    ap.add_argument("--triage", action="store_true",
                    help="with --verdict: label every still-`pending` finding with "
                         "the evidence class that decides its next action "
                         "(test-reached / no-reference-anywhere); status is NOT "
                         "changed, because a class is not a disposition")
    ap.add_argument("--worklist", action="store_true",
                    help="with --verdict --triage: also write the pending backlog "
                         "as an ordered, evidence-carrying action list; status "
                         "is NOT changed")
    ap.add_argument("--check", action="store_true",
                    help="freshness only: regenerate the declaration table and "
                         "fail if it differs from the committed ledger, "
                         "without writing anything")
    args = ap.parse_args()

    funcs, counts = build(args.module)
    if args.check:
        # A generated ledger with no rebuild path goes stale the moment a caller
        # changes, and tests/test_mesh_wire_or_cut.py then fails on state the code
        # has already moved past (or, worse, passes on a stale ledger). Measured
        # 2026-10-10: engram:DreamConsolidatorWorm.read_pressure still read
        # `dispatched` after the restored EP235 loop made it statically called.
        #
        # Compares the DECLARATION MAP only, never the bytes: `generated_at`
        # changes on every run, so a byte compare could never pass. What it
        # does judge is the key set and -- against HEAD, see `head_ledger` --
        # whether a status moved without its justification moving with it.
        import json as _json

        if not VERDICT_OUT.exists():
            print(f"[Mesh] ledger missing: {VERDICT_OUT.name} -- "
                  f"run: python tools/mesh_function_preflight.py --verdict")
            return 1
        committed = ((_json.loads(VERDICT_OUT.read_text(encoding="utf-8"))
                      or {}).get("declared") or {})
        # Regenerate WITH the committed declarations as prior, exactly as
        # `--verdict` would: hand-set statuses (retired/dispatched) are preserved,
        # so a difference means the CODE moved, not that the CLI was invoked
        # differently. Comparing a prior-less table would report every hand-set
        # status as a change and fail forever.
        fresh = wire_or_cut(funcs, committed)
        current = fresh.get("declared") or {}
        # Two judgements, both from `assess_ledger`: the key set moved (a
        # caller was added or a function disappeared) or a status moved without
        # its justification. Status LEGALITY -- a `wired` row that is not wired
        # -- is still tests/test_mesh_wire_or_cut.py's job, because it re-derives
        # the evidence instead of trusting the file.
        head, head_reason = head_ledger()
        verdict = assess_ledger(committed, current, head)
        for line in report_ledger(verdict, current, head_reason):
            print(line)
        return 0 if verdict["ok"] else 1
    if args.verdict:
        import json

        prior = {}
        if VERDICT_OUT.exists():
            try:
                prior = (json.loads(VERDICT_OUT.read_text(encoding="utf-8"))
                         or {}).get("declared") or {}
            except Exception:
                prior = {}
        table = wire_or_cut(funcs, prior)
        if args.propose:
            table = propose(table)
        if args.triage:
            table = triage(table)
        if args.worklist and not args.triage:
            # The worklist is defined on the triage classes; without the labels
            # it would be an empty page claiming to be the backlog.
            print("--worklist needs --triage: it orders the pending backlog by "
                  "the class triage measures", file=sys.stderr)
            return 2
        if args.stdout:
            if args.worklist:
                sys.stdout.write(worklist_markdown(table))
            else:
                sys.stdout.write(json.dumps(table, indent=1) + "\n")
            return 0
        VERDICT_OUT.write_text(json.dumps(table, indent=1), encoding="utf-8")
        print(f"wrote {VERDICT_OUT} ({table['total']} unreferenced: {table['counts']})")
        if args.triage:
            print(f"  pending triage: {table.get('triage_counts')}")
        if args.worklist:
            WORKLIST_OUT.write_text(worklist_markdown(table), encoding="utf-8")
            pending = sum(int(v) for v in (table.get("triage_counts") or {}).values())
            print(f"wrote {WORKLIST_OUT} ({pending} pending rows, status unchanged)")
        return 0
    # Every table operation lives inside `--verdict`, and the fall-through here
    # accepted them silently. Measured 2026-09-22: `--triage --worklist` wrote
    # the TODO file, printed nothing about the backlog, and left a 3-hour-old
    # worklist on disk untouched -- which reads exactly like "the worklist is
    # empty". A flag that does nothing is worse than a flag that refuses.
    stray = [f for f in ("propose", "triage", "worklist", "stdout")
             if getattr(args, f, False)]
    if stray:
        print("--" + "/--".join(stray) + " only apply with --verdict; "
              "ignoring them", file=sys.stderr)

    if args.summary:
        print(f"functions: {len(funcs)}")
        for pid, _ in PROBE_HELP:
            print(f"  {pid}: {counts.get(pid, 0)}")
        for pr in ("P0", "P1", "P2", "CLEAN"):
            print(f"  {pr}: {counts.get(pr, 0)}")
        return 0

    text = render(funcs, counts, args.module)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text.splitlines())} lines, {len(funcs)} functions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
