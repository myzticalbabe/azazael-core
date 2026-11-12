"""Build the progress-governance cosmos graph from the Azazael governance/progress doc corpus.

Corpus = azazael/*.md (governance, audit, TODO, milestone, ops docs) + the root-level
progress records (SESSION_MILESTONE_*.md, NEXT_SESSION_PLAN.md) that are NOT duplicated
inside the repo. Nodes = docs. Edges = explicit cross-doc citations (markdown link targets,
backticked or extension-qualified filenames, and long distinctive bare uppercase tokens).

Output: a graphify/cosmos-compatible pair consumed by bcc_network_sna.py:
  <out>/cosmos/graph.labels.0.js   (window.GV_LABELS_CHUNKS.push([{id,label,file,type,deg},...]))
  <out>/cosmos/graph.links.js      (window.GV_LINKS Uint32Array of little-endian uint32 pairs)
"""
import base64, json, os, re, struct, sys

AZAZAEL = os.path.abspath("azazael")
ROOT = os.path.abspath(".")
OUT = os.path.abspath("Desktop/AZAZAEL_COSMOS_VIEWER_progress_governance")

STOP = {"readme", "license", "contributing", "changes", "changelog", "setup", "install"}

# Governance/progress/ops semantics by filename — docs that PLAN, RULE, TRACK, AUDIT or
# STANDARDIZE the project. Explicitly excludes lesson logs, session insights, memories,
# tutorials, research notes and per-topic guides (noise for a governance-network SNA).
GOVERNANCE_PROGRESS = re.compile(
    r"(TODO|AUDIT|MILESTONE|PLAN|COMPLIANCE|CHECKLIST|MANDATES|SOP|RULES|PROTOCOL|"
    r"SYSTEM_INSTRUCTIONS|SKILLS_CATALOG|CROSS_ENV_MANIFEST|WORM_CONFIG_TRACE|TEST_RESULTS|"
    r"CHANGELOG|ERROR_LOG|LESSONS_MASTER|SESSION_STARTUP|CATCHALL|BACKLOG|STATUS|TRACKING|"
    r"READ_ME|README)", re.I)


def corpus_files():
    """Governance/progress azazael/*.md + root progress records not duplicated in the repo."""
    files = sorted(f for f in os.listdir(AZAZAEL) if f.lower().endswith(".md"))
    files = [f for f in files if not f.startswith(".") and GOVERNANCE_PROGRESS.search(f)]
    have = set(files)
    root_extra = []
    for f in sorted(os.listdir(ROOT)):
        if not f.lower().endswith(".md"):
            continue
        if f in have:
            continue  # repo copy is the live source of truth
        # root progress/plan records only
        if f.startswith("SESSION_MILESTONE_") or f in ("NEXT_SESSION_PLAN.md",):
            root_extra.append(f)
    return files, root_extra


def collect_texts(files, root_extra):
    out = []
    for f in files:
        p = os.path.join(AZAZAEL, f)
        if os.path.getsize(p) > 4_000_000:  # skip generated/giant (e.g. no such in .md)
            continue
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        out.append({"name": f, "path": f"azazael/{f}", "text": txt})
    for f in root_extra:
        txt = open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()
        out.append({"name": f, "path": f, "text": txt})
    return out


def main():
    files, root_extra = corpus_files()
    docs = collect_texts(files, root_extra)
    docs = [d for d in docs if d["text"].strip()]
    names = [d["name"] for d in docs]
    base = {d["name"]: d for d in docs}
    # dedupe basenames (identical name in repo + root handled above); also drop dup .md/.MD variants
    seen = {}
    uniq = []
    for d in docs:
        if d["name"] in seen:
            continue
        seen[d["name"]] = True
        uniq.append(d)
    docs = uniq
    names = [d["name"] for d in docs]
    idx = {n: i for i, n in enumerate(names)}

    # build per-doc list of cited doc indices
    edges = set()
    for d in docs:
        txt = d["text"]
        for other in docs:
            if other["name"] == d["name"]:
                continue
            stem = other["name"][:-3] if other["name"].lower().endswith(".md") else other["name"]
            ref = False
            # 1) extension-qualified mention (SOP.md / path/CODEBASE_AUDIT_TODO.md)
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(stem) + r"\.md\b", txt):
                ref = True
            # 2) backticked bare token
            if re.search(r"`\s*" + re.escape(stem) + r"\s*`", txt):
                ref = True
            # 3) markdown link target containing the stem
            if re.search(r"\]\(\s*[^)]*" + re.escape(stem) + r"(?:\.md)?\s*\)", txt):
                ref = True
            # 4) bare all-caps token — in this corpus's idiom a bare uppercase token
            #    ("per SOP", "in MANDATES", "see CODEBASE_AUDIT_TODO") is a doc reference.
            #    Require len>=3, every char A-Z/0-9/_/-, and not a stopword/acronym-ish.
            if not ref and len(stem) >= 3 and re.fullmatch(r"[A-Z0-9_\-]+", stem):
                low = stem.lower()
                if low in STOP or low in ("ai", "ml", "rl", "gpu", "os", "or", "llm"):
                    pass
                elif re.search(r"(?<![A-Za-z0-9_])[\(`\[]?" + re.escape(stem) + r"[\)`\]]?(?![A-Za-z0-9_])", txt):
                    ref = True
            if ref:
                edges.add(tuple(sorted((idx[d["name"]], idx[other["name"]]))))

    # degrees
    deg = [0] * len(docs)
    for a, b in edges:
        deg[a] += 1
        deg[b] += 1

    # cosmos label chunk (same schema as graph.labels.0.js)
    labels = [{"id": d["name"], "label": d["name"][:-3] if d["name"].lower().endswith(".md") else d["name"],
               "file": d["path"], "type": "doc", "deg": deg[i]} for i, d in enumerate(docs)]

    os.makedirs(os.path.join(OUT, "cosmos"), exist_ok=True)
    with open(os.path.join(OUT, "cosmos", "graph.labels.0.js"), "w", encoding="utf-8") as f:
        f.write("window.GV_LABELS_CHUNKS = window.GV_LABELS_CHUNKS || [];\n")
        f.write("window.GV_LABELS_CHUNKS.push(" + json.dumps(labels) + ");\n")

    pairs = []
    for a, b in sorted(edges):
        pairs += [a, b]
    raw = struct.pack("<%dI" % len(pairs), *pairs)
    with open(os.path.join(OUT, "cosmos", "graph.links.js"), "w", encoding="utf-8") as f:
        f.write("window.GV_LINKS = new Uint32Array(Uint8Array.from(atob('")
        f.write(base64.b64encode(raw).decode())
        f.write("')));\n")

    manifest = {"nodes": len(docs), "edges": len(edges),
                "node_files": [d["path"] for d in docs],
                "edge_list": [[names[a], names[b]] for a, b in sorted(edges)]}
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print(f"progress-governance graph: {len(docs)} docs, {len(edges)} cross-ref edges -> {OUT}")
    print(f"top-degree docs: {sorted(((deg[i], docs[i]['name']) for i in range(len(docs))), reverse=True)[:12]}")


if __name__ == "__main__":
    main()
