"""install_pams_315t.py — put persistent_ai_memory into a 3.15t environment.

WHY THIS EXISTS
---------------
`persistent_ai_memory` (PAMS) was installed ONLY into CPython 3.14, while the
project's production interpreter is free-threaded 3.15t (`python3.15t.exe`).
`persistent_ai_memory` is in neither `requirements-3.15t.txt` nor the
`.venvs/py315t` / `build315t` venvs, so a migration to 3.15t as scoped would drop
the memory system entirely -- the same failure mode that destroyed the
3,639-memory corpus on 2026-10-09.

WHY A COPY AND NOT `pip install`
--------------------------------
The 3.14 install is an EDITABLE install whose source is the site-packages
directory itself: `__editable__.persistent_ai_memory-1.0.0.pth` imports
`__editable___persistent_ai_memory_1_0_0_finder`, whose MAPPING points at
`<site-packages>/persistent_ai_memory/`. There is no source checkout to build
from, so the authoritative artifact set is the installed one. This tool
replicates exactly that:

  * `persistent_ai_memory/` payload (code + config, NOT runtime data)
  * the top-level sibling modules PAMS imports by BARE name. Package modules do
    `from database_maintenance import ...`, which resolves through sys.path to
    the copy at the site-packages ROOT, not to the sibling inside the package.
    Omitting them makes `import mcp_server` bind a package-relative view that
    has no such neighbours.
  * the editable finder, with its two absolute MAPPING paths rewritten to the
    target (otherwise it would hand back 3.14 paths).
  * the .pth that installs the finder, and the dist-info.

Runtime data (`memory_data/`, `keys/`, `logs/`) is deliberately NOT copied:
`pams_server_launcher.py` pins the store to `~/.pams/memory_data`, and a copy of
the old store inside site-packages is precisely the fragmentation defect that
launcher exists to prevent.

Third-party dependencies are NOT installed here -- run pip first, e.g.
    .venvs/py315t/Scripts/python.exe -m pip install --only-binary=:all: \
        aiohttp mcp fastapi uvicorn watchdog pytz
This tool verifies them and says exactly which are missing.

Usage:
    python azazael/tools/install_pams_315t.py [--dry-run] [--verify-only]
"""
from __future__ import annotations

import argparse
import ast
import shutil
import sys
from pathlib import Path
import json

DEFAULT_SOURCE = Path(
    r"C:/Users/clint/AppData/Local/Programs/Python/Python314/Lib/site-packages")
DEFAULT_TARGET = Path(r"C:/Azazael/.venvs/py315t/Lib/site-packages")

PKG = "persistent_ai_memory"
DIST_INFO = "persistent_ai_memory-1.0.0.dist-info"
FINDER = "__editable___persistent_ai_memory_1_0_0_finder.py"
PTH = "__editable__.persistent_ai_memory-1.0.0.pth"

# Runtime stores owned by the launcher, never by site-packages.
DATA_DIRS = {"memory_data", "keys", "logs", "__pycache__"}

# Imported by mcp_server/ai_memory_core; verified, never installed here.
THIRD_PARTY = ["aiohttp", "mcp", "fastapi", "uvicorn", "watchdog", "pytz",
               "numpy", "requests", "psutil", "dateutil"]


def _root_copies(source: Path) -> list[str]:
    """Top-level modules PAMS owns (same basename as a package module)."""
    inside = {p.name for p in (source / PKG).iterdir() if p.is_file()}
    return sorted(f.name for f in source.glob("*.py") if f.name in inside)


def _bare_imports(path: Path) -> set[str]:
    """Absolute (non-relative) module names imported by a file."""
    found: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _vendored_neighbours(source: Path) -> list[str]:
    """Root modules PAMS imports but does NOT ship inside the package.

    These are vendored siblings (e.g. a top-level `six.py`) that the package
    relies on resolving through sys.path. Told apart from real dependencies by
    the name existing as a top-level `.py` in the source site-packages.
    """
    pkg = source / PKG
    local = {p.stem for p in pkg.glob("*.py")}
    needed: set[str] = set()
    for f in pkg.glob("*.py"):
        needed |= _bare_imports(f)
    out = []
    for name in sorted(needed - local):
        candidate = source / f"{name}.py"
        if candidate.is_file() and not (pkg / f"{name}.py").is_file():
            out.append(candidate.name)
    return out


