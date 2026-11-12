"""learning_loop_gephi_launcher.py — one-command Gephi launch for the learning loop.

Opens the GraphML produced by learning_loop_graph.py (centrality + Louvain
community + k-core attributes) in Gephi. Mirrors bcc_gephi_launcher.py: Gephi's
CLI accepts a .graphml argument, boots, and opens the Import report dialog.

Usage (Windows, where Gephi lives):
  python scripts/learning_loop_gephi_launcher.py            # launch with file
  python scripts/learning_loop_gephi_launcher.py --no-launch # print path only
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GML = os.path.join(ROOT, "graphs", "learning_loop", "learning_loop.graphml")

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
        for root, dirs, files in os.walk(d):
            if "gephi64.exe" in files:
                return os.path.join(root, "gephi64.exe")
            dirs[:] = [x for x in dirs if x.lower().startswith("gephi")]
    return None


def main():
    ap = argparse.ArgumentParser(description="Launch Gephi with the learning-loop graph")
    ap.add_argument("--no-launch", action="store_true", help="print the graphml path only")
    args = ap.parse_args()

    if not os.path.exists(GML):
        print(f"[learning_loop_gephi] ERROR: {GML} missing -- run learning_loop_graph.py first.")
        sys.exit(1)

    if args.no_launch:
        print(f"[learning_loop_gephi] drag into Gephi: {GML}")
        return

    gephi = find_gephi()
    if not gephi:
        print("[learning_loop_gephi] ERROR: gephi64.exe not found -- install Gephi 0.11.x or pass its path.")
        sys.exit(2)
    print(f"[learning_loop_gephi] launching {gephi} with learning_loop.graphml")
    subprocess.Popen([gephi, GML], cwd=os.path.dirname(gephi))
    print("[learning_loop_gephi] Gephi booting (~20-25 s); click OK on the Import report,")
    print("  then Overview -> Layout -> ForceAtlas2 -> Run; Ranking -> size by Betweenness;")


if __name__ == "__main__":
    main()