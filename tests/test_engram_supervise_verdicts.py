# coding: utf-8
"""The engram supervisor must distinguish outstanding pairs from breakage.

`pending` conflict pairs are created by the store itself (every `mem_save`
spawns candidates for comparison), so treating them as NO-GO refuses every
later tool call until they are judged -- and judging is one of the calls being
refused. That is the unjudged-block defect. `orphaned` relations are a
different thing entirely: the relation's endpoint observation is missing, so
the row is structurally broken and NO-GO is right.

Fixtures are TRANSCRIBED from real output captured on this host 2026-10-15
(`engram conflicts stats --project azazael`, and `conflicts list`). They are not
written to match the parser: the real `stats` payload is the one used verbatim,
including its `By relation type:` block, which is the trap this file pins --
`related: 915` sits in the same document as the judgment counts and must never
be read as one of them.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from engram_supervise import (  # noqa: E402 - after sys.path
    main, verdict_from_conflicts_stats,
)

#: VERBATIM real output, 2026-10-15. Note there is no `pending:` line at all --
#: engram omits a status whose count is zero -- and no `Total:` line, which only
#: `conflicts list` prints.
REAL_STATS_ZERO_PENDING = """\
Conflicts Stats (project: azazael)

  By judgment_status:
    judged:      1069

  By relation type:
    supersedes:          43
    compatible:          87
    related:             915
    scoped:              24

  Deferred:    0
  Dead:        0
"""

#: Shape from the Go source (cmd/engram/conflicts.go:259-291) with counts moved
#: off zero, so the branch that only fires above zero is actually exercised.
SYNTHETIC_PENDING = """\
Conflicts Stats (project: azazael)

  By judgment_status:
    pending:     3
    accepted:    1042
    rejected:    24
    orphaned:    0

  Deferred:    0
  Dead:        0
"""

SYNTHETIC_ORPHANED = """\
Conflicts Stats (project: azazael)

  By judgment_status:
    pending:     0
    judged:      10
    orphaned:    2

  Deferred:    0
  Dead:        0
"""

#: Both blocks populated at once: the number the verdict must use (`pending: 2`)
#: and a decoy from the OTHER block (`related: 915`).
SYNTHETIC_PENDING_BESIDE_RELATION_COUNTS = """\
Conflicts Stats (project: azazael)

  By judgment_status:
    pending:     2
    judged:      1069

  By relation type:
    supersedes:          43
    compatible:          87
    related:             915
    scoped:              24

  Deferred:    0
  Dead:        0
"""

#: VERBATIM real output for a store with nothing pending.
EMPTY_LISTING = """\
Conflicts List (project: azazael)
  Total:  0
  Showing: 0
  No relations found.
"""

#: A bad flag / usage dump: an unrecognized payload must not read as healthy.
UNRECOGNIZED = """\
Usage: engram conflicts <sub> [flags]

  scan      scan for conflicts
"""


def test_the_real_zero_pending_stats_report_a_go():
    verdict, lines = verdict_from_conflicts_stats(REAL_STATS_ZERO_PENDING)
    assert verdict == "GO", lines
    assert "judged=1069" in " ".join(lines), lines


def test_relation_type_counts_never_masquerade_as_judgment_counts():
    """The trap: `related: 915` shares a document with the judgment counts.

    Reading every `key: digit` line would make this store look like it has 915
    outstanding pairs; it has none.
    """
    verdict, lines = verdict_from_conflicts_stats(REAL_STATS_ZERO_PENDING)
    assert verdict == "GO", lines
    assert "915" not in " ".join(lines), lines


def test_pending_pairs_are_clarify_not_no_go():
    verdict, lines = verdict_from_conflicts_stats(SYNTHETIC_PENDING)
    assert verdict == "CLARIFY", lines
    joined = " ".join(lines)
    assert "3" in joined, joined
    assert "judged=1066" in joined, joined


def test_pending_is_read_from_its_own_block_not_the_relation_block():
    verdict, lines = verdict_from_conflicts_stats(
        SYNTHETIC_PENDING_BESIDE_RELATION_COUNTS)
    assert verdict == "CLARIFY", lines
    joined = " ".join(lines)
    assert "2 unjudged" in joined, joined
    assert "915" not in joined, joined


def test_an_orphaned_relation_is_still_a_hard_no_go():
    verdict, lines = verdict_from_conflicts_stats(SYNTHETIC_ORPHANED)
    assert verdict == "NO-GO", lines
    assert "orphaned" in " ".join(lines), lines


def test_an_empty_listing_is_a_go():
    verdict, lines = verdict_from_conflicts_stats(EMPTY_LISTING)
    assert verdict == "GO", lines


def test_unrecognized_output_is_unknown_and_not_a_silent_go():
    verdict, lines = verdict_from_conflicts_stats(UNRECOGNIZED)
    assert verdict == "unknown", lines
    assert "unrecognized" in " ".join(lines), lines


# -- the exit-code contract a caller depends on -----------------------------

def _stub(monkeypatch, conflicts):
    """Replace every probe so main() only exercises its verdict handling."""
    import engram_supervise as sup
    monkeypatch.setattr(sup, "ENGRAM_BIN", sys.executable)
    monkeypatch.setattr(sup, "check_doctor", lambda: ("GO", ["doctor: ok"]))
    monkeypatch.setattr(sup, "check_conflicts", lambda: conflicts)
    monkeypatch.setattr(sup, "check_hook", lambda: ["nudge: none due"])
    monkeypatch.setattr(sup, "check_plan", lambda: ["open plan items: 0"])
    return sup


def test_clarify_exits_zero_so_the_gate_stops_blocking(monkeypatch, capsys):
    """CLARIFY must be advisory: this is what ends the hard block."""
    sup = _stub(monkeypatch, verdict_from_conflicts_stats(SYNTHETIC_PENDING))
    assert sup.main() == 0
    assert "CLARIFY" in capsys.readouterr().out


def test_no_go_still_exits_one(monkeypatch, capsys):
    sup = _stub(monkeypatch, verdict_from_conflicts_stats(SYNTHETIC_ORPHANED))
    assert sup.main() == 1
    assert "NO-GO" in capsys.readouterr().out


def test_a_clean_store_still_exits_zero(monkeypatch, capsys):
    sup = _stub(monkeypatch, verdict_from_conflicts_stats(REAL_STATS_ZERO_PENDING))
    assert sup.main() == 0
    assert "GO" in capsys.readouterr().out
