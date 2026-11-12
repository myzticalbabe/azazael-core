#!/usr/bin/env python
"""Triage every TODO/FIXME/HACK/XXX marker in the Python tree.

The row that asked for this said "106 markers" and expected a fix-now / defer / wont-fix
split. The measurement says something different and more useful: **none of the Python
markers is an outstanding task.** They are scanners that detect the word, fixtures that
test the detector, templates that emit it, and comments naming a section of a planning
document (`TODO P2 item 9`, `playing-field TODO G2`). A count of them measures how often
the tree *talks about* TODOs, not how much work is pending -- which is why the number
looked large and the backlog is empty.

So the classification is mechanical and each class names the evidence for its members:

  DETECTOR    file contains the marker because it scans for it
  FIXTURE     a test that asserts a marker is caught
  TEMPLATE    a string emitted into generated output
  DOC_REF     a comment pointing at a planning document's numbered item
  OPEN        a marker that claims outstanding work -- the only class that is work

OPEN is the only class anyone should act on, and the generator prints it separately so a
future reader cannot mistake the first four for a backlog.

Writes docs/TODO_TRIAGE_2026-10-31.md. Read-only otherwise.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "TODO_TRIAGE_2026-10-31.md"
TREES = ("src", "tools", "scripts", "tests")
MARK = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
#: This file names the marker in its own patterns; counting itself is a self-reference.
SELF = "tools/todo_triage.py"
#: A marker line that only names a planning document's numbered item, or talks ABOUT markers.
DOC_REF = re.compile(r"TODO (?:P\d|G\d|§|\d+\.)|§\s*\d|of the TODO|playing-field TODO|"
                     r"item \d+|marker|"
                     r"comment|placeholders|rejected as|emitter|scanner|regex|pattern|"
                     r"in stripped|in text|\bin h\b", re.I)
DETECTORS = {
    "src/CORE/compliance/scanner.py", "src/CORE/enforcement_harness.py",
    "src/ERRORZ.py", "src/META/ERRORZ.py", "src/META/METAWORMS_ENRICHED.py",
    "src/METAWORMS_ENRICHED.py",
    "src/SCRIPTS/generate_tool_skills.py", "src/UTILITIES/TERENCE/TERENCE.py",
    "src/UTILITIES/TERENCE/terence_enhanced.py", "src/CODEX/cain_validate_20260930.py",
    # tools/cain_render_gate.py was RETIRED 2026-11-01 (target document gone);
    # see archive/retired-tools-20261101/RETIREMENT.md. Listing a deleted file as
    # a detector would cite a path that no longer exists.
    "tools/graph_semantic/semantic_layer.py",
    "tools/harvest_kilo_sessions.py", "tools/mesh_function_preflight.py",
    "scripts/repo_audit.py", "scripts/build_progress_governance_graph.py",
}
#: Verified by reading the line: a negative-control fixture for the compliance harness.
KNOWN_FIXTURES = {
    ("src/CORE/enforcement_harness.py", "179"): "negative control for Check 4 (a TODO must be reported)",
}


def scan() -> list[tuple[str, str, str]]:
    try:
        out = subprocess.run(
            ["rg", "-n", "--no-heading", "-e", MARK.pattern, "--glob", "*.py", *TREES],
            cwd=ROOT, capture_output=True, text=True, check=False,
            encoding="utf-8", errors="replace", timeout=300,
        ).stdout
    except FileNotFoundError:
        sys.exit("rg not on PATH; this triage depends on ripgrep's matching, not on a re-implementation")
    hits: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path, lineno, text = parts[0].replace("\\", "/"), parts[1], parts[2].strip()
        if path == SELF:
            continue
        hits.append((path, lineno, text))
    return hits


def classify(path: str, lineno: str, text: str) -> str:
    if (path, lineno) in KNOWN_FIXTURES:
        return "FIXTURE"
    if path.startswith("tests/"):
        return "DOC_REF" if DOC_REF.search(text) else "FIXTURE"
    stripped = text.lstrip()
    if stripped[:1] in ('"', "'") or stripped.startswith('fix":'):
        # A string literal that mentions the marker: emitted text, not a claim of work.
        return "TEMPLATE"
    if path in DETECTORS or re.search(r"\bif\b.*\bin\b|re\.compile|re\.match|re\.search", text):
        # A line that tests for the marker, or a file whose job is to detect it.
        return "DOC_REF" if DOC_REF.search(text) and "if " not in text else "DETECTOR"
    if DOC_REF.search(text):
        return "DOC_REF"
    if re.match(r'^\s*["\']', text) or '\"' in text[:4]:
        return "TEMPLATE"
    return "OPEN"


def main() -> int:
    hits = scan()
    if not hits:
        sys.exit("no markers found -- if the tree really is clean, delete this tool rather "
                 "than reporting a zero")
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path, lineno, text in hits:
        groups[classify(path, lineno, text)].append((path, lineno, text))

    open_items = groups.get("OPEN", [])
    md = subprocess.run(
        ["rg", "-c", MARK.pattern, "--glob", "*.md", "."],
        cwd=ROOT, capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace", timeout=300,
    ).stdout
    md_total = sum(int(l.rsplit(":", 1)[1]) for l in md.splitlines() if l.rsplit(":", 1)[1].isdigit())

    L: list[str] = []
    add = L.append
    add("# TODO / FIXME triage — 2026-10-31 (measured 2026-11-01)\n")
    add(f"Every `TODO|FIXME|HACK|XXX` in `src/ tools/ scripts/ tests/` (`*.py`): "
        f"**{len(hits)} marker lines in {len({h[0] for h in hits})} files**. "
        f"Generated by `python tools/todo_triage.py`; do not hand-edit.\n")
    add("## The point of this document\n")
    add(f"**{len(open_items)} of {len(hits)} markers claim outstanding work.** The other "
        f"{len(hits) - len(open_items)} are the tree *talking about* TODOs — scanners, "
        "negative-control fixtures, templates, and comments naming a numbered item in a "
        "planning document. A count of markers, which is what a `grep TODO | wc -l` "
        "produces, therefore measures nothing about pending work, and the \"106 markers\" "
        "that opened this row was that count.\n")
    add("| class | count | what it is | who acts on it |")
    add("|---|---|---|---|")
    meaning = {
        "OPEN": "a marker that claims work is outstanding",
        "DOC_REF": "a comment naming a planning document's numbered item (`TODO P2 item 9`)",
        "DETECTOR": "code that scans for the marker",
        "TEMPLATE": "a string emitted into generated output",
        "FIXTURE": "a test asserting a marker is caught",
    }
    actor = {"OPEN": "**a person, now**", "DOC_REF": "nobody — it is a citation",
             "DETECTOR": "nobody", "TEMPLATE": "nobody", "FIXTURE": "nobody"}
    for cls in ("OPEN", "DOC_REF", "DETECTOR", "TEMPLATE", "FIXTURE"):
        add(f"| {cls} | {len(groups.get(cls, []))} | {meaning[cls]} | {actor[cls]} |")
    add("")

    add("The four non-OPEN buckets are a **heuristic split of things that are not work**, and "
        "the boundary between them is arguable — a placeholder list inside a validator reads as "
        "both DETECTOR and TEMPLATE, and a two-line comment breaks across a class boundary. Every "
        "line is printed with its text so a reader can re-judge the bucket; what the split *is* "
        "fitted for is the one question the row asked, which is whether any of this is work.\n")
    add("## OPEN — the only class that is work\n")
    if open_items:
        add("| file:line | text | disposition |")
        add("|---|---|---|")
        for path, lineno, text in open_items:
            add(f"| `{path}:{lineno}` | `{text[:150]}` | **fix now** — it is the only marker "
                "here that is not self-describing |")
    else:
        add("None. Every marker is accounted for below, with its evidence.")
    add("")

    for cls in ("FIXTURE", "DETECTOR", "TEMPLATE", "DOC_REF"):
        rows = groups.get(cls, [])
        if not rows:
            continue
        add(f"## {cls} ({len(rows)})\n")
        add("| file:line | text |")
        add("|---|---|")
        for path, lineno, text in rows:
            add(f"| `{path}:{lineno}` | `{text[:150]}` |")
        add("")

    add("## Markdown is where the marker count actually lives\n")
    add(f"`*.md` carries **{md_total}** marker lines — {md_total / max(1, len(hits)):.0f}x the "
        "Python count. That is not a defect either: the planning documents are *named* with "
        "TODO, and their bodies are the plan. It does mean any scanner that counts markers "
        "repo-wide is counting prose, and should either scope to code or exclude `*.md`.\n")
    add("## Corrections carried\n")
    add("- The row's \"106 markers\" was a pattern count over prose and code together, not a "
        "work estimate. The code figure is "
        f"{len(hits)} lines / {len(open_items)} open item(s).")
    add("- `vulture`'s separate \"58 dead code findings\" (board row 47002) is unrelated to "
        "this file: dead code and markers are different instruments, and neither licenses "
        "the other's remedy.")
    add("- The TERENCE module (`src/UTILITIES/TERENCE/`) holds marker strings as *suggested "
        "fixes* it prints. Those are fixtures, not debt — a reader who counts them gets a "
        "backlog that cannot be worked.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    counts = Counter({k: len(v) for k, v in groups.items()})
    print("markers:", len(hits), "files:", len({h[0] for h in hits}), "classes:", dict(counts))
    print("markdown marker lines:", md_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
