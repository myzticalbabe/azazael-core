"""Cross-environment drift: per-file md5 for each azazael tree, then compare.

Pruning MUST match ast_audit_env.py's SKIP_DIRS exactly, otherwise the drift
manifest and the AST census report two different file counts for the same tree
(measured 2026-11-01: 5,268 vs 2,404 py files, the gap being archive/,
archived/ and graphify-out/).

Usage:
  python ast_drift.py manifest <root> <label>
  python ast_drift.py findcmd            # prints the identical WSL find+md5 command
  python ast_drift.py compare
"""
import hashlib
import json
import os
import sys

BASE = r"C:/Users/clint/ast_audit"

# identical to ast_audit_env.py::SKIP_DIRS
SKIP = {
    ".cache", ".conan", ".eggs", ".git", ".hg", ".kali_staging", ".mypy_cache",
    ".pams_venv", ".pytest_cache", ".ruff_cache", ".svn", ".tox", ".venv", ".venvs",
    "__pycache__", "archive", "archived", "build", "dist", "dist-packages",
    "graphify-out", "node_modules", "sdist", "site-packages", "target", "venv",
}


def manifest(root, label):
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.md5(fh.read()).hexdigest()
            except OSError as exc:
                note_failure("tools/ast_drift.py", exc)
                pass
    with open(f"{BASE}/drift_{label}.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print(f"{label}: {len(out)} py files")


def findcmd():
    prunes = " ".join(f'-name {d} -o' for d in sorted(SKIP))
    print(f'find . \\( {prunes} -name .git \\) -prune -o -name "*.py" -type f -print0 | xargs -0 md5sum')


def load(label):
    with open(f"{BASE}/drift_{label}.json", encoding="utf-8") as fh:
        return json.load(fh)


def compare():
    envs = ["windows", "kali", "ubuntu"]
    mans = {e: load(e) for e in envs}
    for e in envs:
        print(f"{e:9} {len(mans[e])} files")

    def pair(a, b):
        A, B = mans[a], mans[b]
        common = set(A) & set(B)
        same = [p for p in common if A[p] == B[p]]
        diff = sorted(common - set(same))
        only_a = sorted(set(A) - set(B))
        only_b = sorted(set(B) - set(A))
        pct = 100.0 * len(diff) / len(common) if common else 0.0
        print(f"\n{a} vs {b}: common {len(common)}, identical {len(same)}, DIFFERENT {len(diff)} ({pct:.1f}%), only-{a} {len(only_a)}, only-{b} {len(only_b)}")
        print(f"  differ sample: {diff[:8]}")
        print(f"  only-{a} sample: {only_a[:8]}")
        print(f"  only-{b} sample: {only_b[:8]}")
        return {"common": len(common), "same": len(same), "diff": len(diff),
                "pct": round(pct, 1), "only_a": len(only_a), "only_b": len(only_b),
                "diff_paths": diff, "only_a_paths": only_a, "only_b_paths": only_b}

    res = {
        "windows_vs_kali": pair("windows", "kali"),
        "windows_vs_ubuntu": pair("windows", "ubuntu"),
        "kali_vs_ubuntu": pair("kali", "ubuntu"),
    }
    with open(f"{BASE}/drift_report.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("\nwrote drift_report.json")



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

if __name__ == "__main__":
    if sys.argv[1] == "manifest":
        manifest(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "findcmd":
        findcmd()
    else:
        compare()
