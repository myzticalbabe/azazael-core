#!/usr/bin/env python3
"""audit_stale_refs.py — track down every remaining retired-pathway reference.

Operations keep getting misdirected by stale interpreter / hub paths that
survive each migration. This is the census instrument for that class of
fault, built on the repoint_315t.py doctrine:

  CHOICE     — a launcher deciding which interpreter/tree to EXECUTE.
               A retired name here misdirects operations. Severity ERROR
               (or WARN when the file checks existence at runtime).
               These must be fixed.
  INVENTORY  — a description of what exists on a machine (census files,
               docstrings, install records, frozen archives). Rewriting
               history falsifies the record. Reported as INFO, never fail.

Usage:
  python scripts/audit_stale_refs.py [--root DIR] [--json] [--fail-on warn]

Exit codes: 0 = no ERROR hits (WARN/INFO only); 1 = at least one ERROR hit;
2 = usage/config failure. JSON report goes to state/stale_refs.json.

Second family (2026-10-21): the NATURES taxonomy removal (U3/G3, D1 —
NATURE_CLUSTERS.py + CROSS_NATURE_ROUTER.py deleted Oct 02, NATURE_DEFINITIONS
== {} asserted by packages/azazael-core/run_tests.py). The fold-back
signatures are a live NATURE_CLUSTERS.py source file, a repopulated
(non-empty) NATURE_DEFINITIONS literal, or any import of the deleted
CROSS_NATURE_ROUTER module. Importing the (empty) NATURE_DEFINITIONS symbol
itself is a legitimate shim and is NOT flagged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "stale_refs.json"

TEXT_SUFFIXES = {
    ".py", ".sh", ".bat", ".ps1", ".yaml", ".yml", ".toml", ".json",
    ".cfg", ".ini", ".txt", ".md", "",
}
TEXT_NAMES = {"Dockerfile", "Dockerfile.ubuntu", "Makefile"}

SKIP_DIRS = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "archive", "archived", "archived_unified",
    "wheels", "dist", "build", "graphify-out", "graphs", "output",
    "nuitka_build_backup", "state", ".nox", ".debug",
    # Generated / frozen outputs — never executed, so they cannot misdirect
    # operations; they are INVENTORY of a past state by construction.
    ".graphify-live", "var", "logs", "temp", ".hypothesis",
}

#: Files whose JOB is naming the retired thing (detectors, migrators,
#: uninstallers). An ERROR here would flag the cure as the disease; their
#: matches demote to INFO with the reason recorded.
DETECTOR_FILES = {
    "src/CORE/WORMS/CLUSTER_INSPECTOR.py",
    "src/UTILITIES/TARBABY/uninstall_py314.sh",
    "tools/repoint_315t.py",
}

#: Files whose JOB spanned both trees (sync/parity/atlas/inventory).
#: C:/Users/clint/azazael WAS a live second tree; archived 2026-10-21 to
#: G:/azazael.archived-20261021. old-hub-fwd matches here are now
#: expected-absent; the rule stays to catch future strays.
CROSS_TREE_FILES = {
    "scripts/sync_all.py",
    "scripts/check_tree_parity.py",
    "scripts/graphify_refresh_all.sh",
    "src/CORE/azazael_ctl.py",
}

#: Constants kept so a census can name the retired thing
#: (e.g. RETIRED_314T, LEGACY_GRAPHIFY_PY). Naming, not choosing.
CENSUS_CONSTANT_RX = re.compile(r"^\s*(RETIRED|LEGACY|DEPRECATED)_[A-Z_0-9]+\s*=")
SKIP_SUFFIXES = (".bak", ".pyc", ".whl", ".exe", ".mp4", ".png")
SKIP_NAMES = {"repoint_315t.json", "stale_refs.json", "AUTO_DOWNLOADER.log",
              "ERROR_LOG.md", "audit_stale_refs.py"}

#: In prose docs (.md/.txt) a retired name is usually history, not an
#: instruction — except when the line itself reads like a command. Only
#: command-like doc lines keep ERROR; the rest drop to INFO/INVENTORY so
#: agents are not sent to rewrite the historical record.
# `python` only counts when it is INVOKED (name + arguments). A bare prose
# mention ("LIVE PROOF (cycle 116, ..., python3.14t):") is evidence, not an
# instruction. `.exe` still fires on its own: a `python3.14t.exe` token is
# executable-shaped wherever it appears, so evidence quotes of it stay loud
# until a human downgrades the line.
DOC_COMMAND_RX = re.compile(
    r"RUN\b|COPY\b|python[0-9.]*(?:\.exe)?\s+\S|pip\s|uv\s|pytest|nox|\.exe\b|\.bat\b|\.sh\b"
    r"|/usr/|/opt/|C:[/\\]|/root/|/mnt/|``\x60|\$\s|^\s*\$")
DOC_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: str
    canonical: str
    severity: str  # ERROR | WARN | INFO
    kind: str      # CHOICE | INVENTORY
    note: str


RULES: list[Rule] = [
    Rule("retired-venv-314t", r"venv-314t\b", "(removed; use .venvs/py315t or py314m)",
         "ERROR", "CHOICE", "retired 2026-10-19; executing it is misdirection"),
    Rule("retired-bin-314t", r"python3\.14t(\.exe)?", "python3.15t.exe / .venvs/py315t",
         "ERROR", "CHOICE", "retired free-threaded 3.14 binary"),
    Rule("retired-prefix-314t", r"/usr/local/python314t\b", "/opt/python315t or /root/py315t",
         "ERROR", "CHOICE", "retired WSL 3.14t prefix"),
    Rule("dead-prefix-315t", r"/usr/local/python315t\b", "/opt/python315t",
         "ERROR", "CHOICE", "prefix that never existed on WSL"),
    # NOTE: /usr/local/bin/python3.15t (the ALIAS) is live and legitimate;
    # only the never-built <prefix>/bin/python3.15t path is dead.
    Rule("dead-bin-315t", r"python315t/bin/python3\.15t\b", "bin/python3.15 (+ /usr/local/bin/python3.15t alias)",
         "ERROR", "CHOICE", "binary name the CPython build never produces"),
    Rule("old-hub-fwd", r"C:/Users/clint/azazael\b", "C:/Azazael/azazael",
         "ERROR", "CHOICE", "hub moved 2026-10 (U3/move-day)"),
    Rule("old-wsl-root", r"/home/clint/projects/azazael\b", "/root/azazael",
         "ERROR", "CHOICE", "WSL root was never under /home/clint"),
    Rule("stale-tree-e", r"azazael-project-e", "(removed stale duplicate tree)",
         "ERROR", "CHOICE", "quarantined duplicate; COPY/refs break builds"),
    Rule("never-path-kali", r"E:/Kali/azazael\b", "Kali tree lives inside the distro (/root/azazael)",
         "ERROR", "CHOICE", "E:\\Kali holds only ext4.vhdx; path never existed"),
    Rule("bare-requirements", r"COPY\s+requirements\.txt\b", "requirements-3.15t.txt",
         "ERROR", "CHOICE", "no bare requirements.txt in this repo"),
    Rule("old-desktop-tarbaby", r"C:/Users/clint/Desktop/TARBABY",
         "verify on Windows; desktop moved under C:/Azazael/desktop",
         "WARN", "CHOICE", "runtime-checked paths; confirm the old dir is gone"),
    Rule("appdata-314", r"Python314", "(base installs may legitimately exist)",
         "INFO", "INVENTORY", "naming the install is census, not a runtime choice"),
    Rule("prebuilt-314-site", r"/usr/local/lib/python3\.14/site-packages",
         "(orphaned 11GB prebuilt stack; GIL-sidecar only)",
         "INFO", "INVENTORY", "historical recovery record, not an install path"),
    Rule("nature-map-repopulated", r"NATURE_DEFINITIONS\s*=\s*\{(?!\s*\})",
         "NATURE_DEFINITIONS = {} (D1 asserted empty)",
         "ERROR", "CHOICE", "repopulated taxonomy literal contradicts D1 removal"),
    Rule("cross-nature-router", r"CROSS_NATURE_ROUTER",
         "(module deleted Oct 02 U3/G3; no live importer)",
         "ERROR", "CHOICE", "deleted router module; any import/exec ref misdirects"),
    Rule("nature-integration-tooling", r"nature_integration_map",
         "verify against D1 flat dispatch before trusting output",
         "WARN", "CHOICE", "tooling that assumes the removed taxonomy"),
    Rule("nature-count-stale", r"(?i)\b(26|25|28)\s+natures?\b",
         "bytecode-authoritative count is 16 (2026-10-09 forensic)",
         "WARN", "CHOICE", "pre-U3 nature counts; 26/25/28 contradict the pyc record"),
]


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            continue
        try:
            yield path, path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeError, OSError):
            continue


#: Deleted taxonomy modules whose SOURCE must never reappear. The .pyc
#: ghosts in __pycache__ are forensic evidence (skipped by SKIP_SUFFIXES),
#: not live code — only a .py source file is a fold-back.
DELETED_TAXONOMY_SOURCES = {"NATURE_CLUSTERS.py", "CROSS_NATURE_ROUTER.py",
                            "test_nature_integration_map.py"}


def audit_text(rel: str, suffix: str, text: str) -> list[dict]:
    """Apply all rules to one file's text. Split out so single files can
    be verified without rescanning a whole tree."""
    compiled = [(r, re.compile(r.pattern)) for r in RULES]
    hits: list[dict] = []
    is_doc = suffix in DOC_SUFFIXES
    lines = text.split("\n")
    for rule, rx in compiled:
        for m in rx.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            severity, kind = rule.severity, rule.kind
            line_text = lines[lineno - 1] if lineno - 1 < len(lines) else ""
            if is_doc and severity == "ERROR":
                if not DOC_COMMAND_RX.search(line_text):
                    severity, kind = "INFO", "INVENTORY"
            # A `#` comment never executes: it records, it does not direct.
            # Same CHOICE-vs-INVENTORY doctrine as prose docs.
            if suffix == ".py" and severity == "ERROR":
                if line_text.strip().startswith("#"):
                    severity, kind = "INFO", "INVENTORY"
            # A `>` blockquote cites a record (e.g. RETIRED tombstones that
            # must name the retired path to say it is retired). Citations are
            # not instructions.
            if severity == "ERROR":
                if line_text.strip().startswith(">"):
                    severity, kind = "INFO", "INVENTORY"
            # JSON is data, not execution. A stale name in data is worth a
            # human verifying the consumer (WARN), not a failing ERROR.
            if suffix == ".json" and severity == "ERROR":
                severity = "WARN"
            # Detector/migrator files name the retired thing as their job.
            if rel in DETECTOR_FILES and severity == "ERROR":
                severity, kind = "INFO", "INVENTORY"
            # Cross-tree tools name the live second tree on purpose.
            if rel in CROSS_TREE_FILES and rule.id == "old-hub-fwd" \
                    and severity == "ERROR":
                severity, kind = "INFO", "INVENTORY"
            # Census constants (RETIRED_*/LEGACY_*) name; they do not choose.
            if severity == "ERROR" and CENSUS_CONSTANT_RX.search(line_text):
                severity, kind = "INFO", "INVENTORY"
            hits.append({"rule": rule.id, "severity": severity,
                         "kind": kind, "file": rel, "line": lineno,
                         "match": m.group(0)[:80], "canonical": rule.canonical,
                         "note": rule.note})
    return hits


def audit(root: Path) -> dict:
    hits: list[dict] = []
    files_scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name not in DELETED_TAXONOMY_SOURCES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        hits.append({"rule": "nature-module-live", "severity": "ERROR",
                     "kind": "CHOICE", "file": rel, "line": 1,
                     "match": path.name,
                     "canonical": "(deleted Oct 02 U3/G3; reappearance is fold-back)",
                     "note": "removed taxonomy module is live source again"})
    for path, text in iter_files(root):
        files_scanned += 1
        rel = path.relative_to(root).as_posix()
        hits.extend(audit_text(rel, path.suffix, text))
    by_rule: dict[str, int] = {}
    for h in hits:
        by_rule[h["rule"]] = by_rule.get(h["rule"], 0) + 1
    errors = sum(1 for h in hits if h["severity"] == "ERROR")
    warns = sum(1 for h in hits if h["severity"] == "WARN")
    return {"root": str(root), "files_scanned": files_scanned,
            "errors": errors, "warnings": warns, "by_rule": by_rule, "hits": hits}


def main() -> int:
    ap = argparse.ArgumentParser(description="Track retired-pathway references.")
    ap.add_argument("--root", action="append", default=[],
                    help="tree to scan (default: this repo; repeatable)")
    ap.add_argument("--json", action="store_true", help="print the JSON report")
    ap.add_argument("--tag", default="",
                    help="land this run as state/stale_refs[_TAG].json (one per environment)")
    ap.add_argument("--fail-on", default="error", choices=["error", "warn"],
                    help="exit 1 on error hits (default) or any warn+ hit")
    ap.add_argument("--error-only", action="store_true",
                    help="list ERROR hits; still count everything in JSON")
    args = ap.parse_args()

    roots = [Path(r).resolve() for r in (args.root or [ROOT])]
    for r in roots:
        if not r.is_dir():
            print(f"not a directory: {r}", file=sys.stderr)
            return 2

    reports = [audit(r) for r in roots]
    out = STATE.with_name(f"stale_refs_{args.tag}.json" if args.tag else STATE.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, indent=2), encoding="utf-8")

    total_err = sum(r["errors"] for r in reports)
    total_warn = sum(r["warnings"] for r in reports)
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for rep in reports:
            print(f"== {rep['root']} ({rep['files_scanned']} files) "
                  f"errors={rep['errors']} warnings={rep['warnings']}")
            for h in rep["hits"]:
                if args.error_only and h["severity"] != "ERROR":
                    continue
                print(f"  [{h['severity']}] {h['rule']} {h['file']}:{h['line']}: "
                      f"{h['match']!r} -> {h['canonical']}")
    print(f"report: {out}")
    if args.fail_on == "warn":
        return 1 if (total_err + total_warn) else 0
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())
