#!/usr/bin/env python
"""Control tiers — every pre-commit stanza, with its target and its reachability.

Why this exists. The hook text was read as a list of gates for months, and three
of the twelve stanzas turned out to be shaped the same way:

  * a stanza guarded by `if [ -f tools/<gate>.py ]` passes **silently** when the
    file is absent -- the guard that was meant to keep a half-installed tree from
    breaking commits is also a way for a deleted gate to stop gating;
  * a stanza whose tool exists but which no test imports is a gate nobody has
    executed since it was written, so its first real run is the commit it
    blocks;
  * every blocking stanza carries an `ALLOW_*` escape hatch, and no document
    listed them together, so "how do I get past this" was answered from memory.

So the table is derived from the hook, never transcribed: for each
`# --- Check ...` stanza, the target script, whether it exists, the tier of the
evidence that anything exercises it, and the bypass variable it honours.

Tiers (the sweep protocol's, applied to a hook stanza):

  T1  the target exists and a test imports it -- someone has executed it
  T2  the target exists and a non-test module invokes it, but no test imports it
  T3  the target exists and only a token mention links it to anything
  T4  the target does not exist -- the stanza is a silent skip under its guard
  UNRESOLVED  the stanza invokes no `tools/*.py`, so there is nothing to tier

A T4 is not automatically a defect: a stanza may legitimately test for a tool
another lane installs. It is reported because under `[ -f ... ]` a T4 is
indistinguishable, from the commit log, from a gate that ran and passed.

Usage:
    python tools/control_tiers.py                # table
    python tools/control_tiers.py --json PATH    # also write the table as JSON
    python tools/control_tiers.py --check        # exit 1 on a blocking T4
    python tools/control_tiers.py --stanza 2     # one stanza, all fields

Exit codes: 0 no blocking stanza is T4, 1 one is, 2 the hook could not be read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".githooks" / "pre-commit"
DEFAULT_JSON = REPO / "logs" / "control_tiers.json"

STANZA_RE = re.compile(r"^#\s*[-\u2500]{2,}\s*Check\b(?P<rest>.*?)[-\u2500]{2,}\s*$")
# Any script path the stanza names anywhere (used as the fallback).
TOOL_RE = re.compile(r"((?:tools|scripts|src)/[A-Za-z0-9_/]+\.py)")
BYPASS_RE = re.compile(r"\$\{(ALLOW_[A-Z0-9_]+)[^}]*\}")
BYPASS_RE_TARGET = re.compile(r"(ALLOW_[A-Z0-9_]+)")
IMPORT_RE = r"(?:^|\n)\s*(?:from\s+{stem}\s+import|from\s+tools\.{stem}\s+import|import\s+{stem}\b)"


@dataclass
class Stanza:
    number: str
    line: int
    label: str
    mode: str
    target: str | None = None
    target_path: str | None = None
    target_source: str = "none"   # invoked | mention | none
    exists: bool = False
    tier: str = "UNRESOLVED"
    test_imports: int = 0
    module_invokes: int = 0
    guarded: bool = False
    bypass: list[str] = field(default_factory=list)
    bypass_in_target: list[str] = field(default_factory=list)
    body_lines: int = 0

    def row(self) -> str:
        target = self.target or "-"
        bypass = ",".join(self.bypass) or "-"
        guard = "silent-skip" if self.guarded else "none"
        return (f"{self.number:<5} {self.line:>4}  {self.mode:<13} {target:<26} "
                f"{self.tier:<5} {self.target_source:<7} tests={self.test_imports:<3} "
                f"mods={self.module_invokes:<3} guard={guard:<11} bypass={bypass}")


def _mode_of(text: str) -> str:
    low = text.lower()
    if "advisory" in low:
        return "advisory"
    if "non-blocking" in low:
        return "non-blocking"
    if "blocking" in low:
        return "blocking"
    return "blocking"


def parse_hook(text: str) -> list[Stanza]:
    """Split the hook on its stanza headers and read each stanza's own words."""
    lines = text.splitlines()
    marks = [(i, STANZA_RE.match(line)) for i, line in enumerate(lines) if STANZA_RE.match(line)]
    out: list[Stanza] = []
    for idx, (i, m) in enumerate(marks):
        assert m is not None
        rest = m.group("rest").strip(": ").strip()
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = "\n".join(lines[i:end])
        header = lines[i]
        number = rest.split(":", 1)[0].strip() or "?"
        label = rest.split(":", 1)[1].strip() if ":" in rest else ""
        st = Stanza(number=number, line=i + 1, label=label, mode=_mode_of(header),
                    guarded="[ -f " in body, body_lines=end - i)
        _resolve_target(st, lines[i:end])
        st.bypass = sorted(set(BYPASS_RE.findall(body)))
        out.append(st)
    return out


def _resolve_target(st: Stanza, body: list[str]) -> None:
    """Which script the stanza actually runs -- not which one it recommends.

    Stanza 8 executes `tools/supervisor_mcp.py --verdict` and *names*
    `tools/supervisor_doctor.py --fix` in an echo. Reading the first
    `tools/*.py` in the body tiered the remediation pointer as the gate: right
    about a thing, wrong about which. So an executed line wins, `echo`/comment
    lines are skipped, and a body with no invocation says so instead of
    borrowing one.
    """
    executed: str | None = None
    mentioned: str | None = None
    for raw in body:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        hit = TOOL_RE.search(line)
        if hit is None:
            continue
        if line.startswith("echo") or line.startswith('echo "'):
            mentioned = mentioned or hit.group(1)
            continue
        executed = executed or hit.group(1)
        break
    chosen = executed or mentioned
    if chosen is None:
        return
    st.target_path = chosen
    st.target_source = "invoked" if executed else "mention"
    st.target = Path(chosen).stem
    st.exists = (REPO / chosen).is_file()


