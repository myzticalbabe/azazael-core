#!/usr/bin/env python3
"""write_target — say where a path will actually land, before you trust the write.

The hazard this exists for, measured 2026-11-03: the agent tooling on this box
resolves relative paths against ``C:/Users/clint``, not against the repo at
``C:/Azazael/azazael``. A write to ``azazael/SCRIPTURE.md`` therefore landed in a
second, unrelated tree at ``C:/Users/clint/azazael`` — and both ``write_file`` and
``str_replace`` reported success. The files were only found missing because a
later ``wc`` could not see them. Reporting success is not evidence of landing.

So: ask this what the path resolves to, and which git tree owns it.

    python tools/write_target.py docs/WHITEPAPER_x.md      # check one path
    python tools/write_target.py --self-test               # control in both directions

Exit 0 = the path resolves inside the expected repo root. Exit 1 = it does not
(the wrong tree, or no repo at all). Nothing is created, moved or deleted.

The expected root is derived from where this file lives (``tools/`` -> parent),
never hardcoded, so a copy of this tool travels with whatever tree it sits in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def find_toplevel(p: Path) -> Path | None:
    """Nearest ancestor holding a .git entry (dir or worktree file), else None."""
    for cand in (p, *p.parents):
        if (cand / ".git").exists():
            return cand
    return None


def expected_root() -> Path:
    return Path(__file__).resolve().parent.parent


def same(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return False
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def check(target: str, expect: Path | None = None) -> int:
    expect = expect or expected_root()
    raw = Path(target)
    resolved = raw.resolve() if raw.is_absolute() else (Path.cwd() / raw).resolve()
    top = find_toplevel(resolved)
    exists = resolved.exists()

    # Where would a parent have to be created? A write into a missing directory
    # silently manufactures one, which is exactly how the stray tree appeared.
    nearest = resolved
    while not nearest.exists() and nearest.parent != nearest:
        nearest = nearest.parent

    print(f"target   : {target}")
    print(f"resolves : {resolved}")
    print(f"exists   : {exists}")
    print(f"nearest  : {nearest}")

    if same(top, expect):
        print(f"[PASS] owned by expected repo root {expect}")
        if nearest != resolved:
            print(f"[note] parent(s) would be created up from {nearest}")
        return 0

    print(f"[FAIL] expected repo root {expect}")
    print(f"       actual toplevel   {top if top else '(none — outside any git repo)'}")
    if top is not None:
        try:
            rel = resolved.relative_to(top)
        except ValueError:
            rel = None
        if rel is not None:
            fixed = suggest(expect, rel)
            print(f"       this is the wrong tree; did you mean {fixed}?")
            print(f"       that path exists: {fixed.exists()}")
    return 1


def suggest(expect: Path, rel: Path) -> Path:
    """Best guess at the intended path inside the expected repo.

    A path relative to the home tree looks like ``azazael/SCRIPTURE.md``, and the
    expected root is itself named ``azazael`` — joining the two unchanged produced
    ``C:/Azazael/azazael/azazael/SCRIPTURE.md``, which is where the first version of
    this tool pointed. Strip the repeated leading segment, and prefer whichever
    candidate already exists.
    """
    candidates = [expect / rel]
    parts = rel.parts
    if len(parts) > 1 and parts[0].lower() == expect.name.lower():
        candidates.insert(0, expect.joinpath(*parts[1:]))
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def self_test() -> int:
    """A control that can fail in BOTH directions (canon 0B-C).

    Known-healthy inside the repo must PASS; known-wrong outside it must FAIL.
    A detector that only ever says FAIL is as useless as one that only says PASS.
    """
    expect = expected_root()
    good = str(expect / "docs" / "__write_target_selftest_probe.md")
    bad = str(Path.home() / "azazael" / "__write_target_selftest_probe.md")

    print("=== control 1: path inside the expected repo (must PASS) ===")
    r_good = check(good, expect)
    print()
    print("=== control 2: path in the duplicate tree (must FAIL) ===")
    r_bad = check(bad, expect)
    print()

    ok = (r_good == 0) and (r_bad == 1)
    print(f"self-test: control1={'PASS' if r_good == 0 else 'UNEXPECTED'} "
          f"control2={'FAIL' if r_bad == 1 else 'UNEXPECTED'} "
          f"-> {'VALIDATED' if ok else 'INVALID ORACLE'}")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 2
    if args[0] == "--self-test":
        return self_test()
    expect = None
    if "--expect" in args:
        i = args.index("--expect")
        expect = Path(args[i + 1]).resolve()
        del args[i:i + 2]
    if not args:
        print("no path given")
        return 2
    return check(args[0], expect)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
