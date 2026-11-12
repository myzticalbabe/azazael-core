# coding: utf-8
"""Do the ledger's `wired` declarations have evidence a reader can check?

RULE this tool realises. `NEURAL_MESH_WIRE_OR_CUT.json` declares a status per
unreferenced mesh function. `wired` claims a production caller exists, and
`tests/test_mesh_wire_or_cut.py` asserts that PF16 does not fire on those rows
-- but PF16 silence is not a caller (2026-10-19 measurement: 136 of 137 `wired`
rows carry `module-linked: no` in their own evidence, i.e. no static caller was
found). A status whose only support is another instrument's silence is an
over-claim, and an over-claim is unreviewable.

So each `wired` row is classified by the strongest link that can be found:

  REGISTRY  the defining module registers the class with `@register_worm`, or
            names it in a registry table -- the row is reachable through
            dynamic dispatch, which is what `wired` means for a worm.
  MODULE    some other module imports the class or calls it, so a static
            reference exists. The evidence text says which ("imports" beats
            "names"), because a bare name in a type annotation is weaker than
            a call and the record should not flatten the difference.
  NONE      neither. The row's `wired` claim rests on PF16 silence alone.

`--apply` writes `link_evidence` into the ledger for REGISTRY and MODULE rows,
naming the file that carries the link, and rewrites `reason` for NONE rows to
say what is missing. It never invents a caller: a row it cannot link keeps its
status and gains a `link_gap` field instead, so the claim and its support sit
in the same record.

`--check` exits 1 when any `wired` row has neither `link_evidence` nor
`link_gap` -- i.e. when somebody added a wired declaration without saying why.

What this does NOT do: it does not prove a dispatch path actually invokes the
method. It locates the registration and the reference; the call-graph proof for
dynamic dispatch is out of scope, and the tool says `registry` rather than
`called` for exactly that reason.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

#: Files the classifier could not read, surfaced by main() rather than
#: swallowed: an unreadable module is a missing piece of evidence, and a run
#: that silently scored it "no link" would be an accusation without a witness.
_UNREADABLE: "list[str]" = []

LEDGER = Path(__file__).resolve().parent.parent / "NEURAL_MESH_WIRE_OR_CUT.json"
ROOTS = ("src", "scripts", "tools")
SKIP_PARTS = {".venv", "__pycache__", "archived", ".build", "node_modules"}


def _py_files() -> list[Path]:
    out: list[Path] = []
    for root in ROOTS:
        base = LEDGER.parent / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            out.append(path)
    return out


def build_index(files: list[Path]) -> tuple[dict, dict, dict]:
    """name -> defining file, and name -> {file: (rank, verb, lineno)}.

    References are collected through the AST, not by matching lines: the
    first version scanned line text and scored `NeuralPipeline.py:7` -- a
    docstring sentence naming `SchemaAwareFeatureExtractor` -- as a module
    link. Prose is not a caller, and an instrument that cannot tell them
    apart manufactures the support it is supposed to be measuring for.
    """
    classes: dict[str, Path] = {}
    funcs: dict[str, Path] = {}
    # rank 1 = call, 2 = import, 3 = bare name (weakest); lower wins.
    refs: dict[str, dict[Path, tuple[int, str, int]]] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError) as exc:
            _UNREADABLE.append("%s: %s" % (path, exc))
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, path)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(node.name, path)
        local: dict[str, tuple[int, str, int]] = {}
        for node in ast.walk(tree):
            for name, rank, verb in _references(node):
                seen = local.get(name)
                if seen is None or rank < seen[0]:
                    local[name] = (rank, verb, node.lineno)
        for name, hit in local.items():
            refs.setdefault(name, {})[path] = hit
    return classes, funcs, refs


def _references(node: ast.AST) -> list[tuple[str, int, str]]:
    """(name, rank, verb) for the reference this AST node makes, if any."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        out = []
        for alias in node.names:
            out.append((alias.name.split(".")[-1], 2, "imports"))
            if alias.asname:
                out.append((alias.asname, 2, "imports"))
        return out
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return [(func.id, 1, "calls")]
        if isinstance(func, ast.Attribute):
            return [(func.attr, 1, "calls")]
        return []
    if isinstance(node, ast.Name):
        return [(node.id, 3, "names")]
    if isinstance(node, ast.Attribute):
        return [(node.attr, 3, "names")]
    return []


