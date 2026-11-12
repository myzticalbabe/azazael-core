#!/usr/bin/env python3
# coding: utf-8
"""Make the graphify version skew enforceable instead of advisory.

The situation
-------------
`graphify_interpreter.resolve_graphify_python` prints this on every graph
refresh:

    WARNING: version skew -- skill 0.9.55 vs package 0.9.50. Align them
    (pin the package to the skill: uv tool install graphifyy==0.9.55
    --force, then re-run scripts/graphify_node_limit_patch.py ...)

It has printed it on every pass and nobody has acted, because it is only a
warning. A warning that repeats forever and changes nothing is decoration.
The point of this tool is to give the check a **return code**, so a caller
that cares can ask.

Why the skew is not fixed here
------------------------------
The documented remedy is `uv tool install graphifyy==0.9.55 --force`. That
is deliberately not run, for two reasons:

1. It installs into `AppData/Roaming/uv/tools/`, outside this project. A
   tool must not modify a user's global environment unprompted.
2. The currently-installed 0.9.50 carries the **huge-mode patch** --
   `REQUIRED_INSTALL_PARTS` and the `exporters/html.py` marker both check
   out, and `resolve_graphify_python` says `patched`. `--force` replaces
   the install, and the skill file says the patch script must then be
   re-run and the markers re-verified. Doing that unattended risks
   trading a version-skew warning for an unpatched renderer on a tree whose
   graph.html needs the huge-mode path to render at all.

So the skew is reported, priced, and left for a decision. The remedies are
printed, not taken.

What "aligned" means
--------------------
The skill's `.graphify_version` and the installed package's dist metadata
must agree AND the install must carry the patch markers. A version match
alone is not alignment: an unpatched 0.9.55 would satisfy a naive equality
check and quietly produce a graph.html that cannot render this tree.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

REMEDY = ("uv tool install graphifyy=={skill} --force, then run "
          "scripts/graphify_node_limit_patch.py and re-verify the markers")


def skill_version() -> str | None:
    try:
        import graphify_interpreter as g
        return g.SKILL_VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def package_version(py: Path) -> str:
    r = subprocess.run(
        [str(py), "-c",
         "import importlib.metadata as m;print(m.version('graphifyy'))"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60)
    return (r.stdout or "").strip() or "?"


def patch_markers(py: Path) -> dict[str, Any]:
    """Is the resolved install patched? Version equality is not enough."""
    try:
        import graphify_interpreter as g
        install = g._graphify_install_dir(py)
        if install is None:
            return {"install": None, "patched": False, "reason": "no install dir"}
        missing = [p for p in g.REQUIRED_INSTALL_PARTS
                   if not (install / p).exists()]
        html = (install / "exporters" / "html.py")
        patched = (not missing and html.exists()
                   and g.HUGEMODE_MARKER in html.read_text(
                       encoding="utf-8", errors="replace"))
        return {"install": str(install), "patched": patched,
                "missing_parts": missing}
    except Exception as exc:
        return {"install": None, "patched": False, "reason": str(exc)}


def analyse() -> dict[str, Any]:
    skill = skill_version()
    resolved = None
    try:
        import graphify_interpreter as g
        resolved = g.resolve_graphify_python(report=lambda *_: None)
    except Exception:
        resolved = None
    out: dict[str, Any] = {
        "skill_version": skill,
        "resolved": str(resolved) if resolved else None,
        "package_version": None,
        "markers": None,
        "aligned": False,
        "remedy": None,
    }
    if not resolved:
        out["reason"] = "no capable graphify install resolved"
        return out
    pkg = package_version(resolved)
    mk = patch_markers(resolved)
    out["package_version"] = pkg
    out["markers"] = mk
    out["aligned"] = bool(skill and pkg and skill == pkg and mk.get("patched"))
    if not out["aligned"] and skill:
        out["remedy"] = REMEDY.format(skill=skill)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check graphify skill/package alignment as an exit code.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on skew (default: report only, exit 0)")
    args = ap.parse_args(argv)

    rep = analyse()

    if args.json:
        print(json.dumps(rep, indent=2))
        return 1 if (args.strict and not rep["aligned"]) else 0

    print("skill version:   %s" % (rep["skill_version"] or "?"))
    print("package version: %s" % (rep["package_version"] or "?"))
    print("resolved:        %s" % (rep["resolved"] or "none"))
    mk = rep.get("markers") or {}
    print("patched:         %s" % mk.get("patched"))
    if mk.get("missing_parts"):
        print("missing parts:   %s" % ", ".join(mk["missing_parts"]))
    if rep.get("reason"):
        print("reason:          %s" % rep["reason"])

    if rep["aligned"]:
        print("\nOK: skill and package agree and the install is patched.")
        return 0

    print("\nSKEW. This is reported, not fixed: the remedy installs a package "
          "outside\nthis project and would replace a PATCHED install, so it is "
          "a decision, not a step.")
    if rep.get("remedy"):
        print("   %s" % rep["remedy"])
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
