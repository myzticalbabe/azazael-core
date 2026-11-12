"""bcc_gephi_launcher.py — one-command Gephi launch for the BCC network.

Rebuilds the current BCC exports from the cosmos graph + person-intimacy layer
(the same pipeline as the SNA runs), writes a rich bcc_network.graphml
(centrality + Louvain community + k-core attributes), launches Gephi WITH the
graphml passed as a file argument, and reveals the export folder as a backup.

Since 2026-09-04 it is verified that Gephi's CLI accepts a .graphml argument:
`gephi64.exe bcc_network.graphml` boots (~20-25 s) and then opens the graphml
Import report dialog automatically — no menu/drag needed. Accept the report
(OK) to load the 48-node / 91-edge BCC network with all attributes.

Usage:
  python bcc_gephi_launcher.py                 # regen + launch-with-file + reveal
  python bcc_gephi_launcher.py --no-regen       # launch only (uses existing files)
  python bcc_gephi_launcher.py --no-launch      # regen only (CI / headless)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import csv

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))  # ~/
SNA = os.path.join(ROOT, "azazael", "scripts", "bcc_network_sna.py")
COSMOS = os.path.join(ROOT, "Desktop", "AZAZAEL_COSMOS_VIEWER_bcc", "cosmos")
PERSON_EDGES = os.path.join(ROOT, "azazael", "scripts", "bcc_person_edges.json")
OUT = os.path.join(ROOT, "gephi_export_bcc")
RESULTS = os.path.join(ROOT, "bcc_codebase_import_sna_v3.json")

GEPHI_HINTS = [
    os.path.join(os.environ.get("LOCALAPPDATA", "C:/"), "Programs", "Gephi-0.11.2", "bin", "gephi64.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", "C:/Program Files"), "Gephi-0.11.2", "bin", "gephi64.exe"),
]


def find_gephi():
    for p in GEPHI_HINTS:
        if os.path.exists(p):
            return p
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", "")):
        if not base:
            continue
        d = os.path.join(base, "Programs") if "LocalApp" in base else base
        for root, dirs, _ in os.walk(d):
            if "gephi64.exe" in _:
                return os.path.join(root, "gephi64.exe")
            dirs[:] = [x for x in dirs if x.lower().startswith("gephi")]
    return None


def regen():
    """Re-run the SNA export pipeline (current graph + person layer)."""
    os.makedirs(OUT, exist_ok=True)
    cmd = [sys.executable, SNA, "--out", RESULTS,
           "--person-edges", PERSON_EDGES, "--export-csv", OUT]
    subprocess.run(cmd, check=True, cwd=ROOT, timeout=1800)
    # GraphML with centrality + community + k-core attributes (import = one drag)
    import networkx as nx
    d = json.load(open(RESULTS, encoding="utf-8"))
    rows = []
    with open(os.path.join(OUT, "nodes.csv"), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    G = nx.Graph()
    for row in rows:
        G.add_node(row["Id"], Label=row["Label"], Type=row["Type"],
                   Degree=int(row["Degree"]), Betweenness=float(row["Betweenness"]),
                   Eigenvector=float(row["Eigenvector"]), Closeness=float(row["Closeness"]),
                   Community=row["Community"])
    with open(os.path.join(OUT, "edges.csv"), encoding="utf-8-sig") as f:
        for e in csv.DictReader(f):
            G.add_edge(e["Source"], e["Target"], EdgeType=e.get("EdgeType", "org_attendance"))
    label2id = {row["Label"]: row["Id"] for row in rows}
    for k, sh in d["core_clique"]["core_shells"].items():
        for m in sh["members"]:
            nid = label2id.get(m)
            if nid and nid in G:
                G.nodes[nid]["KCore"] = int(k)
    gml = os.path.join(OUT, "bcc_network.graphml")
    nx.write_graphml(G, gml, encoding="utf-8")
    print(f"[bcc_gephi] exports current: {G.number_of_nodes()} nodes / {G.number_of_edges()} edges")
    print(f"[bcc_gephi] {gml} (attributes: Degree/Betweenness/Eigenvector/Closeness/Community/KCore/EdgeType)")
    return gml


def reveal(gml_path):
    """Open Explorer with the graphml selected (the single drag source)."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", gml_path])


def main():
    ap = argparse.ArgumentParser(description="One-command Gephi launch for the BCC network")
    ap.add_argument("--no-regen", action="store_true", help="skip export rebuild")
    ap.add_argument("--no-launch", action="store_true", help="regenerate only")
    args = ap.parse_args()

    gml = os.path.join(OUT, "bcc_network.graphml")
    if not args.no_regen:
        gml = regen()
    if args.no_launch:
        print("[bcc_gephi] exports ready (no launch requested). Drag onto Gephi:")
        print("  " + gml)
        return

    gephi = find_gephi()
    if not gephi:
        print("[bcc_gephi] ERROR: gephi64.exe not found — install Gephi 0.11.x or pass its path.")
        sys.exit(2)
    print(f"[bcc_gephi] launching {gephi} with {os.path.basename(gml)}")
    # Verified 2026-09-04: passing the graphml as a CLI arg makes Gephi open its
    # Import report dialog automatically after boot (~20-25 s).
    subprocess.Popen([gephi, gml], cwd=os.path.dirname(gephi))
    time.sleep(2)
    reveal(gml)
    print("[bcc_gephi] done. Gephi is booting with bcc_network.graphml — after ~20-25 s")
    print("           an 'Import report' dialog appears: click OK to load the network")
    print("           (48 nodes / 91 edges, undirected). Then: Overview -> Layout ->")
    print("           ForceAtlas2 -> Run ~10 s -> Stop; Ranking -> size by Degree.")


if __name__ == "__main__":
    main()
