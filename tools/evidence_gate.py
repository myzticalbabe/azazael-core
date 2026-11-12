#!/usr/bin/env python
"""Evidence gate — the agent must measure a claim before it may make it.

Why this exists. Three separate failures this month had one shape: a statement
that was true when written was repeated after the code moved, and nothing
re-derived it.

  * `rotate_secret` had zero call sites and returned `success: True` while
    writing no credential anywhere -- a control whose success signal was
    independent of the thing it claimed to do.
  * `NEURAL_MESH_WIRE_OR_CUT.json` declared rows `wired` on the strength of a
    bare token: 134 rows of this ledger currently rest on a name that appears in
    a file which does not import the defining module. `burst` matched the word
    "burst" in a token-bucket comment; `is_enabled` matched Selenium's
    `WebElement.is_enabled()`.
  * `enforce_type_caps` / `enforce_global_quota` were correct code with no
    caller, so `DB_SIZE_QUOTA_MB` only ever printed a warning.

So this gate does not read prose and does not trust a status field. Every check
re-derives its answer from source, from the ledger's own evidence, or from a
live store, and reports the measured number next to the verdict. A check that
cannot measure says UNVERIFIED -- which is not a pass.

Baseline ratchet. Legacy debt (the 134 token-only `wired` rows, the dead
controls already in the tree) is recorded in `state/evidence_gate_baseline.json`
and does not fail the gate, because a gate that fails on day one gets disabled.
NEW debt fails. `--update-baseline` is an explicit act that shows its diff.

Usage:
    python tools/evidence_gate.py                 # gate (exit 1 on new debt)
    python tools/evidence_gate.py --json          # machine-readable
    python tools/evidence_gate.py --update-baseline
    python tools/evidence_gate.py --root <dir>    # for the negative-control test

Exit codes: 0 all checks passed, 1 new violation or failed check, 2 usage error.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "state" / "evidence_gate_baseline.json"

sys.path.insert(0, str(REPO / "src"))
try:  # resolved at load, reported at check time -- a broken tree must not
    from CORE.WORMS.expansion.config import SecretRotator  # noqa: E402
    _ROTATION_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 -- the gate reports, it does not crash
    SecretRotator = None  # type: ignore[assignment]
    _ROTATION_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

SCAN_DIRS = ("src", "tools", "scripts", "tests")
SKIP_PARTS = {"__pycache__", "node_modules", ".git", "graphify-out", "build",
              "dist", "_archive", "archive"}

# Names that promise an effect. A control that nothing calls is not a control;
# it is a comment with a function signature.
CONTROL_PREFIXES = ("rotate_", "enforce_", "prune_", "cleanup_", "purge_",
                    "verify_", "validate_", "gate_", "audit_", "restore_",
                    "repair_", "migrate_", "backfill_", "reconcile_",
                    "remediate_", "quarantine_")
CONTROL_NAMES = {"rotate_secret"}


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _iter_py(root: Path, dirs=SCAN_DIRS):
    for d in dirs:
        base = root / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if SKIP_PARTS.intersection(p.parts):
                continue
            yield p


def _imports(tree: ast.AST) -> set[str]:
    """Every dotted module path and bound symbol name a file brings in."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
                out.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod:
                out.add(mod)
                out.update(mod.split("."))
            for a in node.names:
                out.add(a.name)
    return out


# --------------------------------------------------------------------------
# check: AST parse coverage
# --------------------------------------------------------------------------
def check_parse_coverage(root: Path) -> dict:
    """Every file must be parseable, or it is invisible to every other check."""
    files, failed = 0, []
    for path in _iter_py(root):
        files += 1
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            failed.append(f"{_rel(path, root)}:{exc.lineno}: {exc.msg}")
    return {
        "id": "ast_parse_coverage",
        "status": "pass" if not failed else "fail",
        "measured": f"{files - len(failed)}/{files} files parse",
        "detail": "an unparseable file is excluded from every AST check below",
        "offenders": failed,
    }


