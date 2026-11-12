# coding: utf-8
"""Brainstem persistence tests (2026-09-15).

Found while building the RL monitor: the Q-table is three tables
(`_Q_WEIGHTS`, `_Q_UPDATE_COUNTS`, `_Q_VARIANCE`), and only the first was ever
persisted, restored, or reset. Every restart therefore:

* reset the **pull-count** to zero for every key, so `get_q_updates()` --
  whose documented purpose is telling a *proven-neutral* arm (weight 0.0,
  pulls > 0) from a *never-tried* one (both 0) -- reported every arm as
  untried. Any selector applying a minimum-pull quota to cold arms re-explored
  proven ones forever, which is the exact cold-start collapse the counter was
  added to prevent.
* reset the **Welford variance**, so adaptive alpha silently fell back to its
  0.25 default and the learning-rate schedule restarted on every boot.

`spine_reset()` had the mirror-image bug: it cleared the weights but left the
counts and variance in place, producing a Q-table that was empty of values and
full of evidence.

State is isolated to a tmp dir so the developer's real
`~/.azazael/spine_state.json` is never touched.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    sr.spine_reset()
    yield
    sr.spine_reset()


# ── the bug: counts and variance were not part of the round-trip ─────

def test_pull_counts_survive_a_disk_roundtrip():
    for _ in range(4):
        sr.record_outcome("mesh", "PulledWorm", True, 1.0)
    assert sr.get_q_updates("mesh", "PulledWorm") == 4
    assert sr.save_spine_state()["q_update_counts"] >= 1

    sr.spine_reset()
    assert sr.get_q_updates("mesh", "PulledWorm") == 0  # reset really resets
    assert sr.load_spine_state()["restored"] is True
    assert sr.get_q_updates("mesh", "PulledWorm") == 4, "pull history was lost"


def test_variance_survives_a_disk_roundtrip():
    for i in range(6):
        sr.record_outcome("mesh", "VarWorm", i % 2 == 0, 1.0)
    saved_var = dict(sr._Q_VARIANCE)
    assert saved_var.get("mesh:VarWorm") is not None
    assert sr.save_spine_state()["saved"] is True

    sr.spine_reset()
    assert sr._Q_VARIANCE == {}, "reset must clear the variance too"
    sr.load_spine_state()
    assert sr._Q_VARIANCE.get("mesh:VarWorm") == pytest.approx(saved_var["mesh:VarWorm"])


def test_reset_clears_all_three_brainstem_tables():
    """A partial reset left values gone but evidence present."""
    sr.record_outcome("mesh", "ResetWorm", True, 1.0)
    assert sr.get_q_weights()
    assert sr.get_q_update_counts()
    assert sr._Q_VARIANCE
    sr.spine_reset()
    assert sr.get_q_weights() == {}
    assert sr.get_q_update_counts() == {}
    assert sr._Q_VARIANCE == {}


# ── the distinction the counter exists to make ──────────────────────

def test_bulk_accessor_matches_the_per_key_accessor():
    sr.record_outcome("mesh", "BulkWorm", True, 1.0)
    sr.record_outcome("synapse", "BulkWorm", False, 1.0)
    counts = sr.get_q_update_counts()
    assert counts["mesh:BulkWorm"] == sr.get_q_updates("mesh", "BulkWorm")
    assert counts["synapse:BulkWorm"] == sr.get_q_updates("synapse", "BulkWorm")
    # A copy, not the live table.
    counts["mesh:BulkWorm"] = 999
    assert sr.get_q_updates("mesh", "BulkWorm") == 1


def test_starved_key_is_distinguishable_from_proven_neutral():
    """weight 0.0 means 'neutral' only when pulls > 0."""
    r_unknown = sr.get_q_weight("mesh", "NeverTried")
    assert r_unknown == 0.0
    assert sr.get_q_updates("mesh", "NeverTried") == 0      # starved

    # Drive a key to a neutral weight with real evidence behind it.
    for _ in range(8):
        sr.record_outcome("mesh", "Measured", True, 1.0)
        sr.record_outcome("mesh", "Measured", False, 1.0)
    assert sr.get_q_updates("mesh", "Measured") == 16       # proven, not starved
    assert sr.get_q_updates("mesh", "Measured") != sr.get_q_updates("mesh", "NeverTried")


# ── merge semantics ────────────────────────────────────────────────

def test_counts_take_max_across_a_disk_merge():
    """Counts are monotone: a parallel save must not lower them."""
    sr.record_outcome("mesh", "MergeWorm", True, 1.0)
    sr.record_outcome("mesh", "MergeWorm", True, 1.0)
    assert sr.get_q_updates("mesh", "MergeWorm") == 2

    # Simulate a second process whose file recorded fewer observations.
    sr.save_spine_state()
    with sr._HOMEOSTAT_LOCK:
        sr._Q_UPDATE_COUNTS["mesh:MergeWorm"] = 1
        sr._Q_WEIGHTS.pop("mesh:MergeWorm", None)
    sr.load_spine_state()
    assert sr.get_q_updates("mesh", "MergeWorm") == 2, "disk merge lowered a count"


def test_counts_travel_in_the_gossip_payload():
    sr.record_outcome("mesh", "GossipWorm", True, 1.0)
    payload = sr.export_spine_gossip()
    assert payload.get("q_update_counts", {}).get("mesh:GossipWorm") == 1


def test_gossip_merge_never_lowers_a_local_count():
    sr.record_outcome("mesh", "PinnedWorm", True, 1.0)
    sr.record_outcome("mesh", "PinnedWorm", True, 1.0)
    sr.record_outcome("mesh", "PinnedWorm", True, 1.0)
    assert sr.get_q_updates("mesh", "PinnedWorm") == 3

    # Build a peer payload by hand (trust policy off keeps this deterministic).
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        peer = sr.export_spine_gossip()
        peer["gossip_from"] = "peer-env"
        peer["seq"] = int(peer.get("seq") or 0) + 50
        peer["q_update_counts"] = {"mesh:PinnedWorm": 1}
        sr.merge_spine_gossip(peer)
        assert sr.get_q_updates("mesh", "PinnedWorm") == 3, "peer lowered our count"
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


# ── load/merge reversion (2026-09-16 per-function audit) ─────────────
#
# `load_spine_state()` ASSIGNED every restored Q-weight, while its sibling
# `_merge_disk_state()` adopted them only when absent. The difference is a
# silent reversion: the module's own comments already state the rule for the
# policy map and the variance -- an in-memory adaptation is always newer than
# the disk copy, so adopt, never overwrite -- and the weights were the one
# table that ignored it. Reproduced by saving at -0.33, learning to +0.67, then
# loading: the learner came back at -0.33 with both sides reporting success.
#
# The companion defect is in `_ensure_loaded()`, whose guard was the emptiness
# heuristic `not _HOMEOSTAT and not _Q_WEIGHTS`. Any process that recorded one
# outcome (or dreamed) before its first getter set `_LOADED_ONCE` WITHOUT
# restoring anything, then ran its whole life on an empty brainstem. The two
# are one change: the assignment above is the only reason the heuristic was
# safe, so fixing either alone is worse than fixing neither.

def test_load_spine_state_does_not_roll_back_live_q_weights():
    for i in range(6):
        sr.record_outcome("mesh", "RollWorm", i % 2 == 0, 1.0)
    assert sr.save_spine_state()["saved"] is True
    saved = sr.get_q_weight("mesh", "RollWorm")

    sr.dream_reinforce("mesh", "RollWorm", 1.0)
    sr.dream_reinforce("mesh", "RollWorm", 1.0)
    live = sr.get_q_weight("mesh", "RollWorm")
    assert live != saved, "the dream must actually move the weight"

    sr.load_spine_state()
    assert sr.get_q_weight("mesh", "RollWorm") == pytest.approx(live), \
        "load_spine_state() reverted a live adaptation to the saved value"


def test_load_spine_state_still_restores_keys_it_does_not_have():
    """Adopt-when-absent must still adopt: the fix is not 'never load'."""
    sr.record_outcome("mesh", "AbsentWorm", True, 1.0)
    sr.save_spine_state()
    sr.spine_reset()
    assert sr.get_q_weight("mesh", "AbsentWorm") == 0.0
    sr.load_spine_state()
    assert sr.get_q_weight("mesh", "AbsentWorm") != 0.0
    assert sr.get_q_updates("mesh", "AbsentWorm") >= 1


def test_ensure_loaded_restores_even_when_memory_is_not_empty(monkeypatch):
    sr.record_outcome("mesh", "LatchedWorm", True, 1.0)
    sr.save_spine_state()
    sr.spine_reset()

    # One in-process write before any getter: the old emptiness guard latched
    # `_LOADED_ONCE` here and never restored the file.
    sr._Q_WEIGHTS["mesh:Unrelated"] = 0.5
    monkeypatch.setattr(sr, "_LOADED_ONCE", False)
    sr._ensure_loaded()
    assert sr.get_q_weight("mesh", "LatchedWorm") != 0.0, \
        "_ensure_loaded() latched the skip and restored nothing"


def test_dream_reinforce_docstring_is_reachable():
    """`_ensure_loaded()` sat above the docstring, so `__doc__` was None."""
    doc = sr.dream_reinforce.__doc__
    assert doc and "dream" in doc.lower()


# ── gossip Q recency rule ────────────────────────────────────────────


def _seed_ledger(worm, local_iso, weight):
    """A local ledger record with a chosen stamp, and a chosen local weight."""
    sr.record_outcome("mesh", worm, True, 1.0)
    rec = sr._HOMEOSTAT["mesh:" + worm]
    rec.pop("hlc", None)          # compare on the ISO field, as legacy rows do
    rec["last_at"] = local_iso
    sr._Q_WEIGHTS["mesh:" + worm] = weight


def _peer_payload(**overrides):
    base = {"gossip_from": "peer-env", "seq": 99, "spine_map": {},
            "homeostat": {}, "q_weights": {}, "spine_events": []}
    base.update(overrides)
    return base


def test_gossip_adopts_a_peer_weight_when_the_peer_ledger_is_newer():
    """The docstring's rule: the value whose ledger record is newer wins."""
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        _seed_ledger("PeerNewer", "2026-09-01T00:00:00", 0.0)
        res = sr.merge_spine_gossip(_peer_payload(
            homeostat={"mesh:PeerNewer": {"successes": 9, "failures": 0,
                                          "last_at": "2026-09-10T00:00:00"}},
            q_weights={"mesh:PeerNewer": 0.75}))
        assert res["merged"] is True
        assert sr.get_q_weight("mesh", "PeerNewer") == pytest.approx(0.75)
        assert res["q_weights"] == 1
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


