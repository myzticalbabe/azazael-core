# coding: utf-8
"""Tests for the cross-env convergence deep dive (2026-09-15).

The mesh's cross-env agreement layer decided "who wins" by comparing
``datetime.now().isoformat()`` strings. That is naive local time compared
lexicographically, which failed in three ways (none of them detectable):

1. No timezone — an env on UTC-8 and one on UTC+0 emit strings that look
   comparable and are hours apart, so the newer write loses.
2. No clock trust — a peer whose clock runs fast wins every tie forever.
3. No causality — a value derived from a peer's state is not ordered after
   that state, so a read-modify-write loses its own update.

``CORE/hlc.py`` supplies a hybrid logical clock (Kulkarni et al., 2014) and
the spine stores an ``hlc`` sidecar next to every human-readable timestamp.
These tests pin the three properties that were missing, plus the migration
behaviour (old ISO-only records must still order correctly) and the
per-field merge that makes two envs publishing disjoint fields converge to
the union instead of clobbering each other.

Spine state is isolated to a tmp dir, so the developer's real
``~/.azazael/spine_state.json`` is never read or written by this file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytestmark = pytest.mark.order

import CORE.hlc as H
from CORE import spine_registry as sr


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
    """Clear the process-wide clock's remote view between tests.

    The spine's clock is a singleton (one monotone sequence for every store),
    so a test that observes a far-future stamp would otherwise shift the
    starting point of every later test in the process. The drift bound below
    is what makes this safe in production; this keeps the suite deterministic.
    """
    H.hlc().reset_remote_view()
    yield
    H.hlc().reset_remote_view()


class _FakeTime:
    """Stand-in for the ``time`` module so a clock can be shifted per-test."""

    def __init__(self, now: float):
        self._now = now

    def time(self) -> float:  # noqa: D102 - module surface used by hlc
        return self._now


# ── the primitive: monotone, causal, deterministic ──────────────────

def test_hlc_is_monotone_when_the_wall_clock_goes_backwards(monkeypatch):
    """An NTP step / DST / suspend-resume must not invert ordering."""
    clock = H.HybridLogicalClock(node="windows")
    real = H.time.time()

    monkeypatch.setattr(H, "time", _FakeTime(real))
    stamps = [clock.stamp() for _ in range(5)]

    # The wall clock jumps eight hours BACKWARDS mid-sequence.
    monkeypatch.setattr(H, "time", _FakeTime(real - 8 * 3600))
    stamps.extend(clock.stamp() for _ in range(5))

    assert stamps == sorted(stamps), "clock returned a stamp it had passed"
    assert len(set(stamps)) == len(stamps), "clock returned a duplicate stamp"


def test_hlc_causality_beats_wall_clock_skew(monkeypatch):
    """A write derived from a peer's state is ordered AFTER that state.

    This is the property a bare wall clock cannot provide, and therefore the
    one that made the old ISO comparison invertable. The drift window is
    widened here so the test isolates causality from the bound exercised in
    ``test_observe_refuses_to_adopt_a_far_future_peer_clock``.
    """
    real = H.time.time()
    wide = 24 * 3600_000
    first = H.HybridLogicalClock(node="windows")
    second = H.HybridLogicalClock(node="bare")

    # First env's clock reads eight hours AHEAD of true time.
    monkeypatch.setattr(H, "time", _FakeTime(real + 8 * 3600))
    first_stamp = first.stamp()

    # Second env's clock reads eight hours BEHIND. It observes the first
    # env's write (a gossip merge) and then writes its own derived update.
    monkeypatch.setattr(H, "time", _FakeTime(real - 8 * 3600))
    second.observe(first_stamp, max_drift_ms=wide)
    second_stamp = second.stamp()

    assert second_stamp > first_stamp, "causality lost to clock skew"

    # Show the inversion the old comparison produced: on wall-clock strings
    # the *second*, later write looks eight hours older.
    older_looking = "2026-09-15T10:00:00"   # second env, UTC+0, wrote second
    newer_looking = "2026-09-15T18:00:00"   # first env, UTC-8, wrote first
    assert older_looking < newer_looking, "naive ISO compares backwards here"


def test_hlc_packs_lexicographically():
    """The packed form must sort the same as the triple it encodes."""
    triples = [(1, 0, "a"), (1, 1, "a"), (2, 0, "a"),
               (2, 0, "b"), (10, 3, "z"), (999, 99999, "z")]
    packed = [H.pack_stamp(*t) for t in triples]
    assert packed == sorted(packed)
    assert [H.unpack_stamp(p) for p in packed] == triples


def test_hlc_tie_break_is_deterministic_between_nodes():
    """Two envs stamping the same millisecond still agree on the winner."""
    a = H.HybridLogicalClock(node="windows")
    b = H.HybridLogicalClock(node="bare")
    a._physical = b._physical = 1_700_000_000_000
    a._counter = b._counter = 0
    assert a.peek() != b.peek()
    assert max(a.peek(), b.peek()) == H.pack_stamp(1_700_000_000_000, 0, "windows")


def test_normalize_stamp_accepts_iso_epoch_and_hlc():
    """The migration must be total: anything maps to a comparable stamp."""
    already = H.pack_stamp(1_700_000_000_000, 7, "windows")
    assert H.normalize_stamp(already) == already          # idempotent
    iso = H.normalize_stamp("2026-09-15T10:00:00")
    epoch = H.normalize_stamp("1700000000")
    assert H.unpack_stamp(iso) is not None
    assert H.unpack_stamp(epoch) is not None
    assert H.unpack_stamp(epoch)[0] == 1_700_000_000_000
    # Anything unusable becomes the epoch, never raises.
    assert H.unpack_stamp(H.normalize_stamp("not a time"))[0] == 0
    assert H.unpack_stamp(H.normalize_stamp(""))[0] == 0


def test_clock_skew_is_measurable():
    """Skew used to be invisible, so divergence had no diagnosis."""
    clock = H.HybridLogicalClock(node="windows")
    assert clock.skew_ms() == 0
    far_future = H.pack_stamp(int(H.time.time() * 1000) + 60_000, 0, "bare")
    clock.observe(far_future)
    assert clock.skew_ms() > 0
    assert clock.observations() == 1
    # A diagnostic reset must NOT rewind the logical clock.
    before = clock.stamp()
    clock.reset_remote_view()
    assert clock.skew_ms() == 0
    assert clock.stamp() > before


def test_observe_refuses_to_adopt_a_far_future_peer_clock():
    """One broken clock must not pin this host's clock to its era.

    Without the bound, observing a year-2999 stamp makes every subsequent
    local stamp year-2999 too, so the host's own writes look newer than
    everything for years and a correct peer's later write loses to them.
    """
    real = H.time.time()
    clock = H.HybridLogicalClock(node="windows")
    far_future = H.pack_stamp(int(real * 1000) + 900 * 365 * 86_400_000,
                             0, "broken-peer")
    returned = clock.observe(far_future)
    # The observation is still recorded — but NOT as skew. A refused stamp is
    # reported through its own accessor, because reporting it as skew made the
    # number meaningless: a live store carrying 98 year-2999 stamps reported
    # +30,682,668,693,692ms (~972 years) as "how far ahead our peers are",
    # forever, and that is indistinguishable from a real measurement.
    assert clock.skew_ms() == 0
    assert clock.rejected_skew_ms() > 0
    assert clock.rejected() == 1
    # ...but the local clock stays in the present, not the peer's era.
    assert H.unpack_stamp(returned)[0] < int(H.time.time() * 1000) + H.MAX_DRIFT_MS

    # Skew within the window IS adopted (real, if large, disagreement).
    mild = H.pack_stamp(int(H.time.time() * 1000) + 60_000, 0, "peer")
    assert H.unpack_stamp(clock.observe(mild)) >= H.unpack_stamp(mild)
    assert clock.rejected() == 1
    assert clock.observations() == 2


def test_observe_infers_causality_from_a_legacy_timestamp():
    """An ISO-only record from before the upgrade still orders correctly.

    This is the real migration case: a peer (or a disk snapshot) writes
    ``datetime.now().isoformat()`` and nothing else. It must slot into the
    same ordering as new stamps rather than being skipped.
    """
    from datetime import datetime

    clock = H.HybridLogicalClock(node="windows")
    legacy_ms = int(H.time.time() * 1000) - 60_000  # one minute ago
    legacy = datetime.fromtimestamp(legacy_ms / 1000).isoformat()
    fresh = clock.observe(legacy)
    assert H.unpack_stamp(fresh) > H.unpack_stamp(H.normalize_stamp(legacy))


# ── the store: per-field convergence ────────────────────────────────

def test_fabric_state_converges_as_a_field_union():
    """Two envs publishing DISJOINT fields must end up with both.

    This is the headline data-loss fix: the record used to be replaced
    wholesale, so a peer with a newer stamp erased the other env's fields —
    which is why a catalogue meant to hold two envs' shards held one.
    """
    sr.publish_fabric_state("SharedWorm", {"_lease_env": "windows"})
    peer_stamp = sr._hlc_now()  # strictly newer than the publish above
    sr._merge_fabric({"SharedWorm": {
        "provider": "SharedWorm",
        "updated_at": "2999-01-01T00:00:00",
        "hlc": peer_stamp,
        "state": {"_genome_version": 4},
        "field_stamps": {"_genome_version": peer_stamp},
    }})
    state = sr.get_fabric_state("SharedWorm")
    assert state["_lease_env"] == "windows", "peer erased our field"
    assert state["_genome_version"] == 4, "peer's field did not arrive"


def test_fabric_merge_is_idempotent():
    sr.publish_fabric_state("IdemWorm", {"n": 1})
    peer = {"provider": "IdemWorm", "updated_at": "2999-01-01T00:00:00",
            "hlc": sr._hlc_now(), "state": {"n": 9},
            "field_stamps": {"n": sr._hlc_now()}}
    first = sr._merge_fabric({"IdemWorm": peer})
    second = sr._merge_fabric({"IdemWorm": peer})
    assert first >= 1
    assert second == 0, "re-merging the same payload must be a no-op"
    assert sr.get_fabric_state("IdemWorm")["n"] == 9


def test_legacy_record_without_a_sidecar_still_orders_correctly():
    """Old ISO-only records must not lose (or win) wrongly after upgrade.

    The window is what matters. A near-now ISO from before the sidecar existed
    must participate in ordering on its merits — newer wins, older loses — and
    that is the whole migration guarantee. A far-future ISO does NOT get to win:
    year 2999 is a broken clock whichever format it is written in, so it is
    refused exactly like a far-future packed stamp. (This test originally used
    year 2999 to mean "newer than everything", which is how the frozerecord bug
    went unnoticed — see `test_mesh_clock_poison.py`.)
    """
    from datetime import datetime, timedelta

    sr.publish_fabric_state("LegacyWorm", {"n": 1})
    newer_iso = (datetime.now() + timedelta(seconds=2)).isoformat()
    sr._merge_fabric({"LegacyWorm": {
        "provider": "LegacyWorm", "updated_at": newer_iso,
        "state": {"n": 7}}})
    assert sr.get_fabric_state("LegacyWorm")["n"] == 7, "legacy ISO in the window lost"
    sr._merge_fabric({"LegacyWorm": {
        "provider": "LegacyWorm", "updated_at": "1999-01-01T00:00:00",
        "state": {"n": 5}}})
    assert sr.get_fabric_state("LegacyWorm")["n"] == 7, "stale legacy won"
    # A legacy ISO beyond the drift window is refused, in either format.
    sr._merge_fabric({"LegacyWorm": {
        "provider": "LegacyWorm", "updated_at": "2999-01-01T00:00:00",
        "state": {"n": 9}}})
    assert sr.get_fabric_state("LegacyWorm")["n"] == 7, "year-2999 legacy ISO won"


def test_field_stamps_survive_a_disk_roundtrip():
    """Convergence must not reset on restart."""
    sr.publish_fabric_state("RTWorm", {"a": 1})
    assert sr.save_spine_state()["saved"] is True
    sr.spine_reset()
    sr.load_spine_state()
    rec = sr.get_fabric_record("RTWorm")
    assert rec is not None
    assert rec["field_stamps"].get("a"), "per-field stamp was not persisted"


def test_reloaded_stamps_still_reject_a_stale_peer():
    """After a restart, an older peer record must still lose."""
    sr.publish_fabric_state("RWorm", {"a": 1})
    sr.save_spine_state()
    sr.spine_reset()
    sr.load_spine_state()
    sr._merge_fabric({"RWorm": {
        "provider": "RWorm", "updated_at": "1999-01-01T00:00:00",
        "state": {"a": 0}}})
    assert sr.get_fabric_state("RWorm")["a"] == 1


# ── the record-level sidecar ───────────────────────────────────────

def test_rec_stamp_prefers_the_sidecar_over_the_iso_field():
    rec = {"updated_at": "2999-01-01T00:00:00",
           "hlc": H.pack_stamp(1, 0, "bare")}
    assert sr._rec_stamp(rec, "updated_at") == H.pack_stamp(1, 0, "bare")
    legacy = {"updated_at": "2999-01-01T00:00:00"}
    assert sr._rec_stamp(legacy, "updated_at") == H.normalize_stamp(
        "2999-01-01T00:00:00")


def test_pinning_updated_at_pins_the_ordering_sidecar():
    """A caller that pins the content's time must actually control ordering.

    Without this, an explicit ``updated_at`` (used by backfill and by the
    merge tests) would be silently ignored and the record ordered by "now".
    """
    sr.record_spine_node("windows", "OldW", {"updated_at": "2001-01-01T00:00:00"})
    sr.record_spine_node("windows", "NewW", {})
    fresh = sr.get_spine_map()
    assert fresh["windows:NewW"]["hlc"] > fresh["windows:OldW"]["hlc"]


def test_quarantine_votes_keep_both_a_readable_and_an_ordering_stamp():
    sr.cast_quarantine_vote("quarantine:StampWorm", "windows")
    vote = sr.get_quarantine_votes("quarantine:StampWorm")[
        "quarantine:StampWorm"]["windows"]
    assert "T" in vote["at"], "human-readable ISO field was replaced"
    assert H.unpack_stamp(vote["hlc"]) is not None, "ordering sidecar missing"


def test_quarantine_vote_merge_prefers_the_sidecar():
    """A peer vote whose ISO looks newer but is causally older must lose."""
    sr.spine_reset()
    sr.cast_quarantine_vote("quarantine:OrdWorm", "windows", "quarantine")
    sr._merge_quarantine({"quarantine:OrdWorm": {
        "windows": {"verdict": "clear", "at": "2999-01-01T00:00:00",
                    "hlc": H.pack_stamp(1, 0, "bare")}}})
    got = sr.get_quarantine_votes("quarantine:OrdWorm")[
        "quarantine:OrdWorm"]["windows"]
    assert got["verdict"] == "quarantine", "causally-older vote won"


# ── regression: the ISO consumers must still work ───────────────────

def test_outcome_timestamps_stay_parseable_iso():
    """Adding the sidecar must not change what time-based readers see."""
    from datetime import datetime

    sr.record_outcome("mesh", "IsoWorm", True, 1.0)
    rec = sr.get_homeostat_view()["mesh:IsoWorm"]
    datetime.fromisoformat(rec["last_at"])          # must not raise
    assert H.unpack_stamp(rec["hlc"]) is not None    # and carries ordering
    assert sr.is_idle(window_seconds=60.0) is False
