#!/usr/bin/env python3
"""unresolved_ratchet -- the unresolved-reference count may fall, never rise.

WHY
On 2026-10-31 the census could place 109 of 142 driver references and reported
the other 33 by reason. A number in a JSON file that nobody re-reads is not a
finding: the next session inherits 33 by default rather than by decision, and
the 34th arrives unremarked. This turns it into a budget with the same ratchet
semantics the repo already uses for anti-patterns.

WHY SHELL/BATCH ONLY
The js graph resolves through tree-sitter when the interpreter has it and
through regex when it does not, so its unresolved count is a property of the
environment as well as of the code. Measured: with tree-sitter both paths give
0 unresolved, but a gate that can flap between two interpreters is useless.
Shell/batch is regex on every interpreter, so this count is stable -- and the
graph with a real budget is the driver layer, which is where the 33 live.

USAGE
  python tools/unresolved_ratchet.py --check      # exit 1 if the count grew
  python tools/unresolved_ratchet.py              # print the current counts
  python tools/unresolved_ratchet.py --baseline   # accept the current counts
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import yggdrasil_census as yc  # noqa: E402

BASELINE = HERE / "unresolved_baseline.json"


def measure(root: Path) -> Dict[str, Any]:
    src = (root / "src") if (root / "src").is_dir() else root
    scripts: List[Path] = []
    for f in src.rglob("*"):
        if not f.is_file() or any(p in yc.SKIP_DIRS for p in f.parts):
            continue
        if yc.LANGUAGES.get(f.suffix.lower()) in ("shell", "batch", "powershell"):
            scripts.append(f)
    graph = yc.build_shell_graph(src, scripts, src.parent, yc.Aliases.load())
    unresolved = dict(graph.get("unresolved") or {})
    return {
        "references": graph["statements"] + sum(unresolved.values()),
        "resolved": graph["statements"],
        "unresolved": unresolved,
        "total": sum(unresolved.values()),
    }


def load_baseline() -> Dict[str, Any]:
    if not BASELINE.is_file():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="repo root (default: parent of src/)")
    ap.add_argument("--check", action="store_true", help="fail if any count grew")
    ap.add_argument("--baseline", action="store_true", help="record the current counts")
    ap.add_argument("--note", default=None,
                    help="why this baseline is what it is; recorded beside the counts "
                         "so a later session reads a decision instead of a delta")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else HERE.parent
    now = measure(root)
    if args.json:
        print(json.dumps(now, indent=2))

    if args.baseline:
        payload = {
            "measured": datetime.datetime.now().isoformat(timespec="seconds"),
            "counts": now["unresolved"],
            "total": now["total"],
            "note": ("shell/batch references the census cannot place, by reason. "
                     "Decline only: resolve the reference, or declare an alias in "
                     "tools/resolution_aliases.json."),
            "review": args.note or "",
        }
        BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print("wrote %s (total %d)" % (BASELINE.name, now["total"]))
        return 0

    base = load_baseline()
    if not base:
        print("no baseline at %s -- run --baseline after a review" % BASELINE,
              file=sys.stderr)
        return 0 if not args.check else 1

    grew = Counter()
    for reason, count in now["unresolved"].items():
        prior = int((base.get("counts") or {}).get(reason, 0))
        if count > prior:
            grew[reason] = count - prior

    print("unresolved references -- %d now (resolved %d of %d)"
          % (now["total"], now["resolved"], now["references"]))
    for reason, count in sorted(now["unresolved"].items()):
        prior = int((base.get("counts") or {}).get(reason, 0))
        mark = "GROWTH" if count > prior else ("fell" if count < prior else "=")
        print("  %-20s %4d  baseline %4d  %s" % (reason, count, prior, mark))
    if now["total"] < int(base.get("total", 0)):
        print("  total fell from %s to %d" % (base.get("total"), now["total"]))

    if args.check and grew:
        print("", file=sys.stderr)
        print("unresolved reference(s) ADDED: "
              + ", ".join("%s +%d" % kv for kv in sorted(grew.items())),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