def test_gossip_keeps_the_local_weight_when_our_ledger_is_newer():
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        _seed_ledger("LocalNewer", "2026-09-10T00:00:00", 0.25)
        res = sr.merge_spine_gossip(_peer_payload(
            homeostat={"mesh:LocalNewer": {"successes": 1, "failures": 0,
                                           "last_at": "2026-09-01T00:00:00"}},
            q_weights={"mesh:LocalNewer": 0.9}))
        assert sr.get_q_weight("mesh", "LocalNewer") == pytest.approx(0.25)
        assert res["q_weights"] == 0
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


def test_gossip_weight_without_ledger_evidence_is_adopted_exactly_once():
    """Unknown locally -> adopt; re-merging the same payload is a true no-op.

    Counting only real CHANGES is what keeps this idempotent: a key adopted
    with no ledger record on either side still has no record on the second
    merge, so an adoption counter would fire again forever.
    """
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        payload = _peer_payload(q_weights={"mesh:NoEvidence": 0.4})
        first = sr.merge_spine_gossip(payload)
        second = sr.merge_spine_gossip(payload)
        assert sr.get_q_weight("mesh", "NoEvidence") == pytest.approx(0.4)
        assert first["q_weights"] == 1 and second["q_weights"] == 0
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


def test_gossip_weight_without_evidence_cannot_displace_our_ledger():
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        _seed_ledger("Evidenced", "2026-09-05T00:00:00", 0.25)
        res = sr.merge_spine_gossip(
            _peer_payload(q_weights={"mesh:Evidenced": 0.99}))
        assert sr.get_q_weight("mesh", "Evidenced") == pytest.approx(0.25)
        assert res["q_weights"] == 0
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


