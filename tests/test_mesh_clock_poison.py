# coding: utf-8
"""Tests for the poisoned-clock defects found while building the monitors.

History, because the shape of these tests only makes sense with it:

The HLC (``CORE/hlc.py``) was added to fix cross-env ordering, and it carries a
drift bound (``MAX_DRIFT_MS``) so that one broken peer clock cannot pin this
host's era. The bound worked for the *ordering* path. It did not work for the
two paths around it, and nothing noticed until
``tools/render_monitors.py`` grew a "Clock Health" card and printed the truth
it had been handed:

    +30682668731722ms of peer clock skew, 24 of 24 stamps refused

Three distinct defects, all reproduced from a real store:

1. **A refused stamp was still reported as skew.** ``observe()`` recorded
   ``_max_remote_physical`` *before* the drift guard, so a refused value became
   "how far ahead our peers are". A live ``~/.azazael/spine_state.json`` held
   98 year-2999 stamps (``032472162000000-04631-windows``), and the reported
   skew was ~972 years — a number no one could distinguish from a real reading.
   Fixed by separating the two signals: ``skew_ms()`` (accepted peers only) and
   ``rejected_skew_ms()`` (the worst refused stamp).

2. **``normalize_stamp()`` guessed the epoch unit.** It multiplied every numeric
   input by 1000 ("seconds"), so a millisecond epoch became a wider value and,
   because the packed field is fixed-width, a *stamp ``unpack_stamp()`` cannot
   read*. ``pack_stamp()`` could likewise emit an over-wide serial. Both are now
   impossible: units are detected by magnitude and the serial is clamped, so
   every stamp this module hands out is unpackable.

3. **A poisoned stamp still won merges and would have frozen records.** The
   fabric field merge compared stamps with a raw ``>``, bypassing the guard, and
   the guard itself only covered the candidate direction. A year-2999 field
   stamp therefore beat every correct peer until real time reached year 2999.
   Fixed in both directions: a beyond-drift candidate never wins, and a
   beyond-drift *incumbent* loses to the first trustworthy write — so a poisoned
   record heals instead of freezing.

The store is the proof, not a fixture: fix #1 was found by reading a real
``~/.azazael/spine_state.json``. These tests reproduce it in isolation.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytestmark = pytest.mark.order

import CORE.hlc as H
from CORE import spine_registry as sr

#: The real poisoned value found in the live store, in milliseconds (~year 2999).
POISON_MS = 32_472_162_000_000
POISON_NODE = "windows"


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    """Keep the suite off the developer's real dot-directory."""
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    sr.spine_reset()
    yield
    sr.spine_reset()


@pytest.fixture(autouse=True)
def clean_shared_clock():
    """Clear the process-wide clock's remote view around each test."""
    H.hlc().reset_remote_view()
    yield
    H.hlc().reset_remote_view()


def _poison(counter: int = 4631) -> str:
    return H.pack_stamp(POISON_MS, counter, POISON_NODE)


def _fresh(offset_ms: int = 0) -> str:
    return H.pack_stamp(int(time.time() * 1000) + offset_ms, 0, "wsl2-ubuntu")


# ── defect 1: a refused stamp is not skew ───────────────────────────

def test_a_refused_stamp_is_reported_separately_from_skew():
    """'Peer runs fast' and 'peer is broken' must not read as the same number."""
    clock = H.HybridLogicalClock(node="windows")
    clock.observe(_poison())
    assert clock.rejected() == 1
    assert clock.skew_ms() == 0, "a refused stamp was reported as clock skew"
    assert clock.rejected_skew_ms() > 300_000

    # A stamp INSIDE the window still moves skew, and is not a refusal.
    clock.observe(_fresh(1200))
    assert clock.rejected() == 1
    assert 0 < clock.skew_ms() < H.MAX_DRIFT_MS
    assert clock.rejected_skew_ms() > clock.skew_ms()


def test_reset_remote_view_clears_the_refused_diagnostic():
    """The operator escape hatch for a store already holding poisoned stamps."""
    clock = H.HybridLogicalClock(node="windows")
    clock.observe(_poison())
    assert clock.rejected_skew_ms() > 0
    before = clock.stamp()
    clock.reset_remote_view()
    assert clock.rejected() == 0
    assert clock.rejected_skew_ms() == 0
    assert clock.skew_ms() == 0
    assert clock.stamp() > before, "a reset must never rewind the clock"


def test_a_refused_stamp_does_not_move_the_local_clock():
    """The original bound, restated because the fix must not weaken it."""
    clock = H.HybridLogicalClock(node="windows")
    returned = clock.observe(_poison())
    assert H.unpack_stamp(returned)[0] < int(time.time() * 1000) + H.MAX_DRIFT_MS


# ── defect 2: every stamp is readable, and the unit is detected ─────

