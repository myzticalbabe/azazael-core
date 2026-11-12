#!/usr/bin/env python3
"""orphan_triage.py — classify `orphan_scan.py`'s unreachable modules.

`tools/orphan_scan.py` answers *"is this module reachable from an entry point?"*
and prints 350 unreachable rows. That is a worklist, not a disposition: an
application's own `main` module is unimported **by design**, while a library
module nothing reaches is dead capability. This tool draws that distinction so the
list can be acted on.

FOUR CLASSES, in priority order:

  APP-ENTRY   the module has an `if __name__ == "__main__"` guard (or a top-level
              `main()` call), so it is a PROGRAM shipped to be executed. Being
              unimported is correct behaviour, not a defect. Nothing to do.
  DYNAMIC     the module's basename appears as a STRING LITERAL somewhere in the
              tree — an `importlib.import_module("X")`, a registry row, a
              subprocess `-c` payload. `orphan_scan` documents that it cannot see
              these (`--dynamic` counts them); they are reachability blind spots,
              not orphans. Each row names where the string was seen.
  TEST-ONLY   a COLLECTED test imports it -- pytest runs that file. Reachable
              from the suite, invisible to production. A real finding, with a
              different remedy from DEAD.
  TEST-INERT  only an UNCOLLECTED test imports it (see `_pytest_testpaths`).
              `src/tests/` holds test files pytest never collects, so importing
              one is evidence of intent, NOT of coverage. Kept distinct from
              TEST-ONLY on purpose: merging them let 7 live modules read as
              "reachable from the suite" when nothing executes their tests.
  SCRIPT-DIR  a package whose directory holds MORE program files
              (`.bat`/`.cmd`/`.ps1`/`.sh`) than `.py` modules. `src/SHORTCUTS/`
              is 27 shells beside one `__init__.py`: nothing imports it because
              nothing can, so being unimported is correct, not dead capability.
              The dominance test matters: `CODEX` (18 .py, 5 .bat) is a library
              with a stray script and must not be excused as an asset dir.
  DEAD        none of the above. Nothing imports it, nothing names it, it is not a
              program. This is the genuine worklist.

SOURCE OF TRUTH: this tool consumes `orphan_scan --json`; it does not re-implement
reachability. Run `python tools/orphan_scan.py --json state/orphan_scan.json`
first. If that file is missing this tool refuses rather than reporting an empty
triage -- an absent input is not a clean bill of health.

Usage:
    python tools/orphan_scan.py --json state/orphan_scan.json
    python tools/orphan_triage.py                    # summary + counts
    python tools/orphan_triage.py --md OUT.md        # also write the report
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

#: Tolerance on the module-count comparison. `orphan_scan`'s candidate set is
#: `.py` files under `src/` minus `__pycache__`; this tool's proxy count runs one
#: high on this tree (708 vs 707), so an exact test would cry stale on every run.
#: Wide enough to absorb that off-by-one, narrow enough to catch the real case
#: it was written for: a tracked scan reporting 701 against a 708-file tree.
STALE_TOLERANCE = 5

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
DEFAULT_JSON = REPO / "state" / "orphan_scan.json"

#: A module is an APP-ENTRY if it carries the conventional guard. Matched loosely
#: on purpose (`__name__` compared to `"__main__"` in either quote style, with or
#: without spaces) because the tree uses both.
MAIN_GUARD = re.compile(r"""__name__\s*==\s*['"]__main__['"]""")

#: Suffixes that make a directory a PROGRAM's asset directory rather than a
#: library of importable symbols.
PROGRAM_SUFFIXES = (".bat", ".cmd", ".ps1", ".sh")

#: Directories that are not this repository's own code and must not be searched
#: when looking for dynamic references or for test importers.
SKIP_PARTS = {"__pycache__", "archive", "archived", "archived_unified",
              "site-packages", "node_modules", ".build", ".venvs",
              "nuitka_build_backup", "dist", "build",
              # GENERATED output, not code: graphify's index quotes every module
              # path as a string literal, so counting it as a dynamic reference
              # classed whole families (`AOA.arms.*`) DYNAMIC off a cache that
              # imports nothing -- and a cache rewritten on every graph run, so
              # the class was not even stable between two scans of one tree.
              "graphify-out"}


