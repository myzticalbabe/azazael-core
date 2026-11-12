#!/usr/bin/env python3
"""Check every Azazael environment against the pinned runtime baseline.

Reads `config/runtime_baseline.toml` (build it with
`scripts/build_runtime_baseline.py`), probes each environment's installed
distributions, and reports:

  * contract violations — a contract package present at the wrong version
  * contract gaps      — a contract package missing entirely
  * supporting drift   — informational unless --strict

Exit codes:
    0  no contract violations
    1  contract violation(s) found
    2  an environment could not be probed (results incomplete)

Usage:
    python azazael/scripts/check_runtime_baseline.py
    python azazael/scripts/check_runtime_baseline.py --json report.json
    python azazael/scripts/check_runtime_baseline.py --from-captures
    python azazael/scripts/check_runtime_baseline.py --envs win-main,kali
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "config" / "runtime_baseline.toml"
CAPTURES = REPO / "reports" / "env_inventory"

# How each environment is reached. `capture` names the file used by
# --from-captures (matching build_runtime_baseline.py's SOURCES).
ENV_SPECS: dict[str, dict] = {
    # 3.14 RETIREMENT (2026-10-19): C:\Users\clint\venv-314t is archived and is
    # no longer an environment. win-main is production (3.15t); win-315 is the
    # same build with the GIL on; win-314m is the ONE sanctioned 3.14 runtime,
    # for a dependency that has no 3.15 build.
    "win-main": {
        "kind": "windows",
        "python": r"C:\Azazael\.venvs\py315t\Scripts\python.exe",
        "capture": "win_venv-315t.txt",
        "free_threaded": True,
    },
    "win-315": {
        "kind": "windows",
        "python": r"C:\Azazael\.venvs\py315\Scripts\python.exe",
        "capture": "win_venv-315.txt",
        "free_threaded": False,
    },
    "win-314m": {
        "kind": "windows",
        "python": r"C:\Azazael\.venvs\py314m\Scripts\python.exe",
        "capture": "win_venv-314m.txt",
        "free_threaded": False,
    },
    "win-313tools": {
        "kind": "windows",
        "python": r"C:\Users\clint\venv-313-tools\Scripts\python.exe",
        "capture": "win_venv-313-tools.txt",
        "free_threaded": False,
    },
    "win-interp": {
        "kind": "windows",
        "python": r"C:\Users\clint\venv-interpreter\Scripts\python.exe",
        "capture": "win_venv-interpreter.txt",
        "free_threaded": False,
    },
    "ubuntu": {
        "kind": "wsl",
        "distro": "Ubuntu-26.04",
        "python": "/usr/bin/python3",
        "capture": "ubuntu_pip.txt",
        "free_threaded": True,
    },
    "kali": {
        "kind": "wsl",
        "distro": "kali-linux",
        "python": "/usr/bin/python3",
        "capture": "kali_pip.txt",
        "free_threaded": True,
    },
}

LOCK_RE = re.compile(r"^([A-Za-z0-9_.+-]+)==(.+)$")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_baseline(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:
        sys.exit("ERROR: tomllib is stdlib only on Python 3.11+; run this script on 3.11+")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as exc:  # pragma: no cover
        return 125, f"{type(exc).__name__}: {exc}"


def parse_freeze(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = LOCK_RE.match(line.strip())
        if m:
            out[normalize(m.group(1))] = m.group(2)
    return out


def probe(name: str, spec: dict) -> tuple[dict[str, str] | None, str]:
    """Return (packages, error). packages is None when the probe failed."""
    if spec["kind"] == "windows":
        if not Path(spec["python"]).exists():
            return None, f"interpreter not found: {spec['python']}"
        rc, out = run([spec["python"], "-m", "pip", "list", "--format=freeze"])
    else:
        if shutil.which("wsl.exe") is None and shutil.which("wsl") is None:
            return None, "wsl not available"
        exe = shutil.which("wsl.exe") or shutil.which("wsl")
        rc, out = run([exe, "-d", spec["distro"], "--", spec["python"],
                       "-m", "pip", "list", "--format=freeze"])
    if rc != 0:
        head = " ".join(out.split())[:200]
        return None, f"exit {rc}: {head}"
    pkgs = parse_freeze(out)
    if not pkgs:
        return None, "probe returned no distributions"
    return pkgs, ""


def load_capture(spec: dict) -> tuple[dict[str, str] | None, str]:
    path = CAPTURES / spec["capture"]
    if not path.exists():
        return None, f"capture missing: {path}"
    pkgs = parse_freeze(path.read_text(encoding="utf-8", errors="replace"))
    return (pkgs, "") if pkgs else (None, "capture empty")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--json", type=Path, help="write the full result as JSON")
    ap.add_argument("--from-captures", action="store_true",
                    help="use saved pip-freeze captures instead of live probing")
    ap.add_argument("--envs", help="comma-separated subset of environment names")
    ap.add_argument("--strict", action="store_true",
                    help="treat supporting-package drift as failure too")
    ap.add_argument("--quiet", action="store_true", help="only print the verdict")
    args = ap.parse_args()

    if not args.baseline.exists():
        print(f"ERROR: baseline not found: {args.baseline}\n"
              f"       run: python azazael/scripts/build_runtime_baseline.py",
              file=sys.stderr)
        return 2

    base = load_baseline(args.baseline)
    contract: dict[str, str] = base.get("contract", {})
    supporting: dict[str, str] = base.get("supporting", {})
    roles: dict[str, list[str]] = base.get("roles", {})
    # "env/package" -> why it is known and unfixable. Applied per environment.
    known: dict[str, str] = base.get("known_divergences", {})

    wanted = [e.strip() for e in args.envs.split(",")] if args.envs else list(ENV_SPECS)
    unknown = [e for e in wanted if e not in ENV_SPECS]
    if unknown:
        print(f"ERROR: unknown environment(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    results: dict[str, dict] = {}
    probe_failures: list[str] = []

    for name in wanted:
        spec = ENV_SPECS[name]
        pkgs, err = (load_capture(spec) if args.from_captures else probe(name, spec))
        if pkgs is None:
            probe_failures.append(f"{name}: {err}")
            results[name] = {"status": "unreachable", "error": err}
            continue

        raw_violations = {p: {"baseline": v, "found": pkgs[p]}
                          for p, v in contract.items() if p in pkgs and pkgs[p] != v}
        gaps = sorted(p for p in contract if p not in pkgs)
        drift = {p: {"baseline": v, "found": pkgs[p]}
                 for p, v in supporting.items() if p in pkgs and pkgs[p] != v}

        # Split violations into genuinely-open and known/unfixable.  A known
        # entry whose package now matches the baseline is ignored (stale) rather
        # than silently suppressing anything.
        violations: dict[str, dict] = {}
        known_hits: dict[str, dict] = {}
        for pkg, det in raw_violations.items():
            reason = known.get(f"{name}/{pkg}")
            if reason:
                known_hits[pkg] = {**det, "reason": reason}
            else:
                violations[pkg] = det

        results[name] = {
            "status": "fail" if (violations or gaps) else "ok",
            "packages": len(pkgs),
            "contract_violations": violations,
            "contract_gaps": gaps,
            "known_divergences": known_hits,
            "supporting_drift": drift,
        }

    if not args.quiet:
        print("=" * 78)
        print("AZAZAEL RUNTIME BASELINE CHECK")
        print(f"baseline: {args.baseline.relative_to(REPO)}   "
              f"source: {'captures' if args.from_captures else 'live probe'}")
        print("=" * 78)
        print(f"  contract packages  : {len(contract)}")
        print(f"  supporting packages: {len(supporting)}")
        print(f"  roles              : {', '.join(roles) or '-'}")
        print()

        for name in wanted:
            r = results[name]
            if r["status"] == "unreachable":
                print(f"  [?] {name:14s} UNREACHABLE — {r['error']}")
                continue
            mark = "FAIL" if r["status"] == "fail" else "OK  "
            print(f"  [{mark}] {name:14s} {r['packages']:5d} dists   "
                  f"violations={len(r['contract_violations']):2d}  "
                  f"gaps={len(r['contract_gaps']):2d}  "
                  f"known={len(r['known_divergences']):2d}  "
                  f"supporting_drift={len(r['supporting_drift']):2d}")
            for pkg, d in sorted(r["contract_violations"].items()):
                print(f"          VIOLATION {pkg:28s} baseline {d['baseline']:14s} found {d['found']}")
            for pkg in r["contract_gaps"]:
                print(f"          GAP       {pkg:28s} baseline {contract[pkg]:14s} MISSING")
            for pkg, d in sorted(r["known_divergences"].items()):
                print(f"          KNOWN     {pkg:28s} baseline {d['baseline']:14s} "
                      f"found {d['found']}")
                print(f"                    reason: {d['reason'][:150]}")
        print()

    contract_failures = sum(
        len(r.get("contract_violations", {})) + len(r.get("contract_gaps", []))
        for r in results.values()
    )
    strict_drift = sum(len(r.get("supporting_drift", {})) for r in results.values())
    known_total = sum(len(r.get("known_divergences", {})) for r in results.values())

    if probe_failures:
        print("Probe failures (results incomplete):", file=sys.stderr)
        for f in probe_failures:
            print(f"  - {f}", file=sys.stderr)

    verdict = "PASS"
    code = 0
    if contract_failures:
        verdict = f"FAIL — {contract_failures} contract violation(s)/gap(s)"
        code = 1
    elif probe_failures:
        verdict = f"INCOMPLETE — {len(probe_failures)} environment(s) unreachable"
        code = 2
    elif args.strict and strict_drift:
        verdict = f"FAIL (strict) — {strict_drift} supporting drift(s)"
        code = 1

    if known_total:
        print(f"note: {known_total} known divergence(s) reported above and not "
              f"counted as drift - rebuild the baseline to review them.")
    print(f"VERDICT: {verdict}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"baseline": str(args.baseline), "verdict": verdict,
             "probe_failures": probe_failures, "environments": results},
            indent=2), encoding="utf-8")
        print(f"json: {args.json}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