def tier(st: Stanza, root: Path) -> None:
    """Tier the evidence that something executes the stanza's target."""
    if not st.target:
        st.tier = "UNRESOLVED"
        return
    if not st.exists:
        st.tier = "T4"
        return
    if st.target_source != "invoked":
        # The stanza names the script but never runs it: whatever tier the file
        # would earn belongs to a different question ("does this tool work"),
        # not to "is this stanza wired".
        st.tier = "T3"
        return
    # The escape hatches a stanza honours are not all written in the hook:
    # ALLOW_BULK_COMMIT lives inside scripts/commit_gate.py, so a table built
    # from hook text alone under-reports exactly the bypass an operator needs.
    target_file = REPO / (st.target_path or "")
    if target_file.is_file():
        st.bypass_in_target = sorted(set(
            BYPASS_RE_TARGET.findall(target_file.read_text(encoding="utf-8", errors="replace"))
        ))
    stem = st.target
    pat = re.compile(IMPORT_RE.format(stem=re.escape(stem)), re.MULTILINE)
    tests = root / "tests"
    if tests.is_dir():
        st.test_imports = sum(
            1 for p in tests.rglob("test_*.py")
            if pat.search(p.read_text(encoding="utf-8", errors="replace"))
        )
    invoke = re.compile(rf"tools[/\\]{re.escape(stem)}\.py|{re.escape(stem)}\b")
    for base in ("src", "tools", "scripts"):
        d = root / base
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if p.name == f"{stem}.py":
                continue
            if invoke.search(p.read_text(encoding="utf-8", errors="replace")):
                st.module_invokes += 1
    if st.test_imports:
        st.tier = "T1"
    elif st.module_invokes:
        st.tier = "T2"
    else:
        st.tier = "T3"


def build(root: Path) -> list[Stanza]:
    if not HOOK.is_file():
        raise SystemExit(2)
    stanzas = parse_hook(HOOK.read_text(encoding="utf-8", errors="replace"))
    for st in stanzas:
        tier(st, root)
    return stanzas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", nargs="?", const=str(DEFAULT_JSON), default=None,
                    help="write the table as JSON (default logs/control_tiers.json)")
    ap.add_argument("--check", action="store_true", help="exit 1 if a blocking stanza is T4")
    ap.add_argument("--stanza", default=None, help="print one stanza's fields")
    args = ap.parse_args(argv)

    try:
        stanzas = build(REPO)
    except SystemExit:
        print(f"[control_tiers] cannot read {HOOK}", file=sys.stderr)
        return 2
    if not stanzas:
        print(f"[control_tiers] no '# --- Check' stanza found in {HOOK}", file=sys.stderr)
        return 2

    if args.stanza is not None:
        for st in stanzas:
            if st.number == args.stanza:
                print(json.dumps(st.__dict__, indent=2))
                return 0
        print(f"[control_tiers] no stanza {args.stanza}", file=sys.stderr)
        return 2

    print(f"{'num':<5} {'line':>4}  {'mode':<13} {'target':<26} {'tier':<5} "
          f"{'source':<7} {'tests':<9} {'mods':<7} {'guard':<11} bypass")
    print("-" * 118)
    for st in stanzas:
        print(st.row())

    counts: dict[str, int] = {}
    for st in stanzas:
        counts[st.tier] = counts.get(st.tier, 0) + 1
    blocking = [s for s in stanzas if s.mode == "blocking"]
    silent = [s for s in blocking if s.tier == "T4"]
    unwired = [s for s in blocking if s.tier in {"UNRESOLVED", "T3"}]
    print("-" * 118)
    print(f"{len(stanzas)} stanzas ({len(blocking)} blocking, "
          f"{len(stanzas) - len(blocking)} advisory/non-blocking) "
          f"tiers: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"bypass variables in the hook: "
          + (", ".join(sorted({b for s in stanzas for b in s.bypass})) or "none"))
    inner = [(s.number, s.target, s.bypass_in_target) for s in stanzas
             if set(s.bypass_in_target) - set(s.bypass)]
    if inner:
        print("bypass variables inside the target script (not visible in the hook):")
        for number, target, names in inner:
            print(f"  {number:<5} {target:<26} " + ", ".join(names))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([s.__dict__ for s in stanzas], indent=2), encoding="utf-8")
        print(f"wrote {out}")

    if args.check:
        problems = []
        if silent:
            problems.append("target missing (silent skip): "
                            + ", ".join(f"{s.number} {s.target_path}" for s in silent))
        if unwired:
            problems.append("blocking stanza executes no resolvable script: "
                            + ", ".join(f"{s.number} {s.target or '-'}" for s in unwired))
        if problems:
            for p in problems:
                print(f"FAIL -- {p}")
            return 1
        print(f"PASS -- every blocking stanza invokes an existing script "
              f"({len(blocking)} blocking, tiers "
              + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
