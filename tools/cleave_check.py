# -*- coding: utf-8 -*-
"""cleave_check.py — the ONE command a delegated cleave unit is verified with.

Why this exists: a max-reasoning lane should not spend reasoning on arithmetic
it already did. The expected numbers are snapshotted once (--snapshot) and every
unit then runs --unit <id>, which either passes or hands back the failing
command's output. No expectations are computed at check time, and a missing
baseline is a refusal, not a default — that is how a check becomes a rubber
stamp.

  python tools/cleave_check.py --snapshot            # record the baseline once
  python tools/cleave_check.py --unit U2             # verify one unit
  python tools/cleave_check.py --envelopes wave1     # ready-to-send work orders
  python tools/cleave_check.py --list                # units + measured sizes

Exit: 0 = every check passed, 1 = a check failed (output printed), 2 = cannot
judge (baseline missing / tool error). Never writes a baseline on its own.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
BASELINE = REPO / "state" / "cleave_baseline.json"
CENSUS = REPO / "tools" / "yggdrasil_census.py"
ATLAS = REPO / "tools" / "yggdrasil_atlas.py"
GATE = REPO / "tools" / "mcp_startup_gate.py"

# id -> (kind, dotted path, wave, size "py/lines/api/mains", note)
# Sizes are the measured atlas values of 2026-10-30; re-measure, never guess.
UNITS = {
    "U1": ("tree", "SHORTCUTS", 1, "1/1/0/0", "retire, do not publish — 1-line stub"),
    "U2": ("tree", "CLI", 1, "2/253/1/1", "smallest real cut"),
    "U3": ("tree", "DAEMONS", 1, "3/876/17/3", "three process entry points"),
    "U4": ("tree", "SHARDS", 1, "4/1125/19/2", "cleave grammar's own vocabulary"),
    "U5": ("tree", "HYDRA", 2, "11/2681/39/7", "1 hub only"),
    "U6": ("tree", "TOOLS", 2, "10/2501/21/6", "imported by nothing"),
    "U7": ("tree", "tests", 2, "13/2039/47/12", "moves last in wave: pins every path"),
    "U8": ("tree", "AOA", 2, "30/7274/77/5", "all 6 hubs — contract test"),
    "U9": ("tree", "CODEX", 2, "87/36081/188/60", "largest free cut"),
    "U10": ("sub", "CORE.technique_wiring", 3, "10/-/28/0", "inside the trunk, nothing imports it"),
    "U11": ("sub", "ORCHESTRATION.scripts", 3, "16/-/89/13", "largest clean subpackage"),
    "U12": ("sub", "ORCHESTRATION.ml_platforms.improvements", 3, "14/-/74/13", ""),
    "U13": ("sub", "CODEX.VIDEOS", 3, "22/-/57/20", "20 entry points"),
    "U14": ("sub", "ORCHESTRATION.BIBL", 3, "3/-/34/2", ""),
    "U15": ("sub", "HYDRA.ml_platforms", 3, "6/-/20/3", ""),
    "U16": ("sub", "UTILITIES.TARBABY", 3, "3/-/16/2", "sibling: PERSEPHONE/bin"),
    "U17": ("sub", "CODEX.CORE", 3, "16/-/32/6", "sibling: SESSION_TEMPLATE/generators"),
    "U18": ("sub", "ORCHESTRATION.TEST", 3, "2/-/12/1", ""),
}


def run(argv, timeout=600, cwd=None):
    p = subprocess.run(argv, cwd=str(cwd or REPO), capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def json_of(script):
    rc, out = run([sys.executable, str(script), "--json"])
    if rc != 0:
        raise SystemExit(f"{script.name} --json failed rc={rc}\n{out[-2000:]}")
    start = out.find("{")
    return json.loads(out[start:])


def importers(dotted):
    """Which files import this dotted path — the SEES list, computed not guessed."""
    head = dotted.split(".")[0]
    hits = []
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError) as exc:
            note_failure("tools/cleave_check.py", exc)
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n == dotted or n.startswith(dotted + ".") or n.split(".")[0] == head and dotted.find(".") < 0:
                    hits.append(str(path.relative_to(REPO)).replace("\\", "/"))
                    break
    return sorted(set(hits))


def first_party():
    return ({p.name[:-3] for p in SRC.glob("*.py")}
            | {p.name for p in SRC.iterdir() if p.is_dir() and not p.name.startswith(".")})


def resolves(dotted, names=None):
    """Does this dotted path exist under src/ — by PATH, not by executing it.
    Executing imports to test them hangs on torch/vllm and measures the wrong
    thing: the failure a cleave introduces is an unresolvable path, and that is
    a file question."""
    names = names if names is not None else first_party()
    parts = dotted.split(".")
    if parts[0] not in names:
        return True                      # third-party or stdlib: not ours to check
    cur = SRC
    for i, part in enumerate(parts):
        nxt = cur / part
        if (nxt.with_suffix(".py")).is_file():
            return i == len(parts) - 1
        if nxt.is_dir():
            cur = nxt
            continue
        return False
    return (cur / "__init__.py").is_file()


def unresolved_in(mods):
    """Per-module list of first-party imports that point at nothing on disk."""
    names = first_party()
    out = {}
    for m in mods:
        path = SRC / (m.replace(".", "/") + ".py")
        if not path.is_file():
            path = SRC / m.replace(".", "/") / "__init__.py"
        if not path.is_file():
            out[m] = ["(module file absent)"]
            continue
        bad = []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            out[m] = [f"(syntax: {exc.msg} line {exc.lineno})"]
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                cands = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                cands = [node.module]
            else:
                continue
            for c in cands:
                if not resolves(c, names):
                    bad.append(c)
        out[m] = sorted(set(bad))      # every module is reported, [] when clean:
        # a dict of only the broken ones makes "all fine" look identical to
        # "nothing found", and those two must never be the same verdict.
    return out


def unit_modules(unit_id):
    kind, dotted, *_ = UNITS[unit_id]
    if kind == "sub":
        base = SRC / dotted.replace(".", "/")
        return sorted({str(p.relative_to(SRC)).replace("\\", "/")[:-3].replace("/", ".")
                       for p in base.rglob("*.py")}) if base.is_dir() else [dotted]
    base = SRC / dotted
    return sorted({str(p.relative_to(SRC)).replace("\\", "/")[:-3].replace("/", ".")
                   for p in base.rglob("*.py")}) if base.is_dir() else []


def smoke_modules(mods, cap=200):
    """Static resolve sweep. Fast (no execution), and it measures exactly the
    thing a cleave breaks: an import path that no longer points at a file."""
    return unresolved_in(mods[:cap])


def test_summary(line):
    """Strip the duration: '126 passed, 1 skipped in 29.48s' differs from the
    baseline only by timing, and a check that fails on timing is a false alarm."""
    line = (line or "").strip()
    cut = line.find(" in ")
    return line[:cut] if cut > 0 else line


def snapshot(with_tests=False):
    census = json_of(CENSUS)
    atlas = json_of(ATLAS)
    smoke = {uid: smoke_modules(unit_modules(uid)) for uid in sorted(UNITS)}
    # value is a LIST of unresolved imports, [] when clean — comparing it to
    # "ok" marks all 253 modules as broken and the exemption list becomes
    # meaningless (measured, then fixed).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    broken = {(u, m): v for u, mods in smoke.items() for m, v in mods.items() if v}
    tests = None
    if with_tests:
        rc, out = run([sys.executable, "-m", "pytest", "src/tests", "-q"], timeout=1800)
        for line in reversed(out.strip().splitlines()):
            if " passed" in line or " failed" in line:
                tests = {"rc": rc, "summary": line.strip()[:200]}
                break
    base = {
        "schema": "cleave-baseline/1",
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "census": {
            "tier_a": census["tiers"]["A_cleave_today"],
            "tier_b_trees": [t["tree"] for t in census["tiers"]["B_needs_shim"]],
            "any_import_pairs": census["edges"]["any_import_pairs"],
            "cycles_real": census["cycles_real"],
            "modules": census["totals"].get("modules"),
            "lines": census["totals"].get("lines"),
        },
        "atlas": {
            "standalone_trees": sorted(k for k, v in atlas["trees"].items() if v["standalone"]),
            "standalone_subs": sorted(k for k, v in atlas["subpackages"].items() if v["standalone"]),
            "inbound": {k: len(v["inbound_trees"]) for k, v in atlas["trees"].items()},
            "totals": atlas["totals"],
        },
        "tests": tests,
        "smoke": smoke,
    }
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(base, indent=1), encoding="utf-8")
    print(f"baseline written: {BASELINE}")
    print(f"  tier A {len(base['census']['tier_a'])}, tier B {len(base['census']['tier_b_trees'])}, "
          f"cycles {base['census']['cycles_real']}, modules {base['atlas']['totals']['modules']}, "
          f"standalone trees {len(base['atlas']['standalone_trees'])} / subs {len(base['atlas']['standalone_subs'])}")
    print(f"  resolve health: {len(broken)} module(s) with a first-party import that "
          f"points at nothing BEFORE any cut (exempt by name, never a unit failure)")
    for (u, m), why in list(broken.items())[:6]:
        print(f"    exempt {u} {m}: {why}")
    if tests is None and with_tests:
        print("  WARNING: tests run but no summary line parsed — tests baseline is null")
    return 0


def check_unit(unit_id, do_gate=False, do_tests=False, smoke=True):
    if not BASELINE.is_file():
        print(f"REFUSED: no baseline at {BASELINE}. Run --snapshot first — a check "
              f"with no expectation cannot fail and therefore cannot pass.")
        return 2
    if unit_id not in UNITS:
        print(f"unknown unit {unit_id}; known: {', '.join(sorted(UNITS))}")
        return 2
    kind, dotted, wave, size, note = UNITS[unit_id]
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    census = json_of(CENSUS)
    atlas = json_of(ATLAS)

    # 1. cycles must not regress: a move that introduces an import cycle is worse
    check("no new module-level cycles",
          census["cycles_real"] == base["census"]["cycles_real"],
          f"now {census['cycles_real']}, baseline {base['census']['cycles_real']}")

    # 2. module count is conserved by a move (a lost file is a silent deletion)
    check("module count conserved",
          atlas["totals"]["modules"] == base["atlas"]["totals"]["modules"],
          f"now {atlas['totals']['modules']}, baseline {base['atlas']['totals']['modules']}")

    # 3. this unit must not have gained importers
    if kind == "tree":
        now_in = len(atlas["trees"].get(dotted, {}).get("inbound_trees", []))
        was_in = base["atlas"]["inbound"].get(dotted)
        check(f"{dotted} gained no importers",
              was_in is None or now_in <= was_in,
              f"now {now_in} inbound, baseline {was_in}")
    else:
        sub = atlas["subpackages"].get(dotted.replace(".", "/"))
        if sub is None:
            check(f"{dotted} still resolves in the atlas", False,
                  "subpackage absent — moved path not re-measured; pass --id with the new location")
        else:
            check(f"{dotted} gained no importers",
                  len(sub["inbound"]) == 0,
                  f"inbound: {list(sub['inbound'])[:5] or 'none'}")

    # 4. no NEW unresolvable first-party import — the exact breakage a move causes
    if smoke:
        base_smoke = (base.get("smoke") or {}).get(unit_id) or {}
        mods = unit_modules(unit_id)
        # No modules at all is a FAILURE, not a vacuous pass: that is what a
        # half-finished cut looks like (measured 2026-10-30 — renaming the only
        # file of CLI made this check pass while the tree was broken).
        if not mods:
            check("import paths resolve", False,
                  "unit has no modules under src/ — nothing to verify, so the cut is incomplete")
        else:
            now = smoke_modules(mods)
            regressed, still = [], []
            for m, bad in sorted(now.items()):
                was = base_smoke.get(m, [])
                new = [b for b in bad if b not in was]
                if new:
                    regressed.append(f"{m} -> {', '.join(new)}")
                elif bad:
                    still.append(m)
            detail = "; ".join(regressed[:3]) if regressed else (
                f"{len(still)} pre-existing gap(s) exempt" if still else "all first-party imports resolve")
            check(f"import paths resolve ({len(now)} module(s), no regression vs baseline)",
                  not regressed, detail)

    # 5. optional: the startup gate still says GO
    if do_gate:
        rc, out = run([sys.executable, str(GATE)], timeout=300)
        check("mcp_startup_gate exit 0", rc == 0, f"rc={rc}")

    # 6. optional: test count unchanged
    if do_tests:
        base_t = base.get("tests") or {}
        rc, out = run([sys.executable, "-m", "pytest", "src/tests", "-q"], timeout=1800)
        summary = ""
        for line in reversed(out.strip().splitlines()):
            if " passed" in line or " failed" in line:
                summary = line.strip()
                break
        check("tests match baseline",
              bool(base_t) and rc == 0
              and test_summary(summary) == test_summary(base_t.get("summary")),
              f"now '{test_summary(summary)}', baseline '{test_summary(base_t.get('summary'))}'")

    failed = [n for n, ok, _ in results if not ok]
    print()
    if failed:
        print(f"UNIT {unit_id} ({dotted}) FAILED {len(failed)}/{len(results)}: {', '.join(failed)}")
        print("Do NOT retry: send this output back with the unit id. A retry without the "
              "output is how a baseline gets rewritten by accident.")
        return 1
    print(f"UNIT {unit_id} ({dotted}) PASSED {len(results)}/{len(results)}")
    return 0


def envelopes(wave):
    out = []
    for uid, (kind, dotted, w, size, note) in UNITS.items():
        if wave != "all" and str(w) != str(wave).replace("wave", ""):
            continue
        sees = importers(dotted)
        out.append(
            f"UNIT: {uid}  {'tree' if kind == 'tree' else 'subpackage'}={dotted}\n"
            f"WHY:  {size} py/lines/public/mains" + (f" — {note}" if note else "") + "\n"
            f"DO:   cut src/{dotted.replace('.', '/')} to its own location; rewrite the "
            f"{len(sees)} import site(s) listed below; no behaviour change\n"
            f"SEES: {', '.join(sees) if sees else '(none — nothing imports this)'}\n"
            f"DONE WHEN:\n"
            f"  python tools/cleave_check.py --unit {uid}    # must print PASSED\n"
            f"REPORT BACK: that command's full output + `git status --short` + files touched.\n"
        )
    print("\n".join(out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--with-tests", action="store_true", help="with --snapshot: record pytest baseline")
    ap.add_argument("--unit", metavar="ID")
    ap.add_argument("--gate", action="store_true", help="with --unit: also run the startup gate")
    ap.add_argument("--tests", action="store_true", help="with --unit: also compare pytest count")
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--envelopes", metavar="WAVE", nargs="?", const="all")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.snapshot:
        return snapshot(with_tests=a.with_tests)
    if a.unit:
        return check_unit(a.unit, do_gate=a.gate, do_tests=a.tests, smoke=not a.no_smoke)
    if a.envelopes:
        return envelopes(a.envelopes)
    if a.list:
        for uid, (kind, dotted, w, size, note) in UNITS.items():
            print(f"{uid:4} wave{w} {kind:4} {dotted:44} {size:>16}  {note}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
