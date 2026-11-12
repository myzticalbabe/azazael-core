#!/usr/bin/env python3
"""AST audit of one codebase root -- run it IN the environment it describes.

One root per invocation, one JSON out. The Windows lane, the Ubuntu mirror and
the Kali mirror describe three different trees and must never be merged into one
number, so this prints what it scanned and refuses nothing: a root that cannot be
read is reported as unreadable rather than as clean.

Design notes, each bought by a specific failure in this tree:

- Standard library only, and no 3.10+ syntax: the Kali and Ubuntu lanes do not
  run the 3.15t Windows interpreter, so a script that needs it can only audit the
  environment that has it. That is the opposite of "all environments".
- Pruned walk on NAMES, not paths: `.venvs`, `site-packages`, `node_modules`,
  `archived` are pruned wherever they appear, because a vendored copy counted as
  first-party code is how a 17k-line tree reports 300k.
- Markers are read from the source TEXT, not the AST: a comment is not a node, and
  TODO/FIXME live exclusively in comments and docstrings.
- Every silent-failure class is counted separately (`except: pass`,
  `except X: continue`, bare `except:`) because collapsing them into "exceptions"
  makes the one that eats errors invisible.
- Duplicate content is recorded by SHA-256 across the whole root, so the same file
  existing in two environments shows up as a fact rather than as two trees.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import sys

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "site-packages",
    "dist-packages", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".eggs", ".venv", "venv", ".venvs", "target", "graphify-out", "archived",
    "archive", ".kali_staging", ".conan", ".cache", ".pams_venv",
    # Generated trees. A first pass without these read the Kali mirror as 1,944
    # Python files, because `build/lib/` is a compiled copy of `src/`; counting
    # it inflates every number and manufactures duplicates that are one file.
    "build", "dist", "sdist",
}

MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK|BUG|DEPRECATED|TEMP)\b[:\s-]*(.{0,140})")

#: Language -> extension. Python is the only one parsed; the rest are counted,
#: because "0 modules" and "not parsed by this tool" are different facts.
OTHER_EXT = {".rs": "rust", ".go": "go", ".js": "javascript", ".mjs": "javascript",
             ".ts": "typescript", ".tsx": "typescript", ".sh": "shell",
             ".c": "c", ".h": "c", ".cpp": "cpp", ".cu": "cuda", ".zig": "zig"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


SKIPPED_DEVICE = []


#: Windows device names. A repo that once held a stray `nul` breaks the walk:
#: relpath() resolved it to the \\.\nul device and raised ValueError, which killed
#: the whole G: audit at the first such file. Such an entry is a filesystem
#: artefact, not code -- skipped and counted, never silently dropped.
DEVICE_NAMES = {"nul", "con", "prn", "aux", "com1", "com2", "com3", "com4",
                "lpt1", "lpt2", "lpt3"}


def walk(root):
    """Yield (path, relpath) for every file, pruning by directory name."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.lower() in DEVICE_NAMES:
                SKIPPED_DEVICE.append(os.path.join(dirpath, fn))
                continue
            full = os.path.join(dirpath, fn)
            try:
                rel = os.path.relpath(full, root)
            except (ValueError, OSError):
                SKIPPED_DEVICE.append(full)
                continue
            yield full, rel


