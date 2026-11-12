#!/usr/bin/env python3
"""Apply the pinned runtime baseline to one or more environments.

Reads config/runtime_baseline.toml and installs the pinned version of every
baseline package the environment is *missing or has at the wrong version*.
Nothing else is touched: packages outside the baseline are left alone, so an
environment's unique stack (Kali's security surface, Ubuntu's Intel stack) is
never disturbed.

Default is a dry run.  Pass --execute to change anything.

    python azazael/scripts/apply_runtime_baseline.py --env win-314t
    python azazael/scripts/apply_runtime_baseline.py --env win-314t --execute
    python azazael/scripts/apply_runtime_baseline.py --all --execute
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_runtime_baseline as crb  # type: ignore  # noqa: E402

BASELINE = REPO / "config" / "runtime_baseline.toml"

# The WSL system interpreters are PEP 668 "externally managed".
WSL_FLAGS = ["--break-system-packages"]

# Never "pin" the tooling that does the pinning, or the build frontend.
EXCLUDE = {"pip", "setuptools", "wheel", "distribute"}

# The contract set is the interoperability requirement and is applied by default.
# The 29 supporting pins are informational: applying them wholesale would drag
# e.g. openai 3.8 -> 3.16 in win-313tools and 2.44 -> 3.16 in win-interp, major
# jumps that could break those environments for no interop gain.  Opt in with
# --include-supporting when you actually want the whole estate on one version set.


def load_baseline() -> dict:
    import tomllib
    with BASELINE.open("rb") as fh:
        return tomllib.load(fh)


def plan_for(base: dict, installed: dict[str, str],
             include_supporting: bool = False) -> tuple[list[str], list[str]]:
    """Return (pip specs, human-readable moves) for one environment."""
    pins: dict[str, str] = {}
    sections = ("contract", "supporting") if include_supporting else ("contract",)
    for section in sections:
        for pkg, want in base.get(section, {}).items():
            if pkg in EXCLUDE:
                continue
            if installed.get(pkg) != want:
                pins[pkg] = want
    specs = [f"{pkg}=={ver}" for pkg, ver in sorted(pins.items())]
    moves = [f"{p} {installed.get(p, 'MISSING')} -> {v}" for p, v in sorted(pins.items())]
    return specs, moves


def run_pip(spec: dict, specs: list[str], timeout: int,
            ignore_installed: bool = False) -> tuple[int, str]:
    extra = ["--ignore-installed"] if ignore_installed else []
    if spec["kind"] == "windows":
        cmd = [spec["python"], "-m", "pip", "install",
               "--only-binary=:all:", *extra, *specs]
    else:
        exe = shutil.which("wsl.exe") or shutil.which("wsl")
        if not exe:
            return 127, "wsl not available"
        cmd = [exe, "-d", spec["distro"], "--", spec["python"], "-m", "pip",
               "install", *WSL_FLAGS, "--only-binary=:all:", *extra, *specs]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, str(exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="environment name (see check_runtime_baseline.py)")
    ap.add_argument("--all", action="store_true", help="every environment")
    ap.add_argument("--execute", action="store_true", help="actually install")
    ap.add_argument("--include-supporting", action="store_true",
                    help="also pin the 29 supporting packages (higher risk)")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    if not BASELINE.exists():
        print("ERROR: build the baseline first: "
              "python azazael/scripts/build_runtime_baseline.py", file=sys.stderr)
        return 2

    targets = list(crb.ENV_SPECS) if args.all else ([args.env] if args.env else [])
    if not targets:
        print("ERROR: pass --env NAME or --all", file=sys.stderr)
        return 2
    unknown = [t for t in targets if t not in crb.ENV_SPECS]
    if unknown:
        print(f"ERROR: unknown environment(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    base = load_baseline()
    failures = 0

    for env in targets:
        spec = crb.ENV_SPECS[env]
        installed, err = crb.probe(env, spec)
        if installed is None:
            print(f"\n=== {env} — UNREACHABLE: {err}")
            failures += 1
            continue

        specs, moves = plan_for(base, installed, args.include_supporting)
        scope = "contract+supporting" if args.include_supporting else "contract"
        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"\n=== {env} ({mode}, {scope}) — "
              f"{len(specs)} package(s) differ from the baseline ===")
        for m in moves:
            print(f"    {m}")
        if not specs:
            print("    already matches the baseline")
            continue
        if not args.execute:
            continue

        rc, out = run_pip(spec, specs, args.timeout)

        # Kali's click/rich/packaging/etc. come from apt, so pip has no RECORD
        # file to uninstall from and aborts with 'uninstall-no-record-file'.
        # Retry with --ignore-installed, which skips the uninstall entirely.
        if rc != 0 and "uninstall-no-record-file" in out:
            print("    note: apt-owned packages present; retrying with --ignore-installed")
            rc, out = run_pip(spec, specs, args.timeout, ignore_installed=True)

        print(f"    rc={rc}")
        for line in out.splitlines():
            if line.startswith(("Successfully installed", "ERROR", "error:")):
                print(f"      {line[:170]}")
        if rc != 0:
            failures += 1

    if args.execute and failures:
        print(f"\n{failures} environment(s) did not complete cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