def registry_link(cls: str, path: Path) -> str:
    """Evidence string when the defining module registers the class, else ''."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (SyntaxError, OSError) as exc:
        _UNREADABLE.append("%s: %s" % (path, exc))
        return ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != cls:
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name == "register_worm":
                return f"{path.as_posix()}:{node.lineno} @register_worm on {cls}"
    # A registry table naming the class is the other dispatch route.
    for line_no, line in enumerate(text.splitlines(), 1):
        if cls in line and ("REGISTRY" in line or "registry[" in line):
            return f"{path.as_posix()}:{line_no} registry table names {cls}"
    return ""


def module_link(cls: str, owner: Path, refs: dict) -> str:
    """Evidence string when another module calls or imports the class, else ''."""
    best: tuple[int, str, Path, int] | None = None
    for path, (rank, verb, line_no) in (refs.get(cls) or {}).items():
        if path == owner:
            continue
        if best is None or rank < best[0]:
            best = (rank, verb, path, line_no)
    if best is None:
        return ""
    verb, path, line_no = best[1], best[2], best[3]
    return f"{path.as_posix()}:{line_no} {verb} {cls}"


def classify(table: dict, files: list[Path]) -> list[dict]:
    classes, funcs, refs = build_index(files)
    rows = []
    for key, row in sorted((table.get("declared") or {}).items()):
        if row.get("status") != "wired":
            continue
        qual = key.split(":", 1)[-1]
        head = qual.split(".")[0]
        owner = classes.get(head) or funcs.get(head)
        if owner is None:
            rows.append({"id": key, "verdict": "NONE", "evidence": "",
                         "detail": "defining module not found in %s" % (ROOTS,)})
            continue
        try:
            rel = owner.relative_to(LEDGER.parent)
        except ValueError:  # a fixture outside the repo (tests)
            rel = owner
        evidence = registry_link(head, owner)
        if evidence:
            rows.append({"id": key, "verdict": "REGISTRY",
                         "evidence": evidence, "detail": rel.as_posix()})
            continue
        evidence = module_link(head, owner, refs)
        if evidence:
            rows.append({"id": key, "verdict": "MODULE",
                         "evidence": evidence, "detail": rel.as_posix()})
        else:
            rows.append({"id": key, "verdict": "NONE", "evidence": "",
                         "detail": rel.as_posix()})
    return rows


def apply(table: dict, rows: list[dict]) -> int:
    declared = table.get("declared") or {}
    touched = 0
    for row in rows:
        target = declared.get(row["id"])
        if target is None:
            continue
        if row["verdict"] in ("REGISTRY", "MODULE"):
            if target.get("link_evidence") != row["evidence"]:
                target["link_evidence"] = row["evidence"]
                target["link_kind"] = row["verdict"].lower()
                target.pop("link_gap", None)
                touched += 1
        else:
            gap = ("no registry registration and no reference outside %s; the "
                   "wired claim rests on PF16 silence alone" % row["detail"])
            if target.get("link_gap") != gap:
                target["link_gap"] = gap
                touched += 1
            # A run that now finds no link must retract an earlier claim, or
            # the record keeps asserting support that no longer exists.
            if "link_evidence" in target or "link_kind" in target:
                target.pop("link_evidence", None)
                target.pop("link_kind", None)
                touched += 1
    return touched


def main(argv: "list[str] | None" = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in args
    do_apply = "--apply" in args
    table = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = classify(table, _py_files())
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    wired = sum(counts.values())
    print("wire evidence -- %d wired declaration(s)" % wired)
    for verdict in ("REGISTRY", "MODULE", "NONE"):
        print("  %-9s %d" % (verdict, counts.get(verdict, 0)))
    gaps = [r for r in rows if r["verdict"] == "NONE"]
    for row in gaps[:15]:
        print("  GAP %s  (%s)" % (row["id"], row["detail"]))
    for note in _UNREADABLE[:5]:
        print("  unreadable: %s" % note)
    if do_apply:
        touched = apply(table, rows)
        LEDGER.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        print("applied: %d declaration record(s) updated" % touched)
    if check:
        # ANY wired row, not just the ones a link was found for: a row with no
        # link and no gap is exactly the unannotated claim this gate exists to
        # catch, and the first version of this check let one through (a probe
        # row whose defining module does not exist passed at exit 0).
        unrecorded = [row for row in rows
                      if not (table["declared"][row["id"]].get("link_evidence")
                              or table["declared"][row["id"]].get("link_gap"))]
        if unrecorded:
            print("FAIL: %d wired row(s) record neither a link nor a gap -- "
                  "re-run --apply" % len(unrecorded))
            for row in unrecorded[:10]:
                print("  %s  (%s)" % (row["id"], row["detail"]))
            return 1
        print("OK: every wired row carries its link evidence or a named gap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
