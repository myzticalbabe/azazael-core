"""The one description of an `evolution_cycle.log` line.

Four tools read `logs/evolution_cycle.log` and had each grown their own regex
literals. Five of those literals were byte-identical copies and would drift the
moment the producer changed its wording, so they live here instead: one
definition, named once, with the consumers recorded next to it.

WHY THERE ARE TWO FAMILIES, AND WHY THEY MUST STAY SEPARATE
-----------------------------------------------------------
The analyst tools (`evolution_progress`, `fitness_arm_liveness`,
`process_benchmark`) match a line from position 0 and capture the worm's own
source token as group 1:

    RE_CYCLE = r"^(\\S+) seed=(\\d+)"      ->  m.group(1) is the subject

The renderer (`render_ouroboros`) scans the same text for substrings and does
NOT capture the subject, so its group numbering is shifted by one:

    RE_CYCLE_SUB = r" seed="               ->  no groups at all

Folding one into the other would silently renumber capture groups and hand
every consumer the wrong field. They are kept apart deliberately; the `_SUB`
suffix marks the substring family. Where a pattern is truly identical in both
families it is defined once (see RE_MORPH, which the renderer used to call
RE_ALT).

WHY iter_lines
--------------
All four consumers open the log as
`open(path, encoding="utf-8", errors="replace")` and iterate lines. That is a
single shared behaviour with three chances to diverge, so it is a function.

Run `python tools/evolution_log.py` to check every pattern against the real log
and confirm the two families agree on the lines they both claim to match.
"""

from __future__ import annotations

import os
import re

# --------------------------------------------------------------------------
# Anchored family - `^(\\S+) ...`, used with .match(); group 1 is the subject.
# --------------------------------------------------------------------------

# evolution_progress, process_benchmark
RE_CYCLE = re.compile(r"^(\S+) seed=(\d+)")

# evolution_progress, fitness_arm_liveness, process_benchmark,
# render_ouroboros (there as RE_ALT, via .search(); "^" without MULTILINE is
# start-of-string, so on one line it is the same test as .match()).
RE_MORPH = re.compile(r"^(\S+) morph: cycle \d+ adds gene (\S+)")

# evolution_progress
RE_STRIP = re.compile(r"^(\S+) morph: stripped prior extra genes from (\d+)")

# evolution_progress, process_benchmark
RE_BEST = re.compile(r"^(\S+) BEST: (\{.*\})")

# process_benchmark, fitness_arm_liveness - the short form of "promotion
# standing". evolution_progress needs the trailing sprt fields, so it uses
# RE_PROMO_SPRT instead; both are the same line, read to different depth.
RE_PROMO = re.compile(
    r"^(\S+) promotion standing: beats=(True|False) margin=([-\d.]+)")

# evolution_progress - full standing line, 9 groups.
RE_PROMO_SPRT = re.compile(
    r"^(\S+) promotion standing: beats=(True|False) margin=([-\d.]+) "
    r"sprt=(\w+)\(n=(\d+) llr=([-\d.]+) sigma=([-\d.]+)\) streak=(\d+)/(\d+)")

# evolution_progress
RE_QD = re.compile(r"^(\S+) qd archive: .*?\(\+(\d+) this cycle\)")

# evolution_progress, process_benchmark
RE_CONV = re.compile(r"^(\S+) converged at gen (\d+)")

# evolution_progress
RE_PROMOTED = re.compile(r"^(\S+) (?:PROMOTED|promotion committed|"
                         r"promote: wrote|champion promoted)")

# process_benchmark
RE_AB = re.compile(
    r"^(\S+) morph A/B: (\S+) default\((\w+)\)=([-\d.]+) "
    r"treatment\((\w+)\)=([-\d.]+) delta=([-\d.]+)")

# fitness_arm_liveness
RE_PARTS = re.compile(
    r"^(\S+) BEST parts: total=([-\d.]+) info=([-\d.]+) alert=([-\d.]+)")

# fitness_arm_liveness
RE_POOLS = re.compile(r"^(\S+) frozen pools: info=(\d+) alert=(\d+)")


# --------------------------------------------------------------------------
# Substring family - used by render_ouroboros with .search(); no subject
# capture, so group numbering starts one field later than the family above.
# --------------------------------------------------------------------------

RE_CYCLE_SUB = re.compile(r" seed=")
RE_MORPH_SUB = re.compile(r"morph: cycle \d+ adds gene (\S+)")
RE_QD_SUB = re.compile(r"qd archive: .*?\(\+(\d+) this cycle\)")

# Truncated on purpose: the renderer only needs n, not llr/sigma/streak.
RE_PROMO_SUB = re.compile(r"promotion standing: beats=(True|False) margin=([-\d.]+) "
                          r"sprt=(\w+)\(n=(\d+)")


def iter_lines(path):
    """Yield the log's lines exactly as every consumer already reads them.

    The four consumers each carried their own copy of
    `for line in open(path, encoding="utf-8", errors="replace")`.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        yield from fh


def main() -> int:
    """Check every pattern against the real log. Prints, never mutates."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log = os.path.join(root, "logs", "evolution_cycle.log")
    if not os.path.exists(log):
        print("log not found: %s" % log)
        return 1

    rows = [(n, v) for n, v in sorted(globals().items())
            if n.startswith("RE_") and isinstance(v, re.Pattern)]
    hits = {n: 0 for n, _ in rows}
    promoted_hits = {"RE_PROMO": 0, "RE_PROMO_SPRT": 0, "RE_PROMO_SUB": 0}
    morph_hits = {"RE_MORPH": 0, "RE_MORPH_SUB": 0}

    total = 0
    for line in iter_lines(log):
        total += 1
        for name, rx in rows:
            if rx.search(line) if name.endswith("_SUB") or name == "RE_ALT" else rx.match(line):
                hits[name] += 1
                if name in promoted_hits:
                    promoted_hits[name] += 1
                if name in morph_hits:
                    morph_hits[name] += 1

    print("log: %s (%d lines)\n" % (log, total))
    for name, rx in rows:
        print("  %-16s %6d  %s" % (name, hits[name], rx.pattern[:58]))

    # The families must agree on the lines they both claim. A short-form match
    # is only trustworthy if the full-depth patterns found the same lines.
    ok = True
    if morph_hits["RE_MORPH"] != morph_hits["RE_MORPH_SUB"]:
        print("\n  ! morph families disagree: %s" % morph_hits)
        ok = False
    nonzero = [h for h in promoted_hits.values() if h]
    if len(set(nonzero)) > 1:
        print("\n  ! promotion-standing families disagree: %s" % promoted_hits)
        ok = False

    print("\n  agreement: %s" % ("ok" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
