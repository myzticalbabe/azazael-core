#!/usr/bin/env python3
"""
gosp_parity_check.py -- Cross-environment parity: Rust gosp.exe vs Python gossamer

Harvests the same 3 sample texts with the RELEASE gosp.exe (KB + heuristic tags)
and parses them with the Python Montague/lambda engine, then compares which files
each engine flags for shared query terms. Structural agreement expected; semantic
depth differs by design (Rust heuristic tags vs Python compositional semantics).

Run in WSL2 Ubuntu (needs gossamer + interop access to the Windows gosp.exe):
    python3 /mnt/c/Azazael/azazael/scripts/gosp_parity_check.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/gossamer-oxford-parser")
try:
    from semantical_main import GossamerOxfordParser  # noqa: E402
except ImportError as _dep_exc:
    raise SystemExit("gosp_parity_check.py needs the gossamer-oxford-parser checkout (WSL2 path /root/gossamer-oxford-parser per docstring): " + str(_dep_exc)) from _dep_exc

def _find_gosp_exe() -> str:
    """Resolve the Rust gosp.exe (2026-09-09 fix: it lives in
    gosp_cli_run/, not gosp_parity/ — check both instead of crashing)."""
    for cand in ("/mnt/c/Users/clint/gosp_parity/gosp.exe",
                 "/mnt/c/Users/clint/gosp_cli_run/gosp.exe"):
        if Path(cand).exists():
            return cand
    return "/mnt/c/Users/clint/gosp_parity/gosp.exe"


GOSP_EXE = _find_gosp_exe()
PARITY = Path("/mnt/c/Users/clint/gosp_parity")
OUT = Path("/mnt/c/Users/clint/GOSP_PARITY_evidence.json")
# Windows process needs Windows-style paths (interop does not translate argv)
WIN_P = "C:/Users/clint/gosp_parity"

SAMPLES = {
    "audit_filing.txt": "The audit committee reviewed the annual filing and found the reserve transfer was not disclosed to the board of directors.",
    "treasurer_fund.txt": "The treasurer moved the investment income into the reserve fund while the custodian executed the reallocation order.",
    "grant_compliance.txt": "Every grantee must file the compliance report with the oversight agency before the quarter ends.",
}
QUERIES = ["audit", "treasurer", "compliance", "fund", "board"]


def main() -> int:
    t0 = time.time()
    PARITY.mkdir(exist_ok=True)
    for name, text in SAMPLES.items():
        (PARITY / name).write_text(text, encoding="utf-8")

    # Rust side: harvest + search each query
    kb = PARITY / "kb.json"
    subprocess.run(
        [GOSP_EXE, "harvest", WIN_P, "-w", "4", "-o", WIN_P + "/kb.json"],
        capture_output=True, text=True, check=True, timeout=120, encoding="utf-8", errors="replace"
    )
    rust_hits = {}
    for q in QUERIES:
        r = subprocess.run(
            [GOSP_EXE, "search", q, "--kb", WIN_P + "/kb.json", "--top-k", "10", "--format", "json"],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace"
        )
        try:
            data = json.loads(r.stdout)
            rust_hits[q] = sorted(
                Path(m["path"]).name for m in data.get("matches", [])
            )
        except Exception:  # noqa: BLE001
            rust_hits[q] = ["<parse-error>"]
        rust_hits[q] = sorted(n.replace("\\", "/").split("/")[-1] for n in rust_hits.get(q, []))

    # Python side: parse each sample, check query terms in the semantic output
    parser = GossamerOxfordParser(max_workers=2)
    py_hits = {q: [] for q in QUERIES}
    parse_summary = {}
    for name, text in SAMPLES.items():
        res = parser.parse(text, depth="full", learn=False)
        blob = json.dumps(res, default=str).lower()
        parse_summary[name] = {
            "tokens": len(res.get("parse_trees", [])),
            "insights": len(res.get("native_intelligence_insights", [])),
            "accuracy": res.get("accuracy_score", 0.0),
        }
        for q in QUERIES:
            if q in blob:
                py_hits[q].append(name)

    agreement = {}
    for q in QUERIES:
        rset, pset = set(rust_hits.get(q, [])), set(py_hits.get(q, []))
        agreement[q] = {
            "rust": sorted(rset),
            "python": sorted(pset),
            "both": sorted(rset & pset),
            "rust_only": sorted(rset - pset),
            "python_only": sorted(pset - rset),
        }

    out = {
        "test": "GOSP cross-env parity (Rust release exe vs Python engine)",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rust_exe": GOSP_EXE,
        "samples": list(SAMPLES),
        "queries": QUERIES,
        "python_parse_summary": parse_summary,
        "agreement": agreement,
        "elapsed_s": round(time.time() - t0, 2),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("=== GOSP cross-env parity ===")
    for q in QUERIES:
        a = agreement[q]
        print(f"  '{q}': both={a['both']} rust_only={a['rust_only']} python_only={a['python_only']}")
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())