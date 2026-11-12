#!/usr/bin/env python
"""Convert an atlas (domain or environment) into a graph3d JSON.

graph3d already exists at `C:/Users/clint/graph3d` and its one idea is that the
third dimension must encode something a flat chart cannot. For an evidence graph
that is the grade of a claim. For a **census** the honest analogue is *how deeply
each limb was measured*, because that is the axis on which a census can mislead:
a limb with 22 worms and no AST pass is not the same kind of fact as a limb with
412 parsed classes, and a flat bar chart makes them look equally solid.

So altitude is measurement depth, and it is derived from fields the census
actually writes -- never assigned for looks:

  verified  the limb was AST-parsed: it has classes and lines (altitude high)
  lead      counted but not parsed: modules/lines with no class pass, or a
            declared skill/evidence limb whose contents are files, not code
  cut       declared in the domain and NOT FOUND on disk (`atlas.missing`) --
            below the horizon, because that is a hole, not a small tree

Links are **declared containment**: one hub node for the atlas, one edge per
limb. The atlas's own `edges.tree_pairs` is a per-root *sum*, not a pair list
(`cross_root_edges_resolved: false`), so drawing inter-limb links would be
inventing coupling the census explicitly says it did not resolve. A converter
that guesses here would produce the most confident-looking and least true
picture in the tree.

Usage:
  python tools/atlas_to_graph3d.py atlas domain_atlas_hives.json -o hives_graph.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def grade_of(limb: dict) -> tuple[str, str]:
    """(grade, why) for one limb, from fields the census wrote."""
    classes = limb.get("classes")
    lines = limb.get("lines") or 0
    kind = limb.get("kind") or ""
    if classes is None or kind in ("shards", "canon", "evidence", "category"):
        return "lead", f"kind={kind or 'unlabelled'} is counted, not AST-parsed"
    if classes and lines:
        return "verified", f"{classes} classes over {lines} lines were AST-parsed"
    if lines or (limb.get("modules") or 0):
        return "lead", "has size but no class pass"
    return "cut", "declared with no measurable content"


def convert(atlas_path: Path, title: str | None) -> dict:
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    trees = atlas.get("trees") or {}
    nodes: list[dict] = []
    links: list[dict] = []

    hub = f"{atlas.get('domain') or atlas.get('environment') or atlas_path.stem}"
    nodes.append({"id": hub, "label": hub.upper(), "val": 1 + len(trees),
                  "group": "HUB", "flock": "HUB", "grade": "verified"})

    for name, limb in trees.items():
        grade, why = grade_of(limb)
        unit = limb.get("unit") or ""
        size = limb.get("classes") if (limb.get("classes") and not unit) else \
            (limb.get("modules") or 0)
        nodes.append({
            "id": name,
            "label": name,
            "val": max(1, int(size or 1)),
            "group": limb.get("kind") or "limb",
            "flock": limb.get("kind") or "limb",
            "grade": grade,
            # the reason travels with the node so a viewer can print it: a
            # grade whose justification is not carried is a colour, not a claim.
            "why": why,
            "contains": limb.get("contains") or "",
        })
        links.append({"source": hub, "target": name, "relation": "CONTAINS",
                      "grade": grade, "flock": limb.get("kind") or "limb"})

    for missing in atlas.get("missing") or []:
        mid = str(missing)
        nodes.append({"id": mid, "label": Path(mid).name, "val": 1,
                      "group": "MISSING", "flock": "MISSING", "grade": "cut",
                      "why": "declared in the domain, absent on disk"})
        links.append({"source": hub, "target": mid, "relation": "DECLARED_ABSENT",
                      "grade": "cut", "flock": "MISSING"})

    totals = atlas.get("totals") or {}
    sub = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()) if v)
    return {
        "nodes": nodes,
        "links": links,
        "meta": {
            "title": title or hub.upper(),
            "subtitle": sub or "no totals recorded",
            "source": str(atlas_path),
            "sha256": hashlib.sha256(atlas_path.read_bytes()).hexdigest()[:16],
            "altitude_legend": "verified=AST-parsed | lead=counted only | cut=declared absent",
        },
    }


def convert_forest(manifest: Path, title: str | None, restrict: list[str] | None) -> dict:
    """One graph over every declared domain.

    This is the view worth rendering in 3D, because the grades then actually
    differ: a domain of parsed Python sits above one that is only counted
    (gosp's three scripts, the 204 skill shards, canon text) and a declared-but-
    absent root hangs below the horizon. The single-domain hives view is honest
    and flat -- all 36 category limbs are counted-not-parsed -- which is a fact
    about that domain, not a reason to invent a grade for it.
    """
    man = json.loads(manifest.read_text(encoding="utf-8"))
    base = manifest.parent
    nodes: list[dict] = []
    links: list[dict] = []
    seen: set[str] = set()
    for desc in man.get("trees") or []:
        name = desc.get("name")
        if not name or (restrict and name not in restrict):
            continue
        atlas_path = base / (desc.get("atlas") or "")
        if not atlas_path.exists():
            continue
        sub = convert(atlas_path, desc.get("label"))
        hub_id = f"D:{name}"
        nodes.append({"id": hub_id, "label": name.upper(),
                      "val": 2 + len(sub["nodes"]), "group": "DOMAIN",
                      "flock": "DOMAIN", "grade": "verified",
                      "why": "domain declaration read and scanned"})
        for n in sub["nodes"]:
            nid = f"{name}/{n['id']}"
            if nid in seen:
                continue
            seen.add(nid)
            n = dict(n, id=nid)
            nodes.append(n)
            links.append({"source": hub_id, "target": nid, "relation": "CONTAINS",
                          "grade": n["grade"], "flock": n["flock"]})
    grades: dict[str, int] = {}
    for n in nodes:
        grades[n["grade"]] = grades.get(n["grade"], 0) + 1
    return {
        "nodes": nodes,
        "links": links,
        "meta": {
            "title": title or "YGGDRASIL — DOMAIN FOREST",
            "subtitle": f"{len([n for n in nodes if n['group'] == 'DOMAIN'])} domains · "
                        f"grades {grades}",
            "source": str(manifest),
            "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()[:16],
            "altitude_legend": "verified=AST-parsed | lead=counted only | cut=declared absent",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["domain", "environment", "forest"],
                    help="which atlas schema (forest = a forest_manifest)")
    ap.add_argument("atlas")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--domains", default="", help="forest mode: comma-separated subset")
    args = ap.parse_args()

    src = Path(args.atlas)
    if not src.exists():
        return _fail(f"no such atlas: {src}")
    if args.kind == "forest":
        restrict = [d.strip() for d in args.domains.split(",") if d.strip()] or None
        graph = convert_forest(src, args.title, restrict)
    else:
        graph = convert(src, args.title)
    out = Path(args.out)
    out.write_text(json.dumps(graph, indent=1, ensure_ascii=False), encoding="utf-8")
    grades: dict[str, int] = {}
    for n in graph["nodes"]:
        grades[n["grade"]] = grades.get(n["grade"], 0) + 1
    print(f"wrote {out}: {len(graph['nodes'])} nodes, {len(graph['links'])} links, grades={grades}")
    return 0


def _fail(msg: str) -> int:
    print(msg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
