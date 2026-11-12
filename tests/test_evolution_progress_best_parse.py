# coding: utf-8
"""Regression tests for the `BEST:` line parse in `tools/evolution_progress.py`.

THE DEFECT (measured, engram #569): the reader was

    genes = json.loads(m.group(2).replace("'", '"'))

The producer is `log('BEST: %s' % (best.genes if best else None))`
(`scripts/evolution_cycle.py`) -- a Python `repr(dict)`, not JSON -- and every
payload carries the trial gene as a Python bool (`'by_session': False`). `False`
is not valid JSON, so **all 18** `BEST:` lines in the live log raised
`JSONDecodeError`: `cur["best"]` was always None and every cycle fell out of the
per-schema arm counts.

THE FIX under test: `_parse_best()` tries `json.loads` first (so a producer that
switches to `json.dumps` keeps working) and falls back to `ast.literal_eval`,
raising `ValueError` when the payload is not a dict.

FIXTURES: the log lines below are transcribed VERBATIM from
`logs/evolution_cycle.log` (lines 1, 18, 30 and 49 at the time of writing), which
is the point -- the fixture is the producer's real output, not a shape invented
here. The test never reads the real log at run time; it builds a tiny log in
`tmp_path`. Payloads that exercise `True`/`None` are marked CONSTRUCTED: they
test the parser's unit behaviour, which the transcribed `False` case cannot
cover on its own. Lines marked TRANSCRIBED are byte-for-byte the producer's.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import evolution_progress as ep  # noqa: E402


# --- TRANSCRIBED VERBATIM from logs/evolution_cycle.log ----------------------
CYCLE_1 = "2026-10-09T13:54:45 seed=68485"
BEST_1 = ("2026-10-09T13:54:46 BEST: {'learning_rate': 0.8617469803421666, "
          "'epsilon': 0.3538767667684726, 'discount': 0.5022987194692475, "
          "'epsilon_decay': 0.3, 'by_session': False}")

CYCLE_2 = "2026-10-11T08:28:11 seed=21691"
BEST_2 = ("2026-10-11T08:28:19 BEST: {'learning_rate': 0.8280583123371346, "
          "'epsilon': 0.3796632304215642, 'discount': 0.8180572808255886, "
          "'epsilon_decay': 0.24707305908589502, 'by_session': False}")

BEST_1_PAYLOAD = BEST_1.split("BEST: ", 1)[1]
BEST_2_PAYLOAD = BEST_2.split("BEST: ", 1)[1]

# CONSTRUCTED: same shape as the real payload, other Python literals.
BEST_TRUE_PAYLOAD = ("{'learning_rate': 0.9, 'epsilon': 0.2, "
                     "'discount': 0.8, 'epsilon_decay': 0.25, "
                     "'by_session': True}")
BEST_NONE_PAYLOAD = ("{'learning_rate': 0.9, 'epsilon': None, "
                     "'discount': 0.8, 'epsilon_decay': 0.25, "
                     "'by_session': False}")


@pytest.fixture(autouse=True)
def _clear_parse_failures():
    """`BEST_PARSE_FAILURES` is module-global; a leaked entry fails a later test."""
    ep.BEST_PARSE_FAILURES.clear()
    yield
    ep.BEST_PARSE_FAILURES.clear()


def test_the_old_reader_would_have_failed_on_this_line():
    """Negative control: the fixture must be able to fail the old implementation."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(BEST_1_PAYLOAD.replace("'", '"'))


def test_parse_best_reads_the_producers_real_repr():
    genes = ep._parse_best(BEST_1_PAYLOAD)
    assert isinstance(genes, dict)
    assert genes["learning_rate"] == 0.8617469803421666
    assert genes["by_session"] is False
    assert isinstance(genes["by_session"], bool)


def test_parse_best_keeps_python_bools_and_none():
    """The fallback path must preserve Python literals, not stringify them."""
    assert ep._parse_best(BEST_TRUE_PAYLOAD)["by_session"] is True
    assert ep._parse_best(BEST_NONE_PAYLOAD)["epsilon"] is None


def test_parse_best_still_reads_json():
    """A producer that switches to json.dumps must keep working."""
    payload = json.dumps({"learning_rate": 0.5, "by_session": False})
    assert ep._parse_best(payload) == {"learning_rate": 0.5, "by_session": False}


def test_parse_best_rejects_non_dict():
    with pytest.raises(ValueError):
        ep._parse_best("[1, 2]")            # valid JSON, wrong type
    with pytest.raises(ValueError):
        ep._parse_best("('a',)")            # valid Python literal, wrong type


def test_parse_populates_best_from_the_real_log_lines(tmp_path):
    """End to end through `parse()`: both real cycles keep their genes."""
    csv = tmp_path / "evolution_cycle.log"
    csv.write_text("\n".join([CYCLE_2, BEST_2, CYCLE_1, BEST_1]) + "\n",
                   encoding="utf-8")

    data = ep.parse(str(csv))
    cycles = data["cycles"]
    assert len(cycles) == 2
    for cyc in cycles:
        assert cyc["best"] is not None
        assert cyc["best"]["by_session"] is False
    # The schema comes from the genes when the log carries no `morph:` line, so
    # the trial gene is named -- this is what the failed parse silently lost.
    assert cycles[0]["schema"] == "by_session"
    assert ep.BEST_PARSE_FAILURES == []


def test_parse_records_an_unreadable_best_line(tmp_path):
    """A payload that will not parse at all is counted, not swallowed."""
    log = tmp_path / "evolution_cycle.log"
    log.write_text("\n".join([CYCLE_2, "2026-10-11T08:28:19 BEST: {not a literal}",
                             BEST_2]) + "\n", encoding="utf-8")

    data = ep.parse(str(log))
    assert len(ep.BEST_PARSE_FAILURES) == 1
    assert ep.BEST_PARSE_FAILURES[0].startswith("2026-10-11T08:28:19:")
    # The readable line still lands: one bad line must not drop the others.
    assert data["cycles"][0]["best"] is not None
