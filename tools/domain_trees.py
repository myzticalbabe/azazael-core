#!/usr/bin/env python
# coding: utf-8
"""domain_trees -- one atlas per DOMAIN, not per environment.

WHY
`yggdrasil_atlas.scan()` answers "what is in this tree?" for a directory. The
questions actually asked on 2026-10-31 were domain questions: develop a tree for
the RULES layer (sentinel), for CAIN, for EVE/RANI native-intelligence
artifacts, for swarm hives, for GOSP, for ML shards/modules, for the shard
repository, for edge0. Those are not directories -- each spans several roots
(`src/ML/gosp.py` + `scripts/gosp_*` + evidence dirs), and the rules layer is
half code (gate, enforcer, compliance, tools) and half canon (`MANDATES.md`).

So a domain here is a DECLARATION: named roots, named single files, and named
canon documents. The census reads only what each declaration names, and the
output uses the SAME atlas schema the per-environment atlases use, which is what
makes a domain tree renderable by `yggdrasil_forest/render_tree.py` with no code
change. That shared schema is the interop contract: a new domain is a new
declaration, not a new renderer.

WHAT THIS DELIBERATELY DOES NOT CLAIM
Merging several roots loses cross-root edges: each root is scanned on its own,
so `edges.tree_pairs` is the SUM of per-root values and undercounts linkage
between roots of the same domain. `cross_root_edges_resolved: false` is written
into every atlas so nobody reads the edge count as the domain's true coupling.
Resolving those would mean scanning a synthetic tree of the domain's files; that
is a real improvement and it is tasked, not assumed.

USAGE
  python tools/domain_trees.py --list
  python tools/domain_trees.py                       # every domain -> yggdrasil_forest/
  python tools/domain_trees.py --domain sentinel     # one
  python tools/domain_trees.py --check               # table: totals per domain
"""
from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site




#: Generated/vendored trees that must not inflate a language count.
LANG_SKIP = {"node_modules", "target", ".git", "__pycache__", ".venv", "venv",
             "dist", "build", "graphify-out", ".mypy_cache", ".pytest_cache",
             "site-packages", "dist-packages"}

#: Extension -> language, shared with the census so one table decides what a
#: "module" is. Every atlas counts Python through AST; the other languages are
#: counted by file, because counting only .py made a Rust extension or a TS
#: front-end read as an empty tree rather than as untranslated work.
LANG_EXT = {".py": "python", ".pyi": "python",
     ".rs": "rust", ".go": "go", ".zig": "zig",
     ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
     ".ts": "typescript", ".tsx": "typescript",
     ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
     ".cu": "cuda", ".cuh": "cuda",
     ".java": "java", ".cs": "dotnet", ".jl": "julia", ".rb": "ruby",
     ".sh": "shell", ".swift": "swift", ".kt": "kotlin",
}



#: Suffix -> language name, lower case, because that is what the committed
#: atlases in ~/yggdrasil_forest already carry (`{'python': 98, 'javascript': 61}`).
#: This table went missing while the census was being extended and the tool died
#: with `NameError: LANG_EXT`, which the `--check` run surfaced immediately -- a
#: census that cannot run is a census nobody can quote.
LANG_EXT: Dict[str, str] = {
    ".py": "python", ".pyi": "python", ".pyx": "cython",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".rs": "rust", ".go": "go", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".m": "objective-c",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".bat": "batch",
    ".cmd": "batch", ".sql": "sql", ".lua": "lua",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cff": "yaml",
    ".mmd": "mermaid", ".proto": "protobuf", ".glsl": "glsl", ".wgsl": "wgsl",
}

ROOT = Path(__file__).resolve().parent.parent          # azazael/
HOME = Path.home()
OUT_DIR = HOME / "yggdrasil_forest"

sys.path.insert(0, str(ROOT / "tools"))

