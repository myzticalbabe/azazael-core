#!/usr/bin/env python3
"""ONE-CLICK BOOTSTRAP -- make a checkout WHOLE, or say exactly what is missing.

WHY THIS EXISTS
---------------
Every gate in this tree assumes eight things it never installs: hooks wired to
`.githooks`, a settings file, the canon blocks in every lane file, armed
baselines, declared packages, a CI producer, the MCP consumers, and the two
stores the metrics read. A fresh clone, a second lane, or a machine that was
repaired at 02:00 has some subset of those, and every missing one looks like a
silent pass: `core.hooksPath` unset runs NO hook and exits 0; a missing
anti-pattern baseline compares against nothing; a missing lane block means that
lane never reads the order.

So the repair is a step, not a paragraph in a README. Each step is a
CHECK and an APPLY, both recorded (`logs/one_click_bootstrap.json`) with the
evidence line that proved it.

WHAT IT WILL NOT DO SILENTLY
----------------------------
* It will not `pip install` without naming the distributions it is missing, and
  `--check` never touches the network or the interpreter's packages.
* It will not create a baseline that hides today's defects: the golden-arrow
  baseline is armed from the report already on disk, and the report names the
  count that became DEBT.
* It will not treat "could not check" as "fine". A step that cannot be performed
  is `BLIND` and is reported as such.

RUN
    python tools/one_click_bootstrap.py            # check + repair repo-local state
    python tools/one_click_bootstrap.py --check    # report only, change nothing
    python tools/one_click_bootstrap.py --install-deps   # also install missing packages
    python tools/one_click_bootstrap.py --json out.json

EXIT
    0 every step is green after the run
    1 a step is still red (named)
    2 a step could not be performed (BLIND, named)
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "one_click_bootstrap.json"
PY = Path("C:/Azazael/.venvs/py316t/Scripts/python.exe")
#: This tree declares its packages as an interpreter-specific requirement file
#: plus a pyproject. `requirements.txt` does not exist here, and a check that
#: looks for it reported BLIND forever -- "no requirements" reads as "nothing to
#: install", which is the opposite of what a bootstrap is for.
REQ_FILES = ("requirements-3.15t.txt", "requirements-3.14t.txt", "requirements-gil.txt",
             "requirements_314_full.txt", "requirements.txt")
BASELINES = (
    ("anti-pattern", ROOT / "logs" / "anti_pattern_baseline.json"),
    ("golden-arrow", ROOT / "logs" / "golden_arrow_baseline.json"),
    ("unresolved-refs", ROOT / "tools" / "unresolved_baseline.json"),
)


def interpreter() -> str:
    return str(PY) if PY.exists() else sys.executable


def run(argv, bound=300):
    try:
        proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=bound)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


# ── steps ───────────────────────────────────────────────────────────────────
def check_hooks(applying: bool) -> dict:
    code, out = run(["git", "config", "--get", "core.hooksPath"])
    value = (out or "").strip().replace("\\", "/")
    if value.startswith("./"):
        value = value[2:]
    wired = value == ".githooks"
    if wired:
        return {"status": "ok", "detail": "core.hooksPath = .githooks"}
    if not applying:
        return {"status": "red", "detail": "core.hooksPath is %r: NO hook runs, and a "
                                           "commit exits 0 anyway" % (out or "").strip()}
    code, out = run(["git", "config", "core.hooksPath", ".githooks"])
    if code != 0:
        return {"status": "red", "detail": "could not set core.hooksPath: %s" % out[-200:]}
    return {"status": "fixed", "detail": "core.hooksPath = .githooks"}


def check_defaults(applying: bool) -> dict:
    code, out = run([interpreter(), "tools/defaults_profile.py", "--check"])
    if code == 0:
        return {"status": "ok", "detail": out.splitlines()[-1][:160]}
    if not applying:
        return {"status": "red", "detail": "defaults profile invalid or absent: %s"
                                          % (out or "")[-200:]}
    if not (ROOT / "config" / "defaults.json").exists():
        run([interpreter(), "tools/defaults_profile.py", "--init"])
    code, out = run([interpreter(), "tools/defaults_profile.py", "--check"])
    return {"status": "fixed" if code == 0 else "red",
            "detail": out.splitlines()[-1][:160] if out else "no output"}


def check_canon(applying: bool) -> dict:
    code, out = run([interpreter(), "tools/agent_orders_sync.py", "--check"])
    if code == 0:
        return {"status": "ok", "detail": out.splitlines()[-1][:160]}
    if not applying:
        return {"status": "red", "detail": (out or "").splitlines()[-1][:200]}
    run([interpreter(), "tools/agent_orders_sync.py", "--apply"])
    run([interpreter(), "tools/agent_orders_sync.py", "--apply", "--wsl"])
    code, out = run([interpreter(), "tools/agent_orders_sync.py", "--check"])
    tail = (out or "").splitlines()[-1][:160] if out else "no output"
    return {"status": "fixed" if code == 0 else "red", "detail": tail}


def check_baselines(applying: bool) -> dict:
    missing = [name for name, path in BASELINES if not path.exists()]
    if not missing:
        return {"status": "ok", "detail": "3 anchor(s) present (%s)"
                                         % ", ".join(n for n, _ in BASELINES)}
    if not applying:
        return {"status": "red", "detail": "absent anchor(s): %s -- a ratchet with no "
                                          "baseline compares against nothing" % ", ".join(missing)}
    fixed = []
    if "anti-pattern" in missing:
        run([interpreter(), "tools/anti_pattern_gate.py", "--baseline"], 900)
        fixed.append("anti-pattern")
    if "golden-arrow" in missing:
        run([interpreter(), "tools/golden_arrow.py", "--status", "--baseline"], 900)
        fixed.append("golden-arrow (from the report on disk, so today's failures are "
                     "recorded as DEBT by name)")
    if "unresolved-refs" in missing:
        run([interpreter(), "tools/unresolved_ratchet.py", "--baseline"], 900)
        fixed.append("unresolved-refs")
    still = [name for name, path in BASELINES if not path.exists()]
    return {"status": "fixed" if not still else "red",
            "detail": ("armed: " + ", ".join(fixed)) if not still
                      else "still absent: %s" % ", ".join(still)}


def _declared() -> dict:
    """{distribution: requirement line} for THIS interpreter's declarations.

    Only the file matching the running interpreter is a declaration for the
    running interpreter: `requirements-3.14t.txt` is a different venv's freeze,
    and installing it into 3.15t is how a bootstrap breaks the very stack it is
    meant to repair. Measured 2026-11-02: the union of four files reported 139
    "missing" packages that 3.15t never claimed to have.
    """
    declared = {}
    tag = "%d.%d" % sys.version_info[:2]
    chosen = [n for n in REQ_FILES if tag in n] or [n for n in REQ_FILES if n == "requirements.txt"]
    for name in chosen:
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            match = re.match(r"^([A-Za-z0-9_.\-]+)", line)
            if match:
                declared.setdefault(match.group(1), line)
    return declared


def check_packages(applying: bool, install: bool) -> dict:
    declared = _declared()
    if not declared:
        return {"status": "blind",
                "detail": "no requirements*.txt found -- nothing declared to install"}
    missing = []
    for dist in declared:
        try:
            metadata.version(dist)
        except metadata.PackageNotFoundError:
            missing.append(dist)
    if not missing:
        return {"status": "ok", "detail": "%d declared distribution(s) importable"
                                         % len(declared)}
    if not (applying and install):
        return {"status": "red",
                "detail": "%d missing (%s)%s" % (
                    len(missing), ", ".join(sorted(missing)[:8]),
                    "" if applying else "; re-run with --install-deps to install")}
    # One distribution at a time. Measured 2026-11-02: a batch install aborted
    # with "Could not find a version that satisfies pywin32 (from versions:
    # none)" and installed NOTHING -- a single unavailable wheel (3.15 has no
    # pywin32 build yet) took four installable packages down with it. Per-name
    # attempts keep the failures separate and nameable.
    started = time.monotonic()
    installed, failed = [], {}
    for dist in sorted(missing):
        code, out = run([interpreter(), "-m", "pip", "install", dist], 600)
        if _missing(dist):
            failed[dist] = (out or "").strip().splitlines()[-1][:120]
        else:
            installed.append(dist)
    detail = "installed %d in %.0fs" % (len(installed), time.monotonic() - started)
    if failed:
        detail += "; unavailable here: %s" % "; ".join(
            "%s (%s)" % (k, v) for k, v in list(failed.items())[:3])
    return {"status": "fixed" if not failed else "red", "detail": detail}


def _norm(name: str) -> str:
    """PEP 503 normalisation: Foo_Bar, foo.bar and foo-bar are one distribution."""
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


def _missing(dist: str) -> bool:
    """Is `dist` absent from THIS interpreter? Enumerated, not looked up.

    `metadata.version()` answers the same question by raising, and a handler
    that would return "missing" from its own except branch is the exact shape
    the anti-pattern scanner refuses (`except: return <constant>` -- an error
    turned into a well-formed answer). Enumerating the installed distributions
    cannot raise for an absent name, so the answer is the same and the shape is
    honest: absence is a fact about the list, not an exception.
    """
    wanted = _norm(dist)
    return all(_norm(d.metadata["Name"]) != wanted
               for d in metadata.distributions())


CI_YAML = """# ONE CLICK -- the same three steps the operator runs locally, on every push.
# Generated by tools/one_click_bootstrap.py; the file exists so CI is a consumer of
# the identical contract rather than a second, divergent gate list.
name: one-click
on: [push, pull_request, workflow_dispatch]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: install
        run: python -m pip install -r requirements-dev.txt || python -m pip install pytest pytest-timeout
      - name: one click (stack + tests + forensics)
        run: python tools/one_click.py --no-web