def test_normalize_stamp_detects_the_epoch_unit():
    """s / ms / µs / ns of the SAME instant must normalise to the same instant.

    Tolerance is per-unit on purpose: a seconds-resolution input cannot express
    the 276ms below the second, and inventing precision there would be worse
    than admitting it. The property under test is the UNIT, not the digit.
    """
    expect = 1_789_493_268_276  # ms
    cases = [("1789493268", 1000),          # seconds  -> whole second only
             ("1789493268276", 1),           # milliseconds
             ("1789493268276000", 1),        # microseconds
             ("1789493268276000000", 1)]     # nanoseconds
    for raw, tol in cases:
        got = H.unpack_stamp(H.normalize_stamp(raw))
        assert got is not None, f"{raw} normalised to an unpackable stamp"
        assert abs(got[0] - expect) <= tol, f"{raw} -> {got[0]}ms (tol {tol})"


def test_pack_stamp_cannot_emit_an_unpackable_stamp():
    """The packed field is fixed-width; an over-wide serial must clamp.

    Before the clamp, ``pack_stamp(10**20, 0, "x")`` printed 21 digits and
    ``unpack_stamp`` returned ``None`` — a stamp that is neither ordered nor
    recognised by the module that created it.
    """
    for value in (0, 1, H.MAX_PHYSICAL, H.MAX_PHYSICAL + 1, 10 ** 20, -5):
        packed = H.pack_stamp(value, 0, "x")
        assert H.unpack_stamp(packed) is not None, f"{value} produced {packed}"


def test_normalize_stamp_output_is_always_unpackable():
    """The function's contract is 'coerce into an HLC stamp' — so it must be one."""
    for raw in ("1789493268276", "32472162000", "", "not a time", "0",
                "9" * 40, "2026-09-15T11:53:12", "001789493268276-00012-windows"):
        assert H.unpack_stamp(H.normalize_stamp(raw)) is not None, raw


# ── defect 3: a poisoned stamp cannot win, and can be healed ────────

def test_ordering_refuses_a_beyond_drift_stamp_in_both_directions():
    """A poisoned candidate loses; a poisoned incumbent loses too."""
    poison, fresh, older = _poison(), _fresh(), _fresh(-60_000)

    assert sr._stamp_newer(fresh, older) is True         # normal ordering intact
    assert sr._stamp_newer(older, fresh) is False
    assert sr._stamp_newer(poison, fresh) is False, "poison won as candidate"
    assert sr._stamp_newer(poison, older) is False
    assert sr._stamp_newer(fresh, poison) is True, "a poisoned record cannot be displaced"
    assert sr._stamp_newer(poison, poison) is False
    # A raw string compare is what used to decide this, and it hands the win to
    # year 2999 for the next 973 years.
    assert poison > fresh


def test_an_absent_candidate_never_wins_even_against_a_poisoned_incumbent():
    """``normalize_stamp("")`` is a valid zero stamp, so absence is judged raw."""
    for empty in ("", "   ", None):
        assert sr._stamp_newer(empty, _poison()) is False


def test_a_correct_write_heals_a_poisoned_field_stamp():
    """End-to-end through the fabric merge, which is how the poison arrived."""
    sr.publish_fabric_state("MeshRouterWorm", {"_routes": {"mesh>mesh": {"hops": 1}}})
    # Simulate the legacy store: the field keeps a year-2999 ordering stamp.
    rec = sr._FABRIC_STATE["MeshRouterWorm"]
    rec["field_stamps"]["_routes"] = _poison()
    rec["hlc"] = _poison()
    rec["updated_at"] = "2999-01-01T00:00:00"

    merged = sr._merge_fabric({"MeshRouterWorm": {
        "state": {"_routes": {"windows>mesh": {"hops": 2}}},
        "field_stamps": {"_routes": _fresh()},
        "updated_at": "2026-09-15T13:00:00",
    }})
    assert merged >= 1, "a trustworthy write could not displace a poisoned stamp"
    assert sr.get_fabric_state("MeshRouterWorm")["_routes"] == {"windows>mesh": {"hops": 2}}


def test_a_poisoned_remote_field_cannot_overwrite_a_local_one():
    """The same merge, poisoned on the incoming side: the local value survives."""
    sr.publish_fabric_state("Lilith", {"_term": 7})
    sr._merge_fabric({"Lilith": {
        "state": {"_term": 9999},
        "field_stamps": {"_term": _poison()},
        "updated_at": "2999-01-01T00:00:00",
    }})
    assert sr.get_fabric_state("Lilith")["_term"] == 7