def _tree_module_count() -> int:
    """`.py` files under `src/` ignoring `__pycache__` -- the scan's candidate set."""
    return len([p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts])


def _stale(data: dict, jp: Path) -> bool:
    """True when the scan's module count describes a different-size tree."""
    recorded = data.get("n_modules")
    if not isinstance(recorded, int):
        return True                      # no provenance at all is worth flagging
    return abs(recorded - _tree_module_count()) > STALE_TOLERANCE


def _dotted_to_path(mod: str) -> Path:
    """`CORE.WORMS.x` -> `src/CORE/WORMS/x.py` (package fallback to `__init__`)."""
    p = SRC / (mod.replace(".", "/") + ".py")
    if p.is_file():
        return p
    return SRC / mod.replace(".", "/") / "__init__.py"


#: The literal pattern a dynamic import must match. A module counts as DYNAMIC
#: only when a quoted literal equals its FULL dotted path or is a prefix of it
#: (`f"CORE.technique_wiring.{name}"` quotes the prefix). Matching on the bare
#: basename was the first version's defect: `"config"` and `"version"` are common
#: words, so 73 rows scored DYNAMIC and `TEST-ONLY` collapsed to a false zero.
def _collect_string_literals() -> dict[str, list[tuple[str, int]]]:
    """Map each dotted string literal in the tree to where it was seen.

    One pass over the corpus, not one grep per orphan: 350 modules x a full-tree
    grep is minutes, this is seconds.
    """
    seen: dict[str, list[tuple[str, int]]] = defaultdict(list)
    pat = re.compile(r"""['"]([A-Za-z_][A-Za-z0-9_.]{2,})['"]""")
    for f in REPO.rglob("*.py"):
        parts = set(f.parts)
        if SKIP_PARTS & parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(REPO)).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), 1):
            for lit in pat.findall(line):
                seen[lit].append((rel, i))
    return seen


def _pytest_testpaths() -> tuple[str, ...]:
    """The directories pytest actually collects, read from `pytest.ini`.

    `tests` is this tree's value as of 2026-10-15. The point is not to hardcode
    it: a test file OUTSIDE these paths is never executed, so importing one is
    NOT evidence of coverage. Without this, a module whose only importer is an
    uncollected test was reported DEAD with the false evidence "nothing imports
    it, nothing names it" -- false on its face, and it hid 7 live modules.
    """
    ini = REPO / "pytest.ini"
    if not ini.is_file():
        # Not a pass and not a swallow: an absent pytest.ini means the collection
        # set is a guess, so say so on the way to the documented default. A
        # present-but-unreadable file is left to raise, which this tool's own
        # doctrine prefers (an unusable input is not a clean bill of health).
        print("orphan_triage: no pytest.ini at %s; assuming testpaths=%s"
              % (ini, ("tests",)), file=sys.stderr)
        return ("tests",)
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "testpaths":
            got = tuple(v.strip().rstrip(",") for v in value.split() if v.strip())
            if got:
                return got
    return ("tests",)


def _collect_tests() -> list[tuple[str, str, bool]]:
    """Every test file in the tree as (relpath, text, collected_by_pytest).

    Scans EVERY directory named `tests`, not just the repo-root one. `src/tests/`
    holds 12 test files (7 of them `test_ml_*.py`) that no earlier version of
    this tool could see, so 7 live modules were classed DEAD -- and three of
    them (`ML.nsga`, `ML.estimators`, `ML.gosp`) are imported by named test
    files whose own headers say so. Read once, not per orphan.
    """
    collected = tuple(d.rstrip("/") + "/" for d in _pytest_testpaths())
    out: list[tuple[str, str, bool]] = []
    for d in REPO.rglob("tests"):
        if not d.is_dir() or SKIP_PARTS & set(d.parts):
            continue
        for f in sorted(d.rglob("*.py")):
            try:
                rel = str(f.relative_to(REPO)).replace("\\", "/")
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append((rel, text, rel.startswith(collected)))
    return out


