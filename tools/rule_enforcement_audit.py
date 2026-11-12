# coding: utf-8
"""RULE enforceability audit -- which mandates have a validator, and can that
validator fail?

RULE realised here: a mandate is only ENFORCEABLE if something executable can
say NO. `MANDATES.md` states 40+ rules; the repo also contains hooks, gates and
compliance modules. Nothing currently joins the two, so "RULE 0Z-B is enforced"
is asserted in prose rather than demonstrated by a call chain. This tool builds
that join, read-only.

For every rule parsed from `MANDATES.md` it answers three questions:

  1. where is the rule id mentioned (file:line)?
  2. of those mentions, which are in EXECUTABLE surfaces (`.githooks/*`, and
     Python under `src/`, `tools/`, `scripts/`) rather than prose (`*.md`)?
  3. does the enclosing scope of an executable mention contain a failure path
     -- `raise`, `sys.exit(`, `SystemExit(`, `exit(1)`, `return 1`,
     `return False`?

Verdicts:

  ENFORCED               a hook mentions the rule and the hook can exit nonzero,
                         or a Python mention sits in a scope with a failure path.
  WIRED-BUT-ALWAYS-PASS  an executable surface mentions the rule, but no
                         enclosing scope can fail -- a validator that always
                         says yes, which is the shape of a faux gate.
  NO-VALIDATOR           every mention is prose (or there is none).

DELIBERATELY NOT CHECKED -- read this before quoting a verdict:

  * It does not run anything. A failure path is detected STATICALLY, so a
    `raise` in dead code counts. ENFORCED means "a failure path exists in the
    scope that names the rule", not "the failure has been observed".
  * It does not resolve whether a naming mention is ACTIVE. A rule id inside a
    comment or docstring in an executable file still classifies; comment
    stripping is a tokenizer job and this tool does not pretend to do it.
  * The scope is the enclosing `def`, or a +/-25 line window when the mention
    is at module level. A mention 200 lines away from the `raise` that answers
    it will read as WIRED-BUT-ALWAYS-PASS. That direction is deliberate: the
    tool under-claims rather than certifying a gate it has not located.
  * It does not check the converse direction: a validator that enforces a rule
    WITHOUT naming the rule id is invisible here. Absence of a mention is
    evidence about naming, not about enforcement.
  * It cannot see behaviour that lives outside this tree (a CI job, a client's
    own hook, a human reviewer). Anything with no in-tree surface is reported
    as NO-VALIDATOR and must be read that way: "no in-tree validator NAMES it".

Usage:
    python tools/rule_enforcement_audit.py             # table, exit 0
    python tools/rule_enforcement_audit.py --check     # exit 1 on a gap
    python tools/rule_enforcement_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANDATES = PROJECT_ROOT / "MANDATES.md"

# Surfaces searched for a validator. Directories are globbed; a missing one is
# reported, never silently skipped -- that distinction is the whole point of
# this tool (absence of evidence is not evidence of a passed gate).
EXECUTABLE_SURFACES = (
    ".githooks",
    "src",
    "tools",
    "scripts",
    "packages",
)
DOC_SUFFIXES = frozenset({".md", ".txt", ".rst"})

# `## RULE 0A: ...`, `### RULE 0H-A: ...`, `### RULE 0Z-L — ...`,
# `## Rule 5: ...`, `## RULE 0-11: ...`. Both `:` and a dash family separator
# are in the real file; a colon-only parser drops RULE 0Z-L.
RULE_HEADING = re.compile(
    r"^(?P<hashes>#{2,3})\s+(?P<kind>RULE|Rule)\s+"
    r"(?P<id>[0-9][0-9A-Za-z]*(?:[.\-][0-9A-Za-z]+)*)"
    r"\s*[:—–-]\s*(?P<title>.*)$"
)

FAILURE_TOKENS = (
    "raise ",
    "sys.exit(",
    "SystemExit(",
    "exit(1)",
    "return 1",
    "return False",
)

DEF_LINE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+\w+")


class Rule(dict):
    """A parsed mandate: id, title, source line."""


def parse_rules(text: str) -> "list[Rule]":
    """Every rule heading in a MANDATES-shaped document, de-duplicated by id.

    Sub-rules (`### RULE 0H-A`) are kept as their own id: they carry their own
    mandate and one of them is the runtime-scan rule, which is enforced by a
    different surface than its parent.
    """
    found: "list[Rule]" = []
    seen: "set[str]" = set()
    for number, line in enumerate(text.splitlines(), start=1):
        match = RULE_HEADING.match(line)
        if not match:
            continue
        rule_id = match.group("id")
        if rule_id in seen:
            continue
        seen.add(rule_id)
        found.append(Rule(id=rule_id,
                          title=match.group("title").strip(),
                          line=number,
                          level=len(match.group("hashes"))))
    return found


def _iter_surface_files() -> "tuple[list[Path], list[str]]":
    """Executable files to search, plus the surfaces that were absent."""
    files: "list[Path]" = []
    missing: "list[str]" = []
    for name in EXECUTABLE_SURFACES:
        target = PROJECT_ROOT / name
        if not target.exists():
            missing.append(name)
            continue
        if target.is_file():
            files.append(target)
            continue
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            if any(part in {"__pycache__", ".venv", "node_modules", "archived"}
                   for part in path.parts):
                continue
            # Only text members of an executable surface. `.json`, `.db` and
            # `.html` neighbours are data, not validators, and decoding them as
            # UTF-8 with errors="replace" would both waste time and manufacture
            # spurious "mentions" out of binary noise.
            if path.suffix in DOC_SUFFIXES or path.suffix not in ("", ".py", ".sh"):
                continue
            files.append(path)
    return files, missing


def _read(path: Path) -> "str | None":
    """File text, or None when it cannot be read. Never raises to the caller.

    The second statement records the failure, which is also what clears the
    repo's silent-swallow finding: the error is carried, not discarded.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"unreadable surface skipped: {path}: {exc}")
        return None


