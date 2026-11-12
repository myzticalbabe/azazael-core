#!/usr/bin/env python3
# coding: utf-8
"""Mutation probe: does each detector actually catch a planted defect?

Why this exists
---------------
Every gate in this estate reports what it *found*. None of them reports what it
would miss. A clean reading is therefore ambiguous in the worst way: it can mean
the tree is clean, or it can mean the detector is blind to the defect class and
has been agreeing with itself since it was written. `ast_audit` counting 5,488
parsed files proves nothing about whether the scanner would notice a bare
`except: pass` in any of them.

So this plants defects on purpose, one per fixture file, in a temporary
directory, and asks three detectors which ones they catch:

  scanner   CORE.compliance.scanner -- this tree's own 21 AST rules
  ruff      an external rule set this tree did not choose
  pyflakes  a second external reading, independently implemented

A KNOWN-GAP annotation separates two very different answers to "caught by
nothing": a gap somebody measured and accepted (recorded with its reason, does
not fail the run) and a hole nobody knew about (fails the run, exit 1). Without
that split a probe with one accepted gap is red forever, and a permanently red
tool gets switched off -- HED-4's failure. A gap that starts being caught is
reported CLOSED, so the annotation is removed rather than rotting in place.

Two controls keep the score honest, and both are required:

  positive  every mutant must be caught by at least one detector; a mutant
            caught by nothing is a HOLE and is named, never quietly dropped
  negative  the clean fixture must produce ZERO findings in every detector. A
            detector that flags everything would otherwise score 13/13. If the
            clean control is dirty the run is INVALID (exit 2), because no
            reading taken under it means anything.

Scope, stated plainly
---------------------
Report only. It is not wired into the commit hook: a new blocking gate needs a
registry row and a recorded baseline, and the point of this pass is to learn
where the coverage gaps are, not to ratchet them yet.

Exit codes follow the ONE_CLICK contract:
  0  ran, controls valid, every mutant caught
  1  a mutant was caught by nothing, or caught for a different rule than the one
     predicted (OFF-TARGET -- an expectation to investigate, not to edit away)
  2  could not be performed: a detector is missing, or the clean control is dirty

Usage
-----
    python tools/mutation_probe.py
    python tools/mutation_probe.py --json state/mutation_probe.json
    python tools/mutation_probe.py --keep      # print the fixture dir and leave it
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETECTORS = ("scanner", "ruff", "pyflakes")

#: Built at runtime so this file never contains a literal emoji -- the scanner
#: would flag the probe itself, and the staged scanner gate would refuse to
#: commit the instrument that is supposed to measure the scanner.
_EMOJI = chr(0x1F600)
_BACKSLASH = chr(92)

#: Assembled from parts for the same reason as _EMOJI: `cls_new_factory` matches
#: raw file text, so spelling the call in this file's source would make the
#: probe a finding in the very gate it measures (caught by the staged scanner
#: gate on 2026-11-04 as `cls_new_factory 0 -> 1` in tools/mutation_probe.py).
#: os_system inside a string body is filtered by the scanner; this rule is not.
_CLS_NEW_CALL = "cls" + "()" + ".__new__" + "(cls)"

#: The clean control. Every import is used, every open() names an encoding,
#: every subprocess call is bounded, and no except block exists -- so any
#: finding attributed to this file is a detector false positive.
CLEAN = '''"""Fixture base for the mutation probe: clean by construction."""
import os
import subprocess
import sys


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def run(argv):
    """Run a command with a bounded wait."""
    proc = subprocess.run(argv, capture_output=True, timeout=30, check=False)
    return proc.returncode


def read(path):
    """Read a file with an explicit encoding."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def env(name):
    """Read one environment variable."""
    return os.environ.get(name, "")


