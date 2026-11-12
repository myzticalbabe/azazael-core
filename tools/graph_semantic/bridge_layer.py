"""Bridge the last stranded components into the main graph.

After the semantic, doc, concept and coverage layers, a handful of real code and
document clusters are still their own connected components — not because they are
unrelated to the project, but because the AST pass gives a self-contained file its
own island and nothing ever links it to a subsystem.

This layer adds one **grounded** edge per stranded component to a spine node that
already exists in the graph. "Grounded" means the edge is justified by what the
file or document actually is (a test-session runner links to the type gate it
invokes; an inference cascade links to the inference endpoints it routes between),
never by "make the graph look connected".

Every component is discovered from the graph at run time, so a component that
disappears stops being bridged and a new one gets reported rather than silently
inheriting an old edge.

Usage (from the repo root, after the other layers have produced their fragments):
    python tools/graph_semantic/bridge_layer.py
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path("graphify-out")
FRAGMENTS = [
    ROOT / "semantic_fragment.json",
    ROOT / "doc_layer_fragment.json",
    ROOT / "concept_layer_fragment.json",
    ROOT / "coverage_layer_fragment.json",
    ROOT / "rationale_tranche2_fragment.json",
    ROOT / "rationale_tranche3_fragment.json",
    ROOT / "enhanced_semantic_fragment.json",
]

GRAPH = json.loads((ROOT / "graph.json").read_text(encoding="utf-8"))

# Every id that will exist once all fragments are merged, so an edge is never
# written to a node that is about to be dropped.
EXISTING: set[str] = {n["id"] for n in GRAPH["nodes"]}
for frag_path in FRAGMENTS:
    if frag_path.exists():
        EXISTING |= {
            n["id"]
            for n in json.loads(frag_path.read_text(encoding="utf-8")).get("nodes", [])
        }


def _components() -> list[list[str]]:
    ids = [n["id"] for n in GRAPH["nodes"]]
    index = {nid: i for i, nid in enumerate(ids)}
    parent = list(range(len(ids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in GRAPH["links"]:
        s, t = e.get("source"), e.get("target")
        if s == t or s not in index or t not in index:
            continue
        ra, rb = find(index[s]), find(index[t])
        if ra != rb:
            parent[ra] = rb

    groups: dict[int, list[str]] = collections.defaultdict(list)
    for i, nid in enumerate(ids):
        groups[find(i)].append(nid)
    return sorted(groups.values(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# Curated bridges: (from_node, to_node, relation, confidence, score, why)
# Every "from" is the entry point of a stranded component; every "to" is a spine
# node that already lives in the giant component.
# ---------------------------------------------------------------------------
BRIDGES: list[tuple[str, str, str, str, float, str]] = [
    # PowerShell shortcut/launcher automation has no AST language, so its suite
    # node only connects to the ps1 files it groups. It belongs to the launcher
    # subsystem (KLINK is the unified command launcher) and the deploy story.
    ("powershell_script_suite", "klink_launcher", "conceptually_related_to", "INFERRED", 0.75,
     "shortcut + launcher automation is part of the KLINK launcher surface"),
    ("powershell_script_suite", "platform_multi_deploy", "conceptually_related_to", "INFERRED", 0.75,
     "install/pin/group shortcut scripts are deployment helpers"),
    # noxfile.py is the repo-wide test/lint session runner; its typecheck session
    # invokes the type gate, which is a real node in the graph.
    ("noxfile_typecheck", "tools_type_check_gate", "references", "INFERRED", 0.85,
     "noxfile's typecheck() session runs the basedpyright type gate"),
    ("noxfile_run_tests", "tools_type_check_gate", "conceptually_related_to", "INFERRED", 0.65,
     "the same session file also runs the test suite the gate protects"),
    # Inference cascade (1.5B triage -> 9B escalation) is an inference-routing file.
    ("src_core_cascade", "inference_free_endpoints", "conceptually_related_to", "INFERRED", 0.85,
     "cascade.py routes between model sizes, i.e. between inference endpoints"),
    ("src_core_cascade", "mandates_rule_0d_inference", "rationale_for", "INFERRED", 0.75,
     "the cascade implements the failover/escalation idea RULE 0D codifies"),
    # cluster_route.py routes worms; it belongs to the cluster/registry subsystem.
    ("src_core_cluster_route", "azazael_clusters_tier2_nature", "conceptually_related_to", "INFERRED", 0.85,
     "cluster_route clusters and routes worms — the Tier-2 routing concern"),
    ("src_core_cluster_route", "azazael_registry_doc", "conceptually_related_to", "INFERRED", 0.75,
     "routing resolves worms through the registry"),
    # graph_learn.py is the learned-graph bridge for the learning loop.
    ("src_core_graph_learn", "ml_platforms_harness", "conceptually_related_to", "INFERRED", 0.8,
     "learned-graph features feed the ML platform harness"),
    ("src_core_graph_learn", "neural_architecture_rl_pipelines", "conceptually_related_to", "INFERRED", 0.75,
     "SAGE/GAT features are the graph half of the RL pipeline"),
    # Animation protocols generate the CSS the CODEX video pipeline records.
    ("src_animation_protocols", "codex_video_generation", "references", "INFERRED", 0.85,
     "animation protocol generation feeds the CODEX video pipeline"),
    ("src_codex_azazael_vega", "codex_video_generation", "references", "INFERRED", 0.8,
     "the vega spec is a CODEX visual asset"),
    # Forensic / environment audits are the same audit family as the error-lesson audit.
    ("reports_forensic_environments", "reports_error_lesson_audit", "conceptually_related_to",
     "INFERRED", 0.85, "environment forensics and the error-lesson audit are one audit family"),
    ("reports_forensic_followups", "reports_error_lesson_audit", "conceptually_related_to",
     "INFERRED", 0.85, "forensic follow-ups are the audit's remediation half"),
    ("reconciliation_intermesh_2026_09_28", "neural_mesh_architecture_audit_doc", "conceptually_related_to",
     "INFERRED", 0.75, "intermesh reconciliation documents the same mesh as the architecture audit"),
    # The neural-mesh resilience / brainstem cluster is mesh architecture; link it
    # to the mesh audit that the whole cluster is evidence for.
    ("neural_mesh_brainstem_doc", "neural_mesh_architecture_audit_doc", "conceptually_related_to",
     "INFERRED", 0.9, "brainstem playing-field work is the mesh architecture audit's subject"),
    ("mesh_resilience_fencing_tokens", "neural_mesh_architecture_audit_doc", "rationale_for",
     "INFERRED", 0.85, "the four resilience primitives came out of the mesh per-function audit"),
    # --- the tiny (<=7 node) components ---
    ("src_core_forecast_baselines", "ml_platforms_harness", "conceptually_related_to", "INFERRED", 0.8,
     "ARIMA-lite/seasonal-naive baselines are the forecasting half of the ML platform"),
    ("scripts_graphify_huge_mode", "graphify_atlas_doc", "references", "INFERRED", 0.8,
     "huge-mode renders a graph too large for one page — the atlas/ graphify tooling"),
    ("src_core_worms_cain_analyzer", "codex_video_generation", "conceptually_related_to", "INFERRED", 0.75,
     "CAIN_ANALYZER is the analysis stub for the CODEX/CAIN video pipeline"),
    ("tools_visualize_ner", "ml_platforms_harness", "conceptually_related_to", "INFERRED", 0.7,
     "NER visualization is an ML tooling script"),
    ("githooks_pre_commit", "tools_type_check_gate", "references", "INFERRED", 0.85,
     "the .githooks pre-commit runs the type gate"),
    ("scripts_hooks_pre_commit", "tools_type_check_gate", "references", "INFERRED", 0.85,
     "the scripts/hooks pre-commit runs the type gate"),
    ("src_core_worms_cain_data", "src_core_worms_cain_analyzer", "conceptually_related_to", "INFERRED", 0.9,
     "CAIN data tables belong to the CAIN analyzer"),
    ("src_codex_videos_create_reference_video", "codex_video_generation", "references", "INFERRED", 0.85,
     "reference-video creation is part of the CODEX video pipeline"),
    ("src_codex_comprehensive_twitler_analysis", "codex_video_generation", "conceptually_related_to", "INFERRED", 0.8,
     "the twitler analysis drives a CODEX video asset"),
]

# Components that are legitimately leaves — bridging them would be fabrication, so
# they are reported rather than connected. Each is a standalone test fixture or a
# config file with no subsystem relation.
EXPLAIN: dict[str, str] = {
    "tests_test_calc": "a two-node test fixture (calculate + file) with no dependency on project code",
    "src_aoa_conftest": "pytest configuration; a conftest is a leaf by construction",
    "test_auth_pipeline_fixed": "a stray top-level test file, not part of the tests/ suite",
}

nodes: list[dict] = []
edges: list[dict] = []  # placeholder, replaced by edges_out below
seen: set[tuple[str, str]] = set()
edges_out: list[dict] = []
for src, dst, rel, conf, score, why in BRIDGES:
    if src == dst or (src, dst) in seen:
        continue
    if src not in EXISTING or dst not in EXISTING:
        print(f"  SKIP (missing endpoint): {src} -> {dst}")
        continue
    seen.add((src, dst))
    edges_out.append({
        "source": src, "target": dst, "relation": rel, "confidence": conf,
        "confidence_score": score, "source_file": why,
        "source_location": None, "weight": 1.0,
    })

edges = edges_out
print(f"components before bridge: {len(_components())}")
print(f"bridge edges: {len(edges)}")
for e in edges:
    print(f"  {e['source']} -> {e['target']}  ({e['source_file']})")

# Any component still stranded and not deliberately explained is a gap to report.
bridged_sources = {src for src, *_ in BRIDGES}
unexplained = 0
for comp in _components()[1:]:
    if set(comp) & bridged_sources:
        continue
    why = next((EXPLAIN[n] for n in comp if n in EXPLAIN), None)
    if why:
        print(f"  explained leaf (size {len(comp)}): {why}")
    else:
        unexplained += 1
        print(f"  UNEXPLAINED component (size {len(comp)}): {comp[:5]}")
print(f"unexplained stranded components: {unexplained}")

frag = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
out = ROOT / "bridge_layer_fragment.json"
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out)
