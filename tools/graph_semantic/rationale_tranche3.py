"""Third tranche of authored rationale — the remaining distinct high-value docs.

After tranche 1 (design intent) and tranche 2 (the large load-bearing docs), what
is left uncovered splits into two groups:

* **Duplicates** — the `src/CODEX/DOCUMENTATION/*` copies of `docs/*` (TD-00x,
  CURRENT_STATE, ADR, TASK_REGISTRY). Those are already attached to their canonical
  twin by `coverage_layer.py`, so authoring a second rationale for them would just
  be noise.
* **Distinct docs nobody had read** — the audit/lesson/session reports, the
  install and security docs, the API reference, the enhancement plan, the progress
  anchor, and this pipeline's own README + report.

This tranche covers the second group. Same conventions as tranche 2: node id ==
the doc-layer stem, every edge target must already exist.

Usage (from the repo root):
    python tools/graph_semantic/rationale_tranche3.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path("graphify-out")
FRAGMENTS = [
    ROOT / "semantic_fragment.json",
    ROOT / "doc_layer_fragment.json",
    ROOT / "concept_layer_fragment.json",
    ROOT / "coverage_layer_fragment.json",
    ROOT / "rationale_tranche2_fragment.json",
]

GRAPH = json.loads((ROOT / "graph.json").read_text(encoding="utf-8"))
EXISTING: set[str] = {n["id"] for n in GRAPH["nodes"]}
for frag_path in FRAGMENTS:
    if frag_path.exists():
        EXISTING |= {
            n["id"]
            for n in json.loads(frag_path.read_text(encoding="utf-8")).get("nodes", [])
        }


def stem(path: str) -> str:
    p = path[:-3] if path.lower().endswith(".md") else path
    return re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_")


DOCS: list[tuple[str, str, str, str]] = [
    ("reports/terence_scope_2026-09-10.md",
     "TERENCE work order — full codebase scope",
     "document",
     "The work order that scoped TERENCE across the whole codebase before the "
     "preview/status defects were fixed. Its value now is as the baseline the fixes "
     "are measured against."),
    ("LESSONS_NEURAL_ADVANCEMENT.md",
     "Lessons — neural advancement realignment",
     "rationale",
     "Realignment lessons for the neural work: which advances were real and which were "
     "naming. It is the counterpart to the mesh audits."),
    ("docs/CHANGELOG.md",
     "Azazael ecosystem changelog",
     "document",
     "The ecosystem-level changelog, distinct from the per-package one in "
     "src/ORCHESTRATION. Two changelogs are two timelines; read both or neither."),
    ("src/CODEX/DOCUMENTATION/GLOSSARY.md",
     "Azazael ecosystem glossary",
     "document",
     "The canonical term definitions. When a mandate and an implementation disagree on "
     "a word, the glossary is the tie-breaker."),
    ("docs/INFERENCE_INTEGRATION_STRATEGY.md",
     "Inference integration strategy",
     "document",
     "The 10-point integration plan for inference across the ecosystem, with the "
     "P0–P2 implementation order. It is the plan RULE 0D's failover enforces."),
    ("docs/LESSONS_SESSION_JUNE4.md",
     "Lessons — session June 4, 2026",
     "rationale",
     "Session lesson record. Dated and never edited, so it is evidence of what was "
     "believed at the time."),
    ("docs/Azazael_ENHANCEMENT_PLAN.md",
     "Azazael ML platform enhancement plan",
     "document",
     "Planned improvements to the ML platform suite. A plan, not a status report — "
     "check the harness docs for what actually shipped."),
    ("docs/LESSONS_TASK_I_DESKTOP_AUDIT.md",
     "Lessons — Task I: desktop app audit",
     "rationale",
     "Desktop-app audit lessons. The desktop audit is where several 'operational' "
     "projects turned out to be partial; this records which and why."),
    ("src/CODEX/DOCUMENTATION/SECURITY_AUDIT_CHECKLIST.md",
     "Security audit checklist",
     "document",
     "The checklist form of the security audit, so a review can be repeated and "
     "diffed rather than re-improvized."),
    ("docs/INSTALL_GUIDE.md",
     "Azazael Ubuntu bare-metal install guide",
     "document",
     "Bare-metal install on Ubuntu 26.04, including the offline wheel path. Pairs with "
     "the Linux install guide and the wheel-availability notes."),
    ("reports/HUB_TRIAGE_2026-10-02.md",
     "Hub backlog triage — 2026-10-02",
     "document",
     "Triage of the hub's untracked work. A triage document is a decision record: each "
     "item was adopted, deferred, or dropped."),
    ("reports/ORPHAN_ADOPTION_MESH_CLUSTER_2026-10-02.md",
     "Orphan adoption — mesh cluster",
     "document",
     "Adoption pass that found the mesh-cluster files stranded on the hub. It is the "
     "evidence for how the mesh code came back into the tree."),
    ("reports/ORPHAN_ADOPTION_SCRIPTS_TOOLS_2026-10-02.md",
     "Orphan adoption — scripts and tools",
     "document",
     "Adoption pass for the scripts/ and tools/ extras orphaned on the hub."),
    ("reports/FULL_SUITE_BASELINE_2026-10-02.md",
     "Full-suite baseline — 2026-10-02",
     "document",
     "The recorded test-suite baseline. A baseline is only useful with its date and "
     "its interpreter; both are stated here."),
    ("scripts/.progress_anchor.md",
     "Progress anchor — graphify refresh session",
     "document",
     "A resumable progress anchor written for a long session. The pattern exists so a "
     "long build is resumable rather than restartable."),
    ("SESSION_INSIGHTS_2026-07-30.md",
     "Session insights — 2026-07-30",
     "rationale",
     "One of the dated session-insight records. Together with the others it forms a "
     "timeline; a theme that recurs across several is the signal worth a rule."),
    ("SESSION_INSIGHTS_2026-07-07.md",
     "Session insights — 2026-07-07",
     "rationale",
     "Dated session-insight record, same role as the rest of the series."),
    ("tools/graph_semantic/README.md",
     "Graph semantic extraction — runbook",
     "document",
     "This pipeline's own runbook: how the graph gets its documentation layer, the run "
     "order, the traps that cost time, and the honest limits. Start here to re-run or "
     "extend the extraction."),
    ("reports/SEMANTIC_EXTRACTION_2026-10-03.md",
     "Full semantic extraction — method and measurements",
     "document",
     "The method, the before/after measurements, and the corrections for the semantic "
     "extraction pass. It records the two corrections that changed the approach "
     "(which graph a fragmentation figure belongs to; that fragmentation is an edge "
     "problem, not a clustering one)."),
    ("src/CODEX/DOCUMENTATION/API_REFERENCE.md",
     "Azazael ecosystem API reference",
     "document",
     "The generated API reference for the ecosystem surface."),
]

EDGES: list[tuple[str, str, str, str, float]] = [
    ("reports_terence_scope_2026_09_10", "terence_doc", "conceptually_related_to", "INFERRED", 0.9),
    ("reports_terence_scope_2026_09_10", "reports_error_lesson_audit", "references", "INFERRED", 0.7),
    ("lessons_neural_advancement", "neural_mesh_architecture_audit_doc", "conceptually_related_to", "INFERRED", 0.8),
    ("docs_changelog", "changelog_doc", "conceptually_related_to", "INFERRED", 0.85),
    ("docs_changelog", "docs_readme_index", "references", "INFERRED", 0.7),
    ("src_codex_documentation_glossary", "docs_readme_index", "references", "INFERRED", 0.7),
    ("docs_inference_integration_strategy", "mandates_rule_0d_inference", "rationale_for", "INFERRED", 0.9),
    ("docs_inference_integration_strategy", "inference_free_endpoints", "conceptually_related_to", "INFERRED", 0.8),
    ("docs_lessons_session_june4", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.75),
    ("docs_azazael_enhancement_plan", "ml_platforms_harness", "references", "INFERRED", 0.85),
    ("docs_azazael_enhancement_plan", "ml_platforms_documentation_protocol", "conceptually_related_to", "INFERRED", 0.7),
    ("docs_lessons_task_i_desktop_audit", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.75),
    ("src_codex_documentation_security_audit_checklist", "anti_pattern_scanner", "conceptually_related_to", "INFERRED", 0.7),
    ("docs_install_guide", "platform_multi_deploy", "references", "INFERRED", 0.9),
    ("docs_install_guide", "platform_cp314t_wheels", "conceptually_related_to", "INFERRED", 0.8),
    ("reports_hub_triage_2026_10_02", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.75),
    ("reports_orphan_adoption_mesh_cluster_2026_10_02", "neural_mesh_architecture_audit_doc",
     "conceptually_related_to", "INFERRED", 0.8),
    ("reports_orphan_adoption_scripts_tools_2026_10_02", "projects_map_doc", "conceptually_related_to", "INFERRED", 0.7),
    ("reports_full_suite_baseline_2026_10_02", "tools_type_check_gate", "conceptually_related_to", "INFERRED", 0.7),
    ("scripts_progress_anchor", "graphify_atlas_doc", "conceptually_related_to", "INFERRED", 0.7),
    ("session_insights_2026_07_30", "azazael_insights", "conceptually_related_to", "INFERRED", 0.8),
    ("session_insights_2026_07_07", "azazael_insights", "conceptually_related_to", "INFERRED", 0.8),
    ("tools_graph_semantic_readme", "graphify_atlas_doc", "references", "INFERRED", 0.9),
    ("reports_semantic_extraction_2026_10_03", "graphify_atlas_doc", "references", "INFERRED", 0.9),
    ("reports_semantic_extraction_2026_10_03", "measure_before_optimizing", "rationale_for", "INFERRED", 0.75),
    ("src_codex_documentation_api_reference", "ml_platforms_documentation_protocol", "references", "INFERRED", 0.7),
]

nodes: list[dict] = []
edges: list[dict] = []
ids: set[str] = set()
for path, label, ftype, rationale in DOCS:
    nid = stem(path)
    if nid in ids:
        continue
    ids.add(nid)
    nodes.append({"id": nid, "label": label, "file_type": ftype, "source_file": path,
                  "source_location": None, "source_url": None, "captured_at": None,
                  "author": None, "contributor": None, "rationale": rationale})

have = EXISTING | ids
skipped = 0
for s, t, rel, conf, score in EDGES:
    if s not in have or t not in have or s == t:
        skipped += 1
        print(f"  SKIP edge (missing endpoint): {s} -> {t}")
        continue
    edges.append({"source": s, "target": t, "relation": rel, "confidence": conf,
                  "confidence_score": score, "source_file": "tools/graph_semantic/rationale_tranche3.py",
                  "source_location": None, "weight": 1.0})

print(f"tranche3 docs: {len(nodes)}  edges: {len(edges)}  skipped: {skipped}")
frag = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
out = ROOT / "rationale_tranche3_fragment.json"
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out)