# ── invalidate_q must survive the save path (forensic 2026-09-29) ───────
#
# Every read path merges counters with `max()` so N parallel save-then-exit
# jobs compose instead of clobbering each other. That same rule means a count
# can never go DOWN -- so `invalidate_q()`, whose entire job is discounting
# evidence whose cause was fixed, was a complete no-op end-to-end. It set the
# count in memory, journalled `q_invalidated` and emitted a spine event, and
# then `save_spine_state()`'s `_merge_disk_state()` folded the old number back
# over it. Reproduced on an isolated store before the fix:
#
#     invalidate_q said : evidence_to = 0
#     in memory now     : n = 0
#     after save (mem)  : n = 100
#     on DISK           : n = 100
#
# The journal and the monitors were counting invalidations that did not happen.

def test_an_invalidation_survives_the_save_it_rides_on():
    sr._Q_WEIGHTS["route:windows>wsl2-ubuntu"] = -0.9
    sr._Q_UPDATE_COUNTS["route:windows>wsl2-ubuntu"] = 100
    sr._HOMEOSTAT["route:windows>wsl2-ubuntu"] = {"successes": 10, "failures": 90}
    sr.save_spine_state()

    r = sr.invalidate_q("route:windows>wsl2-ubuntu", reason="cause fixed",
                        factor=0.0)
    assert r["evidence_to"] == 0, "the invalidation did not compute"

    sr.save_spine_state()
    assert sr._Q_UPDATE_COUNTS["route:windows>wsl2-ubuntu"] == 0, \
        "save_spine_state() restored the invalidated count in memory"
    on_disk = json.loads(sr._STATE_FILE.read_text(encoding="utf-8"))
    assert on_disk["q_update_counts"]["route:windows>wsl2-ubuntu"] == 0, \
        "the invalidation did not reach disk"


