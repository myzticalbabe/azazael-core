# coding: utf-8
"""wsl_revision_diff — report content drift between this tree and a WSL mirror.

Why this exists
---------------
Two working copies of AZAZAEL have diverged: the Windows live tree and the
Ubuntu-26.04 mirror at ``/root/azazael``. A ``git reset --hard`` on Windows
discarded uncommitted work that the mirror still holds, so the mirror is a
*source of supersets*, and a half-completed restore is exactly the failure mode
that hides behind green-looking tests (the registry kept a docstring pointing at
``startup_warm.restore_spine_state`` while that function no longer existed).

This tool answers one question per file: which side has more?

    WSL_SUPERSET   local has nothing the mirror lacks -- safe to copy across
    LOCAL_SUPERSET mirror has nothing this tree lacks -- mirror is stale
    DIVERGED       both sides hold unique lines -- needs a human merge
    IDENTICAL      byte-equal after newline normalisation
    LOCAL_ONLY     the mirror does not have the file at all

Usage
-----
    python tools/wsl_revision_diff.py                    # summary + worst drift
    python tools/wsl_revision_diff.py --all              # every non-identical file
    python tools/wsl_revision_diff.py --status DIVERGED  # only ambiguous files
    python tools/wsl_revision_diff.py --path src/CORE    # one subtree
    python tools/wsl_revision_diff.py --json logs/drift.json

Read-only: it never writes to either tree.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

DEFAULT_WSL_ROOT = "//wsl.localhost/Ubuntu-26.04/root/azazael"
DEFAULT_SUBDIRS = ("src", "scripts", "tools", "tests")
SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache",
             "node_modules", ".venv", "venv", ".ruff_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo")
#: Only text-ish files are compared by default. An earlier revision read EVERY
#: file, which dragged multi-hundred-megabyte binaries across the 9p mount and
#: made a single src/ sweep take longer than the 520 s shell budget.
TEXT_EXTS = (".py", ".pyi", ".md", ".json", ".txt", ".cfg", ".ini",
             ".toml", ".yaml", ".yml", ".sh", ".bat", ".ps1", ".sql")
#: Refuse to read-and-diff anything larger than this (bytes).
MAX_FILE_BYTES = 2 * 1024 * 1024


def _norm(path: Path) -> str:
    """Read a file with newlines normalised, tolerating odd encodings."""
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_delta(local_text: str, wsl_text: str) -> tuple[int, int]:
    """(local_only, mirror_only) line counts between two normalised texts."""
    a = local_text.splitlines()
    b = wsl_text.splitlines()
    local_only = mirror_only = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag in ("delete", "replace"):
            local_only += i2 - i1
        if tag in ("insert", "replace"):
            mirror_only += j2 - j1
    return local_only, mirror_only


def _iter_files(root: Path, subdirs, exts=None) -> list[Path]:
    """Walk the given subtrees, returning files whose suffix is wanted.

    `exts=None` means every text-ish suffix in TEXT_EXTS; pass a set to
    override (e.g. {".py"}), or an empty set for "no filter".
    """
    wanted = TEXT_EXTS if exts is None else tuple(exts)
    out = []
    for sub in subdirs:
        base = root / sub
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(SKIP_SUFFIXES):
                    continue
                if wanted and not name.endswith(wanted):
                    continue
                out.append(Path(dirpath) / name)
    return sorted(out)


def classify(local_only: int, mirror_only: int) -> str:
    if local_only and mirror_only:
        return "DIVERGED"
    if mirror_only:
        return "WSL_SUPERSET"
    if local_only:
        return "LOCAL_SUPERSET"
    return "IDENTICAL"


def _compare_one(local_root: Path, mirror_root: Path,
                 rel: str) -> dict:
    """Compare one relative path across the two trees."""
    lf = local_root / rel
    mf = mirror_root / rel
    l_ok, m_ok = lf.is_file(), mf.is_file()

    if not m_ok:
        return {"path": rel, "status": "LOCAL_ONLY", "local_lines": 0,
                "mirror_lines": 0, "local_only": 0, "mirror_only": 0}
    if not l_ok:
        return {"path": rel, "status": "MIRROR_ONLY", "local_lines": 0,
                "mirror_lines": 0, "local_only": 0, "mirror_only": 0}

    try:
        if max(lf.stat().st_size, mf.stat().st_size) > MAX_FILE_BYTES:
            return {"path": rel, "status": "TOO_LARGE", "local_lines": 0,
                    "mirror_lines": 0, "local_only": 0, "mirror_only": 0}
        lt, mt = _norm(lf), _norm(mf)
    except OSError as exc:
        return {"path": rel, "status": "UNREADABLE", "local_lines": 0,
                "mirror_lines": 0, "local_only": 0, "mirror_only": 0,
                "error": str(exc)[:120]}

    lo, mo = _line_delta(lt, mt)
    return {
        "path": rel,
        "status": classify(lo, mo),
        "local_lines": len(lt.splitlines()),
        "mirror_lines": len(mt.splitlines()),
        "local_only": lo,
        "mirror_only": mo,
    }


def scan(local_root: Path, mirror_root: Path, subdirs,
         only_path: str | None = None, workers: int = 24,
         progress: bool = False, exts=None) -> list[dict]:
    """Compare every wanted file present in EITHER tree.

    The mirror lives on a 9p mount, so per-file reads are the bottleneck;
    the comparisons are pure I/O and run on a thread pool.
    """
    rels = set()
    for root in (local_root, mirror_root):
        for f in _iter_files(root, subdirs, exts):
            rel = f.relative_to(root).as_posix()
            if only_path and not rel.startswith(only_path):
                continue
            rels.add(rel)

    ordered = sorted(rels)
    rows = []
    if workers and workers > 1 and len(ordered) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_compare_one, local_root, mirror_root, r)
                       for r in ordered]
            for i, fut in enumerate(futures, 1):
                rows.append(fut.result())
                if progress and i % 250 == 0:
                    sys.stderr.write("  ... %d/%d\n" % (i, len(ordered)))
                    sys.stderr.flush()
    else:
        for rel in ordered:
            rows.append(_compare_one(local_root, mirror_root, rel))

    rows.sort(key=lambda r: (-r["mirror_only"], -r["local_only"], r["path"]))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--local-root",
                    default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--wsl-root", default=DEFAULT_WSL_ROOT)
    ap.add_argument("--subdirs", default=",".join(DEFAULT_SUBDIRS))
    ap.add_argument("--path", default=None, help="restrict to this prefix")
    ap.add_argument("--status", default=None,
                    help="only rows with this status (e.g. DIVERGED)")
    ap.add_argument("--min-delta", type=int, default=4,
                    help="hide rows where neither side differs by more "
                         "than this (default 4)")
    ap.add_argument("--all", action="store_true",
                    help="show every non-identical row")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--json", default=None, help="also write the full JSON")
    ap.add_argument("--workers", type=int, default=24,
                    help="parallel readers (the mirror is on a 9p mount)")
    ap.add_argument("--progress", action="store_true",
                    help="print a progress counter to stderr")
    ap.add_argument("--ext", default="py",
                    help="comma-separated suffixes, or 'all' (default py). "
                         "'text' uses the built-in text-ish set")
    args = ap.parse_args(argv)

    local_root = Path(args.local_root)
    mirror_root = Path(args.wsl_root)
    if not mirror_root.is_dir():
        print("mirror root not reachable: %s" % mirror_root)
        print("(is the %s distro running?)" % mirror_root.parts[4]
              if len(mirror_root.parts) > 4 else "")
        return 2

    subdirs = [s.strip() for s in args.subdirs.split(",") if s.strip()]
    if args.ext == "all":
        exts = set()
    elif args.ext == "text":
        exts = None
    else:
        exts = {"." + e.strip().lstrip(".") for e in args.ext.split(",")
                if e.strip()}
    rows = scan(local_root, mirror_root, subdirs, args.path,
                workers=args.workers, progress=args.progress, exts=exts)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print("local  : %s" % local_root)
    print("mirror : %s" % mirror_root)
    print("files  : %d compared" % len(rows))
    for status in ("WSL_SUPERSET", "DIVERGED", "LOCAL_SUPERSET",
                   "MIRROR_ONLY", "LOCAL_ONLY", "UNREADABLE", "TOO_LARGE",
                   "IDENTICAL"):
        if counts.get(status):
            print("  %-13s %4d" % (status, counts[status]))
    print()

    def show(r):
        if args.status:
            return r["status"] == args.status
        if args.all:
            return r["status"] != "IDENTICAL"
        return max(r["mirror_only"], r["local_only"]) > args.min_delta

    shown = [r for r in rows if show(r)]
    print("%-14s %6s %6s %6s %6s  %s"
          % ("STATUS", "local", "mirror", "l-only", "m-only", "path"))
    for r in shown[:args.limit]:
        print("%-14s %6d %6d %6d %6d  %s"
              % (r["status"], r["local_lines"], r["mirror_lines"],
                 r["local_only"], r["mirror_only"], r["path"]))
    if len(shown) > args.limit:
        print("... %d more (raise --limit)" % (len(shown) - args.limit))

    if args.json:
        outp = Path(args.json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(
            {"local_root": str(local_root), "mirror_root": str(mirror_root),
             "counts": counts, "rows": rows}, indent=2), encoding="utf-8")
        print("\nwrote %s" % outp)

    # Exit 0 even when drift exists: this is a report, not a gate.
    return 0


if __name__ == "__main__":
    sys.exit(main())
