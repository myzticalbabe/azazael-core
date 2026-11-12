"""repoint_316t.py — move execution from 3.15t to the individualized 3.16t.

Wave 1 (this tool): stateless launchers and the session chain — the files
that CHOOSE an interpreter to execute. Everything else (census files,
skills prose, docs, MCP server configs, historical records) is INVENTORY:
it describes what exists and is reported, never rewritten.

Same contract as tools/repoint_315t.py: an explicit rule table with an
expected hit count per rule; a rule matching a different count is reported
as MISMATCH and skipped, not applied approximately. Dry run by default;
--apply writes with timestamped backups and re-parses the result.

    python azazael/tools/repoint_316t.py            # dry run, prints the plan
    python azazael/tools/repoint_316t.py --apply    # write, with backups
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Interpreter locations: derived from ROOT, never written as literals
# (staged-scanner hardcoded_win_path gate). _posix matches the forward-slash
# style of one_click/golden_arrow/session_end; _win matches watchdog's raw
# single-backslash style.
def _venv(name: str) -> Path:
    return ROOT.parent / ".venvs" / name / "Scripts" / "python.exe"

PY315T = _venv("py315t").as_posix()
PY316T = _venv("py316t").as_posix()
PY315T_WIN = str(_venv("py315t"))
PY316T_WIN = str(_venv("py316t"))
_SITE_BASE = os.environ.get("USERPROFILE", "")
VENVS = (os.path.join(_SITE_BASE, ".azazael", "venvs") if _SITE_BASE
         else "C:/.azazael/venvs")

# (relative path, old text, new text, expected occurrences, note, wave)
RULES: list[tuple[str, str, str, int, str, str]] = [
    ("tools/one_click.py",
     'PY = Path("' + PY315T + '")',
     'PY = Path("' + PY316T + '")', 1,
     "one-click front door -> 3.16t", "wave1"),
    ("tools/one_click_bootstrap.py",
     'PY = Path("' + PY315T + '")',
     'PY = Path("' + PY316T + '")', 1,
     "bootstrap -> 3.16t", "wave1"),
    ("tools/golden_arrow.py",
     'pinned = Path("' + PY315T + '")',
     'pinned = Path("' + PY316T + '")', 1,
     "test gate pinned interpreter -> 3.16t", "wave1"),
    ("tools/watchdog_5min.py",
     'PY = Path(r"' + PY315T_WIN + '")',
     'PY = Path(r"' + PY316T_WIN + '")', 1,
     "watchdog -> 3.16t", "wave1"),
    ("tools/session_end.py",
     'PY315T = Path("' + PY315T + '")',
     'PY316T = Path("' + PY316T + '")', 1,
     "session-end chain -> 3.16t (name follows the interpreter)", "wave1"),
]

# Deliberately NOT touched in wave 1 (reported, never rewritten).
DEFERRED = {
    "tools/session_start.py / supervisor_mcp.py / supervisor_doctor.py":
        "MCP-adjacent chain; moves with the MCP-server wave, not alone.",
    "tools/preflight_all.py / lane_acceptance.py / graphify_interpreter.py":
        "multi-interpreter aware; repointing one constant falsifies the ladder.",
    "skills / docs / ARCHITECTURE.md / reports / state/*.md":
        "inventory and prose describing what existed; rewriting history is a lie.",
    "config/production.yaml / config/config.yaml / src/config/*":
        "config choices; migrate with the owning lane's sign-off, not by sweep.",
    "~/.agents / ~/.config / ~/.codebuff (outside repo)":
        "client lane configs; the MCP gate checks them, this tool does not own them.",
    "MCP servers (pmb, engram, supervisor, scrapling, pams, codegraph)":
        "wave LAST: respawn order + stdio bindings must be proven on 3.16t first.",
}


def _coerce(path: Path, text: str) -> tuple[bool, str]:
    try:
        if path.suffix == ".json":
            json.loads(text)
            return True, "strict JSON re-parse OK"
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml  # noqa: PLC0415
            except ImportError:
                return True, "yaml not installed here; skipped re-parse"
            yaml.safe_load(text)
            return True, "YAML re-parse OK"
        if path.suffix == ".py":
            compile(text, str(path), "exec")
            return True, "compile OK"
    except Exception as exc:  # noqa: BLE001
        return False, f"re-parse FAILED: {type(exc).__name__}: {exc}"
    return True, "no re-parse rule for this suffix"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report: dict = {"at": stamp, "apply": args.apply, "rules": [],
                    "deferred": DEFERRED}

    edited: dict[Path, list] = {}
    for rel, old, new, expected, note, wave in RULES:
        path = ROOT / rel
        entry = {"file": rel, "note": note, "wave": wave,
                 "expected": expected, "verdict": ""}
        if not path.is_file():
            entry["verdict"] = "MISSING_FILE"
            report["rules"].append(entry)
            continue
        text = edited.get(path, [None])[0] or path.read_text(
            encoding="utf-8", errors="replace")
        found = text.count(old)
        if found != expected:
            entry["verdict"] = f"MISMATCH (found {found}, expected {expected})"
            report["rules"].append(entry)
            print(f"  MISMATCH  {rel}: {note} — found {found}, "
                  f"expected {expected}")
            continue
        if old == new:
            entry["verdict"] = "ALREADY_DONE"
            report["rules"].append(entry)
            print(f"  ok        {rel}: {note} — already 3.16t")
            continue
        new_text = text.replace(old, new)
        ok, detail = _coerce(path, new_text)
        entry["reparse"] = detail
        if not ok:
            entry["verdict"] = "REPARSE_FAILED"
            report["rules"].append(entry)
            print(f"  REPARSE   {rel}: {detail}")
            continue
        edited[path] = [new_text, rel]
        entry["verdict"] = "WOULD_APPLY" if not args.apply else "APPLIED"
        report["rules"].append(entry)
        print(f"  {'APPLY' if args.apply else 'plan '}     {rel}: {note}")

    if args.apply:
        for path, (new_text, rel) in edited.items():
            backup = path.with_name(path.name + f".bak-{stamp}")
            shutil.copy2(path, backup)
            path.write_text(new_text, encoding="utf-8", newline="")
            print(f"  wrote {rel}  (backup {backup.name})")

    out = ROOT / "state" / "repoint_316t.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    applied = [r for r in report["rules"]
               if r["verdict"] in ("APPLIED", "WOULD_APPLY")]
    print(f"\n{'applied' if args.apply else 'planned'}: {len(applied)} edits;  "
          f"mismatches: {sum(1 for r in report['rules'] if r['verdict'].startswith('MISMATCH'))}")
    print("deferred (not wave 1):")
    for k in DEFERRED:
        print(f"  - {k}")
    print("report:", out)
    if not args.apply:
        print("\n(dry run — re-run with --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