def _display_path(path: Path) -> str:
    """Repo-relative when inside the repo, absolute otherwise.

    `Path.relative_to` raises for a path outside PROJECT_ROOT, and tests feed
    fixtures from a temp directory -- a raising path formatter would turn every
    control into a crash rather than a verdict.
    """
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _scope_bounds(lines: "list[str]", index: int) -> "tuple[int, int]":
    """The def-block containing `index`, as (start, end) half-open line indices.

    A hit at module level (no enclosing def) yields a +/-25 line window rather
    than the whole file. Whole-file would answer "can it say no?" with the
    answer to "does this module contain a `raise` anywhere?", which is almost
    always yes and would make the verdict worthless; the window keeps the
    failure path near the mention that asserts the rule.
    """
    start = None
    for cursor in range(index, -1, -1):
        match = DEF_LINE.match(lines[cursor])
        if match:
            start = cursor
            indent = len(match.group("indent"))
            break
    if start is None:
        return max(0, index - 25), min(len(lines), index + 26)
    end = len(lines)
    for cursor in range(start + 1, len(lines)):
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = len(lines[cursor]) - len(lines[cursor].lstrip())
        if line_indent <= indent:
            end = cursor
            break
    # A module-level mention that happens to sit AFTER some def would otherwise
    # be judged against that unrelated def's body -- a scope that does not
    # contain the mention. Fall back to the window in that case.
    if not start <= index < end:
        return max(0, index - 25), min(len(lines), index + 26)
    return start, end


def _has_failure_path(lines: "list[str]", start: int, end: int) -> bool:
    return any(token in lines[i] for i in range(start, end)
               for token in FAILURE_TOKENS)