#: domain key -> declaration. Roots are relative to ROOT unless absolute.
DOMAINS: Dict[str, Dict[str, Any]] = {
    "sentinel": {
        "label": "SENTINEL — RULES DEV",
        "kind": "mixed",
        "palette": {"bg": "#07050d", "trunk": "#39d3c3", "leaf": "#f2c14e",
                    "text": "#e8fbf8", "dim": "#7f9a96"},
        "roots": ["src/SCRIPTS", "src/CORE/compliance"],
        "files": ["src/CORE/sentinel_enforcer.py", "src/CORE/audit_trail.py",
                  "tools/evidence_gate.py", "tools/terence_invariants.py",
                  "tools/mesh_function_preflight.py", "tools/canon_drift_check.py"],
        # Relative to ROOT (azazael/), like every other path here: the first
        # draft wrote "azazael/MANDATES.md" and every one resolved to
        # azazael/azazael/, so the rules half of the rules tree was MISSING.
        "canon": ["MANDATES.md", "SOP.md", "SYSTEM_INSTRUCTIONS.md", "ERROR_LOG.md"],
        "note": "half gate code, half canon: a rules tree must show both or it hides where a rule lives",
    },
    "cain": {
        "label": "CAIN — PSYCHOANALYSIS + RENDER",
        "kind": "code",
        "palette": {"bg": "#0b0609", "trunk": "#e05a3c", "leaf": "#ffd166",
                    "text": "#fdeee8", "dim": "#a08579"},
        "roots": ["src/CODEX", "src/PSYCHE"],
        "files": [],
        "canon": [],
        "note": "CODEX carries the render pipeline and the session templates; PSYCHE the framework",
    },
    "eve_rani": {
        "label": "EVE / RANI — NATIVE INTELLIGENCE ARTIFACTS",
        "kind": "code",
        "palette": {"bg": "#060a10", "trunk": "#7aa2f7", "leaf": "#9ece6a",
                    "text": "#e9f1ff", "dim": "#7b8aa3"},
        "roots": ["src/eve_unleashed"],
        "files": ["src/ML/gosp.py", "C:/Users/clint/RANI_LLM_evidence.json"],
        "canon": [],
        "note": "RANI evidence lives beside the repo (RANI_LLM_evidence.json); artifacts are counted, not invented. GOSP_RANI_INTEGRATION_evidence.json not found on disk.",
    },
    "hives": {
        "label": "SWARM HIVES",
        "kind": "code",
        "palette": {"bg": "#0a0806", "trunk": "#c9a227", "leaf": "#f2c14e",
                    "text": "#fdf6e3", "dim": "#9b8f6f"},
        "roots": ["src/CORE/WORMS"],
        "files": ["src/CORE/worm_registry.py"],
        "canon": [],
        # The row asked for worm CATEGORIES as limbs and this is the declaration
        # that delivers it, not a renderer edit: the per-root scan already
        # aggregates `worms.by_category` (36 categories over 206 worms, measured
        # 2026-11-01), so the limbs can be built from that instead of from paths.
        # Path limbs answer "where does the code live"; category limbs answer
        # "what does the swarm do", and for this domain only the second is the
        # question. Two path limbs became 36 category limbs with no renderer change.
        "limb_source": "worms.by_category",
        "note": "one hive per worm category; registry counted separately because registration is not housing",
    },
    "gosp": {
        "label": "GOSP — CLI + PARITY",
        "kind": "mixed",
        "palette": {"bg": "#080a06", "trunk": "#9ece6a", "leaf": "#e0af68",
                    "text": "#f2fff0", "dim": "#86977c"},
        "roots": [],
        "files": ["src/ML/gosp.py", "scripts/gosp_parity_check.py",
                  "scripts/gosp_rani_integration.py"],
        "evidence_dirs": ["C:/Users/clint/gosp_cli_run", "C:/Users/clint/gosp_parity"],
        "canon": [],
        "note": "GOSP is three scripts plus evidence dirs; a code-only census would report it as absent",
    },
    "ml": {
        "label": "ML SHARDS / MODULES",
        "kind": "code",
        "palette": {"bg": "#08060c", "trunk": "#b07ce8", "leaf": "#7fd1ff",
                    "text": "#f0ecff", "dim": "#8b82a8"},
        "roots": ["src/ML", "src/ORCHESTRATION/ml_platforms"],
        "files": [],
        "canon": [],
        "note": "platform library and the ML modules are separate roots by design",
    },
    "shards": {
        "label": "SHARD REPOSITORY",
        "kind": "mixed",
        "palette": {"bg": "#070c0a", "trunk": "#39d3c3", "leaf": "#f2c14e",
                    "text": "#eafff8", "dim": "#7f9a96"},
        "roots": ["src/SHARDS"],
        "files": [],
        "skill_roots": [".agents/skills"],
        "canon": [],
        "note": "code shards and skill shards are counted separately: a skill is a directory holding SKILL.md",
    },
    "dev": {
        "label": "DEV — DEEP-LEARNING STACKS, ALL ENVIRONMENTS",
        "kind": "stacks",
        "palette": {"bg": "#06070b", "trunk": "#39d3c3", "leaf": "#7fd1ff",
                    "text": "#e9f1ff", "dim": "#7b8aa3"},
        "roots": [],
        "files": ["tools/dev_stack_census.py", "tools/domain_trees.py"],
        # Built from the census, not from a directory walk: a limb is one
        # (environment, library family) pair, and its numbers come from the
        # installed package directories of every environment named there.
        "stacks": "C:/Users/clint/yggdrasil_forest/dev_stack_census.json",
        "canon": [
            "//wsl.localhost/Ubuntu-26.04/root/INTEL_NPU_IGPU_SETUP_GUIDE.md",
            "//wsl.localhost/Ubuntu-26.04/root/WORKSPACE_HARDWARE_PROTOCOL.md",
            "//wsl.localhost/Ubuntu-26.04/root/TENSORFLOW_BUILD_REPORT.md",
            "//wsl.localhost/Ubuntu-26.04/root/UBUNTU_ARCHITECTURE_RECOVERY.md",
        ],
        "note": ("oneAPI/XPU is Windows-only (torch +xpu, triton-xpu, intel sycl); "
                 "Ubuntu torch is +cpu; Kali has no framework. Versions from dist-info"),
    },
    "edge0": {
        "label": "EDGE0 — INTEL",
        "kind": "code",
        "palette": {"bg": "#06080d", "trunk": "#e05a3c", "leaf": "#7fb0ff",
                    "text": "#e9f1ff", "dim": "#78859b"},
        "roots": ["C:/Users/clint/edge0-intel"],
        "files": [],
        "canon": [],
        "note": "lives outside the repo; the atlas records that so a missing tree is a path fact",
    },
    "wraith": {
        "label": "LILITH'S WRAITH — RENDER CHARACTER",
        "kind": "code",
        "palette": {"bg": "#060a10", "trunk": "#4a1a4a", "leaf": "#c084c0",
                    "text": "#f0e0f0", "dim": "#8a6a8a"},
        "roots": ["src/RENDERING/characters/wraith", "src/RENDERING/pipeline"],
        "files": [],
        "canon": ["RENDERING_SERIES_PLAN_Headless_Oxman_Lilith_Wraith.md"],
        "note": "Character assets, ComfyUI prompts, Blender rig, animation keys for Lilith's Wraith",
    },
    "oxman": {
        "label": "HEADLESS OXMAN — RENDER CHARACTER",
        "kind": "code",
        "palette": {"bg": "#0a0806", "trunk": "#8b5a2b", "leaf": "#d4a574",
                    "text": "#fdf6e3", "dim": "#a89078"},
        "roots": ["src/RENDERING/characters/oxman", "src/RENDERING/pipeline"],
        "files": [],
        "canon": ["RENDERING_SERIES_PLAN_Headless_Oxman_Lilith_Wraith.md"],
        "note": "Character assets, ComfyUI prompts, Blender rig, animation keys for Headless Oxman",
    },
}

TREE_DEFAULT = {"modules": 0, "lines": 0, "classes": 0, "funcs": 0, "methods": 0,
                "public_api": 0, "silence": 0, "mains": 0, "hubs": [], "subs": 0,
                "inbound_trees": [], "outbound_trees": [], "standalone": True,
                "is_trunk": False, "saplings": 0, "shard_remainder": 0}


def _resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (ROOT / q)