def _rewrite_finder(source: Path, target: Path) -> str:
    """The finder with MAPPING paths repointed at `target`."""
    text = (source / FINDER).read_text(encoding="utf-8")
    return text.replace(str(source).replace("\\", "\\\\"),
                        str(target).replace("\\", "\\\\")) \
               .replace(str(source), str(target))


def install(source: Path, target: Path, dry_run: bool = False) -> int:
    if not (source / PKG).is_dir():
        print(f"FAIL source package missing: {source / PKG}")
        return 2
    payload = [p for p in (source / PKG).iterdir()
               if p.name not in DATA_DIRS and not p.name.endswith(".bak-logtooldcall")]
    roots = _root_copies(source)
    vendored = _vendored_neighbours(source)

    plan = ([f"{PKG}/{p.name}" for p in payload]
            + roots + vendored + [FINDER, PTH, DIST_INFO])
    print(f"source : {source}")
    print(f"target : {target}")
    print(f"artifacts ({len(plan)}): " + ", ".join(plan))
    if dry_run:
        return 0

    dest_pkg = target / PKG
    dest_pkg.mkdir(parents=True, exist_ok=True)
    for item in payload:
        dst = dest_pkg / item.name
        if item.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    for name in roots + vendored:
        shutil.copy2(source / name, target / name)
    if (source / DIST_INFO).is_dir():
        dst = target / DIST_INFO
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(source / DIST_INFO, dst)
    (target / FINDER).write_text(_rewrite_finder(source, target), encoding="utf-8")
    shutil.copy2(source / PTH, target / PTH)
    print(f"\nwrote {len(payload)} payload + {len(roots)} root + "
          f"{len(vendored)} vendored + finder/pth/dist-info")
    return 0


def verify(target: Path) -> int:
    """Import through the target interpreter and report the exact failure."""
    import importlib.util as util

    python = target.parent.parent / "Scripts" / "python.exe"
    print(f"interpreter: {python}")
    code = (
        "import sys, importlib, json\n"
        "out = {'version': sys.version.split()[0],\n"
        "       'freethreaded': (not sys._is_gil_enabled()) if hasattr(sys, '_is_gil_enabled') else None}\n"
        "try:\n"
        "    import ai_memory_core\n"
        "    out['ai_memory_core'] = ai_memory_core.__file__\n"
        "    out['get_base_path'] = str(ai_memory_core.get_base_path())\n"
        "    out['AIMemorySystem'] = hasattr(ai_memory_core, 'AIMemorySystem')\n"
        "    from persistent_ai_memory import mcp_server\n"
        "    out['mcp_server'] = mcp_server.__file__\n"
        "    out['run_main'] = hasattr(mcp_server, 'run_main')\n"
        "except Exception as exc:\n"
        "    out['error'] = '%s: %s' % (type(exc).__name__, exc)\n"
        "missing = []\n"
        "for m in __MODS__:\n"
        "    if importlib.util.find_spec(m) is None:\n"
        "        missing.append(m)\n"
        "out['missing_deps'] = missing\n"
        "print(json.dumps(out))\n"
    ).replace("__MODS__", repr(THIRD_PARTY))
    import subprocess
    proc = subprocess.run([str(python), "-c", code], capture_output=True, text=True,
                          timeout=600, encoding="utf-8", errors="replace")
    payload = {}
    for line in reversed(proc.stdout.splitlines()):
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    print("STDOUT:", json.dumps(payload, indent=2))
    if proc.stderr.strip():
        print("STDERR:", proc.stderr.strip()[-1500:])
    util  # referenced for the stdlib-availability note above
    ok = bool(payload) and not payload.get("error") \
        and not payload.get("missing_deps") and payload.get("run_main")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--target", default=str(DEFAULT_TARGET))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    source, target = Path(args.source), Path(args.target)
    if not args.verify_only:
        rc = install(source, target, args.dry_run)
        if rc or args.dry_run:
            return rc
    return verify(target)


if __name__ == "__main__":
    sys.exit(main())
