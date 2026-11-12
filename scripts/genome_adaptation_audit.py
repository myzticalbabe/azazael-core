#!/usr/bin/env python3
# coding: utf-8
"""GENOME ADAPTATION AUDIT -- is the L1 wiring search going anywhere?

`l1` reports a genome, a generation and a fitness every cycle. That is a report
about a *file*. This audit asks the three questions that separate an adaptive
search from a rewrite:

  1. is the generation ADVANCING (not just the file being rewritten)?
  2. WHICH AXES are being searched -- the genome declares four (`active`,
     `priority`, `budget_ms`, `cooldown_s`) and a mutation operator existing is
     not an axis being exercised;
  3. does the promoted genome change the dispatch order that actually gets
     executed? The L1 effector is `decide_execution_order`, which demotes every
     organ absent from `active` to the tail; if `active` covers the whole
     registry, the observable effect is exactly zero and the loop is maintaining
     a file rather than steering the fleet.

A fourth question is answered too, because the other three are meaningless
without it: **can the objective the search is scored against move at all?**
Measured 2026-09-21 it could not -- and this report still said
`ADAPTING_PARTIAL`, which is why `NOT_LEARNING_OBJECTIVE_PINNED` now exists.

Question 3 is measured as a real A/B through the production ranking code, once
with the genome loaded and once with it resolved away -- never by re-implementing
the ranking key here, because two copies of one formula is how telemetry comes to
disagree with the thing it describes. Both arms run with `record=False`: an audit
asking a hypothetical must not write `spawn_selected` rows into the ledger whose
entire purpose is to join decisions to executions.

Read-only. Never writes genome or lineage state. Exit code is 0 for any
successful measurement -- a MAINTENANCE verdict is a finding, not an error.

    python scripts/genome_adaptation_audit.py
    python scripts/genome_adaptation_audit.py --json
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from CORE import l1_wiring_audit as wiring_audit  # noqa: E402


def _print(report):
    p = report.get("promoted") or {}
    d = report.get("dimensions") or {}
    h = report.get("history") or {}
    e = report.get("effector") or {}
    dims = d.get("values") or {}

    print("genome adaptation audit  (%s)" % report.get("at"))
    print("verdict: %s" % report.get("verdict"))
    print()
    print("GENOME")
    print("  fingerprint      %s" % p.get("fingerprint"))
    print("  generation       %s   advanced=%s  differs_from_parent=%s"
          % (p.get("generation"), p.get("generation_advanced"),
             p.get("differs_from_parent")))
    print("  parent           %s" % p.get("parent"))
    print("  origin           %s" % p.get("origin"))
    print("  paired_delta     %s" % p.get("paired_delta"))
    print("  promoted_at      %s" % p.get("promoted_at"))
    print()
    print("AXES (searched vs merely declared)")
    print("  active=%s  priority=%s  cooldown_s=%s  budget_ms=%s"
          % (dims.get("active"), dims.get("priority"),
             dims.get("cooldown_s"), dims.get("budget_ms")))
    print("  live      %s" % (d.get("live") or []))
    print("  excluded  %s   (operator exists, objective blind to it)"
          % (d.get("excluded") or []))
    print("  unwired   %s   (no operator at all)"
          % (d.get("unwired") or []))
    m = report.get("margin") or {}
    print()
    print("EFFECT FLOOR (can the gate be cleared at all?)")
    print("  latest margin=%s  max best_delta=%s  headroom=%s"
          % (m.get("latest_margin"), m.get("max_best_delta"), m.get("headroom")))
    print("  cycles that could clear it=%s of %s measured (unmeasured %s)"
          % (m.get("reachable_rows"), m.get("samples"), m.get("unmeasured_rows")))
    if m.get("blocked"):
        print("  BLOCKED: no cycle has ever produced a delta above the floor")
    print()
    print("PROMOTION HISTORY (from the lineage ledger, not the genome)")
    print("  l1 rows=%s  promotions=%s  rejections=%s"
          % (h.get("rows"), h.get("promotions"), h.get("rejections")))
    print("  could-promote=%s  dry-evaluations=%s  pre-field(unknown)=%s"
          % (h.get("applied_rows"), h.get("dry_rows"),
             h.get("unknown_apply_rows")))
    print("  first=%s" % h.get("first_at"))
    print("  last =%s" % h.get("last_at"))
    print("  last promotion=%s" % h.get("last_promoted_at"))
    for reason, n in sorted((h.get("by_reason") or {}).items(),
                           key=lambda kv: -kv[1]):
        print("    %4d  %s" % (n, reason))
    print()
    print("EFFECTOR (does the genome change what gets executed?)")
    print("  registry=%s  demoted_organs=%s"
          % (e.get("registry_size"), e.get("demoted_organs")))
    print("  natures with a choice=%s  reordered=%s  first-slot changed=%s"
          % (e.get("natures_with_choices"), e.get("natures_reordered"),
             e.get("first_slot_changes")))
    print("  positions changed=%s" % e.get("positions_changed"))
    for ex in e.get("examples") or []:
        print("    %-16s %s -> %s  (%s positions)"
              % (ex.get("nature"), ex.get("without_genome_first"),
                 ex.get("with_genome_first"), ex.get("positions_changed")))
    if not e.get("ok"):
        print("  MEASUREMENT FAILED: %s" % e.get("reason"))

    # The one question that makes every other verdict meaningful. Measured
    # 2026-09-21: this objective was a mathematical constant (0.65) for the whole
    # life of the loop, so every promotion was a random walk and this report
    # still said ADAPTING_PARTIAL.
    o = report.get("objective") or {}
    movable = o.get("movable_terms") or []
    print()
    print("OBJECTIVE (can the outer loop rank two candidates at all?)")
    print("  value=%s  selectable=%s  canary_tolerance=%s"
          % (o.get("value"), o.get("reachable"), o.get("canary_tolerance")))
    for k, v in sorted((o.get("terms") or {}).items()):
        print("    %-12s %-10s %s"
              % (k, v, "movable" if k in movable else "PINNED"))
    if o.get("unmeasured_terms"):
        print("    %-12s %-10s %s"
              % (",".join(o.get("unmeasured_terms")), "-", "UNMEASURED"))
    print("  reason        %s" % o.get("reason"))
    for w in (o.get("warnings") or []):
        print("  warning       %s" % w)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable report only")
    ap.add_argument("--compact", action="store_true",
                    help="the loop-stage subset of the report")
    args = ap.parse_args()

    report = wiring_audit.audit()
    if args.compact:
        print(json.dumps(wiring_audit.compact(report), sort_keys=True))
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