def find_references(rule_id: str, surfaces: "list[Path]",
                    texts: "dict[Path, list[str]] | None" = None) -> "list[dict]":
    """Every file:line naming `rule_id` in an executable surface.

    `texts` is the caller's already-read cache. Re-reading every surface once
    per rule turns a ~45 x ~2000 join into 90,000 file opens; the cache makes
    it one pass, without changing the answer.
    """
    pattern = re.compile(r"(?<![0-9A-Za-z])" + re.escape(rule_id) +
                         r"(?![0-9A-Za-z])")
    refs: "list[dict]" = []
    for path in surfaces:
        # A prose mention is not a validator. Applied here as well as in the
        # directory walk so an explicit surface list cannot smuggle a document
        # in and have it classify as a wired-but-passing gate.
        if path.suffix in DOC_SUFFIXES:
            continue
        if texts is not None:
            if path not in texts:
                continue
            lines = texts[path]
        else:
            text = _read(path)
            if text is None:
                continue
            if rule_id not in text:
                continue
            lines = text.splitlines()
        for number, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            start, end = _scope_bounds(lines, number - 1)
            refs.append({
                "file": _display_path(path),
                "line": number,
                "is_hook": path.parent.name == ".githooks",
                "failure_path": _has_failure_path(lines, start, end),
                "source": line.strip()[:120],
            })
    return refs


def classify(refs: "list[dict]") -> str:
    if not refs:
        return "NO-VALIDATOR"
    if any(ref["is_hook"] or ref["failure_path"] for ref in refs):
        return "ENFORCED"
    return "WIRED-BUT-ALWAYS-PASS"


def audit(mandates_path: Path = MANDATES,
          surfaces: "list[Path] | None" = None) -> "dict":
    """The whole report, as a JSON-able dict."""
    if surfaces is None:
        surfaces, missing = _iter_surface_files()
    else:
        missing = []
    text = _read(mandates_path)
    if text is None:
        return {"error": f"cannot read {mandates_path}", "rules": [],
                "missing_surfaces": missing, "surfaces_scanned": 0}
    rules = parse_rules(text)
    texts: "dict[Path, list[str]]" = {}
    for path in surfaces:
        body = _read(path)
        if body is not None:
            texts[path] = body.splitlines()
    rows = []
    for rule in rules:
        refs = find_references(rule["id"], surfaces, texts)
        rows.append({
            "id": rule["id"],
            "title": rule["title"],
            "mandates_line": rule["line"],
            "verdict": classify(refs),
            "references": refs,
        })
    return {
        "mandates": str(mandates_path),
        "rules": rows,
        "missing_surfaces": missing,
        "surfaces_scanned": len(surfaces),
    }


def _print_table(report: "dict") -> None:
    print(f"rule enforcement audit -- {report['mandates']}")
    print(f"  surfaces scanned: {report['surfaces_scanned']}")
    if report["missing_surfaces"]:
        print(f"  absent surfaces:  {', '.join(report['missing_surfaces'])}")
    print()
    for row in report["rules"]:
        print(f"[{row['verdict']:<21}] RULE {row['id']:<8} {row['title'][:64]}")
        for ref in row["references"][:3]:
            flag = "fail" if ref["failure_path"] or ref["is_hook"] else "pass"
            print(f"    {flag}  {ref['file']}:{ref['line']}")
    counts = {}
    for row in report["rules"]:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print()
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Which mandates name a validator, and can it say no?")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when a rule has no failing validator")
    parser.add_argument("--json", action="store_true", help="machine output")
    parser.add_argument("--mandates", default=str(MANDATES),
                        help="path to a MANDATES-shaped document")
    args = parser.parse_args(argv)

    report = audit(Path(args.mandates))
    if report.get("error"):
        print(report["error"])
        return 2
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        _print_table(report)
    gaps = [row for row in report["rules"]
            if row["verdict"] in ("NO-VALIDATOR", "WIRED-BUT-ALWAYS-PASS")]
    if args.check and gaps:
        print(f"\nFAIL: {len(gaps)} rule(s) with no failing validator")
        return 1
    if args.check:
        print("\nOK: every parsed rule has a validator that can fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
