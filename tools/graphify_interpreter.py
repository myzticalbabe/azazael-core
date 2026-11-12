# coding: utf-8
"""Which interpreter runs graphify -- resolved by capability, not by path.

Extracted 2026-09-22 from `scripts/graph_refresh_no_llm.py`, which hard-coded
the answer to the pip install in the system Python 3.14 and defended it in a
comment. The premise in that comment was correct -- the 3.14t venv has no
graphify, so `sys.executable -m graphify` reaches nothing -- and the target was
the one install on the box that cannot work. Measured:

    ~/.local/bin/graphify.exe          0.9.50  exporters/html.py present, PATCHED
    Python314 site-packages (pip)      0.8.8   no exporters package at all
    ~/.claude/skills/graphify (skill)  0.9.55

`scripts/graphify_node_limit_patch.py` rewrites `exporters/html.py` and
`cli.py`; `scripts/graphify_huge_mode.py` -- the renderer for a 688k-node graph
-- is read OUT of `exporters/html.py`. An install without that file can only
render the small-graph page, so it produces the wrong artifact rather than an
error. Hence the rule below: accept only an install that has the file, prefer a
patched one over a newer unpatched one, and refuse rather than fall back.

Two call sites share this module: the no-LLM refresh (`scripts/graph_refresh_no_llm.py`)
and the semantic rebuild (`tools/graph_rebuild.py`). The second was also broken
-- it used bare `sys.executable`, i.e. a venv with no graphify at all -- and
neither should be free to drift from the other again.

Error #208, Lesson #101, ADR-019.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Directories a capable install must have. `exporters/html.py` is the marker:
#: it is what `scripts/graphify_node_limit_patch.py` patches, and the huge-mode
#: renderer lives in it.
REQUIRED_INSTALL_PARTS = ("exporters", "exporters/html.py")

#: Marker proving the huge-mode renderer patch is applied to an install.
HUGEMODE_MARKER = "HUGE_MODE_NODES"

#: The skill the agent reads declares the engine version it expects. A skew
#: between the two is reported, never silently tolerated: two installs three
#: releases apart is how this was found.
SKILL_VERSION_FILE = Path.home() / ".claude" / "skills" / "graphify" / ".graphify_version"

#: Kept as the last-resort candidate so the historical path still resolves if
#: it is the only capable install on the box.
LEGACY_GRAPHIFY_PY = Path(
    r"C:\Users\clint\AppData\Local\Programs\Python\Python314\python.exe")


def graphify_python_candidates() -> list[Path]:
    """Interpreters that might carry graphify, explicit override first."""
    home = Path.home()
    out: list[Path] = []
    env = os.environ.get("GRAPHIFY_PY")
    if env:
        out.append(Path(env))
    # production first: the 3.15t runner, then its same-version GIL sidecar
    out += [
        Path("C:/Azazael/.venvs/py315t/Scripts/python.exe"),
        Path("C:/Azazael/.venvs/py315/Scripts/python.exe"),
    ]
    # uv tool installs, Windows and POSIX layouts
    out += [
        home / "AppData/Roaming/uv/tools/graphifyy/Scripts/python.exe",
        home / ".local/share/uv/tools/graphifyy/Scripts/python.exe",
        home / ".local/share/uv/tools/graphifyy/bin/python",
    ]
    out.append(Path(sys.executable))
    # 3.14 is RETIRED (2026-10-19): kept last so a capable historical install
    # still resolves rather than being lost, never as a production choice.
    out.append(LEGACY_GRAPHIFY_PY)
    return out


def _graphify_install_dir(py: Path) -> Path | None:
    """Ask the interpreter where its graphify package lives. None if absent."""
    probe = ("import importlib.util as u, pathlib, sys; "
             "s = u.find_spec('graphify'); "
             "print(pathlib.Path(s.submodule_search_locations[0]) if s and "
             "s.submodule_search_locations else '')")
    try:
        proc = subprocess.run([str(py), "-c", probe], capture_output=True,
                              text=True, timeout=60,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (proc.stdout or "").strip().splitlines()
    return Path(line[-1]) if line and line[-1] else None


def _graphify_version(py: Path) -> str:
    """The package version as the interpreter reports it, or '?'."""
    try:
        proc = subprocess.run(
            [str(py), "-c",
             "import importlib.metadata as m; print(m.version('graphifyy'))"],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    return (proc.stdout or "?").strip() or "?"


def resolve_graphify_python(report=print) -> Path | None:
    """The interpreter whose graphify install can render this tree.

    Preference: a PATCHED capable install beats an unpatched one, because only
    the patched one has the huge-mode renderer, and every other difference here
    is cosmetic. Returns None when nothing qualifies -- refusing is the point;
    the caller must not fall back to an install that would write the wrong
    artifact.
    """
    seen: set[str] = set()
    capable: list[tuple[bool, Path, str, Path]] = []
    for py in graphify_python_candidates():
        key = str(py).lower()
        if key in seen or not py.exists():
            continue
        seen.add(key)
        install = _graphify_install_dir(py)
        if install is None:
            continue
        missing = [p for p in REQUIRED_INSTALL_PARTS
                   if not (install / p).exists()]
        ver = _graphify_version(py)
        if missing:
            report("  skip %s (graphify %s): no %s -- cannot carry the "
                   "huge-mode patch" % (py, ver, ", ".join(missing)))
            continue
        patched = HUGEMODE_MARKER in (install / "exporters" / "html.py").read_text(
            encoding="utf-8", errors="replace")
        capable.append((patched, py, ver, install))
    if not capable:
        return None
    capable.sort(key=lambda row: (row[0], row[2]), reverse=True)
    patched, py, ver, install = capable[0]
    report("  graphify %s (%s) at %s"
           % (ver, "patched" if patched else "UNPATCHED", install))
    if not patched:
        report("  WARNING: no candidate has the huge-mode patch (%s). Run "
               "scripts/graphify_node_limit_patch.py before trusting "
               "graph.html for this tree." % HUGEMODE_MARKER)
    try:
        skill = SKILL_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        skill = ""
    if skill and ver != "?" and skill != ver:
        report("  WARNING: version skew -- skill %s vs package %s. Align them "
               "(pin the package to the skill: uv tool install "
               "graphifyy==%s --force, then re-run "
               "scripts/graphify_node_limit_patch.py and re-verify the "
               "markers)." % (skill, ver, skill))
    return py


def graphify_argv(step: tuple[str, ...], report=print) -> list[str] | None:
    """`[interpreter, -m graphify, *step]`, or None when nothing qualifies.

    The convenience both call sites want: resolve, then build the command. A
    caller that gets None must refuse to run rather than substituting an
    interpreter of its own choosing -- that substitution is Error #208.
    """
    py = resolve_graphify_python(report=report)
    if py is None:
        return None
    return [str(py), "-m", "graphify", *step]
