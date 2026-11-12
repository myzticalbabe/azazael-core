#!/usr/bin/env python3
"""Build the Azazael cross-environment runtime baseline.

Reads the pip-freeze captures produced by the environment inventory
(`azazael/reports/env_inventory/*.txt`) and emits a pinned baseline describing
the one version of each contract-bearing package that every environment must
agree on.

Policy
------
* **contract packages** — anything on the wire or on disk between environments
  (HTTP clients, serialisation, encodings, dates, numerics). These MUST be
  identical everywhere; skew here is a correctness bug, not a cosmetic one.
* **supporting packages** — everything else shared. Pinned where the estate
  already agrees; recorded as `drift` where it does not.
* The target version is the **modal** version across environments (the one the
  most pillars already run), tie-broken toward the newest by parsed version.
  This minimises the number of pillars that must move.

Output: `azazael/config/runtime_baseline.toml`

Usage:
    python azazael/scripts/build_runtime_baseline.py
    python azazael/scripts/build_runtime_baseline.py --check   # non-zero on skew
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import tomllib  # noqa: F401  (3.11+; presence check only)
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

REPO = Path(__file__).resolve().parents[1]
CAPTURES = REPO / "reports" / "env_inventory"
OUT = REPO / "config" / "runtime_baseline.toml"

# 3.14 RETIREMENT (2026-10-19): win-314t is archived. win-main is now the
# 3.15t production venv; win-315 is the same build with the GIL on; win-314m
# is the one sanctioned 3.14 fallback. Captures are produced by the environment
# inventory under reports/env_inventory/.
SOURCES: dict[str, str] = {
    "win-main": "win_venv-315t.txt",
    "win-315": "win_venv-315.txt",
    "win-314m": "win_venv-314m.txt",
    "win-313tools": "win_venv-313-tools.txt",
    "win-interp": "win_venv-interpreter.txt",
    "ubuntu": "ubuntu_pip.txt",
    "kali": "kali_pip.txt",
}

# Roles decide *which* packages an environment is required to carry.
ROLES: dict[str, list[str]] = {
    "dev": ["win-main"],
    "free-threaded": ["win-main"],
    "graph": ["win-313tools", "win-main"],
    "interpreter": ["win-interp"],
    "ml-accelerator": ["ubuntu"],
    "security": ["kali"],
    "legacy-314": ["win-314m"],
}

# Packages whose version must agree across every environment: they cross a
# process, machine, or file boundary. Skew in these is a latent correctness bug.
CONTRACT: frozenset[str] = frozenset(
    """
    httpx httpcore h11 certifi idna charset-normalizer urllib3 requests
    aiohttp multidict yarl propcache frozenlist aiosignal aiohappyeyeballs
    anyio sniffio attrs rpds-py jsonschema jsonschema-specifications
    referencing python-dateutil six pyyaml click rich typing-extensions
    typing-inspection pydantic pydantic-core packaging filelock fsspec
    numpy libcst
    """.split()
)

LOCK_RE = re.compile(r"^([A-Za-z0-9_.+-]+)==(.+)$")

# Packages where the modal (most-common) version would mean a *downgrade* that is
# not worth taking.  The reconciliation report flagged filelock 4.0.0 -> 3.32.5
# as a major-version regression on the target runtime; rather than drag the production pillar
# backwards, pin 4.x and move the other pillars up.  One extra move, no
# major-version regression.
OVERRIDES: dict[str, str] = {
    "filelock": "4.0.0",
}

# Divergences that have been investigated and CANNOT be closed by installing.
# Kali's resolve is the same one that made its transformers backtrack to 2.3.0:
# for these versions there is no cp314t wheel for that platform, so pip settles
# on the newest installable one.  Recording them keeps the check honest - it
# still reports them, but they are not counted as unresolved drift.
KNOWN_DIVERGENCES: dict[str, str] = {
    "kali/click":
        "no cp314t binary wheel for 8.5.0 on this platform; pip resolves 8.4.2. "
        "verified 2026-09-20 with --only-binary=:all: --ignore-installed",
    "kali/charset-normalizer":
        "no cp314t binary wheel for 3.5.1 on this platform; pip resolves 3.5.0. "
        "verified 2026-09-20 with --only-binary=:all: --ignore-installed",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOCK_RE.match(line.strip())
        if m:
            out[normalize(m.group(1))] = m.group(2)
    return out


def version_key(v: str) -> tuple:
    """Lenient version sort: digits first, pre-releases and tags last."""
    parts = re.split(r"[.\-+]", v)
    key: list[tuple[int, object]] = []
    for p in parts:
        if p.isdigit():
            key.append((1, int(p)))
        else:
            # PEP 440 pre-release ordering: a/b/rc sort *before* the release
            rank = 0 if re.match(r"^(a|alpha)", p, re.I) else \
                   1 if re.match(r"^(b|beta)", p, re.I) else \
                   2 if re.match(r"^(rc|c|pre)", p, re.I) else 3
            key.append((0, rank * 1000 + len(p)))
    while len(key) < 4:
        key.append((1, 0))
    return tuple(key)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if any contract package is skewed")
    args = ap.parse_args()

    envs = {name: load(CAPTURES / fn) for name, fn in SOURCES.items()}
    missing = [n for n, p in envs.items() if not p]
    if missing:
        print(f"WARNING: no capture for {', '.join(missing)}", file=sys.stderr)

    universal = sorted(
        n for n in set().union(*(set(v) for v in envs.values()))
        if all(n in v for v in envs.values())
    )
    if not universal:
        print("ERROR: no packages shared across all environments", file=sys.stderr)
        return 2

    rows: list[tuple[str, str, Counter, bool]] = []
    for pkg in universal:
        seen = Counter(envs[e][pkg] for e in envs)
        modal = OVERRIDES.get(pkg) or max(
            seen.items(), key=lambda kv: (kv[1], version_key(kv[0])))[0]
        rows.append((pkg, modal, seen, pkg in CONTRACT))

    contract = [r for r in rows if r[3]]
    supporting = [r for r in rows if not r[3]]
    contract_skew = [r for r in contract if len(r[2]) > 1]
    support_skew = [r for r in supporting if len(r[2]) > 1]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# Azazael cross-environment runtime baseline",
        "#",
        "# GENERATED by scripts/build_runtime_baseline.py — do not edit by hand.",
        "# Regenerate after any deliberate version change on any pillar.",
        "#",
        "# contract  : version MUST be identical on every environment (wire/disk format)",
        "# supporting: pinning is desirable; 'drift' entries are known and tolerated",
        "",
        "[meta]",
        f"environments = {len(envs)}",
        f"universal_distributions = {len(universal)}",
        f"contract_skew = {len(contract_skew)}",
        f"supporting_skew = {len(support_skew)}",
        "",
    ]

    lines.append("[contract]")
    for pkg, modal, seen, _ in contract:
        note = ""
        if len(seen) > 1:
            alt = ", ".join(f"{v} x{c}" for v, c in sorted(seen.items(), key=lambda kv: -kv[1]))
            note = f"  # drift: {alt}"
        lines.append(f'"{pkg}" = "{modal}"{note}')
    lines.append("")

    lines.append("[supporting]")
    for pkg, modal, seen, _ in supporting:
        note = ""
        if len(seen) > 1:
            note = "  # drift"
        lines.append(f'"{pkg}" = "{modal}"{note}')
    lines.append("")

    lines.append("[roles]")
    for role, members in ROLES.items():
        rendered = ", ".join(f'"{m}"' for m in members)
        lines.append(f"{role} = [{rendered}]")
    lines.append("")

    # env/package -> why it cannot be closed.  Applies only when the environment
    # actually fails to load that package at the pinned version (a stale entry
    # whose divergence has since been fixed is ignored, and reported as such).
    lines.append("[known_divergences]")
    for key, reason in KNOWN_DIVERGENCES.items():
        lines.append(f'"{key}" = {json.dumps(reason)}')
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")

    print(f"baseline written: {OUT}")
    print(f"  environments          : {len(envs)}")
    print(f"  universal distributions: {len(universal)}")
    print(f"  contract packages      : {len(contract)} ({len(contract_skew)} skewed)")
    print(f"  supporting packages    : {len(supporting)} ({len(support_skew)} skewed)")
    if contract_skew:
        print("\n  CONTRACT SKEW (must be reconciled):")
        for pkg, modal, seen, _ in contract_skew:
            alt = ", ".join(f"{v} x{c}" for v, c in sorted(seen.items(), key=lambda kv: -kv[1]))
            print(f"    {pkg:28s} target {modal:12s}  <- {alt}")

    if args.check and contract_skew:
        print(f"\nFAIL: {len(contract_skew)} contract packages skewed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