def test_purge_repairs_poisoned_stamps_without_touching_anything_by_default():
    """The operator tool for a store that already holds poisoned stamps.

    Refusal makes them harmless; nothing removes them, so they would ride in
    every gossip payload forever. Repair (not delete) is what keeps the record's
    relative age intact — a record with no stamp loses its next merge.
    """
    sr.record_spine_node("windows", "PoisonWorm", {"q": 1})
    sr._HOMEOSTAT["mesh:PoisonWorm"] = {
        "successes": 1, "failures": 0,
        "last_at": "2026-09-15T12:00:00", "hlc": _poison()}
    sr.publish_fabric_state("PoisonedFabricWorm", {"n": 1})
    frec = sr._FABRIC_STATE["PoisonedFabricWorm"]
    frec["hlc"] = _poison()
    frec["field_stamps"]["n"] = _poison()
    frec["updated_at"] = "2026-09-15T12:00:00"

    dry = sr.purge_implausible_stamps(dry_run=True)
    assert dry["repaired"] >= 3, dry
    assert sr._FABRIC_STATE["PoisonedFabricWorm"]["hlc"] == _poison(), \
        "a dry run modified the store"

    done = sr.purge_implausible_stamps(dry_run=False)
    assert done["repaired"] >= 3
    assert sr._FABRIC_STATE["PoisonedFabricWorm"]["hlc"] != _poison()
    assert sr._stamp_adoptable(sr._FABRIC_STATE["PoisonedFabricWorm"]["hlc"])
    assert sr._stamp_adoptable(frec["field_stamps"]["n"])
    assert sr._stamp_adoptable(sr._HOMEOSTAT["mesh:PoisonWorm"]["hlc"])
    # Idempotent: a second pass has nothing left to repair.
    assert sr.purge_implausible_stamps(dry_run=False)["repaired"] == 0


# ── alerting on clock trust (audit §5 item 5) ───────────────
#
# Clock trust became measurable on 2026-09-15 and the monitor renders it, but
# nothing *alerted* on it, so a broken peer clock was visible only to somebody
# already looking at the dashboard. The two signals are alerted separately:
# accepted skew (a peer runs fast) and refusals (a peer cannot be ordered
# against at all).

def _capture_alerts(monkeypatch):
    """Replace the Slack send with a recorder, so no test can reach the network."""
    import CORE.notifier as notifier

    sent: list = []
    monkeypatch.setattr(notifier, "send_slack_alert",
                        lambda msg, url=None, title="": sent.append((title, msg)) or True)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("AZAZAEL_SLACK_WEBHOOK", raising=False)
    monkeypatch.delenv("AZAZAEL_CLOCK_SKEW_ALERT_MS", raising=False)
    monkeypatch.delenv("AZAZAEL_CLOCK_REJECT_ALERT", raising=False)
    return notifier, sent


def test_a_healthy_clock_alerts_nothing(monkeypatch):
    notifier, sent = _capture_alerts(monkeypatch)
    H.hlc().reset_remote_view()
    assert notifier.check_clock_and_alert() == 0
    assert sent == []


def test_a_refused_stamp_raises_a_clock_trust_alert(monkeypatch):
    notifier, sent = _capture_alerts(monkeypatch)
    H.hlc().reset_remote_view()
    H.hlc().observe(_poison())
    assert notifier.check_clock_and_alert() == 1
    assert len(sent) == 1
    title, msg = sent[0]
    assert "Clock Trust" in title
    assert "refused" in msg
    # The remedy must be in the alert, not left to the reader to find.
    assert "purge_implausible_stamps" in msg


def test_accepted_skew_above_the_threshold_alerts(monkeypatch):
    notifier, sent = _capture_alerts(monkeypatch)
    H.hlc().reset_remote_view()
    H.hlc().observe(_fresh(120_000))   # 2 min ahead, inside the 300s bound
    assert notifier.check_clock_and_alert() == 1
    assert any("Clock Skew" in title for title, _ in sent)
    assert notifier.check_clock_and_alert.__module__ == "CORE.notifier"


def test_clock_alert_thresholds_are_configurable(monkeypatch):
    notifier, sent = _capture_alerts(monkeypatch)
    monkeypatch.setenv("AZAZAEL_CLOCK_SKEW_ALERT_MS", "600000")
    H.hlc().reset_remote_view()
    H.hlc().observe(_fresh(120_000))
    assert notifier.check_clock_and_alert() == 0, "threshold override ignored"
    assert sent == []


def test_clock_check_is_part_of_the_notifier_surface():
    import CORE.notifier as notifier

    assert "check_clock_and_alert" in notifier.__all__
    with open(notifier.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert "check_clock_and_alert(webhook_url)" in src, \
        "the clock check is not wired into run_check's cycle"


def test_a_refused_remote_field_still_counts_as_a_refusal():
    """Refusal must be visible, not silent: the card has to be able to say why."""
    H.hlc().reset_remote_view()
    sr._merge_fabric({"Lilith": {
        "state": {"_term": 9999},
        "field_stamps": {"_term": _poison()},
        "updated_at": "2999-01-01T00:00:00",
    }})
    assert H.hlc().rejected() >= 1
    assert H.hlc().rejected_skew_ms() > 300_000
    assert H.hlc().skew_ms() == 0
