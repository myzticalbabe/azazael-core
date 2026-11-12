"""Audit the authored ANCHORS map — does every anchor actually land on a node?

The semantic layer claims "every authored node is anchored to a real AST symbol".
That claim is only true if each ``ANCHORS`` entry:

1. names a node that exists in ``NODES`` (a typo'd key is silently skipped by
   ``emit()`` because of the ``if sid not in NODE_IDS: continue`` guard), and
2. gives at least one label that resolves to an AST node in ``_ast_index.json``
   (an unresolvable label contributes zero ``references`` edges).

There is a third, less obvious failure shape this tool also reports: **graphify
rewrites a semantic node id to its source-file stem on merge.** An authored node
``type_check_gate`` with ``source_file ``tools/type_check_gate.py`` lands on disk
as ``tools_type_check_gate``. Edges emitted with the authored id are rewritten
consistently (graphify processes the fragment as a unit), so the anchor still
works — but the authored id is not the on-disk id, which matters to anything that
tries to look the node up by the id written in this repo.

Usage (from the repo root):
    python tools/graph_semantic/audit_anchors.py
    python tools/graph_semantic/audit_anchors.py --json
Exit code is 1 if any *hard* problem is found (missing node / unresolved label).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path("graphify-out")
SEM = Path("tools/graph_semantic/semantic_layer.py")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def parse_anchors(src: str) -> list[tuple[str, list[str]]]:
    m = re.search(r"ANCHORS: dict\[str, list\[str\]\] = \{(.*?)\n\}", src, re.S)
    if not m:
        return []
    out = []
    for sid, body in re.findall(r'"([^"]+)"\s*:\s*\[([^\]]*)\]', m.group(1)):
        out.append((sid, re.findall(r'"([^"]+)"', body)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    anchors = parse_anchors(SEM.read_text(encoding="utf-8"))
    node_ids = {
        n["id"]
        for n in json.loads((ROOT / "semantic_fragment.json").read_text(encoding="utf-8"))["nodes"]
    }
    by_label: dict[str, list[str]] = json.loads(
        (ROOT / "_ast_index.json").read_text(encoding="utf-8")
    )["by_label"]
    on_disk = {
        n["id"]
        for n in json.loads((ROOT / "graph.json").read_text(encoding="utf-8"))["nodes"]
    }

    missing_node: list[str] = []
    unresolved: list[tuple[str, str]] = []
    rewritten: list[tuple[str, str]] = []
    for sid, labels in anchors:
        if sid not in node_ids:
            missing_node.append(sid)
            continue
        if sid not in on_disk:
            rewritten.append((sid, sid if sid in on_disk else f"(rewritten; on-disk id differs)"))
        for lab in labels:
            if not by_label.get(norm(lab)):
                unresolved.append((sid, lab))

    report = {
        "anchors": len(anchors),
        "missing_node": missing_node,
        "unresolved_labels": [{"node": s, "label": l} for s, l in unresolved],
        "authored_id_rewritten_on_disk": [s for s, _ in rewritten],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"anchors: {len(anchors)}")
        print(f"missing NODES key: {len(missing_node)} {missing_node}")
        print(f"unresolved labels: {len(unresolved)} {unresolved}")
        print(f"authored id differs on disk: {len(rewritten)}")
        for s, _ in rewritten:
            print(f"   {s} -> rewritten by graphify to a source-file-stem id")
    hard = len(missing_node) + len(unresolved)
    print("OK" if hard == 0 else f"FAIL: {hard} hard problem(s)", file=sys.stderr)
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
