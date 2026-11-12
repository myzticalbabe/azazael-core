#!/usr/bin/env python3
"""build_obsidian_vault.py — Convert the BCC investigation corpus into an Obsidian vault.

Reads the corpus HTML/MD/TXT docs from a root dir and produces a new Obsidian vault:

  <vault>/
    _source/              copied original corpus files (html/md/txt)
    <DocName>.md          one connector note per doc: frontmatter + wikilinks
    BCC_NETWORK.canvas    Obsidian Canvas (all docs as nodes, cross-links as edges)
    00_INDEX.md           vault index with category groupings

Obsidian graph view renders the .md notes and their [[wikilinks]]; the canvas is
the explicit spatial layout. Wikilinks target .md note basenames, so the graph
shows every cross-document connection the corpus already has.

Usage:
  python build_obsidian_vault.py --root C:/Users/clint \
      --out "C:/Users/clint/Documents/BCC_Investigation_Vault"
  python build_obsidian_vault.py --include-excluded   # also copy app_home/bcc_pp2/etc

Artifacts: per-doc notes + canvas + index; nothing destructive (copy-only).
"""
import argparse
import json
import os
import re
import shutil
import sys
import random

# metadata enrichment: category per doc (best-effort from FINAL_STATUS inventory wording)
CATEGORY_HINTS = [
    ("INVESTIGATION_GAPS", "Evidence & Analysis"),
    ("FUNDING_REVIEW", "Evidence & Analysis"),
    ("INVESTMENT_PEOPLE_REPORT", "Evidence & Analysis"),
    ("DEEP_DIVE_HARM", "Evidence & Analysis"),
    ("PATIENT_HARM_REPORT", "Evidence & Analysis"),
    ("CONSOLIDATED_SCROLL", "Evidence & Analysis"),
    ("DONOR_RECONSTRUCTION", "Evidence & Analysis"),
    ("MISSING_EVIDENCE_INFERENCE", "Evidence & Analysis"),
    ("INFERRED_ANGLES", "Evidence & Analysis"),
    ("HIDDEN_HARM_INFERENCE", "Evidence & Analysis"),
    ("HIDDEN_ASPECTS_SWEEP", "Evidence & Analysis"),
    ("EXPANDED_EVIDENCE", "Evidence & Analysis"),
    ("HIDDEN_NETWORK", "Evidence & Analysis"),
    ("HARM_EVIDENCE_MASTER", "Evidence & Analysis"),
    ("CAMPAIGN_FINANCE_EVIDENCE", "Evidence & Analysis"),
    ("EMPLOYEE_REVIEWS_EVIDENCE", "Evidence & Analysis"),
    ("INVESTMENT_MANAGER_EVIDENCE", "Evidence & Analysis"),
    ("PEOPLE_NETWORK_CARD", "Evidence & Analysis"),
    ("HOUSING_EVICTION_HARM_NETWORK", "Evidence & Analysis"),
    ("BCC_FINANCIAL_ANOMALY", "Evidence & Analysis"),
    ("BCC_EVENT_TIMELINE", "Evidence & Analysis"),
    ("DATA_REFERENCE", "Audit & Reference"),
    ("G_ARCHIVE_CATALOG", "Audit & Reference"),
    ("AOA_LIBRA_GAP_CATALOG", "Audit & Reference"),
    ("GEN_DIFF_FINDINGS", "Audit & Reference"),
    ("COMPLAINT_LETTER", "Letters & Complaints"),
    ("COMPLAINT_LETTER_REDACTED", "Letters & Complaints"),
    ("PACOURTS_CP_RECORDS_REQUEST", "Letters & Complaints"),
    ("DOS_BCO10_RECORDS_REQUEST", "Letters & Complaints"),
    ("COMPLAINT_AGENCY_LETTER", "Letters & Complaints"),
    ("COI_REQUEST_LETTER", "Letters & Complaints"),
    ("SAMHSA_PERFORMANCE_H79SM088878", "Supporting"),
    ("BCC_PROPERTY_OWNERSHIP", "Supporting"),
    ("DDAP_INVESTIGATION_EVIDENCE", "Supporting"),
    ("FORENSIC_EXTRACTION_BLUEPRINT", "Supporting"),
    ("BCC_GRIEVANCE_LETTER", "Supporting"),
    ("EXCLUSION_INTEGRITY_SWEEP", "Supporting"),
    ("BCC_FINANCIAL_WORM_MAP", "Supporting"),
    ("HHS_OIG_MONITORING", "Supporting"),
    ("RECUSAL_EVIDENCE", "Supporting"),
    ("FUNCTION_MAP", "Supporting"),
    ("PEOPLE_SEARCH_ALT", "Supporting"),
    ("PEOPLE_NETWORK_SCROLL", "Supporting"),
    ("SCROLL_DEEPDIVE", "Supporting"),
    ("SCROLL_FUNDING", "Supporting"),
    ("PEOPLE_DOSSIER", "Supporting"),
    ("SOCIAL_PRESENCE_LEADS", "Supporting"),
    ("KALI_SWEEP_RESULTS", "Supporting"),
]