def _import_forms(mod: str) -> tuple[re.Pattern, ...]:
    """The import STATEMENTS that count as a real edge to `mod`.

    A statement match, deliberately not a name match. The suite is part of this
    tool's evidence base, so a test file that merely NAMES a module must not be
    read as importing it: on 2026-10-15 this repo's own `tests/test_orphan_triage.py`
    named seven modules, and the previous tail+parent heuristic flipped
    `ML.nsga` from TEST-INERT to TEST-ONLY purely because a new test mentioned
    it. An instrument whose answer about a module moves when an unrelated test
    is added is not measuring imports.

    The four forms are the ones the tree actually uses:
      `import a.b`  `from a.b import x`  `from a import b`  `import_module("a.b")`
    """
    esc = re.escape(mod)
    parent, _, tail = mod.rpartition(".")
    forms = [
        # `import a.b`, and `import a.b as x`; `.` is included so a deeper
        # submodule import still counts, while `a.b_c` does NOT match `a.b`.
        re.compile(r"(?m)^\s*import\s+%s(?:\s|,|$|#|\.)" % esc),
        re.compile(r"(?m)^\s*from\s+%s\s+import\s" % esc),
        re.compile(r"import_module\(\s*['\"]%s['\"]" % esc),
    ]
    if parent:
        forms.append(re.compile(
            r"(?m)^\s*from\s+%s\s+import\s+[^\n#]*\b%s\b"
            % (re.escape(parent), re.escape(tail))))
    return tuple(forms)


def _test_import_of(
    mod: str, tests: list[tuple[str, str, bool]]
) -> tuple[str, bool] | None:
    """Return (test file, collected) for the file that imports `mod`, or None.

    The importer's collection status travels with the hit: a hit from a file
    pytest never collects is a different finding, and worse than a missing one
    (TEST-INERT).
    """
    forms = _import_forms(mod)
    tail_re = re.compile(r"\b%s\b" % re.escape(mod.rpartition(".")[2]))
    for rel, text, collected in tests:
        if not tail_re.search(text):      # cheap prefilter, keeps this linear
            continue
        if any(f.search(text) for f in forms):
            return rel, collected
    return None


