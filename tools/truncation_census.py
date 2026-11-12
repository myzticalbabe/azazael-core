#!/usr/bin/env python3
"""truncation_census -- classify every `[:N]` slice, so a cut is a verdict.

WHY THIS EXISTS
---------------
Measured 2026-11-05: `tools/` holds 938 `[:N]` slices across 227 files. Most are
legitimate -- a 12-char commit id, a 120-column log line, a preview that says so.
A few are not: the cut value is compared, keyed, or asserted, and the reading it
produced rests on a prefix. That difference cannot be seen by counting.

The first pass at this was going to be 938 edits, which is neither reviewable nor
reversible. This is the census instead: every site gets a class derived from what
the code actually does with the value, the counts are the deliverable, and the
files with silent cuts on a verdict path are named so the fix has a target.

CLASSES -- assigned in this order, first match wins
--------------------------------------------------
`fixed_width`     a width that IS the format (40/64 hex) or an expression named
                  for an identity field (sha, digest, commit, head, hash...).
                  `head[:12]` is a display of an id, not a shortened reading.
`signature`       the cut is compared or `in`-tested against a literal of the
                  same length, or against a bytes literal. `h[:4] == b"%PDF"` is
                  a format sniff where the width IS the question, not a reading
                  that lost its tail. Added after reading three flagged sites and
                  finding all three correct (2026-11-05).
`labelled`        the slice sits in a string that also carries its own length
                  (`%d chars`, `len(...)`, `bytes`) -- the reader is told it is
                  a prefix.
`elision`         an explicit marker follows the cut (`...`, `\u2026`) or the call is
                  a named truncation helper (truncate/clip/preview/elide/...).
`display`         the value is handed to print/log/trace: a shortened message is
                  rendering, not a reading, and it is not what this census is for.
`message`         the value is interpolated into an f-string or `%` template. The
                  same argument as `display`, one level out: the truncated text
                  is being written, not decided on.
`silent`          none of the above: the value is used as if it were whole.

ORTHOGONAL FLAG
---------------
`verdict_adjacent`  the slice sits inside a boolean or selection TEST -- an
                    operand of Compare / BoolOp, an If/While/Assert test, an
                    IfExp test, or a comprehension condition. This is the flag
                    that matters: a silent cut on a verdict path is a reading
                    that can be wrong and that no test will notice.

The first version of this flag matched any ancestor of those types, which is
lexical containment and not position: every `print(...)` inside an `if` body has
the `if` as an ancestor, so 483 of 931 sites read "verdict-adjacent" and the
number meant nothing (measured 2026-11-05, caught before it was quoted). The
annotation below is sticky from the test expression downward and false elsewhere.

USAGE
-----
    python tools/truncation_census.py                # summary + top files
    python tools/truncation_census.py --root src     # any subtree
    python tools/truncation_census.py --json logs/truncation.json
    python tools/truncation_census.py --strict       # exit 1 on verdict_adjacent silent cuts
    python tools/truncation_census.py --list silent  # every silent site, file:line

EXIT  0 clean or advisory · 1 --strict and a silent cut is verdict-adjacent ·
      2 the census could not run (no files parsed). "Could not run" is not a pass.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Widths that are a wire format rather than a shortening of a reading.
FORMAT_WIDTHS = {40, 64}
IDENTITY_NAMES = {
    "sha", "sha1", "sha256", "hash", "digest", "commit", "head", "rev", "revision",
    "tag", "hex", "md5", "uuid", "guid", "blob_sha", "oid", "head_sha", "sha_w",
}
HELPER_MARKERS = ("trunc", "clip", "short", "preview", "abbrev", "elide", "ellips",
                  "summar", "excerpt", "head_of", "snippet")
SIGNATURE_NAMES = {"sig", "magic", "magic_bytes", "prefix_bytes", "header_bytes"}
ELISION_MARKERS = ("...", "\u2026")
LENGTH_WORDS = ("%d char", "%s char", "len(", "bytes", "byte(s)", "truncat")
DISPLAY_CALLS = {"print", "info", "debug", "warning", "warn", "error",
                 "critical", "exception", "log", "trace", "note", "echo", "say",
                 "write", "stdout", "join", "format", "dumps", "write_text",
                 "append", "extend"}


def _test_children(node) -> set:
    """ids of the children of `node` that are a boolean/selection TEST."""
    if isinstance(node, (ast.If, ast.While, ast.Assert, ast.IfExp)):
        return {id(node.test)}
    if isinstance(node, ast.comprehension):
        # The ITERABLE is not a test: `for f in findings[:3]` is a cap on how many
        # items are looked at, not a boolean question about a prefix. Marking it
        # made every "first N findings" loop read as a verdict (caught by reading
        # terence_invariants.py:172, 2026-11-05).
        return {id(cond) for cond in node.ifs}
    if isinstance(node, ast.BoolOp):
        return {id(value) for value in node.values}
    if isinstance(node, ast.Compare):
        return {id(node.left)} | {id(operand) for operand in node.comparators}
    return set()


def annotate_tests(tree) -> dict:
    """{id(node): inside-a-test}, sticky downward from every test expression."""
    flags, stack = {}, [(tree, False)]
    while stack:
        node, in_test = stack.pop()
        flags[id(node)] = in_test
        tests = _test_children(node)
        for child in ast.iter_child_nodes(node):
            stack.append((child, in_test or id(child) in tests))
    return flags


def _name_of(node) -> str:
    """Best-effort identifier for an expression, for the identity-name test."""
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return ""


def _called_name(node) -> str:
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return ""


def _slice_width(node):
    """The upper bound of `x[:N]` / `x[a:N]` when it is a literal int, else None."""
    if not isinstance(node, ast.Subscript):
        return None
    sl = node.slice
    if isinstance(sl, ast.Slice):
        upper = sl.upper
        lower, step = sl.lower, sl.step
    elif isinstance(sl, ast.Constant) and isinstance(sl.value, int):   # x[3]
        return None
    else:
        return None
    if step is not None or lower is not None:
        return None
    if isinstance(upper, ast.Constant) and isinstance(upper.value, int):
        return upper.value
    return None


def _ancestors(index: dict, node) -> list:
    chain, cur = [], index.get(id(node))
    while cur is not None:
        chain.append(cur)
        cur = index.get(id(cur))
    return chain


def _marker_in(text: str) -> bool:
    return any(marker in text for marker in ELISION_MARKERS)


def _signature(node, index: dict, width: int) -> bool:
    """True when the cut is tested against a literal, i.e. the width is the test."""
    parent = index.get(id(node))
    if isinstance(parent, ast.Compare):
        for op, operand in zip(parent.ops, parent.comparators):
            values = operand.elts if isinstance(operand, ast.Tuple) else [operand]
            if not all(isinstance(v, ast.Constant) for v in values):
                continue
            literals = [v.value for v in values]
            if isinstance(op, ast.In) and all(isinstance(v, bytes) for v in literals):
                return True
            if (isinstance(op, (ast.Eq, ast.NotEq, ast.In))
                    and all(isinstance(v, (bytes, str)) and len(v) == width
                            for v in literals)):
                return True
    return _name_of(node.value) in SIGNATURE_NAMES


def classify(node, index: dict, flags: dict, lines: list, width: int) -> tuple:
    """(class, inside_a_test) for one `[:N]` site."""
    line = node.lineno if hasattr(node, "lineno") else 0
    text = lines[line - 1] if 0 < line <= len(lines) else ""
    segment = text[getattr(node, "col_offset", 0):]
    chain = _ancestors(index, node)
    in_test = bool(flags.get(id(node), False))

    name = _name_of(node.value)
    if width in FORMAT_WIDTHS or name in IDENTITY_NAMES:
        return "fixed_width", in_test
    if _signature(node, index, width):
        return "signature", in_test
    if any(word in text for word in LENGTH_WORDS):
        return "labelled", in_test
    if _marker_in(segment):
        return "elision", in_test
    if any(m in _called_name(anc) for anc in chain[:3] for m in HELPER_MARKERS):
        return "elision", in_test
    if any(m in _called_name(node.value) or m in name for m in HELPER_MARKERS):
        return "elision", in_test
    for anc in chain[:4]:
        if isinstance(anc, ast.Call) and _called_name(anc) in DISPLAY_CALLS:
            return "display", in_test
    if any(isinstance(anc, ast.JoinedStr) for anc in chain[:3]):
        return "message", in_test
    parent = index.get(id(node))
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mod):
        return "message", in_test
    return "silent", in_test


def survey_file(path: Path) -> tuple:
    """(sites, error) for one module. `error` is a string or None, never a pass."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], "unreadable: %s" % exc
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], "will not parse: line %s: %s" % (exc.lineno, exc.msg)
    index = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            index[id(child)] = parent
    flags = annotate_tests(tree)
    lines = source.splitlines()
    sites = []
    for node in ast.walk(tree):
        width = _slice_width(node)
        if width is None:
            continue
        cls, adjacent = classify(node, index, flags, lines, width)
        sites.append({"file": str(path), "line": node.lineno, "width": width,
                      "class": cls, "verdict_adjacent": adjacent, "expr": _name_of(node.value)})
    return sites, None