def title_of(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(12000)
    m = re.search(r"<title>(.*?)</title>", head, re.I | re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    if path.lower().endswith(".md"):
        for line in head.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return os.path.splitext(os.path.basename(path))[0]


def links_of(path: str) -> list:
    """Outbound corpus-doc links in an HTML file (href='X.html')."""
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    return sorted(set(re.findall(r'''href=["']([A-Za-z0-9_\-]+\.html)(?:[#"][^"']*)?["']''', src, re.I)))


def build_vault(root: str, out: str, include_excluded: bool, seed: int = 42) -> dict:
    source_dir = os.path.join(out, "_source")
    os.makedirs(source_dir, exist_ok=True)

    # collect files
    docs = []
    for name in sorted(os.listdir(root)):
        low = name.lower()
        if name.startswith("."):
            continue  # dotfiles (.aider.chat.history.md etc) are session artifacts, not corpus
        if not (low.endswith(".html") or low.endswith(".md") or low.endswith(".txt")):
            continue
        if name == "FINAL_STATUS_REPORT.html":
            continue  # reserved as the meta doc itself (itself excluded)
        if not include_excluded and low in ("app_home.html", "bcc_pp2.html", "wb_datasets.html"):
            continue
        if low.startswith("_990_"):
            if not include_excluded:
                continue
        docs.append(name)
    docs = [d for d in docs if os.path.isfile(os.path.join(root, d))]

    # stub note for FINAL_STATUS_REPORT so existing corpus links to it resolve
    fsr_stub = os.path.join(out, "FINAL_STATUS_REPORT.md")
    with open(fsr_stub, "w", encoding="utf-8", newline="\n") as f:
        f.write("---\ntitle: \"FINAL STATUS REPORT — Session 28 Running Record\"\n"
                "category: Audit & Reference\ntype: meta\n---\n\n# Final Status Report\n\n"
                "> The live report itself is excluded from the vault (it is the meta doc); "
                "this stub keeps incoming links resolvable. See `_source/FINAL_STATUS_REPORT.html`.\n")

    # 1) copy sources
    for name in docs:
        shutil.copy2(os.path.join(root, name), os.path.join(source_dir, name))

    # 2) write connector notes
    notes = {}
    by_category = {}
    for name in docs:
        stem = os.path.splitext(name)[0]
        title = title_of(os.path.join(root, name))
        cat = next((c for k, c in CATEGORY_HINTS if stem.startswith(k)), "Investigation")
        by_category.setdefault(cat, []).append(stem)

        out_links = []
        if name.lower().endswith(".html"):
            out_links = links_of(os.path.join(root, name))
        note_lines = ["---", f"title: \"{title.replace(chr(34), chr(39))}\"",
                      f"source: «{name}»", f"category: {cat}", "type: corpus-doc", "---", "",
                      f"# {title}", "",
                      f"> Source doc: [[{_q(name)}]] · original: `_source/{name}`", ""]
        if out_links:
            note_lines += ["## Linked docs", ""]
            note_lines += [f"- [[{_q(ln)}]]" for ln in out_links]
        else:
            note_lines += ["## Linked docs", "", "_No outbound corpus links._"]
        note_lines += ["", "---", f"_Generated by build_obsidian_vault.py from `{name}`_", ""]
        note_path = os.path.join(out, stem + ".md")
        with open(note_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(note_lines))
        notes[stem] = {"title": title, "category": cat, "out": out_links}

    # 3) index note
    idx = ["---", "title: BCC Investigation Vault Index", "type: index", "---", "",
           "# 🗄️ BCC Investigation — Vault Index", "",
           f"_Generated {__import__('datetime').date.today().isoformat()}_", "",
           f"**{len(notes)} corpus docs** mirrored as connector notes. Use **Graph view** "
           "(Ctrl-G) to see every cross-document connection; **BCC_NETWORK.canvas** has the "
           "explicit layout.", ""]
    for cat in sorted(by_category):
        idx += [f"## {cat} ({len(by_category[cat])})", ""]
        for stem in sorted(by_category[cat]):
            idx.append(f"- [[{stem}]]")
        idx.append("")
    with open(os.path.join(out, "00_INDEX.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(idx))

    # 4) canvas
    canvas = build_canvas(notes, seed)
    with open(os.path.join(out, "BCC_NETWORK.canvas"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(canvas, f, indent=2)

    # 5) .obsidian config marker (make it loadable as a vault without prompts)
    obsidian_dir = os.path.join(out, ".obsidian")
    os.makedirs(obsidian_dir, exist_ok=True)
    with open(os.path.join(obsidian_dir, "app.json"), "w", encoding="utf-8") as f:
        json.dump({"showUnsupportedFiles": True}, f)
    with open(os.path.join(obsidian_dir, "core-plugins.json"), "w", encoding="utf-8") as f:
        json.dump(["file-explorer", "global-search", "switcher", "graph", "backlink", "canvas",
                   "outgoing-link", "tag-pane", "page-preview", "templates"], f)

    return {"notes": len(notes), "categories": {c: len(v) for c, v in by_category.items()},
            "edges": sum(len(n["out"]) for n in notes.values()), "vault": out}


def _q(name: str) -> str:
    """Wikilink target: note basename for html/md, escaped for txt."""
    stem = os.path.splitext(name)[0]
    ext = os.path.splitext(name)[1].lower()
    if ext == ".html":
        return stem
    if ext == ".md":
        return stem
    return stem


def build_canvas(notes: dict, seed: int = 42) -> dict:
    """Canvas JSON: one file node per note, one edge per cross-link."""
    rng = random.Random(seed)
    nodes, edges = [], []
    positions = {}
    # grid by category
    cats = sorted(set(n["category"] for n in notes.values()))
    cat_col = {c: i for i, c in enumerate(cats)}
    members = {c: [s for s, n in notes.items() if n["category"] == c] for c in cats}
    for c in cats:
        for j, stem in enumerate(sorted(members[c])):
            positions[stem] = (cat_col[c] * 420 + (j % 5) * 260 + 60, (j // 5) * 200 + 60)
    for i, stem in enumerate(notes):
        x, y = positions.get(stem, (60, 60))
        nodes.append({"id": "n%d" % i, "type": "file", "file": stem + ".md",
                      "x": x, "y": y, "width": 240, "height": 60})
    nid = {stem: "n%d" % i for i, stem in enumerate(notes)}
    eid = 0
    seen = set()
    for stem, info in notes.items():
        for target in info["out"]:
            tstem = os.path.splitext(target)[0]
            if tstem not in nid:
                continue
            key = tuple(sorted((stem, tstem)))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"id": "e%d" % eid, "fromNode": nid[stem], "toNode": nid[tstem],
                          "fromSide": "right", "toSide": "left"})
            eid += 1
    return {"nodes": nodes, "edges": edges, "type": "canvas"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build an Obsidian vault from the BCC corpus")
    ap.add_argument("--root", default=r"C:\Users\clint", help="dir holding corpus docs")
    ap.add_argument("--out", default=r"C:\Users\clint\Documents\BCC_Investigation_Vault",
                    help="vault output dir")
    ap.add_argument("--include-excluded", action="store_true",
                    help="also copy app_home/bcc_pp2/wb_datasets/_990_ captures")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    res = build_vault(args.root, args.out, args.include_excluded, args.seed)
    print(f"Vault: {res['vault']}")
    print(f"Notes: {res['notes']} | Categories: {res['categories']} | Cross-links: {res['edges']}")
    print("Open in Obsidian: File > Open folder as vault, or use the obsidian:// URI.")


if __name__ == "__main__":
    sys.exit(main())
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
