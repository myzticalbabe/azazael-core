#!/usr/bin/env python3
"""verify_pipeline.py — did a pipeline stage lose, duplicate, or swap rows?

WHY THIS EXISTS
---------------
Three silent losses in one session, all the same shape, all found late:

  1. bibl_map lost 224 nodes crawl->render. Identity was keyed on a CLEANED
     name, so `generate_html_report` and `generate html report` collided.
     Counts were compared at the wrong stage, so `verify` reported OK.
  2. The PAMS corpus merge inserted 0 of 46 rows. The source had been
     overwritten before its rows were read.
  3. A 3,639 / 60 split: a corpus lived in site-packages while the canonical
     copy on G: held 60 rows, and nobody noticed for four months.

All three are the same question: **does stage N still contain what stage N-1
had?** Counts alone answer it only when the count drops. A silent SWAP --
equal count, different members -- passes every count check, so the default
comparison is a set difference, not a length.

USAGE
-----
    # two JSONL files keyed by a field
    python tools/verify_pipeline.py --a old.jsonl:path --b new.jsonl:path

    # json -> rendered markdown headings
    python tools/verify_pipeline.py --a skeleton.json:nodes:id \\
                                    --b 'lexicon/*.md:###:Heading'

    # two sqlite tables
    python tools/verify_pipeline.py --db a.db:curated_memories:memory_id \\
                                    --db b.db:curated_memories:memory_id

    # require a relationship; non-zero exit on violation
    ... --require equal|subset|superset|overlap

Sources are read-only. Nothing here writes.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
import sys
from pathlib import Path

MAX_SHOWN = 12


# ── source readers ────────────────────────────────────────────────────────

def _read_jsonl(path: Path, field: str) -> set:
    out = set()
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # A torn final line from a killed process is expected; anything
                # else is a real corruption, so it is reported rather than
                # silently dropped.
                sys.stderr.write("  [%s] torn line %d skipped\n"
                                 % (path.name, lineno))
                continue
            if isinstance(rec, dict) and field in rec:
                out.add(str(rec[field]))
    return out


def _dig(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return []
        elif isinstance(cur, dict):
            if part not in cur:
                return []
            cur = cur[part]
        else:
            return []
    return cur


def _read_json(path: Path, spec: str) -> set:
    """spec is `array.path` optionally `.field`; both may be dotted."""
    parts = spec.split(":")
    arr_path = parts[0] or "."
    field = parts[1] if len(parts) > 1 else None
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    items = _dig(data, arr_path)
    if not isinstance(items, list):
        sys.stderr.write("  [%s] '%s' is not a list (%s); got %s\n"
                         % (path.name, arr_path, type(items).__name__,
                            type(items)))
        return set()
    if field is None:
        return {json.dumps(i, sort_keys=True) for i in items}
    return {str(_dig(i, field)) for i in items if isinstance(i, dict)}


def _read_md(path: Path, prefix: str) -> set:
    """Markdown nodes defined as `prefix Name`. Used for rendered output,
    which has no machine-readable sidecar."""
    out = set()
    pat = re.compile(r"^%s\s+(.+?)\s*$" % re.escape(prefix))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat.match(line.rstrip("\n"))
            if m:
                out.add(m.group(1))
    return out


def _read_db(path: Path, spec: str) -> set:
    """spec is TABLE:COLUMN, the part after the path."""
    table, _, column = spec.partition(":")
    column = column or "id"
    # as_uri() is required, not cosmetic: a hand-built "file:C:\x\y.db?mode=ro"
    # fails with "unable to open database file" because the URI parser takes
    # the backslashes and the drive colon literally.
    con = sqlite3.connect("%s?mode=ro" % path.resolve().as_uri(), uri=True)
    try:
        return {str(r[0]) for r in
                con.execute('SELECT "%s" FROM "%s"' % (column, table))}
    finally:
        con.close()


def _split_db(spec: str) -> tuple[str, str]:
    """Split PATH:TABLE:COLUMN, which carries TWO colons.

    rpartition is wrong here -- it takes the last colon and swallows the table
    into the path. The drive letter is the first colon, so strip it, then cut
    at the next one.
    """
    if len(spec) > 1 and spec[1] == ":":
        rest = spec[2:]
        i = rest.find(":")
        if i == -1:
            return spec, ""
        return spec[:2] + rest[:i], rest[i + 1:]
    if ":" in spec:
        path_s, _, rest = spec.partition(":")
        return path_s, rest
    return spec, ""


def _load_db(path: Path, spec: str) -> set:
    return _read_db(path, spec)


# ── source specs ──────────────────────────────────────────────────────────

def _load_jsonl(path: Path, spec: str) -> set:
    field = spec.split(":", 1)[1] if ":" in spec else "id"
    return _read_jsonl(path, field)


def _load_md(path: Path, spec: str) -> set:
    parts = spec.split(":")
    prefix = parts[0] or "###"
    return _read_md(path, prefix)


def _load_json(path: Path, spec: str) -> set:
    return _read_json(path, spec or ".")


def _split_spec(spec: str) -> tuple[str, str]:
    """Split PATH:ARG without being fooled by a Windows drive letter.

    `C:\\proj\\a.jsonl:id`.split(':', 1) yields ('C', '\\proj\\a.jsonl:id') --
    the drive colon is mistaken for the separator, which silently reads the
    wrong file. rpartition takes the LAST colon, so only a bare path with no
    argument still misparses; that case is caught by checking whether the
    left side collapsed to a single drive letter.
    """
    if ":" not in spec:
        return spec, ""
    path_s, _, arg = spec.rpartition(":")
    if len(path_s) == 1 and arg[:1] in ("\\", "/"):
        return spec, ""            # bare "C:\\path\\file" with no argument
    return path_s, arg


def load(spec: str, kind: str) -> set:
    """spec is PATH:ARG. A glob in PATH loads every match and unions them."""
    path_s, arg = _split_db(spec) if kind == "db" else _split_spec(spec)
    paths = [Path(p) for p in sorted(glob.glob(path_s))] or [Path(path_s)]
    out: set = set()
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(p)
        if kind == "jsonl":
            out |= _load_jsonl(p, arg or "id")
        elif kind == "json":
            out |= _load_json(p, arg or ".")
        elif kind == "md":
            out |= _load_md(p, arg or "###")
        elif kind == "db":
            out |= _load_db(p, arg)
        else:
            raise ValueError("unknown source kind %r" % kind)
    return out


def _kind_of(spec: str, db_specs: list) -> str:
    if spec in db_specs:
        return "db"
    path_s, _ = _split_spec(spec)
    low = path_s.lower()
    if low.endswith(".jsonl"):
        return "jsonl"
    if low.endswith(".json"):
        return "json"
    return "md"


def _show(label: str, items: set) -> None:
    if not items:
        return
    print("  %s (%d):" % (label, len(items)))
    for x in sorted(items)[:MAX_SHOWN]:
        print("      %s" % x)
    if len(items) > MAX_SHOWN:
        print("      ... and %d more" % (len(items) - MAX_SHOWN))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[2],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag, kind, helptext in (("--a", None, "source A (jsonl|json|md|db)"),
                                 ("--b", None, "source B (jsonl|json|md|db)")):
        ap.add_argument(flag, action="append", default=[],
                        metavar="SPEC", help=helptext)
    ap.add_argument("--db", action="append", default=[], metavar="PATH:TABLE:COL",
                    help="a sqlite source (added to BOTH sides -- prefer "
                         "--a-db/--b-db, which are unambiguous)")
    ap.add_argument("--a-db", action="append", default=[],
                    metavar="PATH:TABLE:COL", help="sqlite source, side A only")
    ap.add_argument("--b-db", action="append", default=[],
                    metavar="PATH:TABLE:COL", help="sqlite source, side B only")
    ap.add_argument("--require", choices=("equal", "subset", "superset",
                                          "overlap", "none"), default=None)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    args = ap.parse_args()

    a_specs = args.a + args.db + args.a_db
    b_specs = args.b + args.db + args.b_db
    if not a_specs or not b_specs:
        ap.print_help()
        return 2

    A: set = set()
    B: set = set()
    for spec in a_specs:
        A |= load(spec, _kind_of(spec, args.db + args.a_db + args.b_db))
    for spec in b_specs:
        B |= load(spec, _kind_of(spec, args.db + args.a_db + args.b_db))

    only_a = A - B
    only_b = B - A
    shared = A & B

    print("%s: %d    %s: %d    shared: %d"
          % (args.label_a, len(A), args.label_b, len(B), len(shared)))
    print()
    if not only_a and not only_b:
        print("IDENTICAL -- no loss, no addition, no swap")
    else:
        # Neutral wording. "lost downstream" is only true when B is supposed
        # to be downstream of A; when B is a subset (a backup, an older
        # stage) the extra rows in A are simply extra, and calling them
        # "lost" misreads the check that just passed.
        _show("only in %s" % args.label_a, only_a)
        _show("only in %s" % args.label_b, only_b)
    print()

    if args.require is None:
        return 0 if not (only_a or only_b) else 1

    # A superset of B means A contains everything B has, so nothing may be
    # present in B alone. Getting this backwards reports FAIL on a
    # superset that is demonstrably intact.
    ok = {
        "equal": not only_a and not only_b,
        "superset": not only_b,
        "subset": not only_a,
        "overlap": bool(shared),
        "none": not shared,
    }[args.require]
    verdict = "PASS" if ok else "FAIL"
    print("require %-8s -> %s" % (args.require, verdict))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