def gather(root: Path) -> tuple:
    files = sorted(p for p in root.rglob("*.py")
                   if "__pycache__" not in p.parts and "archived" not in str(p))
    sites, errors, parsed = [], [], 0
    for path in files:
        rows, error = survey_file(path)
        if error:
            errors.append({"file": str(path), "error": error})
        else:
            parsed += 1
        sites.extend(rows)
    return sites, errors, parsed, len(files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="classify every [:N] text slice")
    ap.add_argument("--root", default="tools", help="subtree to sweep (default: tools)")
    ap.add_argument("--json", default="", help="write the full census here")
    ap.add_argument("--list", default="", choices=["silent", "verdict", "elision",
                                                  "fixed_width", "labelled", "display",
                                                  "message", "signature"],
                    help="print every site in one class")
    ap.add_argument("--top", type=int, default=12, help="how many files to name")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when a silent cut is verdict-adjacent")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_absolute():
        root = ROOT / root
    if not root.is_dir():
        print("truncation_census: %s is not a directory -- nothing measured" % root)
        return 2
    sites, errors, parsed, total = gather(root)
    if parsed == 0:
        print("truncation_census: 0 of %d file(s) parsed -- the census could not run"
              % total)
        for row in errors[:5]:
            print("  %s: %s" % (row["file"], row["error"]))
        return 2

    by_class = Counter(s["class"] for s in sites)
    by_width = Counter(s["width"] for s in sites)
    adjacent = [s for s in sites if s["verdict_adjacent"]]
    silent_adjacent = [s for s in adjacent if s["class"] == "silent"]
    per_file = Counter(s["file"] for s in sites)

    print("truncation_census: %d site(s) in %d file(s) [%s]"
          % (len(sites), len(per_file),
             " ".join("%s=%d" % (k, by_class[k]) for k in
                      ("silent", "elision", "labelled", "fixed_width", "display",
                       "message", "signature"))))
    print("verdict-adjacent: %d site(s), of which silent: %d"
          % (len(adjacent), len(silent_adjacent)))
    print("widths: %s" % ", ".join("%d:%d" % (w, n) for w, n in by_width.most_common(8)))
    if errors:
        print("unparsed: %d file(s) -- reported, never counted as clean" % len(errors))
        for row in errors[:5]:
            print("  %s: %s" % (row["file"], row["error"]))
    print("busiest files: %s" % ", ".join("%s %d" % (Path(f).name, n)
                                          for f, n in per_file.most_common(args.top)))

    if args.list:
        wanted = silent_adjacent if args.list == "verdict" else \
            [s for s in sites if s["class"] == args.list]
        for s in sorted(wanted, key=lambda r: (r["file"], r["line"])):
            print("  %s:%d  %s[:%d]%s" % (s["file"], s["line"], s["expr"] or "?",
                                          s["width"],
                                          "  [verdict]" if s["verdict_adjacent"] else ""))

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "root": str(root), "files": total, "parsed": parsed,
            "sites": len(sites), "by_class": dict(by_class),
            "by_width": {str(k): v for k, v in by_width.items()},
            "verdict_adjacent": len(adjacent),
            "silent_verdict_adjacent": silent_adjacent,
            "errors": errors, "rows": sites,
        }, indent=2) + "\n", encoding="utf-8")
        print("report: %s" % out)

    if args.strict and silent_adjacent:
        print("truncation_census: %d silent cut(s) on a verdict path"
              % len(silent_adjacent))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
