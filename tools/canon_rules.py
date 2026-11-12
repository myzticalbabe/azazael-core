#!/usr/bin/env python3
# coding: utf-8
"""Which RULEs `MANDATES.md` defines -- decided by heading, never by mention.

`MANDATES.md` records, in the 0B-E section, that 0B-A/0B-B/0B-C/0B-D were removed
on 2026-09-30, and two `Related:` pointers near the foot of the same file name
`RULE 0B-B` and `RULE 0B-D` again. Fourteen verses in the canon carry a
`statute` naming one of those two, and both checkers passed every one of them,
because each asked the same question: *does the string appear anywhere in the
file?* A `Related:` pointer answers yes. That is a citation to the memory of a
law, not to the law.

The predicate here is narrower on purpose: a rule exists when a heading declares
it **and** prose follows the heading. Measured against the failure it prevents --
a canon that quotes a repealed rule as though it were in force -- narrow is the
only useful answer. Where a rule is restored, its body is restored with it, and
this module sees the difference without being told.

  python tools/canon_rules.py            # every rule MANDATES.md defines
  python tools/canon_rules.py RULE 0B-D # one rule, and whether it lands
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
MANDATES = REPO / "MANDATES.md"

HEADING_RE = re.compile(r"^(#{2,4})[ \t]+(.*?)[ \t]*$", re.M)
#: `0B`, `0B-D`, `0H-A`, `0E-1`, `0Z-F` -- the shapes canon actually uses.
RULE_ID_RE = re.compile(r"\bRULE\s+([0-9][0-9A-Za-z]*(?:-[0-9A-Za-z]+)*)\b")
#: A heading followed by a pointer is a cross-reference, not a law. The floor is
#: low (one substantive sentence) so that a terse rule is still a rule, but a
#: `**Related:** RULE 0B-D` line on its own cannot clear it.
MIN_BODY_CHARS = 40
#: Lines that carry provenance rather than the rule itself.
_POINTER_PREFIXES = ("**related", "related:", "**see also", "see also:")


def _prose(body: str) -> str:
    return "\n".join(line for line in body.splitlines()
                     if not line.strip().lower().startswith(_POINTER_PREFIXES)).strip()


def rule_blocks(text: str) -> Dict[str, Dict]:
    """Map rule id -> {heading, line, body, chars} for every `## RULE <id>` section."""
    blocks: Dict[str, Dict] = {}
    headings = list(HEADING_RE.finditer(text or ""))
    for index, match in enumerate(headings):
        title = match.group(2)
        found = RULE_ID_RE.match(title)
        if not found:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[match.end():end].strip()
        prose = _prose(body)
        blocks[found.group(1)] = {
            "heading": title,
            "line": text.count("\n", 0, match.start()) + 1,
            "body": body,
            "chars": len(re.sub(r"\s+", "", prose)),
        }
    return blocks


def load(repo: Path = REPO) -> Optional[Dict[str, Dict]]:
    """The defined rules, or None when canon cannot be read (never `{}`)."""
    path = Path(repo) / MANDATES.relative_to(REPO)
    if not path.is_file():
        return None
    return rule_blocks(path.read_text(encoding="utf-8", errors="replace"))


def ids(blocks: Dict[str, Dict]) -> List[str]:
    return sorted(blocks)


def rule_problem(cited: str, blocks: Dict[str, Dict],
                 text: Optional[str] = None) -> Optional[str]:
    """Why a cited rule does not land, or None when it does.

    `text` is optional and only used to tell "mentioned but repealed" apart from
    "never existed" -- the first is the failure this module was written for.
    """
    found = RULE_ID_RE.search(cited or "")
    if not found:
        return "names no RULE (expected something like \"RULE 0B\")"
    rule_id = found.group(1)
    block = blocks.get(rule_id)
    if block is None:
        if text and ("RULE " + rule_id) in text:
            return ("RULE %s is named in MANDATES.md but has no section: the corpus "
                    "cites a rule that was repealed or never written, and a "
                    "`Related:` pointer is not a body" % rule_id)
        return ("RULE %s is not in MANDATES.md; the corpus narrates canon, it does "
                "not legislate" % rule_id)
    if block["chars"] < MIN_BODY_CHARS:
        return ("RULE %s has a heading at MANDATES.md:%d but no body under it; a law "
                "that states nothing cannot be kept" % (rule_id, block["line"]))
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rule", nargs="*", help="a citation to test, e.g. RULE 0B-D")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)

    text = MANDATES.read_text(encoding="utf-8", errors="replace") \
        if MANDATES.is_file() else ""
    blocks = rule_blocks(text)
    if not blocks:
        print("MANDATES.md defines no RULE heading -- nothing to resolve")
        return 1
    if args.rule:
        cited = " ".join(args.rule)
        problem = rule_problem(cited, blocks, text)
        print("lands: %s" % cited if problem is None else "does not land: %s" % problem)
        return 0 if problem is None else 1
    for rule_id in ids(blocks):
        block = blocks[rule_id]
        marker = "" if block["chars"] >= MIN_BODY_CHARS else "  [HEADING ONLY]"
        print("RULE %-8s %5d chars  line %-5d %s%s"
              % (rule_id, block["chars"], block["line"], block["heading"][:60], marker))
    print("\n%d rule(s) defined" % len(blocks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
