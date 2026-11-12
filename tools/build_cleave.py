#!/usr/bin/env python3
# coding: utf-8
"""build_cleave -- generate the CLEAVE limb list from the census, not from prose.

WHY
`YGGDRASIL_CLEAVE.html` animates "10 Trees Off One Trunk" from
`const LIMBS=[[...]]` -- a hand-written literal. The 2026-10-30 analysis found the
limb *names* accurate but the mechanism wrong: the video would still say "10
Trees" if three existed, and by 2026-10-31 `src/` held 20 trees, one of which the
literal could not represent. A figure whose data is a literal is an illustration,
not a measurement.

WHAT IT DOES
Runs the census, ranks trees by Python module count, takes the top N (default 10,
the figure's own grammar), and rewrites ONLY the `const LIMBS=[...]` block plus
the comment above it, which becomes a provenance line naming the census
timestamp and the selection rule. Everything else in the page is untouched: the
layout, camera, bloom and label placement were repaired and verified on
2026-10-30 and this tool has no business touching them.

Sides, spread and hue are derived from rank, not stored per tree, so the same
census always produces the same figure -- which is what makes a re-render
comparable to the last one.

USAGE
  python tools/build_cleave.py --html Desktop/YGGDRASIL_CLEAVE.html --dry-run
  python tools/build_cleave.py --html Desktop/YGGDRASIL_CLEAVE.html
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import yggdrasil_census as yc  # noqa: E402

#: Six hues cycled by rank. Kept in the palette the repaired page already used
#: (cyan -> green -> amber -> magenta -> lilac) so a regenerated frame is
#: recognisably the same artefact.
HUES = ["#7df9ff", "#7df9ff", "#00ffa3", "#ffd479", "#ff7ad9", "#c4b5fd"]

#: The trunk is never a limb: cleaving it is cleaving the tree.
EXCLUDE = {yc.TRUNK}

DEFAULT_HTML = Path.home() / "Desktop" / "YGGDRASIL_CLEAVE.html"
LIMB_RE = re.compile(r"const LIMBS=\[\[.*?\]\];", re.S)
NOTE_RE = re.compile(r"//[^\n]*limbs:[^\n]*", re.S)


def tier_of(ledger: Dict[str, Any], name: str) -> str:
    """The census's own verdict for one tree, never a second opinion.

    Re-deriving the tier here from `inbound_any_count` was how the figure kept
    printing "A" for a tree the ledger had already moved to stub or to
    mixed-language: one rule in two places is one rule too many.
    """
    tiers = ledger.get("tiers") or {}
    if name in (tiers.get("D_stub") or []):
        return "stub"
    if name in (tiers.get("C_cycle_blocked") or []):
        return "cycle"
    if any(r.get("tree") == name for r in (tiers.get("A_mixed_unaudited") or [])):
        return "A-mixed"
    if any(r.get("tree") == name for r in (tiers.get("A_cross_language") or [])):
        return "A-cross"
    if name in (tiers.get("A_cleave_today") or []):
        return "A"
    if any(r.get("tree") == name for r in (tiers.get("B_needs_shim") or [])):
        return "B"
    return "?"


def select(ledger: Dict[str, Any], top: int) -> List[Dict[str, Any]]:
    trees = ledger["trees"]
    ranked = sorted(
        ((name, row) for name, row in trees.items()
         if name not in EXCLUDE and row["py"] > 0),
        key=lambda kv: (-kv[1]["py"], kv[0]),
    )[:top]
    out: List[Dict[str, Any]] = []
    for i, (name, row) in enumerate(ranked):
        out.append({
            "name": name.lower(),
            "side": -1 if i % 2 == 0 else 1,
            "spread": round(0.34 + (i // 2) * 0.14, 2),
            "hue": HUES[min(i // 2, len(HUES) - 1)],
            "src": name,
            "py": row["py"],
            "stub": bool(row.get("is_stub")),
            "tier": tier_of(ledger, name),
        })
    return out


def js_literal(limbs: List[Dict[str, Any]]) -> str:
    rows = ",".join(
        '["%s",%d,%s,"%s","%s"]' % (l["name"], l["side"], l["spread"],
                                    l["hue"], l["src"])
        for l in limbs
    )
    return f"const LIMBS=[{rows}];"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--html", default=str(DEFAULT_HTML))
    ap.add_argument("--root", default=None, help="src dir (default: ../src)")
    ap.add_argument("--archive", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", default=None, help="write the limb table here")
    args = ap.parse_args()

    root = Path(args.root) if args.root else HERE.parent / "src"
    archive = Path(args.archive) if args.archive else HERE.parent / "archived_unified"
    ledger = yc.census(root, archive)
    limbs = select(ledger, args.top)

    print(f"{'limb':16}{'py':>5}  tier   side spread  hue")
    for l in limbs:
        print(f"{l['name']:16}{l['py']:5}  {l['tier']:<5} {l['side']:>3} "
              f"{l['spread']:>6}  {l['hue']}")

    html = Path(args.html)
    if not html.exists():
        print(f"no page at {html}", file=sys.stderr)
        return 2
    page = html.read_text(encoding="utf-8", errors="replace")
    literal = js_literal(limbs)
    note = (f"// limbs GENERATED by tools/build_cleave.py from "
            f"tools/yggdrasil_census.py at {ledger['generated_at']} -- "
            f"top {len(limbs)} of {ledger['totals']['trees']} trees by python "
            f"modules, trunk {yc.TRUNK} excluded: "
            f"[label, side(-1 left / 1 right), spread, hue, src path]")
    if not LIMB_RE.search(page):
        print("const LIMBS block not found -- page shape changed, refusing to edit",
              file=sys.stderr)
        return 3
    page = LIMB_RE.sub(literal, page, count=1)
    if NOTE_RE.search(page):
        page = NOTE_RE.sub(note, page, count=1)
    else:
        page = page.replace(literal, note + "\n" + literal, 1)

    if args.dry_run:
        print(f"\n--dry-run: would write {len(page):,} chars to {html}")
        return 0

    bak = html.with_suffix(html.suffix + ".bak-20261031")
    if not bak.exists():
        shutil.copy2(html, bak)
        print(f"\nbackup: {bak}")
    html.write_text(page, encoding="utf-8")
    print(f"wrote {html} ({len(page):,} chars)")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"generated_at": ledger["generated_at"], "limbs": limbs},
                       indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")

    # Read the result back: a rewrite that cannot be re-parsed did not land.
    check = html.read_text(encoding="utf-8", errors="replace")
    if literal not in check:
        print("VERIFY FAILED: generated literal not present after write",
              file=sys.stderr)
        return 4
    print("verified: generated LIMBS present in the written page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
