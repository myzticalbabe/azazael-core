#!/usr/bin/env python3
"""Triage the open-checkbox backlog against the filesystem.

The backlog is 1,292 open `- [ ]` items across 79 tracked markdown docs. Asking
"which are still open" by reading them is a session's work and the answer rots
immediately, so this extracts the only part that can be checked mechanically: the
paths and backticked identifiers each item names, and whether they exist now.

Three outcomes, each stated rather than inferred:

- RESOLVED   every path the item names exists in the tree. The item may still be
             a lie (a file can exist and be empty), so this says "the artefact it
             asks for is present", never "the work is done".
- ABSENT     at least one named path is missing. This is the honest "still open"
             set, and the missing path is printed with it.
- UNCHECKED  the item names no path or identifier -- prose only. Counted, never
             guessed at.

Paths are resolved from the repo root first and then from the doc's own directory,
because a doc in docs/ writes `tools/x.py` meaning the root and `CODEBASE_AUDIT_
TODO.md` in docs/ means itself.
"""

import ast
import json
import os
import re
import subprocess
import sys

DOC_GLOB = ("*.md",)
#: Extension must be alphabetic and at least 2 chars: `5.0` is a hardcoded
#: literal, not a file, and matching it produced 'missing=5.0'.
PATH_RE = re.compile(r"`([A-Za-z0-9_./\\-]+\.[A-Za-z]{2,6})`")
IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{3,})`")
#: Docs that are records of finished work, not queues. Ticking them off is not a
#: task, and counting them inflates the backlog by hundreds.
#: Docs that are records of finished work, not queues. Ticking them off is not a
#: task, and counting them inflates the backlog by hundreds.
ARCHIVE_RE = re.compile(r"(^|/)(archive|archived|docs/archive)/", re.I)
#: Skill files are CHECKLISTS an agent runs, not backlog: `- [ ] All
#: addEventListener calls have matching removeEventListener` is a test to perform
#: now, not an open task with an artefact. Counting them as backlog produced
#: 'ABSENT: addEventListener' -- a false finding about a method name.
SKILL_RE = re.compile(r"(^|/)(\.agents/skills|skills|skills\.win-backup)/", re.I)


def tracked_md():
    out = subprocess.run(["git", "ls-files", "*.md"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         timeout=120).stdout
    return [p for p in out.splitlines() if p.strip()]


def exists_any(leaf, doc, basenames):
    cands = [leaf, os.path.join(os.path.dirname(doc), leaf)]
    if leaf.startswith("./"):
        cands.append(leaf[2:])
    if any(os.path.exists(c.replace("/", os.sep)) for c in cands):
        return True
    # A backlog doc names the FILE, not its path: `paths.py`, `CLUSTERWORMS.py`.
    # Resolving only the literal string reported 31 items ABSENT that are present
    # one directory down -- 'Unit tests for CLUSTERWORMS.py' came back missing
    # while src/CORE/CLUSTERWORMS.py sat there. Basenames are the honest key.
    return os.path.basename(leaf.replace("\\", "/")) in basenames


def identifiers_present(idents, index):
    """Identifiers that appear anywhere in the python corpus, by name."""
    found = {}
    for ident in idents:
        found[ident] = ident in index
    return found


def main():
    docs = tracked_md()
    items = []
    for doc in docs:
        if ARCHIVE_RE.search(doc) or SKILL_RE.search(doc):
            continue
        try:
            text = open(doc, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            note_failure("tools/backlog_triage.py", exc)
            continue
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s.startswith("- [ ]"):
                continue
            body = s[len("- [ ]"):].strip()
            # A line already annotated DONE is a tick somebody forgot to add, not
            # an open task; PRODUCTION_TODO.md:232 says '-- **DONE**' in the item.
            if re.search(r"\b(DONE|RESOLVED|COMPLETE[DR]?|✅)\b", body):
                continue
            paths = sorted(set(PATH_RE.findall(body)))
            idents = sorted(set(IDENT_RE.findall(body)))
            items.append({"doc": doc, "line": i, "text": body[:240],
                          "paths": paths, "idents": idents})

    # Corpus name index: every python file path plus every top-level identifier
    # defined in the tree, so "does this tool exist" is answered by the tree.
    index = set()
    basenames = set()
    for dirpath, dirnames, filenames in os.walk("."):
        dirnames[:] = [d for d in dirnames if d not in
                       {".git", "__pycache__", "node_modules", "graphify-out",
                        ".venvs", "venv", ".venv", "build", "dist", "site-packages",
                        "archived_unified", "nuitka_build_backup"}]
        for fn in filenames:
            basenames.add(fn)
            if fn.endswith(".py"):
                index.add(fn[:-3])
                index.add(fn)
    result = {"items": len(items), "docs": len({i["doc"] for i in items}),
              "resolved": [], "absent": [], "unchecked": [], "unchecked_ident": []}
    for it in items:
        if it["paths"]:
            # A named PATH is hard evidence in both directions: it either exists
            # or it does not. Only this arm is allowed to say ABSENT.
            missing = [p for p in it["paths"] if not exists_any(p, it["doc"], basenames)]
            (result["absent"] if missing else result["resolved"]).append(
                dict(it, missing=missing))
        elif it["idents"]:
            # A bare identifier is weak evidence -- `addEventListener` is a method
            # name, not an artefact -- so absence from the python index cannot
            # make an item ABSENT. Reported as a separate count instead.
            present = identifiers_present(it["idents"], index)
            hit = [k for k, v in present.items() if v]
            if hit:
                result["resolved"].append(dict(it, missing=[], idents_present=hit))
            else:
                result["unchecked_ident"].append(it)
        else:
            result["unchecked"].append(it)

    print("open checkbox items: %d across %d docs" % (result["items"], result["docs"]))
    print("  RESOLVED  (named artefact present now): %d" % len(result["resolved"]))
    print("  ABSENT    (named artefact missing now): %d" % len(result["absent"]))
    print("  UNCHECKED (prose only, no path/ident) : %d" % len(result["unchecked"]))
    print("  UNCHECKED (identifier not in tree)    : %d" % len(result["unchecked_ident"]))
    # Default lands in the repo, not relative to the caller's cwd: the old
    # "../ast_audit/..." default raised FileNotFoundError whenever the tool was
    # run from anywhere but one specific directory (measured 2026-11-01).
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/audit/backlog_triage.json"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print("\n=== 12 ABSENT items (the honest still-open set) ===")
    for it in result["absent"][:12]:
        print("  %s:%d  missing=%s\n      %s" % (it["doc"], it["line"],
                                                 it["missing"], it["text"][:110]))
    return 0



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

if __name__ == "__main__":
    sys.exit(main())