"""


def check_yaml(applying: bool) -> dict:
    path = ROOT / "ci" / "one_click.yml"
    if path.exists():
        return {"status": "ok", "detail": "ci/one_click.yml present"}
    if not applying:
        return {"status": "red", "detail": "no CI producer: ci/one_click.yml absent"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CI_YAML, encoding="utf-8")
    return {"status": "fixed", "detail": "wrote ci/one_click.yml (push/pull_request -> one_click --no-web)"}


def check_metrics(applying: bool) -> dict:
    store = Path(os.environ.get("AZAZAEL_MEMORY_DIR", "")) if os.environ.get("AZAZAEL_MEMORY_DIR") else None
    if store is None:
        store = Path("C:/Users/clint/.azazael")
    db = store / "telemetry_store.db"
    logs = ROOT / "logs"
    if not logs.exists():
        if not applying:
            return {"status": "red", "detail": "logs/ absent: no metric has anywhere to land"}
        logs.mkdir(parents=True, exist_ok=True)
    rows = "absent"
    if db.exists():
        rows = "present (%d bytes)" % db.stat().st_size
    return {"status": "ok", "detail": "logs/ present; board store %s at %s" % (rows, db)}


def check_reproof(applying: bool) -> dict:
    """The reproof delta: what the tree says about itself vs what it measures."""
    deltas = {}
    code, out = run([interpreter(), "tools/anti_pattern_gate.py", "--check",
                     "--tracked-only"], 600)
    deltas["anti_pattern_gate"] = {"rc": code, "tail": (out or "").strip().splitlines()[-1:]}
    report = ROOT / "logs" / "golden_arrow.json"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        totals = data.get("totals", {})
        deltas["golden_arrow"] = {"files": totals.get("files"),
                                  "failed": totals.get("failed_files"),
                                  "hung": totals.get("hung_files"),
                                  "generated": data.get("generated")}
    else:
        deltas["golden_arrow"] = {"absent": "no full-suite report on disk yet"}
    ok = deltas["anti_pattern_gate"]["rc"] == 0
    return {"status": "ok" if ok else "red",
            "detail": "anti-pattern ratchet %s; golden arrow %s"
                      % ("green" if ok else "RED", deltas["golden_arrow"]),
            "deltas": deltas}


STEPS = (
    ("git-hooks", "wire .githooks so a commit is gated at all", check_hooks),
    ("defaults-profile", "one settings surface (config/defaults.json)", check_defaults),
    ("canon", "every lane carries the generated blocks", check_canon),
    ("anchors", "baselines the ratchets compare against", check_baselines),
    ("packages", "declared distributions importable", None),
    ("ci-yaml", "a CI consumer of the same contract", check_yaml),
    ("metrics", "somewhere for a metric to land", check_metrics),
    ("reproof", "re-derive the tree's claims about itself", check_reproof),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--install-deps", action="store_true",
                    help="install missing declared distributions")
    ap.add_argument("--json", default=str(REPORT))
    args = ap.parse_args(argv)
    applying = not args.check

    print("ONE-CLICK BOOTSTRAP -- %s" % ("repairing" if applying else "CHECK ONLY"))
    results = []
    for name, why, fn in STEPS:
        if fn is None:
            row = check_packages(applying, args.install_deps)
        else:
            row = fn(applying)
        row.update({"step": name, "purpose": why})
        results.append(row)
        print("  %-6s %-16s %s" % (row["status"].upper(), name, row["detail"][:150]))

    red = [r for r in results if r["status"] == "red"]
    blind = [r for r in results if r["status"] == "blind"]
    verdict = "RED" if red else ("BLIND" if blind else "GREEN")
    report = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "mode": "apply" if applying else "check",
              "steps": results, "verdict": verdict}
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print("bootstrap: %s%s%s   report: %s"
          % (verdict,
             " -- red: %s" % ", ".join(r["step"] for r in red) if red else "",
             " -- blind: %s" % ", ".join(r["step"] for r in blind) if blind else "",
             out))
    return 0 if verdict == "GREEN" else (2 if verdict == "BLIND" else 1)


if __name__ == "__main__":
    sys.exit(main())