def test_a_restart_does_not_resurrect_an_invalidated_count():
    sr._Q_WEIGHTS["route:a>b"] = -0.5
    sr._Q_UPDATE_COUNTS["route:a>b"] = 40
    sr.save_spine_state()
    sr.invalidate_q("route:a>b", reason="cause fixed", factor=0.0)
    sr.save_spine_state()

    sr.spine_reset()
    sr._ensure_loaded()
    assert sr._Q_UPDATE_COUNTS.get("route:a>b", 0) == 0, \
        "a restart restored the invalidated count"


def test_a_peer_cannot_resurrect_an_invalidated_count():
    """Gossip is the third read path, and it must honour the floor too.

    Uses the file's real `_peer_payload` shape: a bare dict with `env` and no
    `q_weights` is rejected by the merge's own type guard, so it would pass
    against any implementation and pin nothing.
    """
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        sr._Q_WEIGHTS["route:a>b"] = -0.5
        sr._Q_UPDATE_COUNTS["route:a>b"] = 40
        sr.invalidate_q("route:a>b", reason="cause fixed", factor=0.0)
        assert sr._Q_UPDATE_COUNTS["route:a>b"] == 0

        res = sr.merge_spine_gossip(_peer_payload(
            q_weights={"route:a>b": -0.5},
            q_update_counts={"route:a>b": 40}))
        assert res["merged"] is True, "the payload never merged, so nothing was tested"
        assert sr._Q_UPDATE_COUNTS["route:a>b"] == 0, \
            "a peer resurrected a count this env explicitly invalidated"
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


def test_a_peer_can_still_contribute_a_fresh_count():
    """The floor is per-key, so it must not mute unrelated keys."""
    import os
    os.environ["AZAZAEL_GOSSIP_POLICY"] = "off"
    try:
        sr._Q_UPDATE_COUNTS["route:a>b"] = 40
        sr.invalidate_q("route:a>b", reason="cause fixed", factor=0.0)
        res = sr.merge_spine_gossip(_peer_payload(
            q_weights={"route:c>d": 0.5},
            q_update_counts={"route:c>d": 12}))
        assert res["merged"] is True
        assert sr._Q_UPDATE_COUNTS.get("route:c>d", 0) == 12, \
            "the floor leaked onto a key that was never invalidated"
    finally:
        os.environ.pop("AZAZAEL_GOSSIP_POLICY", None)