# --------------------------------------------------------------------------
# check: dead controls
# --------------------------------------------------------------------------
def _class_quals(tree: ast.AST) -> None:
    """Tag methods with their class name so qualnames read Class.method."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child._evg_parent = node.name  # type: ignore[attr-defined]


def _scan_functions_with_quals(root: Path) -> tuple[dict, dict]:
    """(definitions, usage).

    definitions: {(module_stem, qualname): {file, line, span}}
    usage:       {file: {imports, names, spans}}
    """
    definitions: dict[tuple[str, str], dict] = {}
    usage: dict[str, dict] = {}
    for path in _iter_py(root):
        rel = _rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            note_failure("tools/evidence_gate.py", exc)
            continue
        _class_quals(tree)
        mod = path.stem
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        # Call sites carry their line so a same-file call from another function
        # counts as reachability while the `def` line itself does not. Without
        # that distinction every script entry point called from its own main()
        # reads as dead, and the check cries wolf until someone silences it.
        calls: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if called:
                calls.append((called, getattr(node, "lineno", 0)))
        spans: dict[str, tuple[int, int]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = getattr(node, "_evg_parent", "")
                qual = f"{parent}.{node.name}" if parent else node.name
                spans[qual] = (node.lineno, getattr(node, "end_lineno", node.lineno))
                if rel.startswith(("src/", "tools/", "scripts/")):
                    definitions[(mod, qual)] = {"file": rel, "line": node.lineno,
                                                "span": spans[qual]}
        usage[rel] = {"imports": _imports(tree), "names": names,
                      "spans": spans, "calls": calls}
    return definitions, usage


def _imports_module(imports: set[str], mod: str) -> bool:
    return any(i == mod or i.endswith("." + mod) or mod in i.split(".") for i in imports)


def _call_sites(name: str, def_file: str, usage: dict, span=None,
                exclude_tests=True) -> list[str]:
    """Places that actually call `name`.


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    Rules, each bought by a false verdict:
      * a CALL, not a mention -- a name in a comment or a dict key is not a
        caller ("burst" in a token-bucket docstring made ChestbursterWorm.burst
        look resurrected);
      * in the defining file, the call must be outside the function's own span,
        otherwise `def foo` counts as its own caller;
      * in another file, that file must import the defining module, which is
        what keeps Selenium's `is_enabled()` from wiring FeatureFlagCtrl.
    """
    mod = Path(def_file).stem
    start, end = (span or (0, 0))
    hits = []
    for rel, info in usage.items():
        if exclude_tests and rel.startswith("tests/"):
            continue
        if rel == def_file:
            for called, line in info.get("calls", []):
                if called == name and not (start <= line <= end):
                    hits.append(f"{rel}:{line}")
            continue
        if name not in {c for c, _ in info.get("calls", [])}:
            continue
        if _imports_module(info["imports"], mod):
            hits.append(f"{rel}:{name}")
    return hits


def _module_public_names(root: Path) -> dict[str, set[str]]:
    """{file: names its module lists in __all__}.

    An exported name legitimately has no internal caller -- the caller is
    outside this repo. Treating that as dead is a false positive that would
    push someone to delete a public entry point, so it is bucketed separately
    and stays visible in the report rather than being counted as a fault.
    """
    out: dict[str, set[str]] = {}
    for path in _iter_py(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            note_failure("tools/evidence_gate.py", exc)
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "__all__" not in targets:
                continue
            names: set[str] = set()
            if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.add(elt.value)
            out[_rel(path, root)] = names
    return out


DISPATCH_READERS = {"get", "pop", "values", "items", "keys", "__getitem__"}


def _registry_dispatch(root: Path) -> dict[str, list[str]]:
    """Control names reachable through a dispatched registry (tier T2).

    A call graph cannot see `GATES = {"x": gate_x}` dispatched by
    `GATES.get(name)`: the call is `gate(...)`, where `gate` came out of a dict.
    That is real reachability, and calling it dead pushed a reader toward
    deleting a live gate.

    Crediting it is not a bypass because three conditions must hold at once:

      * the reference is a bare `Name` or `mod.attr` (a function object), not a
        string in prose -- a docstring or comment naming the gate satisfies
        nothing here;
      * it sits in a module-level container assignment (a registry), not an
        incidental inline literal;
      * the same file *reads that container for dispatch* -- a subscript, or a
        `.get/.pop/.values/.items/.keys` call on it. A registry nothing reads
        is not a dispatcher, so it does not grant credit.

    Registered names are returned in their own bucket and counted separately in
    the check's `measured` line, so the credit is visible rather than silent.
    """
    found: dict[str, list[str]] = {}
    for path in _iter_py(root):
        rel = _rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            note_failure("tools/evidence_gate.py", exc)
            continue
        containers: dict[str, tuple[int, list[str]]] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            value = node.value
            if not names or not isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
                continue
            refs: list[str] = []
            if isinstance(value, ast.Dict):
                elems = [e for e in list(value.keys) + list(value.values) if e is not None]
            else:
                elems = list(value.elts)
            for elt in elems:
                if isinstance(elt, ast.Name):
                    refs.append(elt.id)
                elif isinstance(elt, ast.Attribute):
                    refs.append(elt.attr)
            for cname in names:
                containers[cname] = (node.lineno, refs)
        if not containers:
            continue
        dispatched: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                dispatched.add(node.value.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in DISPATCH_READERS and isinstance(node.func.value, ast.Name):
                    dispatched.add(node.func.value.id)
            elif isinstance(node, ast.comprehension) and isinstance(node.iter, ast.Call):
                fn = node.iter.func
                if (isinstance(fn, ast.Attribute) and fn.attr in DISPATCH_READERS
                        and isinstance(fn.value, ast.Name)):
                    dispatched.add(fn.value.id)
        for cname in sorted(dispatched & set(containers)):
            line, refs = containers[cname]
            for ref in refs:
                found.setdefault(ref, []).append(f"{rel}:{line} registry {cname}")
    return found


def check_dead_controls(root: Path, baseline: dict) -> dict:
    definitions, usage = _scan_functions_with_quals(root)
    public = _module_public_names(root)
    dispatch = _registry_dispatch(root)
    dead: list[str] = []
    exported: list[str] = []
    registered: list[str] = []
    for (_mod, qual), info in sorted(definitions.items()):
        name = qual.rsplit(".", 1)[-1]
        if not (name.startswith(CONTROL_PREFIXES) or name in CONTROL_NAMES):
            continue
        if name.startswith("__"):
            continue
        # a hit in the defining file alone is not reachability: the def itself
        # puts the name there, so require another file or an explicit self-call.
        hits = _call_sites(name, info["file"], usage, span=info.get("span"))
        if hits:
            continue
        row = f"{info['file']}:{info['line']} {qual}"
        if dispatch.get(name):
            registered.append(f"{row} <- {dispatch[name][0]}")
        elif name in public.get(info["file"], set()):
            exported.append(row)
        else:
            dead.append(row)
    known = set(baseline.get("dead_control", []))
    new = sorted(set(dead) - known)
    return {
        "id": "dead_control",
        "status": "pass" if not new else "fail",
        "measured": (f"{len(dead)} unreachable control(s) ({len(new)} new), "
                     f"{len(exported)} exported-but-uncalled, "
                     f"{len(registered)} dispatch-registered (T2)"),
        "detail": ("control-named function with no module-linked caller outside "
                   "tests; exported names are reported separately because their "
                   "caller is outside this repo; dispatch-registered names sit in "
                   "a module-level registry this file actually reads, so the "
                   "caller is a dict lookup rather than a call site"),
        "offenders": new,
        "known": sorted(set(dead) & known),
        "exported_uncalled": sorted(exported),
        "dispatch_registered": sorted(registered),
    }


# --------------------------------------------------------------------------
# check: wired claims are backed by a module-linked caller
# --------------------------------------------------------------------------
def check_canon_pre_check(root: Path) -> dict:
    """The availability pre-check must exist in every canon gate copy.

    On 2026-10-31 SYSTEM_INSTRUCTIONS.md carried it, SOP.md carried it, and
    MANDATES.md -- the file agents actually quote -- did not. That state is the
    worst of the three: a reader following MANDATES is told to HALT and spawn an
    agent that has no implementation anywhere in the repo, so the only way to
    pass the gate is to recite. This check makes the divergence a gate failure
    instead of a discovery.
    """
    files = ["SYSTEM_INSTRUCTIONS.md", "MANDATES.md", "SOP.md"]
    phrases = {
        "pre-check block": "AVAILABILITY PRE-CHECK",
        "non-exemption clause": "UNAVAILABLE is never an exemption",
    }
    # Rules whose line must tell the reader what to do when the agent is absent.
    na_rules = ("RULE 0G", "RULE 0M", "RULE 0Q-A")
    problems: list[str] = []
    checked = 0
    for rel in files:
        path = root / rel
        if not path.exists():
            problems.append(f"{rel}: MISSING")
            continue
        checked += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, phrase in phrases.items():
            if phrase not in text:
                problems.append(f"{rel}: no {label} ({phrase!r})")
        for rule in na_rules:
            line = next((ln for ln in text.splitlines() if ln.startswith(f"- **{rule}")),
                        None)
            if line is None:
                continue          # this copy may not list the rule at all
            if "UNAVAILABLE" not in line and "ABSENT" not in line:
                problems.append(f"{rel}: {rule} line has no UNAVAILABLE/ABSENT branch")
    return {
        "id": "canon_pre_check",
        "status": "pass" if not problems else "fail",
        "measured": f"{checked}/{len(files)} canon files carry the pre-check",
        "detail": ("a canon copy that demands an agent with no mechanism forces the "
                   "reader to recite rather than run the check"),
        "offenders": problems,
    }


def check_wired_claims(root: Path, baseline: dict) -> dict:
    ledger = root / "NEURAL_MESH_WIRE_OR_CUT.json"
    if not ledger.exists():
        return {"id": "wired_claims", "status": "skip",
                "measured": "no ledger at repo root",
                "detail": "nothing to verify", "offenders": []}
    table = (json.loads(ledger.read_text(encoding="utf-8")) or {}).get("declared") or {}
    _, usage = _scan_functions_with_quals(root)
    unverified = []
    wired = 0
    for key, entry in sorted(table.items()):
        if str(entry.get("status")) != "wired":
            continue
        wired += 1
        mod, _, qual = key.partition(":")
        name = qual.rsplit(".", 1)[-1]
        linked = False
        for rel, info in usage.items():
            if rel.startswith("tests/") or name not in info["names"]:
                continue
            if _imports_module(info["imports"], mod):
                linked = True
                break
        if not linked:
            unverified.append(key)
    known = set(baseline.get("wired_token_only", []))
    new = sorted(set(unverified) - known)
    return {
        "id": "wired_claims",
        "status": "pass" if not new else "fail",
        "measured": f"{wired - len(unverified)}/{wired} wired rows module-linked, "
                    f"{len(unverified)} token-only ({len(new)} new)",
        "detail": "a `wired` row must be reached from a file that imports its module",
        "offenders": new,
        "known": sorted(set(unverified) & known),
    }


# --------------------------------------------------------------------------
# check: the mesh ledger is fresh
# --------------------------------------------------------------------------
def check_ledger_fresh(root: Path) -> dict:
    probe = root / "tools" / "mesh_function_preflight.py"
    if not probe.exists():
        return {"id": "ledger_freshness", "status": "skip",
                "measured": "probe not found", "detail": "", "offenders": []}
    try:
        proc = subprocess.run([sys.executable, str(probe), "--check"],
                              cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return {"id": "ledger_freshness", "status": "fail",
                "measured": "probe timed out", "detail": "cannot verify",
                "offenders": ["timeout"]}
    out = (proc.stdout or "") + (proc.stderr or "")
    stale = "STALE" in out
    return {
        "id": "ledger_freshness",
        "status": "fail" if stale else "pass",
        "measured": "STALE" if stale else "fresh",
        "detail": out.strip().splitlines()[0] if out.strip() else "no output",
        "offenders": [l for l in out.splitlines() if l.strip().startswith("!")][:10],
    }


# --------------------------------------------------------------------------
# check: memory store growth
# --------------------------------------------------------------------------
def check_store_growth(root: Path, cap_mb: int = 0, baseline: dict | None = None) -> dict:
    # 512 MB is the production target after the 2026-10-31 trim (1835 -> 196 MB),
    # not the 1536 MB stopgap that existed only while nothing bounded
    # `episode_complete`. A ceiling set at the current size would make growth
    # invisible up to it.
    if not cap_mb:
        cap_mb = int((baseline or {}).get("store_growth_max_mb") or 512)
    db = Path.home() / ".azazael" / "memory_store.db"
    if not db.exists():
        return {"id": "store_growth", "status": "skip",
                "measured": "no store", "detail": "", "offenders": []}
    size_mb = db.stat().st_size / 1e6
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        rows = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        con.close()
    except Exception as exc:  # noqa: BLE001
        return {"id": "store_growth", "status": "fail",
                "measured": f"{size_mb:.0f} MB (row count failed: {type(exc).__name__})",
                "detail": "", "offenders": [str(exc)[:120]]}
    return {
        "id": "store_growth",
        "status": "pass" if size_mb <= cap_mb else "fail",
        "measured": f"{size_mb:.0f} MB / {rows} rows (cap {cap_mb} MB)",
        "detail": "unbounded recorder growth was the 2 GB incident; retention now has a caller",
        "offenders": [] if size_mb <= cap_mb else [f"{size_mb:.0f} MB > {cap_mb} MB"],
    }


# --------------------------------------------------------------------------
# check: rotation is recorded, and staleness is visible
# --------------------------------------------------------------------------
def check_rotation(root: Path, max_age_days: int = 90) -> dict:
    if SecretRotator is None or _ROTATION_IMPORT_ERROR:
        return {"id": "rotation", "status": "fail",
                "measured": "import failed",
                "detail": "",
                "offenders": [_ROTATION_IMPORT_ERROR[:160]]}
    status = SecretRotator().rotation_status(max_age_days=max_age_days)
    if not status["keys_rotated"]:
        return {"id": "rotation", "status": "unverified",
                "measured": "0 rotations recorded",
                "detail": ("no successful rotation is recorded; that is absence of "
                           "evidence, not evidence that credentials are fresh"),
                "offenders": []}
    return {
        "id": "rotation",
        "status": "pass" if not status["stale"] else "fail",
        "measured": f"{status['keys_rotated']} key(s) rotated, {len(status['stale'])} stale",
        "detail": f"threshold {max_age_days}d",
        "offenders": [f"{s['name']} ({s['age_days']}d)" for s in status["stale"]],
    }


CHECKS = ("ast_parse_coverage", "dead_control", "canon_pre_check", "wired_claims",
          "ledger_freshness", "store_growth", "rotation")


def run_gate(root: Path = REPO, only: tuple[str, ...] | None = None) -> dict:
    baseline = {}
    bpath = root / "state" / "evidence_gate_baseline.json"
    if bpath.exists():
        try:
            baseline = json.loads(bpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            baseline = {}
    wanted = set(only or CHECKS)
    results = []
    if "ast_parse_coverage" in wanted:
        results.append(check_parse_coverage(root))
    if "dead_control" in wanted:
        results.append(check_dead_controls(root, baseline))
    if "canon_pre_check" in wanted:
        results.append(check_canon_pre_check(root))
    if "wired_claims" in wanted:
        results.append(check_wired_claims(root, baseline))
    if "ledger_freshness" in wanted:
        results.append(check_ledger_fresh(root))
    if "store_growth" in wanted:
        results.append(check_store_growth(root, baseline=baseline))
    if "rotation" in wanted:
        results.append(check_rotation(root))
    failed = [r for r in results if r["status"] == "fail"]
    unverified = [r for r in results if r["status"] == "unverified"]
    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "root": str(root),
        "checks": results,
        "failed": [r["id"] for r in failed],
        "unverified": [r["id"] for r in unverified],
        "ok": not failed,
    }


def _baseline_from(results: list[dict]) -> dict:
    out = {}
    for r in results:
        if r["id"] == "dead_control":
            out["dead_control"] = sorted(set(r.get("known", [])) | set(r.get("offenders", [])))
        if r["id"] == "wired_claims":
            out["wired_token_only"] = sorted(set(r.get("known", []))
                                             | set(r.get("offenders", [])))
        if r["id"] == "store_growth":
            # Accepted size is a ceiling, not a hiding place: the number stays
            # in the report and anything above it fails.
            mb = 0.0
            for tok in str(r.get("measured", "")).split():
                if tok[:1].isdigit() or tok[:1] == ".":
                    try:
                        mb = float(tok)
                        break
                    except ValueError as exc:
                        note_failure("tools/evidence_gate.py", exc)
                        continue
            out["store_growth_max_mb"] = int(math.ceil(max(mb, 512) / 100.0) * 100)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="evidence_gate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--only", default="", help="comma-separated check ids")
    ap.add_argument("--update-baseline", action="store_true",
                    help="record current debt as the accepted baseline (prints the diff)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    only = tuple(s for s in args.only.split(",") if s) or None
    verdict = run_gate(root, only)

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        for r in verdict["checks"]:
            mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP",
                    "unverified": "UNVERIFIED"}[r["status"]]
            print(f"[{mark:>10}] {r['id']:<18} {r.get('measured', '')}")
            if r.get("detail"):
                print(f"             {r['detail']}")
            for o in (r.get("offenders") or [])[:8]:
                print(f"             ! {o}")
            if r.get("known"):
                print(f"             ({len(r['known'])} known/accepted at baseline)")
        print()
        if verdict["ok"]:
            print("GATE: PASS" + (f" ({len(verdict['unverified'])} unverified)"
                                  if verdict["unverified"] else ""))
        else:
            print(f"GATE: FAIL -- {', '.join(verdict['failed'])}")

    if args.update_baseline:
        bpath = root / "state" / "evidence_gate_baseline.json"
        old = {}
        if bpath.exists():
            try:
                old = json.loads(bpath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                old = {}
        new = _baseline_from(verdict["checks"])
        bpath.parent.mkdir(parents=True, exist_ok=True)
        bpath.write_text(json.dumps(new, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nbaseline written: {bpath}")
        for k, v in new.items():
            if isinstance(v, list):
                added = sorted(set(v) - set(old.get(k, [])))
                print(f"  {k}: +{len(added)} newly accepted (total {len(v)})")
                for item in added[:10]:
                    print(f"    + {item}")
            else:
                print(f"  {k}: {old.get(k, '<unset>')} -> {v}")

    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
