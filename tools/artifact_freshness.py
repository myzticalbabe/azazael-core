#!/usr/bin/env python3
# coding: utf-8
"""Derived-artifact freshness audit.

Every artifact in REGISTRY is *derived* from source. A derived artifact older
than its source is a stale map, and stale maps get acted on: the PF16 census,
the wire-or-cut table, the integration map and the knowledge graph were each
consumed while stale at least once before the 2026-09-18 refresh
(NEURAL_MESH_FUNCTION_AUDIT_ANALYSIS_2026-09-16.md, section 12). This is the
check that catches it before the map is trusted, rather than after.

It also checks one thing mtime cannot see: whether `graphify-out/GRAPH_REPORT.md`
describes the graph sitting next to it, or a different one. Regenerating a
report from a re-derived graph while the graph.json write is refused (graphify's
shrink guard) leaves the pair describing two different graphs -- both files
fresh, neither agreeing. Freshness alone cannot detect that; the clustered-ness
of graph.json can.

Exit 0 = no stale artifact and no report/graph mismatch.
Exit 1 = at least one problem, each printed with its evidence.

Usage:
    python tools/artifact_freshness.py
    python tools/artifact_freshness.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]

# (artifact, source roots, source patterns). Roots are relative to the repo root
# so the audit is portable across the W11 / WSL / bare-metal checkouts.
REGISTRY: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # NEURAL_MESH_FUNCTION_TODO.md cut 2026-10-18 (verified, not assumed): a
    # generated probe census, not task state -- 0 of its 3,659 boxes were ever
    # ticked and a rebuild (one `tools/mesh_function_preflight.py` run) no
    # longer matched the tracked copy. Keeping it tracked would turn every
    # future audit into a permanent `missing` verdict, and spine.py's
    # proprioception gate reads this module, so a missing row reads as stale.
    ("NEURAL_MESH_WIRE_OR_CUT.json", ("src",), ("*.py",)),
    ("NEURAL_MESH_WIRE_OR_CUT_WORKLIST.md", ("src",), ("*.py",)),
    # The three intermesh artifacts (NEURAL_MESH_INTEGRATION_MAP.json,
    # NEURAL_MESH_INTEROP.mmd, logs/capability_wiring_dataset.jsonl) are NOT
    # listed: their producer, tools/nature_integration_map.py, was removed on
    # 2026-10-09 with the nature taxonomy it consumed (U3 surgery / D1). An
    # artifact with no generator can only ever go stale, so tracking it would
    # turn this audit into a permanent failure. They stay on disk as the
    # forensic record of the removed taxonomy -- the preflight exclusion
    # regression asserts their filenames -- and the taxonomy itself was
    # re-extracted into state/RECOVERED_NATURE_DEFINITIONS.json.
    ("graphify-out/graph.json", ("src",), ("*.py",)),
    ("graphify-out/GRAPH_REPORT.md", ("src",), ("*.py",)),
)

GRAPH_JSON = "graphify-out/graph.json"
GRAPH_REPORT = "graphify-out/GRAPH_REPORT.md"
GRAPH_MANIFEST_GLOB = "graphify-out/**/manifest.json"


def source_files(root: Path, roots: Sequence[str],
                 patterns: Sequence[str]) -> list[Path]:
    """Every source file an artifact is derived from."""
    found: list[Path] = []
    for rel in roots:
        base = root / rel
        if not base.is_dir():
            continue
        for pattern in patterns:
            found.extend(p for p in base.rglob(pattern) if p.is_file())
    return found


def audit(root: Path | None = None,
          registry: Iterable[tuple[str, tuple[str, ...], tuple[str, ...]]] | None = None
          ) -> list[dict[str, Any]]:
    """One row per artifact: exists, mtime, and which sources are newer.

    `registry` is bound late (not as a def-time default) so a caller -- or a
    test -- can substitute the set being audited without editing the module.
    """
    root = Path(root) if root is not None else ROOT
    if registry is None:
        registry = REGISTRY
    rows: list[dict[str, Any]] = []
    for artifact, roots, patterns in registry:
        path = root / artifact
        row: dict[str, Any] = {"artifact": artifact, "exists": path.is_file()}
        if not row["exists"]:
            row.update({"stale": True, "newer_count": 0, "newer": [],
                        "reason": "missing"})
            rows.append(row)
            continue
        stamp = path.stat().st_mtime
        newer = [p for p in source_files(root, roots, patterns)
                 if p.stat().st_mtime > stamp]
        newer.sort(key=lambda p: -p.stat().st_mtime)
        row.update({
            "stale": bool(newer),
            "newer_count": len(newer),
            # as_posix(), not str(): the audit runs on W11, WSL and bare metal,
            # and a Windows separator makes the same evidence non-comparable
            # across environments (and breaks the pinned test).
            "newer": [p.relative_to(root).as_posix() for p in newer[:5]],
            "reason": "older than source" if newer else "",
        })
        rows.append(row)
    return rows


def graph_report_mismatch(root: Path | None = None) -> dict[str, Any]:
    """Does GRAPH_REPORT.md describe the graph in graph.json?

    The failure this catches: a report regenerated from a re-derived clustered
    graph while the graph.json write was refused, leaving a fresh report and a
    fresh graph that disagree about communities. Both files pass an mtime
    check, so only the content can tell.
    """
    root = Path(root) if root is not None else ROOT
    graph, report = root / GRAPH_JSON, root / GRAPH_REPORT
    out: dict[str, Any] = {"checked": False, "mismatch": False, "reason": ""}
    if not (graph.is_file() and report.is_file()):
        out["reason"] = "graph.json or GRAPH_REPORT.md absent"
        return out
    try:
        data = json.loads(graph.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out.update({"checked": True, "mismatch": True,
                    "reason": "graph.json unreadable: %s" % str(exc)[:80]})
        return out
    nodes = data.get("nodes") or []
    clustered = any(n.get("community") is not None for n in nodes)
    out.update({
        "checked": True,
        "nodes": len(nodes),
        "clustered": clustered,
        "report_newer": report.stat().st_mtime > graph.stat().st_mtime,
    })
    if out["report_newer"] and not clustered:
        out.update({"mismatch": True,
                    "reason": ("GRAPH_REPORT.md is newer than graph.json but "
                               "graph.json carries no communities: the report "
                               "describes a re-derived graph, not this one")})
    return out


def newest_manifest(root: Path) -> Path | None:
    """The manifest of the most recent graphify run under `root`.

    A manifest records the input set of a derivation: every file graphify
    ingested, with the md5 of the bytes as they were at the time. It is the
    only place in the repo that states what the graph was actually built from,
    which is why `graph_content_drift` reads it instead of a hand-maintained
    file list that can silently fall behind the tool's real inputs.

    Recursive, and newest-by-mtime rather than sorted-by-name. `graphify-out`
    holds more than one manifest at different depths and with different
    schemas: the current one sits at the top (3,668 relative keys, matching
    graph.json's own mtime), a date-stamped copy sits one level down, and an
    older nested `graphify-out/graphify-out/manifest.json` carries 1,368
    *absolute* Windows keys from a run eleven days earlier. Sorting by name
    picks that stale nested one, because "graphify-out" sorts after a date.
    """
    found = [p for p in root.glob(GRAPH_MANIFEST_GLOB) if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def graph_content_drift(root: Path | None = None) -> dict[str, Any]:
    """Has any file changed since the graph was derived?

    An mtime comparison cannot answer this in a git checkout. `git checkout`
    and `git clone` stamp every tracked file with the checkout time, so a
    committed artifact is *always* newer than the sources it was derived from
    and the mtime check reports "current" however wrong the content has
    drifted. This was not hypothetical: `NEURAL_MESH_FUNCTION_TODO.md` pointed
    at `temporal_cross_validation` L438 when the function sits at L442, and
    the mtime check called it current with zero newer sources.

    The manifest's `ast_hash` is a plain md5 of the file's bytes, so content
    drift is decidable without mtimes at all. A file whose current md5 differs
    from the recorded one is a source the graph was derived from and no longer
    matches it -- the graph is stale with respect to that file, full stop.
    """
    root = Path(root) if root is not None else ROOT
    out: dict[str, Any] = {"checked": False, "reason": "",
                           "total": 0, "changed": [], "missing": []}
    manifest = newest_manifest(root)
    if manifest is None:
        out["reason"] = "no graphify manifest under graphify-out/"
        return out
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out.update({"checked": True, "reason": "manifest unreadable: %s" % str(exc)[:80]})
        return out
    if not isinstance(recorded, dict):
        out.update({"checked": True, "reason": "manifest is not an object"})
        return out

    changed, missing = [], []
    for rel, entry in recorded.items():
        if not isinstance(entry, dict):
            continue
        # Some manifests key by absolute path (an older nested run does). Make
        # the key relative so the evidence reads the same on every machine and
        # so the same check works on the WSL and bare-metal checkouts.
        path = Path(rel)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = path.name
        if not (root / rel).is_file():
            missing.append(rel)
            continue
        recorded_hash = entry.get("ast_hash") or ""
        if not recorded_hash:
            continue
        try:
            actual = hashlib.md5((root / rel).read_bytes()).hexdigest()
        except OSError:
            missing.append(rel)
            continue
        if actual != recorded_hash:
            changed.append(rel)
    out.update({"checked": True, "reason": "", "manifest": manifest.relative_to(root).as_posix(),
                "total": len(recorded), "changed": sorted(changed), "missing": sorted(missing)})
    return out


def main(argv: Sequence[str] | None = None,
         registry: Iterable[tuple[str, tuple[str, ...], tuple[str, ...]]] | None = None
         ) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--root", default=None, help="repo root override (tests)")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else ROOT
    rows = audit(root, registry)
    graph = graph_report_mismatch(root)
    drift = graph_content_drift(root)
    stale = [r for r in rows if r["stale"]]
    drifted = bool(drift["checked"] and (drift["changed"] or drift["missing"]))

    if args.json:
        print(json.dumps({"artifacts": rows, "graph": graph,
                          "content_drift": drift,
                          "stale_count": len(stale)}, indent=2))
        return 1 if stale or graph["mismatch"] or drifted else 0

    print("%-44s %-8s %s" % ("ARTIFACT", "STATUS", "NEWER SOURCES"))
    for row in rows:
        if not row["exists"]:
            status, detail = "MISSING", "-"
        elif row["stale"]:
            status, detail = "STALE", "%d (e.g. %s)" % (
                row["newer_count"], ", ".join(row["newer"][:2]))
        else:
            status, detail = "current", "0"
        print("%-44s %-8s %s" % (row["artifact"], status, detail))

    print()
    if not graph["checked"]:
        print("graph/report: not checked (%s)" % graph["reason"])
    elif graph["mismatch"]:
        print("graph/report: MISMATCH -- %s" % graph["reason"])
    else:
        print("graph/report: consistent (nodes=%d clustered=%s)"
              % (graph.get("nodes"), graph.get("clustered")))

    if not drift["checked"]:
        print("graph inputs: not checked (%s)" % drift["reason"])
    elif drifted:
        print("graph inputs: DRIFTED -- %d of %d files changed since %s, "
              "%d missing (md5 vs recorded ast_hash; mtime cannot see this "
              "after a git checkout)"
              % (len(drift["changed"]), drift["total"], drift["manifest"],
                 len(drift["missing"])))
        for rel in drift["changed"][:5]:
            print("    changed: %s" % rel)
        for rel in drift["missing"][:5]:
            print("    missing: %s" % rel)
    else:
        print("graph inputs: %d files match the manifest" % drift["total"])

    if stale or graph["mismatch"] or drifted:
        print()
        print("FAIL: %d stale artifact(s)%s%s"
              % (len(stale), ", plus a graph/report mismatch"
                 if graph["mismatch"] else "",
                 ", plus %d changed graph input(s)" % len(drift["changed"])
                 if drifted else ""))
        print("Refresh with: python tools/mesh_function_preflight.py "
              "&& python tools/mesh_function_preflight.py --verdict "
              "&& python tools/refresh_all.py")
        return 1
    print()
    print("OK: every derived artifact is at least as new as its sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
