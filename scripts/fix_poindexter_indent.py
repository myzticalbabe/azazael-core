#!/usr/bin/env python3
# coding: utf-8
"""fix_poindexter_indent.py — repair the de-indented POINDEXTER.py.

POINDEXTER.py arrived with its block structure flattened. Two distinct shapes of
damage, and they need different repairs — this is why a blanket "indent
everything" pass would have made it worse:

  SHAPE 1 (class bodies). Only the FIRST line of each block lost its 4 spaces.
  `VERSION = "1.0.0"` and every `def` sit at column 0, while the method BODIES
  still carry their original 8/12/16. So a method body at 8 is already correct
  once its `def` moves to 4. Indenting the bodies as well would push them to
  12/16 and break the nesting just as badly.

  SHAPE 2 (`main`). The ENTIRE body is 4 spaces too far left, uniformly.
  `with open(...)` at 0 with `json.dump(...)` at 4 means the inner block is
  right and the whole body is shifted.

Consequence of the damage: `add_system` was a module-level function while
`main()`'s module-level body called `poindexter.add_system(...)`, so importing or
running the file raised
    AttributeError: 'POINDEXTER' object has no attribute 'add_system'
and the reporting code never ran.

Anchor-based (not line-number-based) so the repair survives small edits, and
idempotent: a second run detects that there is nothing left at column 0 and
makes no change.

Usage:
    python scripts/fix_poindexter_indent.py            # dry run, shows the diff
    python scripts/fix_poindexter_indent.py --apply     # write (keeps .bak)
"""

import argparse
import ast
import os
import shutil
import sys

TARGET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "UTILITIES", "POINDEXTER", "POINDEXTER.py")


def _find(lines, predicate, start=0):
    for i in range(start, len(lines)):
        if predicate(lines[i]):
            return i
    return -1


def plan(lines):
    """Return [(line_index, reason), ...] for every line that must move right."""
    moves = []

    # --- SHAPE 1a: MathematicalMetrics body -------------------------------
    i_metrics = _find(lines, lambda s: s.startswith("class MathematicalMetrics"))
    i_poind = _find(lines, lambda s: s.startswith("class POINDEXTER"))
    if i_metrics < 0 or i_poind < 0:
        raise SystemExit("anchors not found: expected both class declarations")

    for i in range(i_metrics + 1, i_poind):
        s = lines[i].rstrip("\n")
        if s.strip() and not s.startswith(" "):
            moves.append((i, "MathematicalMetrics body: block lost its 4-space indent"))

    # --- SHAPE 1b: POINDEXTER body (up to main) ---------------------------
    i_main = _find(lines, lambda s: s.startswith("def main("), i_poind)
    if i_main < 0:
        raise SystemExit("anchor not found: def main(")
    for i in range(i_poind + 1, i_main):
        s = lines[i].rstrip("\n")
        if s.strip() and not s.startswith(" "):
            moves.append((i, "POINDEXTER body: block lost its 4-space indent"))

    # --- SHAPE 2: main body (uniform shift) -------------------------------
    i_end = _find(lines, lambda s: s.startswith('if __name__'), i_main)
    if i_end < 0:
        raise SystemExit("anchor not found: if __name__")
    # main's own docstring is correctly indented; the body starts after it.
    body_start = i_main + 1
    while body_start < i_end and (not lines[body_start].strip()
                                  or lines[body_start].startswith(" ")):
        body_start += 1
    for i in range(body_start, i_end):
        if lines[i].strip():
            moves.append((i, "main body: whole body is 4 spaces too far left"))

    return moves, (i_metrics, i_poind, i_main, i_end, body_start)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the repaired file")
    args = ap.parse_args()

    if not os.path.exists(TARGET):
        raise SystemExit(f"not found: {TARGET}")
    with open(TARGET, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()

    moves, anchors = plan(lines)
    print(f"target : {TARGET}")
    print(f"anchors: metrics={anchors[0]} poindexter={anchors[1]} "
          f"main={anchors[2]} end={anchors[3]} body_start={anchors[4]}")

    if not moves:
        print("\nalready repaired — nothing at column 0 in any block. No change.")
        return

    print(f"\n{len(moves)} line(s) must shift right by 4:")
    for i, why in moves[:12]:
        print(f"  {i + 1:>4}: {why}")
        print(f"        {lines[i].rstrip()[:88]}")
    if len(moves) > 12:
        print(f"  ... and {len(moves) - 12} more")

    repaired = list(lines)
    for i, _ in moves:
        repaired[i] = "    " + repaired[i]

    src = "".join(repaired)
    try:
        ast.parse(src)
        print("\nast.parse: OK")
    except SyntaxError as exc:
        print(f"\nast.parse: STILL BROKEN — line {exc.lineno}: {exc.msg}")
        print("not writing; the plan is incomplete.")
        return

    if not args.apply:
        print("\n(dry run) re-run with --apply to write, keeping a .bak copy.")
        return

    backup = TARGET + ".bak"
    shutil.copyfile(TARGET, backup)
    with open(TARGET, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print(f"\nwritten. backup at {backup}")


if __name__ == "__main__":
    main()