def classify(orphans: list[str]) -> list[tuple[str, str, str]]:
    """Classify orphan modules as (class, module, evidence), one row each.

    Extracted from `main` so the classification can be exercised DIRECTLY on
    real modules by `tests/test_orphan_triage.py`. Reading the classes off a
    full scan's markdown is not a test: it re-asserts whatever the scan last
    wrote, which is how the `ML.nsga` false DEAD survived a full sweep.
    """
    lits = _collect_string_literals()
    tests = _collect_tests()

    rows: list[tuple[str, str, str]] = []   # class, module, evidence
    for mod in orphans:
        p = _dotted_to_path(mod)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            rows.append(("MISSING", mod, "no file at %s" % p))
            continue

        # 1. A program's own main module is unimported by design.
        if MAIN_GUARD.search(text):
            rows.append(("APP-ENTRY", mod, "__name__ == '__main__'"))
            continue

        # 2. So is a package that is a directory of shell scripts. Restricted to
        #    packages (`__init__.py`) so an ordinary module that merely sits in a
        #    directory with a stray .sh is not reclassified.
        if p.name == "__init__.py" and p.parent.is_dir():
            siblings = [f for f in p.parent.iterdir() if f.is_file()]
            progs = sorted(f.name for f in siblings
                           if f.suffix.lower() in PROGRAM_SUFFIXES)
            n_py = sum(1 for f in siblings if f.suffix == ".py")
            # The shells must DOMINATE, or this is a library with a stray
            # script: `CODEX` (18 .py, 5 .bat) and `CODEX.VIDEOS` (22 .py, 1
            # .bat) are libraries, and excusing them here would hide whatever
            # their real class is. Only a thin facade over shells qualifies.
            if progs and len(progs) > n_py:
                rows.append(("SCRIPT-DIR", mod,
                             "%d program file(s) vs %d .py (e.g. %s)"
                             % (len(progs), n_py, progs[0])))
                continue

        # 3. A definite import edge from the suite outranks a speculative string
        #    reference; `orphan_scan` tags this case `[test-only]`.
        t = _test_import_of(mod, tests)
        if t:
            rel, collected = t
            if collected:
                rows.append(("TEST-ONLY", mod, rel))
            else:
                rows.append(("TEST-INERT", mod,
                             "%s (NOT collected: pytest.ini testpaths=%s)"
                             % (rel, ", ".join(_pytest_testpaths()))))
            continue

        # 4. A dynamic import the scanner cannot see. Compared against the FULL
        #    dotted path so common basenames do not qualify.
        own = str(p.relative_to(REPO)).replace("\\", "/")
        dyn = []
        for lit, sites in lits.items():
            if lit == mod or lit.startswith(mod + ".") or mod.startswith(lit + "."):
                dyn += [(r, l) for r, l in sites if r != own]
        if dyn:
            r, l = sorted(dyn)[0]
            rows.append(("DYNAMIC", mod, "%s:%d (string literal)" % (r, l)))
            continue

        rows.append(("DEAD", mod, "nothing imports it, nothing names it"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # REQUIRED, with no default. An earlier version defaulted to
    # `state/orphan_scan.json`, which is a TRACKED artifact from 2026-10-10 that
    # describes a 701-module tree while HEAD has 707 -- so a bare run triaged a
    # four-day-old census and reported it as current. A default input is a
    # cached answer to a question about a tree that has since changed.
    ap.add_argument("--json", required=True,
                    help="orphan_scan --json output to consume (no default: "
                         "pass a scan you just generated)")
    ap.add_argument("--md", help="also write the full markdown report here")
    args = ap.parse_args()

    jp = Path(args.json)
    if not jp.is_file():
        raise SystemExit(
            "REFUSING TO TRIAGE: %s does not exist.\n"
            "Run:  python tools/orphan_scan.py --json %s\n"
            "An absent input is not an empty worklist." % (jp, jp))

    data = json.loads(jp.read_text(encoding="utf-8"))
    orphans: list[str] = data.get("orphans", [])

    # ---- provenance, printed before any number is reported ----
    age_days = max(0.0, (time.time() - jp.stat().st_mtime) / 86400.0)
    print("input               : %s" % jp)
    print("input generated     : %s (%.1f days ago)"
          % (time.strftime("%Y-%m-%d %H:%M", time.localtime(jp.stat().st_mtime)),
             age_days))
    print("input n_modules     : %s" % data.get("n_modules"))
    print("tree src/*.py now   : %d" % _tree_module_count())
    # A scan describes the tree it ran against. If module counts diverge, the
    # worklist below answers for a tree that is not this one.
    if _stale(data, jp):
        print("  ** STALE-INPUT WARNING: the scan's module count differs from the")
        print("     current tree by more than %d. Re-run:" % STALE_TOLERANCE)
        print("       python tools/orphan_scan.py --json %s" % jp)
    print()
    if not orphans:
        raise SystemExit(
            "REFUSING TO TRIAGE: %s lists zero orphans. Either the tree is "
            "genuinely clean or the scan ran from the wrong directory; this "
            "tool cannot tell the difference, so it will not report a pass."
            % jp)

    rows = classify(orphans)

    counts = Counter(c for c, _, _ in rows)
    print("orphan rows         : %d" % len(orphans))
    print()
    for cls in ("APP-ENTRY", "SCRIPT-DIR", "DYNAMIC", "TEST-ONLY",
                "TEST-INERT", "DEAD", "MISSING"):
        print("  %-10s %d" % (cls, counts.get(cls, 0)))
    print()
    print("  APP-ENTRY, SCRIPT-DIR and DYNAMIC are NOT defects -- they are the")
    print("  three ways orphan_scan over-reports. DEAD is the real worklist;")
    print("  TEST-ONLY is real but has a different remedy (wire it or retire it).")
    print("  TEST-INERT is real too, and worse than TEST-ONLY: its only importer")
    print("  never runs, so the module has no executing coverage at all.")
    print()
    # Priority disclosure. Without this the counts look like they contradict
    # `orphan_scan`, which tags ANY test-reachable row `[test-only]` -- a
    # superset of the TEST-ONLY class here, because a row that is also a
    # program is claimed by APP-ENTRY first. State it; never leave the reader
    # to discover a disagreement between a tool and its own input.
    print("  Classes are assigned in PRIORITY ORDER (APP-ENTRY > SCRIPT-DIR >")
    print("  TEST-ONLY > TEST-INERT > DYNAMIC > DEAD), so each row appears once.")
    print("  `orphan_scan`'s")
    print("  own `[test-only]` tag counts every test-reachable row and is")
    print("  therefore a SUPERSET of the TEST-ONLY class below; the two are not")
    print("  expected to match and the difference is app-entry rows.")
    print()
    print("DEAD (the actionable set):")
    for cls, mod, ev in rows:
        if cls == "DEAD":
            print("  %s" % mod)

    if args.md:
        out = Path(args.md)
        with out.open("w", encoding="utf-8") as fh:
            fh.write("# ORPHAN TRIAGE — %s\n\n" % jp.name)
            fh.write("Consumes `%s` (authoritative reachability). "
                     "Classes and their meanings are in the module docstring of "
                     "`tools/orphan_triage.py`.\n\n" % jp.name)
            fh.write("| class | count |\n|---|---|\n")
            for cls in ("APP-ENTRY", "SCRIPT-DIR", "DYNAMIC", "TEST-ONLY",
                        "TEST-INERT", "DEAD", "MISSING"):
                fh.write("| %s | %d |\n" % (cls, counts.get(cls, 0)))
            fh.write("\n## DEAD — the actionable set\n\n| module | evidence |\n|---|---|\n")
            for cls, mod, ev in rows:
                if cls == "DEAD":
                    fh.write("| `%s` | %s |\n" % (mod, ev))
            fh.write("\n## TEST-ONLY — real, different remedy\n\n"
                     "| module | evidence |\n|---|---|\n")
            for cls, mod, ev in rows:
                if cls == "TEST-ONLY":
                    fh.write("| `%s` | %s |\n" % (mod, ev))
            fh.write("\n## TEST-INERT — only an UNCOLLECTED test imports it\n\n"
                     "Real capability with no executing coverage: the importing "
                     "file is outside pytest's `testpaths`, so it never runs.\n\n"
                     "| module | evidence |\n|---|---|\n")
            for cls, mod, ev in rows:
                if cls == "TEST-INERT":
                    fh.write("| `%s` | %s |\n" % (mod, ev))
            fh.write("\n## SCRIPT-DIR — program assets, unimported by design\n\n"
                     "| module | evidence |\n|---|---|\n")
            for cls, mod, ev in rows:
                if cls == "SCRIPT-DIR":
                    fh.write("| `%s` | %s |\n" % (mod, ev))
            fh.write("\n## DYNAMIC — reachability blind spots, not orphans\n\n"
                     "| module | first dynamic reference |\n|---|---|\n")
            for cls, mod, ev in rows:
                if cls == "DYNAMIC":
                    fh.write("| `%s` | %s |\n" % (mod, ev))
            fh.write("\n## APP-ENTRY — programs, unimported by design\n\n")
            fh.write(", ".join("`%s`" % m for c, m, _ in rows if c == "APP-ENTRY"))
            fh.write("\n")
        print()
        print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