def test_a_real_observation_still_raises_the_count():
    """The floor is a watermark on restoration, not a lock on learning."""
    sr._Q_WEIGHTS["route:a>b"] = -0.5
    sr._Q_UPDATE_COUNTS["route:a>b"] = 40
    sr.invalidate_q("route:a>b", reason="cause fixed", factor=0.0)
    for _ in range(3):
        sr.record_outcome("route", "a>b", True, 5.0)
    assert sr._Q_UPDATE_COUNTS["route:a>b"] == 3, \
        "the floor froze the count against genuine new evidence"


# ── the cap must not eat the record of its own predecessor's decision ──
#
# Found 2026-09-29, live ledger. `invalidate_q` and `cap_q_tables` were both
# correct in isolation and destroyed each other in sequence: the invalidation
# set a key's evidence to zero, and the cap -- which prunes the lowest-evidence
# keys first -- then evicted that same key whole on the next save, tombstoning
# it "capped". The invalidation reported success (the key was still there when
# it returned) and the record was gone a moment later. On the live ledger that
# cost 10 genuine ~95ms latency samples per wsl2 route, discarded alongside the
# fabricated 8000.0 ones the discount existed to discount.


def test_invalidate_q_does_not_make_a_key_the_cap_s_next_victim(monkeypatch):
    """A floored key outranks every unfloored key, however stale its value."""
    for i in range(3):
        sr._Q_WEIGHTS[f"nature:keep{i}"] = -0.5
        sr._Q_UPDATE_COUNTS[f"nature:keep{i}"] = 40
    sr._Q_WEIGHTS["route:windows>wsl2-ubuntu"] = -1.0
    sr._Q_UPDATE_COUNTS["route:windows>wsl2-ubuntu"] = 259

    sr.invalidate_q("route:windows>wsl2-ubuntu",
                    reason="penalty was manufactured, not observed",
                    factor=0.0)
    assert "route:windows>wsl2-ubuntu" in sr._Q_WEIGHTS, \
        "precondition: the invalidation itself must not delete the key"

    monkeypatch.setattr(sr, "_Q_KEYS_MAX", 3)
    dropped = sr.cap_q_tables()

    assert dropped == 1
    assert "route:windows>wsl2-ubuntu" in sr._Q_WEIGHTS, \
        "the cap evicted the key the invalidation had just discounted -- " \
        "zeroing evidence to mark it stale also marked it cheapest to lose"
    assert "route:windows>wsl2-ubuntu" not in sr._PURGED_KEYS, \
        "the surviving key must not be tombstoned by a cap that kept it"


def test_the_cap_still_enforces_its_bound(monkeypatch):
    """The fix orders the eviction set; it does not exempt anything from it."""
    for i in range(3):
        sr._Q_WEIGHTS[f"route:old{i}"] = -0.5
        sr._Q_UPDATE_COUNTS[f"route:old{i}"] = 40
    sr._Q_WEIGHTS["nature:rich"] = 0.9
    sr._Q_UPDATE_COUNTS["nature:rich"] = 500

    monkeypatch.setattr(sr, "_Q_KEYS_MAX", 1)
    assert sr.cap_q_tables() == 3
    assert list(sr._Q_WEIGHTS) == ["nature:rich"], \
        "the cap kept the wrong key: it must still drop by evidence"