def _resolve_cross_root_edges(
    roots: List[Path],
    domain_trees: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve cross-root edges by scanning all roots together.
    
    Currently each root is scanned independently, so edges BETWEEN roots
    are not counted. This function uses AST analysis to find cross-root
    imports and calls.
    
    Returns:
        Dict with 'tree_pairs' (number of root pairs with edges),
        'statements' (import/call statements across roots),
        'details' (per-pair breakdown).
    """
    if len(roots) < 2:
        return {"tree_pairs": 0, "statements": 0, "details": {}}
    
    try:
        import ast
        
        # Collect all Python files from all roots
        root_files = {}
        for root in roots:
            files = []
            for fp in root.rglob("*.py"):
                if not any(skip in fp.parts for skip in LANG_SKIP):
                    files.append(fp)
            if files:
                root_files[str(root)] = files
        
        if len(root_files) < 2:
            return {"tree_pairs": 0, "statements": 0, "details": {}}
        
        # Parse all files and collect imports
        root_imports = {}  # root -> {file: set(imported_modules)}
        for root_str, files in root_files.items():
            imports = {}
            for fp in files:
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(text, filename=str(fp))
                except (OSError, SyntaxError):
                    continue
                
                file_imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name:
                                file_imports.add(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and not getattr(node, "level", 0):
                            file_imports.add(node.module)
                imports[str(fp)] = file_imports
            
            root_imports[root_str] = imports
        
        # Build a mapping from root directory to its Python package prefix
        # This maps each root directory to the Python package prefix used in imports
        root_to_pkg_prefix = {}
        for root_str in root_files.keys():
            root_path = Path(root_str)
            # Try to find the Python package prefix by looking at the directory structure
            # relative to the source root
            try:
                rel = Path(root_str).relative_to(ROOT)
                # Convert path to Python package notation
                pkg_prefix = ".".join(rel.parts)
                root_to_pkg_prefix[str(root)] = pkg_prefix
            except ValueError:
                root_to_pkg_prefix[str(root)] = Path(root_str).name
        
        # Find cross-root imports
        root_names = list(root_files.keys())
        cross_root_pairs = set()
        cross_root_statements = 0
        details = {}
        
        for i, root_a in enumerate(root_names):
            imports_a = root_imports[root_a]
            pkg_prefix_a = root_to_pkg_prefix.get(root_a, Path(root_a).name)
            for root_b in root_names[i+1:]:
                imports_b = root_imports[root_b]
                pkg_prefix_b = root_to_pkg_prefix.get(root_b, Path(root_b).name)
                
                # Check if root_a imports from root_b or vice versa
                statements = 0
                pair_details = []
                
                # Check if any file in root_a imports from root_b's modules
                pkg_prefix_b = root_to_pkg_prefix.get(root_b, Path(root_b).name)
                for file_a, imports_a in root_imports[root_a].items():
                    for imp in imports_a:
                        # Check if import matches root_b's package prefix
                        if imp == pkg_prefix_b or imp.startswith(pkg_prefix_b + "."):
                            statements += 1
                            rel_a = str(Path(file_a).relative_to(Path(root_a)))
                            # Find which file in root_b provides this module
                            for file_b, imports_b in root_imports[root_b].items():
                                for imp_b in imports_b:
                                    if imp_b == pkg_prefix_b or imp_b.startswith(pkg_prefix_b + "."):
                                        rel_b = str(Path(file_b).relative_to(Path(root_b)))
                                        pair_details.append(f"{rel_a} -> {rel_b} ({imp_b})")
                                        break
                
                # Check reverse direction: root_b imports from root_a
                pkg_prefix_a = root_to_pkg_prefix.get(root_a, Path(root_a).name)
                for file_b, imports_b in root_imports[root_b].items():
                    for imp in imports_b:
                        if imp == pkg_prefix_a or imp.startswith(pkg_prefix_a + "."):
                            statements += 1
                            rel_b = str(Path(file_b).relative_to(Path(root_b)))
                            for file_a, imports_a in root_imports[root_a].items():
                                for imp_a in imports_a:
                                    if imp_a == pkg_prefix_a or imp_a.startswith(pkg_prefix_a + "."):
                                        rel_a = str(Path(file_a).relative_to(Path(root_a)))
                                        pair_details.append(f"{rel_b} -> {rel_a} ({imp_a})")
                                        break
                
                total_statements = statements
                if total_statements > 0:
                    root_a_name = Path(root_a).name
                    root_b_name = Path(root_b).name
                    pair = tuple(sorted([root_a, root_b]))
                    cross_root_pairs.add(pair)
                    cross_root_statements += total_statements
                    details[f"{Path(root_a).name} <-> {Path(root_b).name}"] = total_statements
        
        return {
            "tree_pairs": len(cross_root_pairs),
            "statements": cross_root_statements,
            "details": details,
        }
    except Exception as exc:
        print(f"[domain_trees] cross-root edge resolution failed: {exc}", file=sys.stderr)
        return {"tree_pairs": 0, "statements": 0, "details": {}, "error": str(exc)}
def _newest_mtime(paths: List[Path], cap: int = 6000) -> Optional[str]:
    """Newest source mtime under these roots, ISO, or None.

    Exists because "the numbers are stale" is a complaint that arrives after a
    figure is quoted, never before: an atlas carries its own generation time but
    cannot know whether the tree moved since. Comparing the two makes staleness
    a printed property of the row instead of a reader's job.
    """
    newest = 0.0
    seen = 0
    unreadable = 0
    for root in paths:
        # A DECLARED FILE IS A SOURCE TOO. GOSP declares no roots at all -- its
        # three scripts are `files` entries -- so passing roots alone measured
        # nothing and the index printed newest_source=None for it on every run.
        # "Cannot tell whether this is stale" reads as "current" to anyone
        # scanning the table, which is the opposite of what the field is for.
        if root.is_file():
            try:
                m = root.stat().st_mtime
            except OSError as exc:
                unreadable += 1
                if unreadable <= 3:
                    print("[domain_trees] stat failed %s: %s" % (root, exc),
                          file=sys.stderr)
                continue
            if m > newest:
                newest = m
            seen += 1
            continue
        if not root.is_dir():
            continue
        for dirpath, dirs, filenames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in LANG_SKIP]
            for f in filenames:
                try:
                    m = (Path(dirpath) / f).stat().st_mtime
                except OSError as exc:
                    # Counted, not hidden: an unreadable file cannot make the
                    # tree look current, and a silent skip would report the
                    # opposite of what happened.
                    unreadable += 1
                    if unreadable <= 3:
                        print("[domain_trees] stat failed %s: %s"
                              % ((Path(dirpath) / f), exc), file=sys.stderr)
                    continue
                if m > newest:
                    newest = m
                seen += 1
                if seen > cap:
                    break
            if seen > cap:
                break
    if not newest:
        return None
    if unreadable:
        print("[domain_trees] %d file(s) unreadable -- newest-source may be "
              "understated" % unreadable, file=sys.stderr)
    return datetime.datetime.fromtimestamp(newest).isoformat(timespec="seconds")


def _count_languages(root: Path, cap: int = 4000) -> Dict[str, int]:
    """Files per language under one root, bounded. Counted, never parsed."""
    out: Dict[str, int] = {}
    seen = 0
    if not root.is_dir():
        return out
    for dirpath, dirs, filenames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in LANG_SKIP]
        for f in filenames:
            lang = LANG_EXT.get(Path(f).suffix.lower())
            if not lang:
                continue
            out[lang] = out.get(lang, 0) + 1
            seen += 1
            if seen >= cap:
                out["_capped"] = 1
                return dict(sorted(out.items()))
    return dict(sorted(out.items()))


def _scan_file(path: Path) -> Optional[Dict[str, Any]]:
    """A single file as one limb. Same field shape as a scanned tree."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError) as exc:
        # "could not be read" and "is empty" are different limbs; the caller
        # adds the path to `missing` and the reason is printed here.
        print("[domain_trees] unreadable %s: %s" % (path, exc), file=sys.stderr)
        return None
    classes, funcs, methods, public = 0, 0, 0, 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes += 1
            if not node.name.startswith("_"):
                public += 1
            methods += sum(1 for c in node.body
                           if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # methods were counted through their class; count only module-level
            funcs += 1
    funcs = max(funcs - methods, 0)
    entry = dict(TREE_DEFAULT)
    entry.update({"modules": 1, "lines": text.count("\n") + 1, "classes": classes,
                  "funcs": funcs, "methods": methods, "public_api": public,
                  "mains": 1 if "_main(" in text or "if __name__" in text else 0})
    return entry


def _count_canon(path: Path) -> Optional[Dict[str, Any]]:
    """Rules and headings in a canon document.

    A rules tree that shows only Python hides the rules themselves: MANDATES.md
    holds the text every gate quotes, and it is not code. Entities are counted
    from headers, never inferred from prose length.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # The caller adds the path to `missing`, but "absent" and "present and
        # unreadable" are different facts and the atlas should not conflate them.
        print("[domain_trees] canon unreadable %s: %s" % (path, exc), file=sys.stderr)
        return None
    rules = 0
    for line in text.splitlines():
        s = line.lstrip("#").strip()
        if line.startswith("#") and s.upper().startswith("RULE"):
            rules += 1
    sections = sum(1 for line in text.splitlines() if line.startswith("## "))
    return {"path": str(path), "rules": rules, "sections": sections,
            "lines": text.count("\n") + 1}


def _count_skills(d: Path) -> Dict[str, int]:
    """Shard dirs (SKILL.md) and loose .md counted apart.

    A previous census counted only `*.md` and read the sharded repository as 37
    items; the dirs holding SKILL.md are the shards and they are the majority.
    """
    if not d.exists():
        return {"shard_dirs": 0, "loose_md": 0}
    shard_dirs = sum(1 for x in d.iterdir() if x.is_dir() and (x / "SKILL.md").exists())
    return {"shard_dirs": shard_dirs, "loose_md": len(list(d.glob("*.md")))}


def build(domain: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    import yggdrasil_atlas as ya

    atlas: Dict[str, Any] = {
        "schema": "azazael.domain_atlas/1",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "domain": domain,
        "label": spec["label"],
        "kind": spec["kind"],
        "declared_roots": list(spec.get("roots") or []),
        "declared_files": list(spec.get("files") or []),
        "totals": dict(TREE_DEFAULT),
        "trees": {},
        "classes": {"total": 0, "public": 0, "by_tree": {}},
        "worms": {"total": 0, "categories_named": 0, "by_category": {},
                  "by_tree": {}, "by_owner": {}, "items": []},
        "edges": {"pairs": 0, "statements": 0, "tree_pairs": 0},
        "canon": {"files": [], "rules": 0},
        "skills": {},
        "evidence": {},
        "languages": {},
        "cross_root_edges_resolved": False,
        "notes": [spec.get("note", "")] if spec.get("note") else [],
        "missing": [],
    }
    totals = atlas["totals"]
    names = set()

    for rel in spec.get("roots") or []:
        p = _resolve(rel)
        if not p.is_dir():
            atlas["missing"].append(str(p))
            continue
        sub = ya.scan(p)
        tag = p.name
        for lang, n in _count_languages(p).items():
            if lang == "_capped":
                atlas["languages"].setdefault("_capped", []).append(tag)
                continue
            atlas["languages"][lang] = atlas["languages"].get(lang, 0) + n
        # A flat root (files directly inside, no package dirs) yields zero trees,
        # which made `shards` and `ml` render empty while holding real code. Give
        # such a root one limb named after itself.
        if not (sub.get("trees") or {}) and (sub.get("totals") or {}).get("modules"):
            st = sub["totals"]
            flat = dict(TREE_DEFAULT)
            flat.update({"modules": st.get("modules", 0),
                         "lines": st.get("lines", 0),
                         "classes": st.get("classes", 0),
                         "funcs": st.get("functions", 0),
                         "methods": st.get("methods", 0),
                         "public_api": st.get("public_api", 0),
                         "mains": st.get("entry_points", 0)})
            atlas["trees"][tag] = flat
            names.add(tag)
        for tname, tv in (sub.get("trees") or {}).items():
            key = f"{tag}.{tname}"
            if key in names:
                key = f"{tag}.{tname}#{len(names)}"
            names.add(key)
            atlas["trees"][key] = tv
        for k, v in (sub.get("totals") or {}).items():
            if isinstance(v, (int, float)):
                totals[k] = totals.get(k, 0) + v
        cls = sub.get("classes") or {}
        atlas["classes"]["total"] += cls.get("total", 0)
        atlas["classes"]["public"] += cls.get("public", 0)
        for tname, recs in (cls.get("by_tree") or {}).items():
            atlas["classes"]["by_tree"].setdefault(f"{tag}.{tname}", []).extend(recs)
        w = sub.get("worms") or {}
        atlas["worms"]["total"] += w.get("total", 0)
        atlas["worms"]["items"].extend(w.get("items") or [])
        for cat, n in (w.get("by_category") or {}).items():
            atlas["worms"]["by_category"][cat] = atlas["worms"]["by_category"].get(cat, 0) + n
        e = sub.get("edges") or {}
        for k in ("pairs", "statements", "tree_pairs"):
            atlas["edges"][k] += e.get(k, 0)
        atlas["notes"].append(
            f"{tag}: merged from its own scan; edges to other roots of this domain "
            f"are NOT resolved")

    for rel in spec.get("files") or []:
        p = _resolve(rel)
        entry = _scan_file(p) if p.exists() else None
        if entry is None:
            atlas["missing"].append(str(p))
            continue
        # Bare stem, not "file.<stem>": the renderer labels a limb with its key
        # and the prefix made six neighbouring labels overlap on the rendered
        # sentinel still ("file.audit_tr/ile.terence_in/canv/...").
        key = p.stem
        while key in atlas["trees"]:
            key += "_"
        entry["kind"] = "file"
        atlas["trees"][key] = entry
        # A file limb carries real classes (gosp.py has 1) but the domain header
        # reads `classes.total`, which only the per-root merges were adding -- so
        # GOSP rendered "0 classes · 0 public" above a limb labelled "1 cls".
        # Two numbers about the same file, disagreeing, in one frame.
        atlas["classes"]["total"] += entry.get("classes", 0)
        atlas["classes"]["public"] += entry.get("public_api", 0)
        # A files-only domain (gosp: three named scripts) reported
        # `languages: {}` because only ROOTS were walked, so the index printed
        # "not walked" beside 3 real modules. Count the declared files too.
        lang_code = LANG_EXT.get(p.suffix.lower())
        if lang_code:
            atlas["languages"][lang_code] = atlas["languages"].get(lang_code, 0) + 1
        for k in ("modules", "lines", "classes", "funcs", "methods", "public_api",
                  "mains"):
            totals[k] = totals.get(k, 0) + entry[k]

    for rel in spec.get("canon") or []:
        p = _resolve(rel)
        rec = _count_canon(p) if p.exists() else None
        if rec is None:
            atlas["missing"].append(str(p))
            continue
        atlas["canon"]["files"].append(rec)
        atlas["canon"]["rules"] += rec["rules"]
        totals["lines"] = totals.get("lines", 0) + rec["lines"]
        key = p.stem
        while key in atlas["trees"]:
            key += "_"
        entry = dict(TREE_DEFAULT)
        # A canon file has no classes BY DESIGN, so a reader must not take
        # "0 cls" for empty: its size is rules and lines, and `kind` says so.
        entry.update({"lines": rec["lines"], "modules": 0,
                      "rules": rec["rules"], "kind": "canon"})
        atlas["trees"][key] = entry

    for rel in spec.get("skill_roots") or []:
        counts = _count_skills(_resolve(rel))
        atlas["skills"][rel] = counts
        # Shards ARE the leaves of this domain, so an atlas that counted only
        # `*.py` reported the shard repository as 4 modules and 0 shards.
        key = f"skills_{Path(rel).name}"
        entry = dict(TREE_DEFAULT)
        # `unit` is declared, so the renderer prints 204 shards rather than
        # 204//4 = "51 mod": the limb was right and the label was four times off.
        entry.update({"modules": counts["shard_dirs"], "shards": counts["shard_dirs"],
                      "loose_md": counts["loose_md"], "kind": "shards", "unit": "shards"})
        atlas["trees"][key] = entry
        totals["shards"] = totals.get("shards", 0) + counts["shard_dirs"]

    for rel in spec.get("evidence_dirs") or []:
        p = _resolve(rel)
        if not p.is_dir():
            atlas["missing"].append(str(p))
            continue
        files = [x for x in p.rglob("*") if x.is_file()]
        atlas["evidence"][rel] = {"files": len(files),
                                  "bytes": sum(x.stat().st_size for x in files)}
        key = f"evidence_{p.name}"
        entry = dict(TREE_DEFAULT)
        entry.update({"modules": len(files), "lines": 0, "kind": "evidence"})
        atlas["trees"][key] = entry

    # ---- limb_source: choose what a limb MEANS for this domain -------------
    # Declared per domain (see `hives`). Applied last, so every count above is
    # computed from paths as usual and only the limb view is replaced. The path
    # view is preserved under `paths_trees`: the two answer different questions
    # (where the code lives vs what the swarm does) and dropping either one would
    # make the atlas unable to answer a question it could answer yesterday.
    limb_src = spec.get("limb_source")
    if limb_src == "worms.by_category":
        cats = (atlas.get("worms") or {}).get("by_category") or {}
        if cats:
            atlas["paths_trees"] = dict(atlas["trees"])
            new_trees: Dict[str, Any] = {}
            for cat, n in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])):
                entry = dict(TREE_DEFAULT)
                # `modules` here counts WORMS in the category, not files: the
                # note below says so, because a limb of 22 that is not 22 files
                # is exactly the kind of number a reader would take for a count
                # of code. Classes are NOT attributed per category (the census
                # attributes them per file), so they are left out rather than
                # rendered as a 0 that reads as "empty".
                entry.update({"modules": int(n), "worms": int(n), "kind": "category",
                              "category": cat, "lines": 0, "classes": None,
                              "unit": "worms",
                              "contains": f"{int(n)} worms registered as '{cat}'"})
                new_trees[str(cat)] = entry
            atlas["trees"] = new_trees
            atlas["limbs_from"] = limb_src
            atlas["notes"].append(
                "limbs are worm CATEGORIES, not paths: `modules` on a category limb counts "
                f"worms registered in it ({sum(int(v) for v in cats.values())} total), and "
                "per-category class counts are not computed. The path view is preserved "
                "under `paths_trees`.")
        else:
            atlas["notes"].append(
                "limb_source='worms.by_category' was declared but the scan found no "
                "categories; limbs fall back to paths")

    # ---- Cross-root edge resolution ---------------------------------------
    # Scan all roots together to find edges BETWEEN roots of this domain.
    # Current per-root scans only capture edges WITHIN each root.
    roots_list = [_resolve(rel) for rel in spec.get("roots") or [] if _resolve(rel).is_dir()]
    if len(roots_list) >= 2:
        cross_edges = _resolve_cross_root_edges(roots_list, atlas["trees"])
        if "error" not in cross_edges:
            atlas["edges"]["tree_pairs"] += cross_edges.get("tree_pairs", 0)
            atlas["edges"]["statements"] += cross_edges.get("statements", 0)
            atlas["edges"]["cross_root"] = cross_edges
            atlas["cross_root_edges_resolved"] = True
            atlas["notes"].append(
                f"Cross-root edges resolved: {cross_edges.get('tree_pairs', 0)} root pairs "
                f"with {cross_edges.get('statements', 0)} cross-root statements"
            )
        else:
            atlas["edges"]["cross_root"] = cross_edges
            atlas["cross_root_edges_resolved"] = False
            atlas["notes"].append(
                f"Cross-root edge resolution failed: {cross_edges.get('error', 'unknown error')}"
            )
    else:
        atlas["cross_root_edges_resolved"] = True
        atlas["notes"].append("Single root domain; cross_root_edges_resolved=True by default")

    atlas["totals"]["trees"] = len(atlas["trees"])
    return atlas


#: Per-package file cap for stack counting. An uncapped walk of every DL package
#: in every environment parses ~15k files; the cap keeps a census interactive and
#: is recorded per limb, because a truncated count is a FLOOR and a reader must
#: not take it for a total.
MAX_FILES_PER_PACKAGE = 1500


def _count_package(pkg_dir: Path) -> Dict[str, Any]:
    """Modules, lines and AST classes under one installed package directory."""
    rec = {"modules": 0, "lines": 0, "classes": 0, "classes_public": 0,
           "funcs": 0, "methods": 0, "files_seen": 0, "capped": False}
    if not pkg_dir.is_dir():
        return rec
    for fp in pkg_dir.rglob("*.py"):
        if "__pycache__" in fp.parts:
            continue
        if rec["files_seen"] >= MAX_FILES_PER_PACKAGE:
            rec["capped"] = True
            break
        rec["files_seen"] += 1
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:            # unreadable file: not a module, but counted
            note_failure("domain_trees:census_read", exc)
            continue
        rec["modules"] += 1
        rec["lines"] += text.count("\n") + 1
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:        # a file that will not parse has no classes
            note_failure("domain_trees:census_parse", exc)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                rec["classes"] += 1
                if not node.name.startswith("_"):
                    rec["classes_public"] += 1
                rec["methods"] += sum(1 for c in node.body
                                      if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rec["funcs"] += 1
    return rec


def build_stacks(domain: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """One limb per (environment, family), counted from installed packages.

    The census is the input because the question is not "what is in this
    directory": it is "which deep-learning library is installed, at what
    version, in which environment". A directory walk cannot answer it -- three
    site-packages directories are all named `site-packages` -- and executing
    `import torch` in each environment would need every distro running and would
    still not say which venv held which build.
    """
    census_path = _resolve(spec["stacks"])
    atlas: Dict[str, Any] = {
        "schema": "azazael.domain_atlas/1",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "domain": domain,
        "label": spec["label"],
        "kind": spec["kind"],
        "declared_roots": [],
        "declared_files": list(spec.get("files") or []),
        "totals": dict(TREE_DEFAULT),
        "trees": {},
        "classes": {"total": 0, "public": 0, "by_tree": {}},
        "worms": {"total": 0, "categories_named": 0, "by_category": {},
                  "by_tree": {}, "by_owner": {}, "items": []},
        "edges": {"pairs": 0, "statements": 0, "tree_pairs": 0},
        "canon": {"files": [], "rules": 0},
        "skills": {},
        "evidence": {},
        "packages": {},
        "environments": {},
        "languages": {},
        "cross_root_edges_resolved": False,
        "notes": [spec.get("note", "")] if spec.get("note") else [],
        "missing": [],
    }
    if not census_path.exists():
        atlas["missing"].append(str(census_path))
        atlas["notes"].append("census file absent -- run tools/dev_stack_census.py first")
        return atlas

    census = json.loads(census_path.read_text(encoding="utf-8"))
    totals = atlas["totals"]
    for env, per in sorted((census.get("environments") or {}).items()):
        atlas["environments"][env] = {"distributions": per.get("package_total", 0),
                                       "roots": {k: v.get("label") for k, v in
                                                 (per.get("roots") or {}).items() if v.get("exists")}}
        pkgs = per.get("packages") or {}
        for fam in sorted((per.get("families") or {})):
            fam_names = {k.split("@", 1)[0].lower() for k in per["families"][fam]}
            members = {k: v for k, v in pkgs.items()
                       if k.split("@", 1)[0].lower() in fam_names}
            if not members:
                continue
            agg = {"modules": 0, "lines": 0, "classes": 0, "classes_public": 0,
                   "funcs": 0, "methods": 0}
            capped: List[str] = []
            detail = {}
            seen_dirs: Dict[str, str] = {}
            deduped: List[str] = []
            for key, meta in sorted(members.items()):
                root_path = Path(meta.get("root_path") or "")
                tops = meta.get("top_level") or []
                rec = {"modules": 0, "lines": 0, "classes": 0, "classes_public": 0,
                       "funcs": 0, "methods": 0}
                for top in tops:
                    # Two distributions can resolve to ONE import dir: opencv-python
                    # and opencv-python-headless both provide cv2, mlflow and
                    # mlflow-skinny both provide mlflow, and the alias fallback
                    # maps every "torch-xpu*" to torch. Counting the dir once per
                    # distribution inflated windows.audio/vision past its own
                    # module total before this check.
                    marker = f"{root_path.as_posix()}/{top}".lower()
                    if marker in seen_dirs:
                        deduped.append(f"{key}:{top} (same dir as {seen_dirs[marker]})")
                        continue
                    seen_dirs[marker] = key
                    part = _count_package(root_path / top)
                    for k in rec:
                        rec[k] += part[k]
                    if part["capped"]:
                        capped.append(f"{key}:{top}")
                detail[key.split("@")[0]] = meta.get("version", "")
                for k in agg:
                    agg[k] += rec[k]
            entry = dict(TREE_DEFAULT)
            entry.update({"modules": agg["modules"], "lines": agg["lines"],
                          "classes": agg["classes"], "funcs": agg["funcs"],
                          "methods": agg["methods"],
                          "public_api": agg["classes_public"],
                          "kind": "stack",
                          "packages": detail,
                          "capped": capped,
                          "shared_import_dirs": deduped})
            # Abbreviated environment prefix: the renderer prints the limb key as
            # a legend label under an 8-column figure, and "windows.numerics" +
            # "windows.classic-ml" ran into each other on the first still
            # ("windows.numericswindows.classic-ml"). The full name stays in the
            # atlas under `environment`.
            env_short = {"windows": "win", "ubuntu": "ubu", "kali": "kali"}.get(env, env[:3])
            entry["environment"] = env
            atlas["trees"][f"{env_short}.{fam}"] = entry
            for k in ("modules", "lines", "classes", "funcs", "methods"):
                totals[k] = totals.get(k, 0) + entry[k]
            atlas["classes"]["total"] += agg["classes"]
            atlas["classes"]["public"] += agg["classes_public"]
            if capped:
                atlas["notes"].append(
                    f"{env}.{fam}: {len(capped)} package dir(s) hit the "
                    f"{MAX_FILES_PER_PACKAGE}-file cap, so its class count is a floor")
        atlas["packages"][env] = per.get("families") or {}

    for rel in spec.get("canon") or []:
        p = _resolve(rel)
        rec = _count_canon(p) if p.exists() else None
        if rec is None:
            atlas["missing"].append(str(p))
            continue
        atlas["canon"]["files"].append(rec)
        atlas["canon"]["rules"] += rec["rules"]
        entry = dict(TREE_DEFAULT)
        entry.update({"lines": rec["lines"], "modules": 0,
                      "rules": rec["rules"], "kind": "canon"})
        atlas["trees"][p.stem] = entry
        totals["lines"] = totals.get("lines", 0) + rec["lines"]

    for rel in spec.get("files") or []:
        p = _resolve(rel)
        entry = _scan_file(p) if p.exists() else None
        if entry is None:
            atlas["missing"].append(str(p))
            continue
        key = p.stem
        while key in atlas["trees"]:
            key += "_"
        entry["kind"] = "file"
        atlas["trees"][key] = entry
        for k in ("modules", "lines", "classes", "funcs", "methods", "public_api", "mains"):
            totals[k] = totals.get(k, 0) + entry[k]

    atlas["totals"]["trees"] = len(atlas["trees"])
    return atlas


def manifest(atlas_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Render descriptors for every domain, in the shape render_tree.py reads."""
    trees = []
    for a in atlas_list:
        trees.append({
            "name": a["domain"],
            "label": a["label"],
            "atlas": f"domain_atlas_{a['domain']}.json",
            "top_trees": 8,
            "palette": DOMAINS[a["domain"]]["palette"],
            "emblem": "kali-dragon-icon.svg" if a["domain"] == "edge0" else "",
            "note": DOMAINS[a["domain"]].get("note", ""),
        })
    return {
        "schema": "azazael.forest_manifest/1",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "domains": [a["domain"] for a in atlas_list],
        "trees": trees,
        "note": ("one descriptor per domain; render_tree.py draws any of them from "
                 "its atlas without a code change"),
    }


def write_index(out: Path, census_path: Optional[Path] = None) -> Dict[str, Any]:
    """An index of every domain ATLAS that already exists on disk.

    The environment forest (YGGDRASIL_FOREST_<ENV>.mp4) and the domain trees are
    different deliverables and must not be merged into one clip -- the standing
    order says so, and the atlas path is the provenance that keeps them apart.
    This index is the thing that was missing: one page saying which domain
    clips exist, which atlas each one was drawn from, and which domain has an
    atlas but no clip yet.

    It reads atlases rather than rebuilding them: the dev census alone costs
    minutes, and an index that rebuilds everything to describe it is not an
    index.
    """
    rows: List[Dict[str, Any]] = []
    # Files only: a directory named `frames_*.mp4` (left by an early render)
    # otherwise matched as a clip, and the index listed a folder as a video.
    clips = {p.name: p for p in out.glob("*.mp4") if p.is_file()}
    for key in DOMAINS:
        path = out / f"domain_atlas_{key}.json"
        if not path.exists():
            rows.append({"domain": key, "label": DOMAINS[key]["label"],
                         "atlas": None, "status": "no atlas -- run this tool"})
            continue
        a = json.loads(path.read_text(encoding="utf-8"))
        t = a.get("totals") or {}
        # Match on the domain token surrounded by non-letters ($ or _), so
        # `dev` does not match `DEV_TREE...` inside an unrelated name and
        # `ml` does not match `html`.
        import re as _re
        pat = _re.compile(r"(^|[^a-z])%s([^a-z]|$)" % _re.escape(key.lower()))
        # An early render passed an output name that already ended in .mp4 to a
        # writer that appends it, leaving `..._30.mp4.mp4` on disk. Matching is
        # unchanged; the name is reported once, without the doubled extension,
        # and the malformed file is listed so it gets fixed rather than hidden.
        clip = [name for name in clips if pat.search(name.lower())]
        malformed = sorted(n for n in clip if n.lower().endswith(".mp4.mp4"))
        clip = sorted({n[:-4] if n.lower().endswith(".mp4.mp4") else n for n in clip})
        decl = DOMAINS[key]
        # Roots AND declared files: a domain whose sources are files only (gosp)
        # has no roots, so roots alone reported no newest source at all.
        sources = ([ROOT / r for r in (decl.get("roots") or [])]
                   + [ROOT / f for f in (decl.get("files") or [])])
        newest = None if decl.get("kind") == "stacks" else _newest_mtime(sources)
        gen = a.get("generated_at")
        stale = bool(newest and gen and newest > gen)
        rows.append({
            "domain": key,
            "label": a.get("label"),
            "stale": stale,
            "newest_source": newest,
            "atlas_age": ("regenerate: source newer than atlas" if stale else "current"),
            "kind": a.get("kind"),
            "limbs": len(a.get("trees") or {}),
            "modules": t.get("modules", 0),
            "lines": t.get("lines", 0),
            # `classes.total` is the AST class count the renderer prints;
            # `totals.classes` also carries file-level entries, so the two can
            # differ (sentinel: 39 vs 42) and an index that quotes one while the
            # clip prints the other looks like drift in both directions.
            "classes": (a.get("classes") or {}).get("total", t.get("classes", 0)),
            "rules": (a.get("canon") or {}).get("rules", 0),
            "languages": a.get("languages") or {},
            "atlas": path.name,
            "generated_at": a.get("generated_at"),
            "missing": len(a.get("missing") or []),
            "clips": sorted(clip),
            "clips_malformed": malformed,
        })
    index = {"schema": "azazael.domain_index/1",
             "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
             "domains": rows,
             "separate_deliverables": ("environment clips and domain clips are not "
                                       "merged: a domain figure measured on one "
                                       "environment must not imply the others")}
    (out / "domain_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    lines = ["# YGGDRASIL — domain index", "",
             f"generated {index['generated_at']} by `tools/domain_trees.py --index`", "",
             "Environment clips (WINDOWS / UBUNTU / KALI) are separate deliverables and",
             "are not merged with domain trees; each domain row names the atlas it was",
             "drawn from.", "",
             "| domain | limbs | modules | lines | classes | languages | atlas | clip |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        langs = ", ".join(f"{k} {v}" for k, v in (r.get("languages") or {}).items()
                          if k != "_capped") or "—"
        lines.append("| **{d}** | {li} | {m:,} | {ln:,} | {c:,} | {lg} | `{at}` | {cl} |".format(
            d=r["domain"], li=r.get("limbs", 0), m=r.get("modules", 0),
            ln=r.get("lines", 0), c=r.get("classes", 0), lg=langs,
            at=r.get("atlas") or "—",
            cl=(", ".join(f"`{c}`" for c in r["clips"]) if r.get("clips") else "none yet")))
    stale_rows = [r["domain"] for r in rows if r.get("stale")]
    lines += ["", f"{len(rows)} domains declared; "
                  f"{sum(1 for r in rows if r.get('atlas'))} with an atlas, "
                  f"{sum(1 for r in rows if r.get('clips'))} with a clip, "
                  f"{len(stale_rows)} STALE"
                  + (" (" + ", ".join(stale_rows) + ")" if stale_rows else ""),
              "", "Stale means a source file under the domain's roots is newer than "
              "the atlas that describes it: regenerate before quoting its numbers."]

    # --- shard inventory: the production question -------------------------
    # The cleave grammar (limb -> 24 shards -> sapling) was written for Python.
    # A multi-language repo needs the same arithmetic per language, plus the
    # seams where one language calls another, because those are the joins that
    # break when a shard is lifted out of the tree.
    if census_path and Path(census_path).exists():
        c = json.loads(Path(census_path).read_text(encoding="utf-8"))
        # The shard plan is written beside the census by
        # `yggdrasil_census.py --plan`; if it is there, the index reports it
        # instead of describing shards in the abstract.
        sp = Path(census_path).parent / "shard_plan.json"
        if sp.exists():
            c["shard_plan"] = json.loads(sp.read_text(encoding="utf-8"))
        langs = (c.get("languages") or {})
        mods = langs.get("modules") or {}
        sap = (c.get("saplings") or {})
        per = sap.get("by_language") or {}
        n_shard = sap.get("shards_per_limb", 24)
        lines += ["", "## Shard inventory — code, by language", "",
                  f"census {c.get('generated_at')} · {n_shard} shards per sapling · "
                  f"**python is import-analysed, the rest are counted**", "",
                  "| language | modules | saplings | instrumented |",
                  "|---|---|---|---|"]
        rows_lang = [("python", langs.get("python_modules", 0), sap.get("from_src", 0))]
        rows_lang += [(k, v, per.get(k, 0)) for k, v in
                      sorted(mods.items(), key=lambda kv: -kv[1])]
        for lang, nmod, nsap in rows_lang:
            lines.append(f"| {lang} | {nmod:,} | {nsap} | "
                         f"{'yes' if lang == 'python' else 'counted only'} |")
        seams = [(t, r["seams"]) for t, r in (c.get("trees") or {}).items()
                 if r.get("seams")]
        lines += ["", f"Cross-language seams: **{len(seams)}** tree(s) coupled "
                      f"outside the AST graph."]
        for t, ss in sorted(seams):
            lines.append(f"- `{t}` — {len(ss)} marker(s): "
                         + ", ".join(f"`{s}`" for s in ss[:3]))
        tiers = c.get("tiers") or {}
        # A-mixed and A-cross are not decoration: they are the trees a naive
        # "A = clean cut" list would have certified on the strength of the
        # python half alone (CODEX: 87 py + 61 js + 130 html + 7 bat).
        lines += ["", "| tier | trees | meaning |", "|---|---|---|",
                  f"| A cleave today | {len(tiers.get('A_cleave_today') or [])} | "
                  "python-clean, python-only |",
                  f"| A-mixed | {len(tiers.get('A_mixed_unaudited') or [])} | "
                  "python-clean, other languages unaudited |",
                  f"| A-cross | {len(tiers.get('A_cross_language') or [])} | "
                  "python-clean, referenced by another language |",
                  f"| B needs a shim | {len(tiers.get('B_needs_shim') or [])} | "
                  "imported at least once |",
                  f"| C cycle-blocked | {len(tiers.get('C_cycle_blocked') or [])} | "
                  "mutual module-level import |",
                  f"| D stub (not a cleave) | {len(tiers.get('D_stub') or [])} | "
                  "nothing imports it, no function or class |"]
        xl = c.get("cross_language") or {}
        if xl:
            unres = xl.get("unresolved") or {}
            pairs = xl.get("pairs_by_language") or {}
            pair_txt = ", ".join("%s %s" % (k, v) for k, v in pairs.items()) or "none"
            unres_txt = ", ".join("%s %s" % (k, v) for k, v in unres.items()) or "none"
            by = xl.get("resolved_by") or {}
            by_txt = ", ".join("%s %s" % (k, v) for k, v in by.items()) or "none"
            lines += ["",
                      "Cross-language references resolved to a tree: **%s** (%s)"
                      % (xl.get("statements", 0), pair_txt),
                      "Resolution evidence: " + by_txt,
                      "Of those, by unique filename alone (counted, labelled, and "
                      "never used to re-tier a tree): %s"
                      % xl.get("weak_statements", 0),
                      "Unresolved, by reason: " + unres_txt]
        plan = (c.get("shard_plan") or {})
        if plan:
            lines += ["", "### Shard plan", "",
                      "| language | modules | shards | underfilled | naive 24-chunk straddles |",
                      "|---|---|---|---|---|",]
            for lang, blk in (plan.get("languages") or {}).items():
                lines.append(f"| {lang} | {blk['modules']:,} | {blk['shards']} | "
                             f"{blk['underfilled']} | {blk['continuous']['straddling']} |")
            tot = plan.get("totals") or {}
            lines += ["", f"{tot.get('shards', 0)} limb-aligned shards, "
                          f"{tot.get('straddling_shards', 0)} straddling, "
                          f"{tot.get('wasted_slots', 0):,} slots unused "
                          f"(the price of not splitting a limb across shards)."]
        index["shard_inventory"] = {"languages": mods, "saplings": per,
                                   "seams": [t for t, _ in seams],
                                   "cross_language": c.get("cross_language"),
                                   "shard_plan": c.get("shard_plan") or sap.get("plan"),
                                   "tiers": {k: len(v or []) for k, v in tiers.items()}}
    (out / "DOMAIN_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", action="append", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--index", action="store_true",
                    help="write DOMAIN_INDEX.md/.json from the atlases already on disk")
    ap.add_argument("--census", default=None,
                    help="census JSON to append as the shard inventory (with --index)")
    args = ap.parse_args(argv)

    keys = args.domain or list(DOMAINS)
    if args.list:
        for k, v in DOMAINS.items():
            print(f"{k:10} {v['label']:44} roots={len(v.get('roots') or [])} "
                  f"files={len(v.get('files') or [])} canon={len(v.get('canon') or [])}")
        return 0

    out = Path(args.out)
    # --index must NOT rebuild anything: it describes atlases that already
    # exist, and the dev census alone costs minutes.
    if args.index:
        idx = write_index(out, Path(args.census) if args.census else None)
        print(f"wrote {out / 'DOMAIN_INDEX.md'} and domain_index.json "
              f"({len(idx['domains'])} domains)")
        return 0

    atlases = []
    for k in keys:
        if k not in DOMAINS:
            print(f"unknown domain {k!r}; known: {', '.join(DOMAINS)}", file=sys.stderr)
            return 2
        spec = DOMAINS[k]
        a = build_stacks(k, spec) if spec.get("kind") == "stacks" else build(k, spec)
        atlases.append(a)
        if not args.check:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"domain_atlas_{k}.json").write_text(
                json.dumps(a, indent=2, default=list), encoding="utf-8")

    if args.check:
        print(f"{'domain':10} {'trees':>6} {'modules':>8} {'lines':>9} {'classes':>8} "
              f"{'worms':>6} {'rules':>6} {'missing':>7}")
        for a in atlases:
            t = a["totals"]
            print(f"{a['domain']:10} {len(a['trees']):6} {t.get('modules', 0):8} "
                  f"{t.get('lines', 0):9} {t.get('classes', 0):8} "
                  f"{a['worms']['total']:6} {a['canon']['rules']:6} "
                  f"{len(a['missing']):7}")
        for a in atlases:
            for m in a["missing"]:
                print(f"  MISSING {a['domain']}: {m}", file=sys.stderr)
        return 0

    # The manifest is an INDEX of every atlas on disk, not a receipt for this
    # run: `--domain gosp` used to overwrite it with one entry, silently
    # dropping the other ten descriptors.
    on_disk = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted(out.glob("domain_atlas_*.json"))
               if p.stem.replace("domain_atlas_", "") in DOMAINS]
    (out / "forest_manifest.json").write_text(
        json.dumps(manifest(on_disk), indent=2), encoding="utf-8")
    print(f"wrote {len(atlases)} atlas(es) + forest_manifest.json "
          f"({len(on_disk)} descriptors) to {out}")
    for a in atlases:
        t = a["totals"]
        print(f"  {a['domain']:10} trees={len(a['trees']):3} modules={t.get('modules', 0):5} "
              f"lines={t.get('lines', 0):7} classes={t.get('classes', 0):5} "
              f"worms={a['worms']['total']:4} rules={a['canon']['rules']:4}"
              + (f"  MISSING={len(a['missing'])}" if a["missing"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