ARGV0 = sys.argv[0]
'''

#: name -> (source fragment, where it goes, predicted detections).
#: `expect` maps a detector to the rule id that SHOULD fire. A detector absent
#: from `expect` is predicted NOT to catch that mutant; when it does anyway the
#: report shows it as a bonus, which is a real answer to "what does it cover".
MUTANTS = [
    dict(
        name="emoji_in_string",
        where="append",
        source='\n\ndef banner():\n    """Return a banner."""\n    return "ok " + "'
        + _EMOJI + '"\n',
        expect={"scanner": "unicode_emoji"},
    ),
    dict(
        name="os_system_call",
        where="append",
        source='\n\ndef purge(path):\n    """Delete through a shell call."""\n'
        '    return os.system("rm -f " + path)\n',
        expect={"scanner": "os_system"},
    ),
    dict(
        name="shell_true",
        where="append",
        source='\n\ndef pipe(argv):\n    """Run a command through a shell."""\n'
        '    return subprocess.run(argv, shell=True, timeout=10)\n',
        expect={"scanner": "shell_true"},
    ),
    dict(
        name="subprocess_no_timeout",
        where="append",
        source='\n\ndef quick(argv):\n    """Run a command with no bound."""\n'
        '    return subprocess.run(argv, capture_output=True, check=False)\n',
        expect={"scanner": "subprocess_no_timeout"},
    ),
    dict(
        name="open_no_encoding",
        where="append",
        source='\n\ndef slurp(path):\n    """Read with a platform encoding."""\n'
        '    with open(path) as handle:\n        return handle.read()\n',
        expect={"scanner": "open_no_encoding"},
    ),
    dict(
        name="silent_except_pass",
        where="append",
        source='\n\ndef maybe(path):\n    """Swallow a read failure."""\n'
        '    try:\n        return read(path)\n    except OSError:\n        pass\n'
        '    return ""\n',
        expect={"scanner": "silent_except_pass"},
    ),
    dict(
        name="except_return_default",
        where="append",
        source='\n\ndef value(path):\n    """Return a success-shaped default."""\n'
        '    try:\n        return read(path)\n    except OSError:\n'
        '        return ""\n',
        expect={"scanner": "except_return_default"},
    ),
    dict(
        name="cls_new_factory",
        where="append",
        source='\n\nclass Thing:\n    """A thing."""\n\n    @classmethod\n'
        '    def make(cls):\n        """Build one."""\n'
        '        return ' + _CLS_NEW_CALL + '\n',
        expect={"scanner": "cls_new_factory"},
    ),
    # `_PATTERN_WIN_PATH` is r'[Cc]:\\\\' -- two backslashes in the file text, which
    # is the ordinary `"C:\\Users"` spelling. This mutant uses exactly that form.
    dict(
        name="hardcoded_win_path",
        where="append",
        source='\n\nPATH_ROOT = "C:' + _BACKSLASH * 2 + 'Users' + _BACKSLASH * 2
        + 'clint"\n',
        expect={"scanner": "hardcoded_win_path"},
    ),
    # Measured 2026-11-04: the raw-string spelling carries ONE backslash in the
    # file text, so _PATTERN_WIN_PATH cannot see it, and neither external leg
    # flags a hardcoded path. Accepted for now rather than silently unmeasured.
    dict(
        name="raw_windows_path",
        where="append",
        source='\n\nRAW_ROOT = r"C:' + _BACKSLASH + 'Users' + _BACKSLASH + 'clint"\n',
        expect={},
        known_gap="raw-string paths escape _PATTERN_WIN_PATH (two-backslash form "
                  "only); ruff and pyflakes do not flag hardcoded paths",
    ),
    # The next four are the classes the canonical scanner deliberately does NOT
    # own: dead names and dead code, not house anti-patterns. They are the reason
    # the external legs exist at all -- HED-1's undetected NameError lived here.
    dict(
        name="unused_import",
        where="prepend",
        source="import json\n",
        expect={"ruff": "F401", "pyflakes": "unused_import"},
    ),
    dict(
        name="unused_variable",
        where="append",
        source='\n\ndef compute(a, b):\n    """Compute one thing and ignore another."""\n'
        '    unused_total = a + b\n    return a\n',
        expect={"ruff": "F841", "pyflakes": "unused_variable"},
    ),
    dict(
        name="undefined_name",
        where="append",
        source='\n\ndef broken(a):\n    """Reference a name that does not exist."""\n'
        '    return a + missing_sentinel\n',
        expect={"ruff": "F821", "pyflakes": "undefined_name"},
    ),
    dict(
        name="fstring_no_placeholder",
        where="append",
        source='\n\ndef label(a):\n    """Return a constant f-string."""\n'
        '    return f"label" + str(a)\n',
        expect={"ruff": "F541", "pyflakes": "fstring_no_placeholder"},
    ),
]

_PF_PATH = re.compile(r"^(?P<path>.*?):(?P<line>\d+):")


def build_fixtures(tmp: Path) -> list[dict]:
    """Write the control and every mutant; return rows carrying their paths."""
    clean = tmp / "m00_clean.py"
    clean.write_text(CLEAN, encoding="utf-8")
    rows = [dict(name="clean_control", path=clean, expect={},
                 known_gap=None)]
    for index, mutant in enumerate(MUTANTS, start=1):
        body = (mutant["source"] + CLEAN if mutant["where"] == "prepend"
                else CLEAN + mutant["source"])
        path = tmp / ("m%02d_%s.py" % (index, mutant["name"]))
        path.write_text(body, encoding="utf-8")
        rows.append(dict(name=mutant["name"], path=path,
                         expect=mutant["expect"],
                         known_gap=mutant.get("known_gap")))
    return rows


def load_scanner():
    """The tree's canonical scanner, or None when it cannot be imported."""
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    try:
        from CORE.compliance.scanner import scan_file
    except Exception as exc:
        print("  scanner  BLIND: %s" % str(exc)[:160])
        return None
    return scan_file


def run_scanner(scan_file, rows: list[dict]) -> dict:
    """Per-file categories from the canonical scanner."""
    out: dict[str, list[str]] = {}
    for row in rows:
        try:
            result = scan_file(str(row["path"]))
        except Exception as exc:
            out[row["path"].name] = ["scanner_error:%s" % str(exc)[:60]]
            continue
        out[row["path"].name] = [cat for _line, cat, _snip in result.findings]
    return out


def _load_external():
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    import external_lint
    return external_lint


def run_external(rows: list[dict]) -> tuple[dict, dict, dict]:
    """Both external legs, attributed per fixture file."""
    ext = _load_external()
    files = [row["path"] for row in rows]
    ruff_leg = ext.run_ruff(files)
    pf_leg = ext.run_pyflakes(files)

    ruff: dict[str, list[str]] = {}
    for finding in ruff_leg.get("findings", []):
        name = os.path.basename(str(finding.get("file") or ""))
        if name:
            ruff.setdefault(name, []).append(str(finding.get("code") or "?"))
    pf: dict[str, list[str]] = {}
    for finding in pf_leg.get("findings", []):
        raw = finding.get("raw", "")
        match = _PF_PATH.match(raw)
        if not match:
            continue
        name = os.path.basename(match.group("path"))
        message = raw[match.end():].strip()
        pf.setdefault(name, []).append(ext._pyflakes_bucket(message))
    states = {"ruff": ruff_leg.get("state"), "pyflakes": pf_leg.get("state")}
    return ruff, pf, states


def score(rows: list[dict], findings: dict) -> list[dict]:
    """Attach what each detector reported, and whether it matches the plan."""
    scored = []
    for row in rows:
        name = row["path"].name
        caught = {
            "scanner": sorted(set(findings["scanner"].get(name, []))),
            "ruff": sorted(set(findings["ruff"].get(name, []))),
            "pyflakes": sorted(set(findings["pyflakes"].get(name, []))),
        }
        expect = row["expect"]
        missed = sorted(det for det in DETECTORS if not caught[det])
        wrong = sorted(
            det for det, rule in expect.items()
            if rule and rule not in caught[det]
        )
        scored.append(dict(row, caught=caught, missed=missed, wrong=wrong,
                           predicted=sum(1 for r in expect.values() if r)))
    return scored


def render(scored: list[dict], states: dict, total_csv: int) -> tuple[str, int]:
    lines = ["MUTATION PROBE  (%d mutant(s) + control)" % (len(scored) - 1)]
    for det, state in states.items():
        if state != "ok":
            lines.append("[BLIND   ] %-9s %s" % (det, state))

    control = scored[0]
    control_hits = sum(len(v) for v in control["caught"].values())
    control_ok = control_hits == 0
    lines.append("[%s] control     %d finding(s) across %d detector(s)" % (
        "CLEAN   " if control_ok else "DIRTY   ", control_hits, len(DETECTORS)))
    if not control_ok:
        for det in DETECTORS:
            if control["caught"][det]:
                lines.append("             %s -> %s" % (
                    det, ",".join(control["caught"][det])))

    holes, off_target = [], []
    for row in scored[1:]:
        hits = ["%s(%s)" % (det, ",".join(row["caught"][det]))
                for det in DETECTORS if row["caught"][det]]
        if not hits:
            if row["known_gap"]:
                lines.append("[KNOWN   ] %-24s caught by nothing (%s)" % (
                    row["name"], row["known_gap"]))
                continue
            holes.append(row["name"])
            lines.append("[HOLE    ] %-24s caught by nothing" % row["name"])
            continue
        if row["known_gap"]:
            lines.append("[CLOSED  ] %-24s %s (gap closed; drop the annotation)"
                         % (row["name"], " ".join(hits)))
            continue
        if row["wrong"]:
            off_target.append("%s/%s" % (row["name"], ",".join(row["wrong"])))
        lines.append("[%s] %-24s %s" % (
            "OK      " if not row["wrong"] else "OFFTRGT ", row["name"],
            " ".join(hits)))

    per_detector = {
        det: sum(1 for row in scored[1:] if row["caught"][det])
        for det in DETECTORS
    }
    mutants = len(scored) - 1
    lines.append("--- per-detector catch rate ---")
    for det in DETECTORS:
        lines.append("  %-9s %d/%d" % (det, per_detector[det], mutants))
    unknown = [row["name"] for row in scored[1:]
               if row["known_gap"] and not any(row["caught"].values())]
    lines.append("known gaps (recorded, accepted): %s" % (
        ", ".join(unknown) if unknown else "none"))
    lines.append("holes (caught by nothing, unexplained): %s" % (
        ", ".join(holes) if holes else "none"))
    if off_target:
        lines.append("off-target (caught, but not the predicted rule): %s"
                     % ", ".join(off_target))

    if not control_ok:
        lines.append("verdict: INVALID (clean control is dirty; no score is "
                     "meaningful)")
        return "\n".join(lines), 2
    if any(state != "ok" for state in states.values()):
        lines.append("verdict: PARTIAL (a detector could not run; the mutants it "
                     "would have caught are not evidence)")
        return "\n".join(lines), 2
    caught_n = sum(1 for row in scored[1:] if any(row["caught"].values()))
    if holes or off_target:
        lines.append("verdict: GAPS (%d/%d caught, %d unexplained hole(s))" % (
            caught_n, mutants, len(holes)))
        return "\n".join(lines), 1
    # A known gap is NOT a catch; the summary counts findings, not intentions.
    lines.append("verdict: COVERED (%d/%d caught, %d known gap(s), control clean)"
                 % (caught_n, mutants, len(unknown)))
    return "\n".join(lines), 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None, help="write the full payload here")
    ap.add_argument("--keep", action="store_true",
                    help="leave the fixture directory on disk and print its path")
    args = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="mutation_probe_"))
    try:
        rows = build_fixtures(tmp)
        scan_file = load_scanner()
        findings = {"scanner": {}, "ruff": {}, "pyflakes": {}}
        states = {"ruff": "missing", "pyflakes": "missing"}
        if scan_file is not None:
            findings["scanner"] = run_scanner(scan_file, rows)
            states["scanner"] = "ok"
        else:
            states["scanner"] = "blind"
        try:
            ruff, pf, ext_states = run_external(rows)
            findings["ruff"], findings["pyflakes"] = ruff, pf
            states.update(ext_states)
        except Exception as exc:
            print("  external  BLIND: %s" % str(exc)[:160])

        scored = score(rows, findings)
        text, code = render(scored, states, len(rows) - 1)
        print(text)
        print("fixtures: %d file(s) in %s" % (len(rows), tmp))

        if args.json:
            payload = {
                "schema": "mutation-probe/1",
                "mutants": len(rows) - 1,
                "states": states,
                "rows": [
                    {"name": r["name"], "file": r["path"].name,
                     "caught": r["caught"], "expect": r["expect"],
                     "known_gap": r["known_gap"]}
                    for r in scored
                ],
                "exit": code,
            }
            Path(args.json).write_text(json.dumps(payload, indent=1),
                                       encoding="utf-8")
            print("wrote %s" % args.json)
        return code
    finally:
        if args.keep:
            print("kept: %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