def test_a_reload_keeps_why_a_key_was_purged(monkeypatch, tmp_path):
    """A tombstone's reason survives a restart.

    The read paths re-adopted every key on disk with the literal value
    "fixture", so a key capped at capacity and a key purged for a manufactured
    penalty both came back labelled synthetic test data. The ledger's only
    record of why a key is gone is that value.
    """
    _seed_tombstones_and_save()

    # Cold start -- the `_merge_disk_state` read path.
    sr._PURGED_KEYS.clear()
    monkeypatch.setattr(sr, "_LOADED_ONCE", False)
    sr._ensure_loaded()
    assert sr._PURGED_KEYS.get("route:windows>wsl2-kali") == "capped", \
        "a capacity eviction was relabelled as synthetic fixture data"

    # Explicit reload -- the `load_spine_state` read path, which is public and
    # callable mid-process, so it carries its own copy of the same loop.
    sr._PURGED_KEYS.clear()
    assert sr.load_spine_state()["restored"] is True
    assert sr._PURGED_KEYS.get("route:windows>route") == "malformed route key", \
        "the recorded reason for a purge was replaced with 'fixture'"


def _seed_tombstones_and_save() -> None:
    """Two keys gone for two different reasons, neither of them a fixture."""
    sr._PURGED_KEYS["route:windows>wsl2-kali"] = "capped"
    sr._PURGED_KEYS["route:windows>route"] = "malformed route key"
    assert sr.save_spine_state()["saved"] is True


# ── a counter reset that cannot be undone by the next save ─────────────
#
# Found 2026-09-29, live ledger, and the third instance of one pattern: a
# reduction assigned into a store that folds its disk copy in with `max()`
# before every write. `purge_fixtures` needed tombstones, `invalidate_q`
# needed the evidence floor, and the homeostat's own `successes` / `failures`
# had neither -- so the 09-29 purge zeroed `route:windows>mesh`, journalled
# the removal, and the next load reported 5/0 again. The false data survived
# its own removal and the audit trail asserted it had gone.


def test_a_counter_reset_survives_the_next_save():
    """The fold-in must not drag a disowned count back over the reset.

    The count has to be ON DISK before the reset for this to test anything --
    that is the whole mechanism. A reset in a process whose disk copy is still
    empty has nothing to lose to, and the test passes against the unfixed
    merge for that reason. This is the 09-29 sequence: two real probes saved,
    then a reset, then the next hourly save.
    """
    sr.record_outcome("route", "windows>mesh", True, 95.0)
    sr.record_outcome("route", "windows>mesh", True, 91.0)
    assert sr.save_spine_state()["saved"] is True
    assert sr._HOMEOSTAT["route:windows>mesh"]["successes"] == 2

    res = sr.reset_counters("route:windows>mesh",
                            reason="eBPF alert counts mixed with real probes")
    assert res["success"] is True
    assert res["before"]["successes"] == 2
    assert sr._HOMEOSTAT["route:windows>mesh"]["successes"] == 0

    # This save folds the file in first, and the file says 2.
    assert sr.save_spine_state()["saved"] is True
    assert sr._HOMEOSTAT["route:windows>mesh"]["successes"] == 0, \
        "the reset was undone by the monotone fold-in on the very next save"


def test_a_counter_reset_survives_a_restart(monkeypatch):
    """The watermark is persisted, so a cold start honours it too."""
    sr.record_outcome("route", "windows>mesh", True, 95.0)
    assert sr.save_spine_state()["saved"] is True

    sr.reset_counters("route:windows>mesh", reason="not separable")
    assert sr.save_spine_state()["saved"] is True

    sr._HOMEOSTAT.clear()
    sr._COUNTER_FLOOR.clear()
    monkeypatch.setattr(sr, "_LOADED_ONCE", False)
    sr._ensure_loaded()

    assert sr._HOMEOSTAT["route:windows>mesh"]["successes"] == 0, \
        "a cold start resurrected a counter the operator had disowned"
    assert "route:windows>mesh" in sr._COUNTER_FLOOR


def test_a_genuine_outcome_still_raises_a_reset_counter():
    """The floor is a watermark, not a lock -- the same rule as the evidence
    floor, and for the same reason: a disowned count must not make a route
    permanently unrankable."""
    sr.record_outcome("route", "windows>mesh", True, 95.0)
    sr.reset_counters("route:windows>mesh", reason="not separable")
    sr.record_outcome("route", "windows>mesh", True, 93.0)
    assert sr._HOMEOSTAT["route:windows>mesh"]["successes"] == 1, \
        "the floor froze the counter against genuine new evidence"


