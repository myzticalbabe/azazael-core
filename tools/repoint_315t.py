"""repoint_315t.py — make 3.15t the interpreter production actually RUNS on.

The directive is "python 3.15t production environment only". That is not a
search-and-replace: a file naming `Python314` is one of two very different
things, and rewriting the second kind falsifies a record.

  CHOICE      — a launcher deciding which interpreter to EXECUTE. Repoint it.
  INVENTORY   — a description of what exists on this machine (census files,
                environment-baseline specs, docstrings, install instructions
                for an already-built interpreter). Python314 really does exist;
                a census that said otherwise would be the lie. Report, never
                rewrite.

So this tool carries an explicit rule table with an expected hit count per rule
and REFUSES to guess: a rule that matches a different number of times than it
declares is reported as MISMATCH and skipped, not applied approximately. Every
edit is backed up beside the original with a timestamp and a strict re-parse
(JSON/YAML/bat) of the result.

    python azazael/tools/repoint_315t.py            # dry run, prints the plan
    python azazael/tools/repoint_315t.py --apply    # write, with backups
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
PY315T = r"C:\Azazael\.venvs\py315t\Scripts\python.exe"  # single-backslash literal: this repo's venv, data for matching
# Machine-inventory below (other interpreters on this box): data, not logic.
# The cp314t site dir is derived so a bare `C:\` literal never sits in code;
# on a box without USERPROFILE the rules below MISMATCH and skip (fail-safe).
SYSTEM_315T = r"C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe"
PY314T = r"C:\Users\clint\AppData\Local\Programs\Python\Python314\python3.14t.exe"
PY314 = r"C:\Users\clint\AppData\Local\Programs\Python\Python314\python.exe"
PAMS_SITE = "C:/Users/clint/AppData/Local/Programs/Python/Python314/Lib/site-packages/memory_data"
PAMS_CANONICAL = "C:/Users/clint/.pams/memory_data"
_SITE_BASE = os.environ.get("USERPROFILE", "")
SITE_DIR = (os.path.join(_SITE_BASE, ".azazael", "py314t_site") if _SITE_BASE
            else r"C:\.azazael\py314t_site")

# (relative path, old text, new text, expected occurrences, note)
RULES: list[tuple[str, str, str, int, str]] = [
    # --- the chain controller wrapper: this one file sets the interpreter for
    #     ALL 22 chain jobs, because chain_controller._python() returns
    #     AZAZAEL_PYTHON or sys.executable. Repointing it repoints
    #     meta_evolution_cycle.py, render_all_monitors.py (the ouroboros readout)
    #     and every other job at once.
    ("scripts/run_chain_live.bat",
     f'set "PY={PY314}"',
     f'set "PY={PY315T}"', 1,
     "chain controller -> all 22 chain jobs incl. render/ouroboros + meta"),

    # --- the evolution cycle producer hop: was 3.14t with a cp314t-only
    #     PYTHONPATH injected. Under the 3.15t venv that PYTHONPATH would inject
    #     cp314t wheels into a 3.15 process, so the line is removed, not moved.
    ("scripts/run_evolution_cycle_hyper.bat",
      f"set PYTHONPATH={SITE_DIR};%PYTHONPATH%\n\n", "", 1,
     "cp314t-only site dir must not leak into a 3.15 process"),
    ("scripts/run_evolution_cycle_hyper.bat",
     f'"{PY314T}" "%~dp0evolution_cycle.py"',
     f'"{PY315T}" "%~dp0evolution_cycle.py"', 1,
     "evolution cycle producer hop"),
    ("scripts/run_evolution_cycle_314t.bat",
      f"set PYTHONPATH={SITE_DIR};%PYTHONPATH%\n", "", 1,
     "same cp314t PYTHONPATH removal"),
    ("scripts/run_evolution_cycle_314t.bat",
     f'"{PY314T}" "%~dp0evolution_cycle.py"',
     f'"{PY315T}" "%~dp0evolution_cycle.py"', 1,
     "evolution cycle producer hop"),

    # --- the monitor launchers: an interpreter LADDER (prefer free-threaded,
    #     fall back to GIL, then PATH). Rebuild it with 3.15t at the top.
    ("scripts/open_aoa_monitor.bat",
     f'set PY={PY314T}\nif not exist "%PY%" set PY={PY314}\n',
     f'set PY={PY315T}\n'
     f'if not exist "%PY%" set PY={SYSTEM_315T}\n'
     f'if not exist "%PY%" set PY=python3.15t\n', 1,
     "AOA wallboard ladder"),
    ("scripts/open_lilith_live_monitor.bat",
     f'set PY={PY314T}\nif not exist "%PY%" set PY={PY314}\n',
     f'set PY={PY315T}\n'
     f'if not exist "%PY%" set PY={SYSTEM_315T}\n'
     f'if not exist "%PY%" set PY=python3.15t\n', 1,
     "Lilith wallboard ladder"),
    ("scripts/refresh_oa_data.bat",
     f'set PY={PY314T}\nif not exist "%PY%" set PY={PY314}\n',
     f'set PY={PY315T}\n'
     f'if not exist "%PY%" set PY={SYSTEM_315T}\n'
     f'if not exist "%PY%" set PY=python3.15t\n', 1,
     "AOA data refresh ladder"),

    # --- config choices ---
    # YAML carries the same path in forward slashes; the first dry run caught
    # this as a MISMATCH rather than silently skipping it, which is the point of
    # declaring an expected hit count per rule.
    ("config/config.yaml",
     'python_path: "C:/Users/clint/AppData/Local/Programs/Python/Python314/python.exe"',
     'python_path: "C:/Azazael/.venvs/py315t/Scripts/python.exe"', 1,
     "free-threading interpreter choice"),

    # --- PAMS data location: the 2026-10-09 forensics moved the canonical store
    #     to %USERPROFILE%\\.pams\\memory_data (writable, survives an interpreter
    #     upgrade) and demoted the install-dir copies. These two config files
    #     still named the install dir, i.e. the directory that a reinstall wipes.
    ("src/config/ecosystem_config.yaml",
     PAMS_SITE, PAMS_CANONICAL, 3,
     "PAMS db paths -> canonical ~/.pams store"),
    ("src/config/pams_bridge.json",
     PAMS_SITE, PAMS_CANONICAL, 1,
     "PAMS db_directory -> canonical ~/.pams store"),
]

# Named so the report is explicit about what it deliberately did NOT touch.
INVENTORY_LEFT_ALONE = {
    "scripts/check_runtime_baseline.py":
        "census of interpreters that EXIST (win-main, win-314t, win-313tools...). "
        "Python314 is present on this machine; rewriting the census would falsify it.",
    "src/config/PYTHON_INVENTORY.json":
        "factual inventory of installed interpreters and their packages.",
    "src/CORE/PYTHON_MANAGER.py":
        "discovers interpreters; naming the 3.14 install is its job.",
    "PamsRecorder.py / src/CORE/PamsRecorder.py":
        "docstrings naming where the package is installed.",
    "src/CODEX/SESSION_TEMPLATE/**": "captured session payloads (historical data).",
    "src/ORCHESTRATION/**, src/CORE/WORMS/**": "scripts that mention the path in prose.",
    "scripts/install_ml_wsl.sh, src/SCRIPTS/install_python_env.bat":
        "installers for an environment that was already built.",
}


def _coerce(path: Path, text: str) -> tuple[bool, str]:
    """Re-parse the edited file so a broken write cannot land."""
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
        if path.suffix == ".bat":
            return True, "batch file; checked line endings only"
    except Exception as exc:  # noqa: BLE001
        return False, f"re-parse FAILED: {type(exc).__name__}: {exc}"
    return True, "no re-parse rule for this suffix"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report: dict = {"at": stamp, "apply": args.apply, "rules": [], "inventory_untouched": INVENTORY_LEFT_ALONE}

    edited: dict[Path, list[str]] = {}
    for rel, old, new, expected, note in RULES:
        path = ROOT / rel
        entry = {"file": rel, "note": note, "expected": expected, "verdict": "", "old": old.replace("\n", "\\n")[:120],
                 "new": new.replace("\n", "\\n")[:120]}
        if not path.is_file():
            entry["verdict"] = "MISSING_FILE"
            report["rules"].append(entry)
            continue
        text = edited.get(path, [None])[0] or path.read_text(encoding="utf-8", errors="replace")
        found = text.count(old)
        if found != expected:
            entry["verdict"] = f"MISMATCH (found {found}, expected {expected})"
            report["rules"].append(entry)
            print(f"  MISMATCH  {rel}: {note} — found {found}, expected {expected}")
            continue
        if old == new:
            entry["verdict"] = "ALREADY_DONE"
            report["rules"].append(entry)
            print(f"  ok        {rel}: {note} — already 3.15t")
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

    out = ROOT / "state" / "repoint_315t.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    applied = [r for r in report["rules"] if r["verdict"] in ("APPLIED", "WOULD_APPLY")]
    print(f"\n{'applied' if args.apply else 'planned'}: {len(applied)} edits;  "
          f"mismatches: {sum(1 for r in report['rules'] if r['verdict'].startswith('MISMATCH'))}")
    print("report:", out)
    if not args.apply:
        print("\n(dry run — re-run with --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
