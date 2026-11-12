"""Coverage layer: skill concepts, lexicon/duplicate grouping, and island bridges.

Closes the three gaps the first pass left open:

1. **Skills corpus** — the 1,235 `skills/*.md` files each carry YAML frontmatter
   (`name`, `description`, `metadata.category`). That is a real semantic source, so
   each skill becomes a `concept` node with its description as rationale, grouped
   under a category hub. Before this they were only doc nodes with headings.
2. **Polyglot islands** — `packages/azazael-core`, `gateway-go` and
   `native/worm_manifest` had no edge into the code graph. Each gets a concept node
   with a rationale taken verbatim from its own manifest, and grounded edges to the
   subsystem it actually belongs to.
3. **Dimensionless files** — `.ps1` scripts and `__init__.py` package markers have
   no AST language support, so they are degree-0 by construction. Grouped under
   concept nodes so they have identity in the graph.

Also groups the BIBL lexicon volumes and the duplicated CODEX documentation tree.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

SCOPE = json.loads(Path("graphify-out/_doc_scope.json").read_text(encoding="utf-8"))
INCLUDED: list[str] = SCOPE["included"]
GRAPH = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
SEM = json.loads(Path("graphify-out/semantic_fragment.json").read_text(encoding="utf-8"))
SEM_IDS = {n["id"] for n in SEM["nodes"]}

# Existing node ids on disk, so every edge lands on a real node.
EXISTING_IDS = {n["id"] for n in GRAPH["nodes"]}

# In a single-shot run_extraction pass, graph.json still holds only the BASE
# code graph when this layer executes: doc_layer/concept_layer fragments are
# not merged into graph.json until build_graph runs after us. Resolving doc
# ids against the base graph silently dropped every doc->skill / BIBL /
# duplicate-tree / ATTACH edge (34 category islands measured 2026-10-06).
# Union the sibling fragments so doc ids resolve in the same pass.
for _frag_name in ("doc_layer_fragment.json", "concept_layer_fragment.json"):
    _frag = Path("graphify-out") / _frag_name
    if _frag.exists():
        EXISTING_IDS |= {n["id"] for n in json.loads(_frag.read_text(encoding="utf-8"))["nodes"]}
BY_LABEL: dict[str, list[str]] = collections.defaultdict(list)
for _n in GRAPH["nodes"]:
    BY_LABEL[re.sub(r"[^a-z0-9]+", "", str(_n.get("label", "")).lower())].append(_n["id"])


def _raw_stem(path: str) -> str:
    p = path[:-3] if path.lower().endswith(".md") else path
    return re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_")


_counts: dict[str, int] = collections.Counter(_raw_stem(p) for p in INCLUDED)


def doc_stem(path: str) -> str:
    s = _raw_stem(path)
    if _counts.get(s, 0) > 1:
        return f"{s}_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:6]}"
    return s


nodes: list[dict] = []
edges: list[dict] = []
keys: set[tuple] = set()


def add_node(nid: str, label: str, ftype: str, sfile: str | None, rationale: str) -> None:
    nodes.append({"id": nid, "label": label, "file_type": ftype, "source_file": sfile,
                  "source_location": None, "source_url": None, "captured_at": None,
                  "author": None, "contributor": None, "rationale": rationale})


def add_edge(s: str, t: str, rel: str, conf: str, score: float, sf: str) -> None:
    if s == t or (s, t, rel) in keys:
        return
    keys.add((s, t, rel))
    edges.append({"source": s, "target": t, "relation": rel, "confidence": conf,
                  "confidence_score": score, "source_file": sf,
                  "source_location": None, "weight": 1.0})


def resolve(nid: str) -> str | None:
    """Return the real on-disk id for a semantic id that may have been remapped."""
    if nid in EXISTING_IDS or nid in SEM_IDS:
        return nid
    return None


# ---------------------------------------------------------------------------
# 1. Skills corpus — YAML frontmatter -> concept node + category hub
# ---------------------------------------------------------------------------
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
skill_categories: dict[str, list[str]] = collections.defaultdict(list)
skills_seen = 0

for path in INCLUDED:
    if not path.startswith("skills/") or not path.endswith(".md"):
        continue
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    m = FRONT_RE.match(text)
    if not m:
        continue
    front = m.group(1)
    name = ""
    desc = ""
    category = "uncategorized"
    priority = ""
    for line in front.splitlines():
        s = line.strip()
        if s.startswith("name:"):
            name = s.split(":", 1)[1].strip().strip('"\'')
        elif s.startswith("description:"):
            desc = s.split(":", 1)[1].strip().strip('"\'')
        elif s.startswith("category:"):
            category = s.split(":", 1)[1].strip().strip('"\'')
        elif s.startswith("priority:"):
            priority = s.split(":", 1)[1].strip().strip('"\'')
    if not name:
        name = Path(path).stem
    skills_seen += 1
    cid = "skill_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    rat = desc or f"Skill: {name}"
    if priority:
        rat = f"{rat} (category: {category}, priority: {priority})"
    else:
        rat = f"{rat} (category: {category})"
    add_node(cid, name, "concept", path, rat)
    ds = resolve(doc_stem(path))
    if ds:
        add_edge(ds, cid, "references", "EXTRACTED", 1.0, path)
    skill_categories[category].append(cid)

for category, members in sorted(skill_categories.items()):
    hid = "skill_category_" + re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    add_node(hid, f"{category} skills ({len(members)})", "concept", None,
             f"Skill category '{category}' — {len(members)} registered skills.")
    for cid in members:
        add_edge(cid, hid, "references", "EXTRACTED", 1.0, "skills/")

print(f"skills with frontmatter: {skills_seen}  categories: {len(skill_categories)}")

# ---------------------------------------------------------------------------
# 2. BIBL lexicon — every volume points at the lexicon concept
# ---------------------------------------------------------------------------
bibl = 0
for path in INCLUDED:
    if path.startswith("src/ORCHESTRATION/BIBL/"):
        ds = resolve(doc_stem(path))
        if ds and resolve("bibl_lexicon"):
            add_edge(ds, "bibl_lexicon", "references", "EXTRACTED", 1.0, path)
            bibl += 1
print(f"BIBL volumes linked: {bibl}")

# ---------------------------------------------------------------------------
# 2b. Duplicated CODEX documentation tree -> its canonical docs/ twin
# ---------------------------------------------------------------------------
canon_by_name: dict[str, str] = {}
for path in INCLUDED:
    if path.startswith("docs/"):
        canon_by_name.setdefault(Path(path).name, path)

dup = 0
for path in INCLUDED:
    if path.startswith("src/CODEX/DOCUMENTATION/"):
        twin = canon_by_name.get(Path(path).name)
        if twin:
            a, b = resolve(doc_stem(path)), resolve(doc_stem(twin))
            if a and b:
                add_edge(a, b, "references", "EXTRACTED", 1.0, path)
                dup += 1
print(f"CODEX documentation duplicates linked to canonical twin: {dup}")

# ---------------------------------------------------------------------------
# 3. Polyglot islands — rationale quoted from each project's own manifest
# ---------------------------------------------------------------------------
POLYGLOT = [
    ("proj_gateway_go", "gateway-go — OpenAI-compatible inference gateway",
     "gateway-go/go.mod", "pkg_gateway_go",
     "OpenAI-compatible front end over llama-server backends. Stdlib only. Routes by "
     "`model` substring, round-robins otherwise, fails over on errors, relays SSE "
     "streaming, health-probes every 15s (own header comment, go.mod module gateway-go, go 1.26.3).",
     "inference_free_endpoints", "mandates_rule_0d_inference"),
    ("proj_azazael_core_package", "azazael-core — extracted core package v3.1.0",
     "packages/azazael-core/pyproject.toml", "pkg_azazael_core",
     "Path resolution, worm registry, free-threading and configuration engine, packaged "
     "for distribution (pyproject.toml description, v3.1.0, requires-python >=3.14).",
     "azazael_registry_doc", "package_duplicate_tree"),
    ("proj_worm_manifest", "worm-manifest — Rust pyo3 registry-lookup extension",
     "native/worm_manifest/Cargo.toml", "pkg_worm_manifest",
     "Fast Rust pyo3 extension for worm registry lookup; Cargo.toml states the goal as "
     "eliminating a 38 s Python cold-start (v0.1.0, pyo3 0.29, cdylib, LTO).",
     "azazael_registry_doc", "measure_before_optimizing"),
]
for nid, label, marker, marker_node, rationale, spine_a, spine_b in POLYGLOT:
    add_node(nid, label, "concept", marker, rationale)
    # The manifest marker node keeps the AST extractor's id (pkg_*), not a path stem.
    if resolve(marker_node):
        add_edge(marker_node, nid, "references", "EXTRACTED", 1.0, marker)
        add_edge(nid, marker_node, "references", "EXTRACTED", 1.0, marker)
    for spine in (spine_a, spine_b):
        if resolve(spine):
            add_edge(nid, spine, "conceptually_related_to", "INFERRED", 0.85, marker)

# The gateway-go Go tree is its own connected component; bridge it by source file.
go_sources = {n.get("source_file") for n in GRAPH["nodes"] if (n.get("source_file") or "").startswith("gateway-go/")}
go_bridged = 0
for n in GRAPH["nodes"]:
    if (n.get("source_file") or "") in go_sources and n.get("file_type") == "code":
        add_edge("proj_gateway_go", n["id"], "references", "EXTRACTED", 1.0, n["source_file"])
        go_bridged += 1
        if go_bridged >= 25:
            break
print(f"polyglot projects bridged: {len(POLYGLOT)}  gateway-go nodes linked: {go_bridged}")

# ---------------------------------------------------------------------------
# 3b. Dimensionless files — .ps1 scripts and __init__.py markers
# ---------------------------------------------------------------------------
ps1 = [n for n in GRAPH["nodes"] if (n.get("source_file") or "").endswith(".ps1")]
init = [n for n in GRAPH["nodes"]
        if (n.get("source_file") or "").endswith("__init__.py")]
# Only the ones that are currently degree-0 need a bridge, but grouping all of them
# is what gives the families a name.
deg: dict[str, int] = collections.defaultdict(int)
for e in GRAPH["links"]:
    deg[e.get("source")] += 1
    deg[e.get("target")] += 1
if ps1:
    add_node("powershell_script_suite", "PowerShell script suite", "concept", None,
             f"{len(ps1)} .ps1 helper scripts (shortcut/layout automation and environment "
             "setup). They have no supported AST language, so they carry no code symbols "
             "and would otherwise be degree-0.")
    for n in ps1:
        add_edge(n["id"], "powershell_script_suite", "references", "EXTRACTED", 1.0, n.get("source_file"))
if init:
    add_node("python_package_markers", "Python package marker modules", "concept", None,
             f"{len(init)} __init__.py package markers. They declare package boundaries and "
             "contain no symbols, so they are degree-0 by construction.")
    for n in init:
        add_edge(n["id"], "python_package_markers", "references", "EXTRACTED", 1.0, n.get("source_file"))
print(f"powershell scripts grouped: {len(ps1)}  package markers grouped: {len(init)}")

# Shell scripts and the Java renderer are degree-light for the same reason: no
# supported AST language. Group them so they carry an identity.
sh = [n for n in GRAPH["nodes"] if (n.get("source_file") or "").endswith(".sh")]
if sh:
    add_node("shell_script_suite", "Shell script suite", "concept", None,
             f"{len(sh)} .sh scripts (sync, install, migrate and launch helpers across the five "
             "environments). No supported AST language, so no code symbols.")
    for n in sh:
        add_edge(n["id"], "shell_script_suite", "references", "EXTRACTED", 1.0, n.get("source_file"))
java = [n for n in GRAPH["nodes"] if (n.get("source_file") or "").endswith(".java")]
for n in java:
    if resolve("gephi_headless_doc"):
        add_edge(n["id"], "gephi_headless_doc", "references", "EXTRACTED", 1.0, n.get("source_file"))
print(f"shell scripts grouped: {len(sh)}  java nodes linked: {len(java)}")

# ---------------------------------------------------------------------------
# 4. Authored rationale for the remaining distinct project docs
# ---------------------------------------------------------------------------
AUTHORED = [
    ("src_orchestration_current_state", "Azazael worm ecosystem — current state", "src/ORCHESTRATION/CURRENT_STATE.md",
     "Snapshot of the worm ecosystem with its per-root layout and tier list. A CURRENT_STATE doc is a "
     "photograph, so it is only true on its own date — the audit that counted this ecosystem found the "
     "counts drifting between revisions."),
    ("src_orchestration_index_mlplatform", "Azazael ML platform — command index", "src/ORCHESTRATION/INDEX.md",
     "Per-tool command index (HYDRA, HORUS, TARBABY, POINDEXTER, WORMZ, METAWORMS). Its twin lives under "
     "src/CODEX/DOCUMENTATION/INDEX.md."),
    ("src_orchestration_changelog", "Azazael changelog (ORCHESTRATION)", "src/ORCHESTRATION/CHANGELOG.md",
     "Versioned log 1.0.0 to 2.1.0. A changelog that stops at 2.1.0 while the package is at 3.1.0 is itself "
     "a staleness signal."),
    ("src_orchestration_readme_harness", "Azazael ML platform development harness", "src/ORCHESTRATION/README.md",
     "30-second quick start, directory structure, commands and validation for the development harness."),
    ("src_orchestration_docs_contributing", "Contributing to Azazael documentation", "src/ORCHESTRATION/docs/CONTRIBUTING.md",
     "How to report issues, make changes, and the documentation style guide."),
    ("src_hydra_documentation_hydra", "HYDRA (code module documentation)", "src/HYDRA/DOCUMENTATION/HYDRA.md",
     "Sequential protocol stages, CLI, architecture and integration for HYDRA. This is the code-adjacent "
     "copy of the TD-002 specification."),
    ("ml_platforms_file_explanations", "ML Platforms module file explanations", "src/ORCHESTRATION/ml_platforms/FILE_EXPLANATIONS.md",
     "Per-file purpose map for the ML platform suite (harness, config, diagnostics, launcher, conversion). "
     "Its use is answering 'what is this file for' without reading it."),
    ("ml_platforms_readme", "ML Platform configuration suite", "src/ORCHESTRATION/ml_platforms/README.md",
     "Structure, quick start, installation and the key improvements over the original files."),
    ("ml_platforms_troubleshooting", "ML Platform troubleshooting guide", "src/ORCHESTRATION/ml_platforms/TROUBLESHOOTING.md",
     "Install failures, missing modules, CUDA OOM and NumPy version mismatch. The NumPy mismatch entry is "
     "one of the concrete cp314t wheel-availability symptoms."),
    ("session_template_readme", "SESSION_TEMPLATE - visual documentation system", "src/CODEX/SESSION_TEMPLATE/README.md",
     "Quick start, requirements, file structure and Cain template features for recording templates to video."),
    ("session_template_index", "SESSION_TEMPLATE index", "src/CODEX/SESSION_TEMPLATE/INDEX.md",
     "Folder structure for templates, data, generators, recorders and videos."),
    ("codex_documentation_tree", "CODEX DOCUMENTATION tree — duplicated reference set", "src/CODEX/DOCUMENTATION/",
     "A near-complete second copy of docs/: glossary, dependency graph, security checklist, enhancement plan, "
     "TASK_REGISTRY and the TD-001..TD-006 series all appear twice. Duplication is how a reader ends up "
     "citing the stale copy, so each duplicate is linked to its canonical twin."),
    ("ml_platforms_api_docs", "ML Platforms generated API documentation set", "src/ORCHESTRATION/ml_platforms/docs/",
     "Generated per-module API docs (config, diagnostics, launcher, harness, mlflow/ray/onnx integration, "
     "q-learning). Generated from the DOCUMENTATION_PROTOCOL section contract, which is why they are uniform."),
    ("deepfacelab_videos", "DeepFaceLab educational video templates", "src/CODEX/DOCUMENTATION/DEEPFACELAB_EDUCATIONAL_VIDEOS.md",
     "Workflow walkthrough (prepare, extract, train, merge) plus the template selection guide for the "
     "educational video pipeline."),
]
for nid, label, sfile, rationale in AUTHORED:
    add_node(nid, label, "document", sfile if sfile.endswith(".md") else None, rationale)

# Attach the authored group nodes to the spine so none lands isolated.
ATTACH = [
    ("codex_documentation_tree", "docs_duplication_under_codex"),
    ("codex_documentation_tree", "codex_technical_documentation_index"),
    ("ml_platforms_api_docs", "ml_platforms_documentation_protocol"),
    ("ml_platforms_api_docs", "documentation_generation_pipeline"),
    ("src_orchestration_index_mlplatform", "docs_readme_index"),
    ("src_orchestration_readme_harness", "ml_platforms_harness"),
    ("session_template_readme", "codex_session_template"),
    ("session_template_index", "codex_session_template"),
    ("deepfacelab_videos", "codex_video_generation"),
    ("src_hydra_documentation_hydra", "docs_td_002_hydra_cluster"),
    ("ml_platforms_troubleshooting", "platform_cp314t_wheels"),
    ("ml_platforms_file_explanations", "ml_platforms_harness"),
    ("ml_platforms_readme", "ml_platforms_harness"),
    ("src_orchestration_current_state", "projects_map_doc"),
    ("src_orchestration_changelog", "changelog_doc"),
    ("src_orchestration_docs_contributing", "docs_contributing_ml"),
]
for a, b in ATTACH:
    if b in SEM_IDS:
        add_edge(a, b, "references", "INFERRED", 0.85, "tools/graph_semantic/coverage_layer.py")

print(f"authored additions: {len(AUTHORED)}")
print(f"TOTAL coverage-layer nodes: {len(nodes)}  edges: {len(edges)}")

frag = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
out = Path("graphify-out/coverage_layer_fragment.json")
out.write_text(json.dumps(frag, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", out, f"{out.stat().st_size / 1024:.0f} KB")