def test_the_counter_floor_is_reported_not_silent():
    """A suppressed count is a claim, so something has to be able to count it.

    Same reason `capability_liveness.py` exists: a capability that suppresses
    a failure mode needs a live counter proving the failure mode stopped, and
    a silent floor is indistinguishable from a reset that never ran.
    """
    sr.record_outcome("route", "windows>mesh", True, 95.0)
    sr.record_outcome("route", "windows>mesh", False, 8000.0)
    assert sr.save_spine_state()["saved"] is True
    sr.reset_counters("route:windows>mesh", reason="not separable")
    assert sr.save_spine_state()["saved"] is True

    rep = sr.counter_floor_report()
    assert rep["resets_in_force"] == 1
    row = rep["keys"]["route:windows>mesh"]
    assert row["current"] == {"successes": 0, "failures": 0}
    assert row["at_floor"] is True, \
        "a reset reported as in force while the counters sit above the floor " \
        "is the unfixed merge wearing a passing report"

    # The pre-reset counts are a fact about a moment, and the journal is where
    # they can be kept -- unlike a total derived from the floor, which is 0
    # whether the reset worked or the fold-in won.
    j = [e for e in sr._POLICY_JOURNAL
         if e.get("action") == "counters_reset"
         and e.get("worm") == "route:windows>mesh"]
    assert j, "the reset was not journalled, so it cannot be shown to have run"
    assert j[-1]["changes"]["before"] == {"successes": 1, "failures": 1}


def test_a_reset_on_a_purged_key_is_not_resurrected_by_the_fold_in(monkeypatch):
    """Tombstones outrank floors: a purged key stays gone.

    Worth pinning because `reset_counters` writes a floor for a key that may
    already be tombstoned, and the floor read path is guarded separately from
    the tombstone path. A key that is gone must not come back wearing a floor.
    """
    sr.record_outcome("route", "windows>route", True, 95.0)
    sr._PURGED_KEYS["route:windows>route"] = "malformed route key"
    sr._HOMEOSTAT.pop("route:windows>route", None)
    sr.reset_counters("route:windows>route", reason="should not matter")
    assert sr.save_spine_state()["saved"] is True

    sr._HOMEOSTAT.clear()
    sr._COUNTER_FLOOR.clear()
    monkeypatch.setattr(sr, "_LOADED_ONCE", False)
    sr._ensure_loaded()

    assert "route:windows>route" not in sr._HOMEOSTAT, \
        "a purged key was re-adopted because a floor was recorded for it"
    assert "route:windows>route" not in sr._COUNTER_FLOOR, \
        "the floor for a purged key was re-adopted from disk"


def test_the_neural_loop_does_not_prune_a_key_that_carries_a_recorded_floor():
    """A recorded decision must not read as absence of one.

    The prune stage calls a key dead when its count is 0, and 0 is what
    `invalidate_q(factor=0.0)` and `reset_counters()` write. Without this the
    repair that preserves a disowned measurement is also what makes its key
    look untouched, and the next hourly meta cycle purges it -- measured on the
    live ledger, where the restored wsl2 routes were tombstoned
    `neural_loop_prune` within 20 minutes of being restored.
    """
    from CORE import neural_loop

    class _Ctx:
        apply = True

    sr._Q_WEIGHTS["route:windows>wsl2-ubuntu"] = -0.33
    sr.record_outcome("route", "windows>wsl2-ubuntu", True, 95.0)
    sr.invalidate_q("route:windows>wsl2-ubuntu", reason="manufactured",
                    factor=0.0)
    assert sr._Q_UPDATE_COUNTS["route:windows>wsl2-ubuntu"] == 0, \
        "precondition: a floored key reads as count 0, which is the hazard"

    res = neural_loop._stage_prune(_Ctx()) if hasattr(
        neural_loop, "_stage_prune") else None
    if res is None:
        pytest.skip("prune stage is not exposed as _stage_prune")
    assert "route:windows>wsl2-ubuntu" in sr._Q_WEIGHTS, \
        "the prune stage deleted a key that carries a recorded evidence floor"
    assert "route:windows>wsl2-ubuntu" not in sr._PURGED_KEYS