def audit(root, marker_cap=4000):
    res = {
        "root": root,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "files_total": 0,
        "other_languages": {},
        "py_files": 0,
        "py_parsed": 0,
        "syntax_errors": [],
        "unreadable": [],
        "loc": 0,
        "classes": 0,
        "functions": 0,
        "async_functions": 0,
        "methods": 0,
        "mains": 0,
        "bare_except": [],
        "except_pass": [],
        "except_continue": [],
        "except_return_none": 0,
        "eval_exec": [],
        "open_no_encoding": [],
        "star_import": [],
        "type_ignore": 0,
        "noqa": 0,
        "markers": [],
        "markers_total": 0,
        "markers_by_kind": {},
        "duplicate_content": [],
        "skipped_device_paths": [],
        "skip_dirs": sorted(SKIP_DIRS),
        "imports": {},
        "modules": [],
    }
    hashes = {}
    for path, rel in walk(root):
        res["files_total"] += 1
        ext = os.path.splitext(path)[1].lower()
        if ext in OTHER_EXT:
            lang = OTHER_EXT[ext]
            res["other_languages"][lang] = res["other_languages"].get(lang, 0) + 1
            continue
        if ext != ".py":
            continue
        res["py_files"] += 1
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            res["unreadable"].append({"path": rel, "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        dig = sha(raw)
        hashes.setdefault(dig, []).append(rel)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except Exception:
                res["unreadable"].append({"path": rel, "error": "cannot decode"})
                continue
        res["loc"] += text.count("\n") + 1
        if "# type: ignore" in text:
            res["type_ignore"] += text.count("# type: ignore")
        if "noqa" in text:
            res["noqa"] += text.count("noqa")
        # markers from text: comments are not AST nodes
        for i, line in enumerate(text.splitlines(), 1):
            m = MARKER_RE.search(line)
            if not m:
                continue
            kind = m.group(1)
            res["markers_total"] += 1
            res["markers_by_kind"][kind] = res["markers_by_kind"].get(kind, 0) + 1
            if len(res["markers"]) < marker_cap:
                res["markers"].append({"path": rel, "line": i, "kind": kind,
                                       "text": line.strip()[:200]})
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            res["syntax_errors"].append({"path": rel, "line": exc.lineno,
                                         "msg": str(exc.msg)[:160]})
            continue
        res["py_parsed"] += 1
        modname = rel[:-3].replace(os.sep, ".")
        if modname.endswith(".__init__"):
            modname = modname[: -len(".__init__")]
        res["modules"].append(modname)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                res["classes"] += 1
                res["methods"] += sum(1 for c in node.body
                                      if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)))
            elif isinstance(node, ast.AsyncFunctionDef):
                res["async_functions"] += 1
            elif isinstance(node, ast.FunctionDef):
                res["functions"] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imports.append(a.name.split(".")[0])
                else:
                    if node.module:
                        imports.append(node.module.split(".")[0])
                    if any(a.name == "*" for a in node.names):
                        res["star_import"].append({"path": rel, "line": node.lineno,
                                                   "module": node.module})
            elif isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name in ("eval", "exec"):
                    res["eval_exec"].append({"path": rel, "line": node.lineno, "call": name})
                elif name == "open":
                    mode = ""
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    has_enc = any(kw.arg == "encoding" for kw in node.keywords)
                    if not has_enc and "b" not in mode:
                        res["open_no_encoding"].append({"path": rel, "line": node.lineno,
                                                        "mode": mode or "r"})
            elif isinstance(node, ast.Try):
                for h in node.handlers:
                    if h.type is None:
                        res["bare_except"].append({"path": rel, "line": h.lineno})
                    body = h.body
                    if len(body) == 1 and isinstance(body[0], ast.Pass):
                        res["except_pass"].append({"path": rel, "line": h.lineno})
                    elif len(body) == 1 and isinstance(body[0], ast.Continue):
                        res["except_continue"].append({"path": rel, "line": h.lineno})
                    elif (len(body) == 1 and isinstance(body[0], ast.Return)
                          and (body[0].value is None
                               or (isinstance(body[0].value, ast.Constant)
                                   and body[0].value.value is None))):
                        res["except_return_none"] += 1
            if isinstance(node, ast.If):
                t = node.test
                if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                        and t.left.id == "__name__"):
                    res["mains"] += 1
        res["imports"][rel] = sorted(set(imports))

    dup = [sorted(v) for v in hashes.values() if len(v) > 1]
    dup.sort(key=lambda v: (-len(v), v[0]))
    res["duplicate_content"] = dup[:400]
    res["duplicate_groups"] = len(dup)
    res["duplicate_files"] = sum(len(v) for v in dup)

    # Orphan modules: parsed, never imported by any other module in this root,
    # not a package __init__, not a test. Reported as a list, not a verdict --
    # an entry point is legitimately imported by nothing.
    imported = set()
    for rel, names in res["imports"].items():
        imported.update(names)
    orphans = []
    for mod in res["modules"]:
        base = mod.split(".")[-1]
        if base == "__init__" or mod.endswith("__main__"):
            continue
        if base.startswith("test_") or base.endswith("_test"):
            continue
        if base in imported:
            continue
        orphans.append(mod)
    res["orphan_modules"] = sorted(orphans)
    res["orphan_count"] = len(orphans)
    res["skipped_device_paths"] = SKIPPED_DEVICE[:50]
    res["skipped_device_count"] = len(SKIPPED_DEVICE)
    del res["imports"]
    del res["modules"]
    return res


def main():
    ap = argparse.ArgumentParser(description="AST audit one codebase root.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        print("ROOT ABSENT: %s" % a.root)
        json.dump({"root": a.root, "absent": True, "label": a.label},
                  open(a.out, "w", encoding="utf-8"), indent=1)
        return 2
    res = audit(a.root)
    res["label"] = a.label or os.path.basename(a.root.rstrip("/\\"))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    # py= is every .py found, parsed= is the subset that parsed. Both bound to
    # py_parsed for one session, which made the console under-report by exactly
    # the syntax-error count (G: 2,817 vs the JSON's 2,824 on 2026-11-01) and read
    # as drift between two runs of one tool over one tree.
    print("%-28s py=%d parsed=%d syntax_err=%d loc=%d cls=%d fn=%d methods=%d "
          "bare_except=%d except_pass=%d except_continue=%d markers=%d orphans=%d dupgroups=%d"
          % (res["label"], res["py_files"], res["py_parsed"], len(res["syntax_errors"]),
             res["loc"], res["classes"], res["functions"], res["methods"],
             len(res["bare_except"]), len(res["except_pass"]), len(res["except_continue"]),
             res["markers_total"], res["orphan_count"], res["duplicate_groups"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
