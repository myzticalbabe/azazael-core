"""Second tranche of authored rationale — the high-value docs the first pass skipped.

The first authored pass covered the ~120 documents that carry *design intent*
(governance, architecture, findings). That left a long tail of large documents
which are genuinely load-bearing but which the pass had not read: the task
registry, the research record, the full-project audits, the session-insight and
lesson records, the worm audit, the project map, and the cross-environment
manifest. They already have identity from the doc layer; this layer gives them a
rationale and an edge to the subsystem they belong to.

Same conventions as `semantic_layer.py`/`coverage_layer.py`:
- node id == the doc-layer stem (`{path_stem}`), so graphify does not have to
  rewrite it and it lines up with the `document` node it upgrades;
- `file_type` in {document, rationale};
- every edge target must already exist on disk or in an earlier fragment.

Usage (from the repo root, after semantic/doc/concept/coverage fragments exist):
    python tools/graph_semantic/rationale_tranche2.py
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


# ---------------------------------------------------------------------------
# (path, label, file_type, rationale)
# ---------------------------------------------------------------------------
DOCS: list[tuple[str, str, str, str]] = [
    ("docs/TASK_REGISTRY.md",
     "Task Registry — ecosystem unconsolidation backlog",
     "document",
     "An itemized registry of unconsolidation, deduplication and cataloging tasks, each with a "
     "priority. Its value is that it enumerates the work the audit found, so the registry itself "
     "is the evidence for whichever items are still open. The CODEX/DOCUMENTATION copy is a twin."),
    ("research/RESEARCH_RECORD.md",
     "Research Record — measurement findings",
     "document",
     "A research log whose claims are backed by measurement (SQLite pragmas, memory-component "
     "sharing, PyO3 traversal). It is the record the architecture decisions cite, which is why "
     "its numbers must be read with their date."),
    ("NEURAL_MESH_FUNCTION_AUDIT_ANALYSIS_2026-09-16.md",
     "Neural Mesh — per-function audit analysis",
     "rationale",
     "The analysis half of the per-function audit: what the census implies, and the primitives it "
     "argues for. This is the document the mesh-resilience primitives (fencing tokens, ledger, "
     "liveness, tombstones) were derived from."),
    ("CATCHALL_BACKLOG.md",
     "Catch-All Refactoring Backlog (graphify-prioritized)",
     "document",
     "A refactor backlog ordered by centrality. It is the highest-degree document in the graph "
     "(583 edges) precisely because it lists per-line file detail — the reference-hub defect the "
     "god-node filter exists to correct."),
    ("AZAZAEL_INSIGHTS.md",
     "Azazael Insights — session memories & architectural reflections",
     "rationale",
     "Accumulated architectural reflections across sessions. Read as a source of recurring themes "
     "rather than a changelog: the same lessons reappearing here are the ones worth a permanent rule."),
    ("FULL_PROJECT_AUDIT_2026-07-30.md",
     "Full project audit — 2026-07-30",
     "document",
     "A whole-tree audit snapshot. Useful paired with the 2026-09-07 audit: the difference between "
     "two audits is the only honest measure of what actually changed."),
    ("FULL_PROJECT_AUDIT_2026-09-07.md",
     "Full project audit — 2026-09-07",
     "document",
     "The later whole-tree audit. Compared against the 2026-07-30 audit it shows which findings "
     "were fixed and which merely re-reported — the pattern the error-lesson audit was created for."),
    ("SESSION_INSIGHTS_2026-05-29.md",
     "Session insights — 2026-05-29",
     "rationale",
     "Per-session insight capture. Sessions are dated and never edited, so they are a timeline of "
     "what was believed at the time, not current state."),
    ("docs/LESSONS_TASK_K_DEVTOOLS.md",
     "LESSONS Task K — Chrome DevTools re-alignment",
     "rationale",
     "Lesson record for the DevTools re-alignment task: which audit panels answered which question. "
     "It is the reason the DevTools skills exist as a set."),
    ("LEARNING_LOOP_AUDIT_20260907.md",
     "Learning-loop subsystem audit — 2026-09-07",
     "document",
     "Audit of the learning loop (reward, evolution cycles, model selection). Its central finding is "
     "that an audit instrument was trusted before it was validated — the defect class the "
     "stale-artifact self-audit skill codifies."),
    ("docs/WORM_AUDIT.md",
     "Worm audit — functionality, race conditions, error recovery",
     "document",
     "Sub-agent audit of worm behavior under concurrency: race conditions and error recovery. It is "
     "the source of several of the free-threading hazard notes."),
    ("docs/PROJECT_MAP.md",
     "Project map — complete ecosystem inventory",
     "document",
     "A complete inventory of the ecosystem. Like CURRENT_STATE, it is a photograph; treat counts in "
     "it as of its date, and prefer the registry for live membership."),
    ("CROSS_ENV_MANIFEST.md",
     "Cross-environment manifest",
     "document",
     "The five-environment topology (Windows A/B, WSL, Kali, G:/) and what is expected to exist in "
     "each. Its AOA copy is a twin. It is what makes 'which tree was this measured on' answerable."),
    ("LESSONS_2026-06-04.md",
     "Lessons 2026-06-04 — G: drive reorganization",
     "rationale",
     "Lesson record of the G: drive reorganization and worm integration, including the degradation "
     "of G: as an archive. It is where the 'archive is stale, do not treat as source' rule came from."),
    ("docs/RUST_CONVERSION_TRACKER.md",
     "Rust conversion — living progress tracker",
     "document",
     "Per-module status of the Rust (pyo3) conversion effort. A 'living' tracker is only current on "
     "its last edit, so it is read with the worm-manifest project, not instead of it."),
    ("SYNC.md",
     "Quartet sync strategy",
     "document",
     "The A/B/WSL/Kali sync strategy and the constraints that shape it (B refusing a direct push, the "
     "ff-only fetch recipe, environment-specific paths)."),
    ("docs/WORM_PROPOSALS.md",
     "Worm proposals — new worms for 3.14t free-threading",
     "document",
     "Proposed worms and the free-threading rationale for them. Proposals are not registrations: a "
     "worm here may not exist in the registry yet."),
    ("SESSION_SUMMARY_2026-06-03.md",
     "Session summary — 2026-06-03",
     "document",
     "Dated session summary. Superseded once the same material is folded into a lesson or a rule."),
    ("algorithms/algorithm_log.md",
     "Algorithmic respawn log",
     "document",
     "The algorithm log for the algorithmic-respawn system: each modification, the pre-analysis, and "
     "the per-file validation — the audit trail the algorithmic-respawn mandate requires."),
    ("docs/LESSONS_TASK_H_IMPORT_ELIMINATION.md",
     "LESSONS Task H — function-body import elimination",
     "rationale",
     "Completion record for eliminating imports from inside function bodies. It is the counterpart to "
     "the scanner's `import_inside_function` rule: the rule exists because this task proved the "
     "pattern was widespread."),
]

# (source_id, target_id, relation, confidence, score) — target must already exist.
EDGES: list[tuple[str, str, str, str, float]] = [
    ("docs_task_registry", "catchall_backlog", "conceptually_related_to", "INFERRED", 0.85),
    ("docs_task_registry", "docs_readme_index", "references", "INFERRED", 0.75),
    ("research_research_record", "architecture_two_ledger_system", "conceptually_related_to", "INFERRED", 0.75),
    ("research_research_record", "ml_platforms_harness", "conceptually_related_to", "INFERRED", 0.7),
    ("neural_mesh_function_audit_analysis_2026_09_16", "neural_mesh_architecture_audit_doc",
     "rationale_for", "INFERRED", 0.9),
    ("catchall_backlog", "anti_pattern_scanner", "rationale_for", "INFERRED", 0.85),
    ("catchall_backlog", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.7),
    ("azazael_insights", "docs_readme_index", "references", "INFERRED", 0.7),
    ("full_project_audit_2026_07_30", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.85),
    ("full_project_audit_2026_09_07", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.85),
    ("full_project_audit_2026_09_07", "full_project_audit_2026_07_30", "conceptually_related_to", "INFERRED", 0.85),
    ("session_insights_2026_05_29", "azazael_insights", "conceptually_related_to", "INFERRED", 0.8),
    ("docs_lessons_task_k_devtools", "reports_error_lesson_audit", "conceptually_related_to", "INFERRED", 0.75),
    ("learning_loop_audit_20260907", "neural_architecture_rl_pipelines", "conceptually_related_to", "INFERRED", 0.85),
    ("docs_worm_audit", "azazael_registry_doc", "conceptually_related_to", "INFERRED", 0.8),
    ("docs_worm_audit", "mandates_rule_0z_f_parallel", "rationale_for", "INFERRED", 0.7),
    ("docs_project_map", "projects_map_doc", "conceptually_related_to", "INFERRED", 0.9),
    ("cross_env_manifest", "platform_multi_deploy", "conceptually_related_to", "INFERRED", 0.85),
    ("cross_env_manifest", "sync_5_environments", "conceptually_related_to", "INFERRED", 0.85),
    ("lessons_2026_06_04", "platform_g_drive_degradation", "rationale_for", "INFERRED", 0.85),
    ("docs_rust_conversion_tracker", "proj_worm_manifest", "conceptually_related_to", "INFERRED", 0.8),
    ("sync", "platform_multi_deploy", "conceptually_related_to", "INFERRED", 0.75),
    ("docs_worm_proposals", "azazael_registry_doc", "references", "INFERRED", 0.75),
    ("session_summary_2026_06_03", "docs_readme_index", "references", "INFERRED", 0.65),
    ("algorithms_algorithm_log", "terence_doc", "conceptually_related_to", "INFERRED", 0.7),
    ("docs_lessons_task_h_import_elimination", "anti_pattern_scanner", "rationale_for", "INFERRED", 0.85),
]

nodes: list[dict] = []
edges: list[dict] = []
ids: set[str] = set()

for path, label, ftype, rationale in DOCS:
    nid = stem(path)
    if nid not in EXISTING and nid in ids:
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
                  "confidence_score": score, "source_file": "tools/graph_semantic/rationale_tranche2.py",
                  "source_location": None, "weight": 1.0})

print(f"tranche2 docs: {len(nodes)}  edges: {len(edges)}  skipped: {skipped}")
frag = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
out = ROOT / "rationale_tranche2_fragment.json"
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out)
