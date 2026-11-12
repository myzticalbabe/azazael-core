"""install_technique_deps_315t.py — install what the extracted techniques need on 3.15t.

WHY THIS EXISTS
---------------
`tools/dep_gap_315t.py` measures what the tree imports and 3.15t cannot resolve.
This closes that gap. The packages below are the ones the *technique* code
actually reaches for, each with the module that needs it, so the list is
justified rather than a wish-list of ML libraries.

Rules this installer follows, because a batch install hides its own failures:

  * ONE package per pip invocation. A single unresolvable name (onnx has no
    3.15 wheel of any kind) otherwise aborts the whole batch and the other
    nineteen reads as "failed" when they were never attempted.
  * `--only-binary=:all:` on every invocation. A source build on a
    free-threaded interpreter either fails deep in a compiler or, worse,
    produces a wheel-less artifact that imports under the GIL only. Missing
    wheel -> BLOCKED, which is a finding, not a retry loop.
  * `--no-input`, and the verdict recorded per package in
    `state/technique_deps_315t.json` with pip's own last line as evidence.

Usage:
    python azazael/tools/install_technique_deps_315t.py              # install
    python azazael/tools/install_technique_deps_315t.py --report     # read the last run
    python azazael/tools/install_technique_deps_315t.py --check      # import-check only
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "state" / "technique_deps_315t.json"

# (pip name, import name, why — module that reaches for it)
MANIFEST: list[tuple[str, str, str]] = [
    # --- verification: the L3 gate is SMT-backed ---
    ("z3-solver", "z3", "CORE/cd2_verifier.py SMTVerifier (LogicLoc / neurosymbolic gating)"),
    # --- RL / training loop: the L6 techniques ---
    ("stable-baselines3", "stable_baselines3", "CORE/sb3_agent.py RLTrainLoop"),
    ("sb3-contrib", "sb3_contrib", "CORE/sb3_agent.py contrib algorithms"),
    ("gymnasium", "gymnasium", "CORE/WORMS/expansion/remediation_env.py, sb3_agent.py"),
    ("optuna", "optuna", "CORE/cluster_route.py hyperparameter search"),
    # --- retrieval / clustering / graph: the L1 and L2 technique substrate ---
    ("faiss-cpu", "faiss", "CORE/WORMS/expansion/engram.py vector search"),
    ("hdbscan", "hdbscan", "CORE/cluster_route.py density clustering"),
    ("igraph", "igraph", "scripts/sna_communities_410k.py community detection"),
    ("rustworkx", "rustworkx", "scripts/bench_rustworkx_vs_networkx.py graph algorithms"),
    ("chromadb", "chromadb", "scripts/bcc_chroma_index.py vector store"),
    # --- reporting / dashboards / IO ---
    ("matplotlib", "matplotlib", "ORCHESTRATION/scripts/*, monitor renderers"),
    ("pyvis", "pyvis", "scripts/ecosystem_graph.py graph rendering"),
    ("loguru", "loguru", "src/AOA/aoa_monitor.py"),
    ("msgpack", "msgpack", "CORE/WORMS/expansion/communication.py wire format"),
    ("wrapt", "wrapt", "CORE/compliance/broker.py"),
    ("pypdf", "pypdf", "tools/audit_parseable_files.py"),
    ("psycopg2-binary", "psycopg2", "CORE/postgres_memory.py"),
    ("openai", "openai", "CORE/inference/nim_client.py OpenAI-compatible providers"),
    # --- embeddings: the memory substrate's recall path ---
    ("sentence-transformers", "sentence_transformers", "PAMS EmbeddingService"),
]

# Named so the report distinguishes "no wheel exists" from "we ran out of budget".
KNOWN_BLOCKED = {
    "onnx": "no 3.15 wheel of any kind (--only-binary: all versions excluded)",
    "onnxruntime": "sdist-only at latest per requirements-3.15t.txt (needs toolchain)",
    "openvino": "sdist-only at latest per requirements-3.15t.txt",
    "ray": "sdist-only at latest per requirements-3.15t.txt",
    "llama-cpp-python": "sdist-only; needs MSVC toolchain",
}


def _resolvable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError) as e:
        print("install_technique_deps_315t:_resolvable failed: %s" % type(e).__name__,
              file=sys.stderr)
        return False


def _pip(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "pip", *args],
        capture_output=True, text=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )


def install_one(pip_name: str, import_name: str, why: str) -> dict:
    record = {"pip": pip_name, "import": import_name, "why": why}
    if _resolvable(import_name):
        record.update(verdict="ALREADY_PRESENT", evidence="importlib.find_spec resolved")
        return record
    proc = _pip(["install", "--only-binary=:all:", "--no-input", "--disable-pip-version-check",
                 pip_name])
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:] or [""]
    record["exit"] = proc.returncode
    record["evidence"] = tail[0][:300]
    if proc.returncode == 0 and _resolvable(import_name):
        record["verdict"] = "INSTALLED"
    elif "No matching distribution" in (proc.stdout or "") + (proc.stderr or ""):
        record["verdict"] = "BLOCKED_NO_WHEEL"
    else:
        record["verdict"] = "FAILED"
        record["stderr"] = (proc.stderr or "")[-400:]
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="print the last run, install nothing")
    ap.add_argument("--check", action="store_true", help="import-check only, install nothing")
    args = ap.parse_args()

    if args.report and REPORT.exists():
        print(REPORT.read_text(encoding="utf-8"))
        return 0
    if args.report:
        print("no report yet at", REPORT)
        return 1

    started = time.time()
    results = []
    for pip_name, import_name, why in MANIFEST:
        if args.check:
            results.append({"pip": pip_name, "import": import_name, "why": why,
                            "verdict": "PRESENT" if _resolvable(import_name) else "ABSENT"})
            continue
        res = install_one(pip_name, import_name, why)
        results.append(res)
        print(f"  {res['verdict']:18s} {pip_name:24s} {res.get('evidence', '')[:90]}", flush=True)

    summary = {
        "interpreter": sys.executable,
        "python": sys.version.split()[0],
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(time.time() - started, 1),
        "checked_only": args.check,
        "installed": [r["pip"] for r in results if r["verdict"] == "INSTALLED"],
        "already_present": [r["pip"] for r in results if r["verdict"] == "ALREADY_PRESENT"],
        "blocked_no_wheel": [r["pip"] for r in results if r["verdict"] == "BLOCKED_NO_WHEEL"],
        "failed": [r["pip"] for r in results if r["verdict"] == "FAILED"],
        "known_blocked_not_attempted": KNOWN_BLOCKED,
        "results": results,
    }
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\ninstalled        :", len(summary["installed"]), summary["installed"])
    print("already present  :", len(summary["already_present"]))
    print("blocked (no wheel):", summary["blocked_no_wheel"])
    print("failed           :", summary["failed"])
    print("report           :", REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
