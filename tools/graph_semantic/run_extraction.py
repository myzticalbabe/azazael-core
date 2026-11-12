"""Run the graph semantic-extraction pipeline in order.

Mechanical steps only. Step 4 (authoring nodes/edges) is the LLM's job — this
runner emits the digest for reading and then consumes whatever the authored
`semantic_layer.py` currently contains.

Usage (from the repo root):
    python tools/graph_semantic/run_extraction.py            # full pipeline
    python tools/graph_semantic/run_extraction.py --digest   # stop after the digest
    python tools/graph_semantic/run_extraction.py --no-build # skip merge/export
    python tools/graph_semantic/run_extraction.py --report   # also regenerate the report
    python tools/graph_semantic/run_extraction.py --report --heartbeat  # + PMB milestone
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# Order matters: ast_index and doc_scope are prerequisites for everything after.
STEPS = [
    ("ast_index.py", "label -> AST node id index (edge anchors)"),
    ("doc_scope.py", "enumerate project docs + exclusions"),
    ("doc_digest.py", "compact digest for the LLM to read"),
    ("semantic_layer.py", "emit the authored semantic fragment"),
    ("doc_layer.py", "one document node per doc + buckets + xrefs"),
    ("concept_layer.py", "deduped concept hubs from headings"),
    ("coverage_layer.py", "skill concepts, lexicon grouping, island bridges"),
    ("rationale_tranche2.py", "authored rationale for the next tranche of docs"),
    ("rationale_tranche3.py", "authored rationale for the remaining distinct docs"),
    ("enhanced_semantic.py", "entity mentions from every document body"),
    ("bridge_layer.py", "grounded edges from the last stranded components"),
]

BUILD = ("build_graph.py", "merge + cluster + label + export")


def child_env() -> dict[str, str]:
    """Environment for child steps: UTF-8 stdout, unconditionally.

    Steps print doc titles, which can contain non-ASCII (and BOMs). On a cp1252
    Windows console that raises UnicodeEncodeError and aborts the pipeline, so
    force UTF-8 on every child regardless of the caller's environment.
    """
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def run(script: str, label: str, *, argv: list[str] | None = None) -> int:
    print(f"\n=== {script} — {label} ===", flush=True)
    cmd = [sys.executable, str(HERE / script), *(argv or [])] if (HERE / script).exists() else [script, *(argv or [])]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        check=False,
        env=child_env(),
        timeout=1800,  # a full merge+cluster can take many minutes
    )
    if proc.returncode != 0:
        print(f"FAILED: {script} (exit {proc.returncode})", file=sys.stderr)
    return proc.returncode


def run_graphify_report() -> int:
    """Regenerate GRAPH_REPORT.md, then re-apply the corrected God Nodes section.

    `graphify cluster-only` rewrites the report from the graph and overwrites both
    the hub-derived community labels and any patched God Nodes section, so the
    patch is applied here immediately after, in one command, instead of being a
    step a human has to remember.
    """
    exe = shutil.which("graphify")
    if not exe:
        print("graphify not on PATH; skip report regeneration", file=sys.stderr)
        return 0
    print("\n=== graphify cluster-only + god_nodes --patch-report ===", flush=True)
    rc = subprocess.run([exe, "cluster-only", ".", "--no-viz", "--no-label"],
                        cwd=str(ROOT), check=False, env=child_env(), timeout=1800)
    if rc.returncode != 0:
        return rc.returncode
    return run("god_nodes.py", "correct the God Nodes ranking", argv=["--top", "20", "--patch-report"])


def run_heartbeat() -> int:
    """Best-effort: record the graph's headline stats as a PMB milestone.

    Optional (`--heartbeat`) because it depends on a host-specific PMB client. It
    never fails the build: a missing client or interpreter is reported and skipped.
    """
    graph = ROOT / "graphify-out/graph.json"
    client = ROOT / "tools/pmb_client.py"
    pmb_py = os.environ.get("PMB_PYTHON", r"C:\Users\clint\.pmb-venv\Scripts\python.exe")
    if not graph.exists() or not client.exists() or not Path(pmb_py).exists():
        print("PMB heartbeat skipped (no graph.json or pmb client)", file=sys.stderr)
        return 0
    data = json.loads(graph.read_text(encoding="utf-8"))
    nodes, links = data["nodes"], data["links"]
    types = collections.Counter(n.get("file_type") for n in nodes)
    state = {
        "date": time.strftime("%Y-%m-%d"),
        "nodes": len(nodes),
        "links": len(links),
        "self_loops": sum(1 for e in links if e["source"] == e["target"]),
        "document_nodes": types.get("document", 0),
        "rationale_nodes": sum(1 for n in nodes if n.get("rationale")),
        "mention_edges": sum(1 for e in links if e.get("relation") == "mentions"),
    }
    payload = {
        "chain_name": "azazael_graph_components",
        "title": f"build {state['date']}: {state['nodes']} nodes / {state['links']} links",
        "state": state,
        "importance": 0.5,
    }
    proc = subprocess.run(
        [pmb_py, str(client), "call", "record_milestone", json.dumps(payload)],
        cwd=str(ROOT), check=False, env=child_env(), timeout=120,
    )
    print(f"PMB heartbeat exit {proc.returncode}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--digest", action="store_true",
                    help="stop after the digest so the agent can author")
    ap.add_argument("--no-build", action="store_true",
                    help="emit fragments but do not merge/export the graph")
    ap.add_argument("--report", action="store_true",
                    help="after build, regenerate GRAPH_REPORT.md and re-apply the God Nodes patch")
    ap.add_argument("--heartbeat", action="store_true",
                    help="after build, record the graph stats as a PMB milestone (best-effort)")
    args = ap.parse_args()

    for script, label in STEPS:
        rc = run(script, label)
        if rc != 0:
            return rc
        if args.digest and script == "doc_digest.py":
            print("\nDigest ready: read graphify-out/_digest_p1..p4.txt, then edit "
                  "tools/graph_semantic/semantic_layer.py and re-run without --digest.",
                  flush=True)
            return 0

    if not args.no_build:
        rc = run(*BUILD)
        if rc != 0:
            return rc
        if args.report:
            rc = run_graphify_report()
            if rc != 0:
                return rc
        if args.heartbeat:
            run_heartbeat()
        if not args.report and not args.heartbeat:
            print("\nNext: python tools/graph_semantic/run_extraction.py --report "
                  "(or graphify cluster-only . --no-viz --no-label then "
                  "god_nodes.py --patch-report)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
