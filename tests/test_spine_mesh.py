# coding: utf-8
"""Tests for the neural mesh spine layer.

Covers (2026-09-13 audit gaps):
- recursive discovery into WORMS/expansion (G1)
- mesh-aware category heuristics (G2)
- spine ledger (CORE.spine_registry): thread safety, bounded memory
- SpineMapperWorm self-mapping (G3)
- HomeostatWorm feedback loop (G4)
- RedQueenWorm stand-down + quorum quarantine (self-improving safety)
- LEAP A: Q-weight brainstem (auto-reinforce + q_order)
- LEAP B: spine state persistence (save/load round-trip)
- LEAP C: SpineCycleWorm one-entry self-improvement cycle
- regression: discover_worms is concurrency-safe under 3.14t
- regression: save_cache is atomic (no torn/empty cache files)
"""
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr
from CORE.worm_registry import (
    WORM_REGISTRY, discover_worms, reset_registry, _guess_category, _guess_cooldown,
    _VALID_CATEGORIES,
)

# ── Discovery / heuristics ───────────────────────────────────────────

def test_discovery_recurses_into_expansion():
    """G1: expansion/*.py worms register via recursive discovery."""
    discover_worms()
    assert "MeshRouterWorm" in WORM_REGISTRY
    assert "Lilith" in WORM_REGISTRY
    assert "SpineMapperWorm" in WORM_REGISTRY


def test_new_mesh_categories_valid():
    """G2: the 8 mesh natures are valid registry categories."""
    for cat in ("mesh", "synapse", "consensus", "engram",
                "federated", "servemesh", "immune", "xenomorph"):
        assert cat in _VALID_CATEGORIES


def test_heuristic_misfile_fixes():
    """G2: names the old heuristics misfiled now land in the right nature."""
    assert _guess_category("GossipSyncWorm", "mesh") == "mesh"
    assert _guess_category("SelfHealWorm", "immune") == "immune"
    assert _guess_category("PartitionHealerWorm", "mesh") == "mesh"
    assert _guess_category("QuorumVoterWorm", "consensus") == "consensus"
    assert _guess_category("HomeostatWorm", "spine") == "synapse"
    assert _guess_category("RedQueenWorm", "spine") == "xenomorph"


def test_heuristic_cooldowns_present():
    assert _guess_cooldown("mesh") == 15.0
    assert _guess_cooldown("xenomorph") == 120.0
    assert _guess_cooldown("unknown_nature") == 30.0


def test_no_worm_name_collisions_across_expansion():
    """Every expansion worm's registered source_file matches its module of definition.

    Catches silent same-name overwrites (registry keys by class name).
    """
    reset_registry()
    discover_worms()
    src_root = Path(__file__).resolve().parent.parent / "src" / "CORE" / "WORMS" / "expansion"
    for name, info in WORM_REGISTRY.items():
        sf = info.get("source_file", "")
        if "expansion" not in sf:
            continue
        mod_name = info.get("module", "")
        assert mod_name.startswith("CORE.WORMS.expansion."), \
            f"{name}: bad module {mod_name}"
        expected = src_root / (mod_name.rsplit(".", 1)[-1] + ".py")
        # Compare normalised paths, not raw strings: `source_file` comes from
        # inspect.getfile() (native separators) while `expected` is built from
        # pathlib, and the two can differ in separator/case on Windows for the
        # same real file.
        # 2026-10-04: compare from "expansion/" onward. The editable install
        # resolves CORE.* to the hub tree while the test file lives in this
        # worktree, so absolute paths can name different TREES for the same
        # relative artifact. The invariant is same-artifact (guards silent
        # same-name overwrites), not same-absolute-path.
        def _suffix(p):
            parts = os.path.normcase(os.path.normpath(str(p))).split(os.sep)
            if "expansion" in parts:
                return os.sep.join(parts[parts.index("expansion"):])
            return os.path.normcase(os.path.normpath(str(p)))
        assert _suffix(sf) == _suffix(expected), \
            f"{name}: registered from {sf} but module says {expected}"


def test_registered_mesh_counts():
    """All 32 expansion mesh worms + 3 spine worms are registered."""
    discover_worms()
    by_cat = {}
    for name, info in WORM_REGISTRY.items():
        by_cat.setdefault(info["category"], set()).add(name)
    expected = {"mesh": 5, "synapse": 5, "consensus": 3, "engram": 4,
                "federated": 3, "servemesh": 3, "immune": 4, "xenomorph": 8}
    for cat, n in expected.items():
        assert len(by_cat.get(cat, ())) >= n, f"{cat}: expected >= {n}"


# ── Spine ledger ─────────────────────────────────────────────────────

def test_outcome_ledger_roundtrip():
    sr.homeostat_reset()
    sr.record_outcome("mesh", "WormA", True, 10.0)
    sr.record_outcome("mesh", "WormA", False, 20.0, error="boom")
    view = sr.get_homeostat_view()
    rec = view["mesh:WormA"]
    assert rec["successes"] == 1 and rec["failures"] == 1
    assert rec["last_error"] == "boom"
    assert len(rec["hist_ms"]) == 2


def test_outcome_ledger_thread_safety():
    sr.homeostat_reset()
    def worker(i):
        for j in range(50):
            sr.record_outcome("t", f"W{i}", (j % 2 == 0), 1.0)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    view = sr.get_homeostat_view()
    assert sum(r["successes"] + r["failures"]
               for r in view.values()) == 8 * 50


def test_outcome_ledger_bounded():
    sr.homeostat_reset()
    for i in range(sr._OUTCOME_EVENTS_MAX + 50):
        sr.record_outcome("t", "W", True, 1.0)
    assert len(sr.get_outcome_events()) <= sr._OUTCOME_EVENTS_MAX


def test_record_outcome_never_raises():
    sr.record_outcome(None, None, True, "not-a-number")  # type: ignore[arg-type]
    sr.homeostat_reset()


# ── SpineMapperWorm ──────────────────────────────────────────────────

def test_spine_mapper_publishes_map():
    sr.spine_reset()
    discover_worms()
    from CORE.WORMS.expansion.spine import SpineMapperWorm
    r = SpineMapperWorm().execute()
    assert r["success"] is True
    body = r["result"]
    assert body["worms"] > 100          # 174+ registry mapped
    assert body["natures"] >= 20        # 27 natures
    assert body["queen_role"] in ("queen_full", "queen_interim", "follower")
    smap = sr.get_spine_map()
    assert len(smap) >= body["worms"]
    assert any(v.get("role") in ("queen_full", "queen_interim")
               for v in smap.values() if v.get("worm") == "Lilith")


def test_homeostat_proposes_but_does_not_apply_by_default():
    """apply=False changes nothing BEYOND the reflex arc's instant response.

    Division of labor: the reflex (inside record_outcome) fires at the
    4th consecutive failure; the homeostat is the slower deliberative
    layer and, un-applied, must not modify the registry further. After
    the reflex has fired, a propose-only homeostat cycle leaves the
    post-reflex cooldown/priority exactly where the reflex put them and
    journals nothing under actor='homeostat'.
    """
    sr.spine_reset()
    discover_worms()
    from CORE.WORMS.expansion.spine import HomeostatWorm
    # The reflex arc mutates this entry in place, so it must be put back or the
    # NEXT test reads a drifted baseline as its starting point. Measured: three
    # runs of these two tests drove `Lilith` priority 10 -> 5 with nothing to
    # restore it, and `test_homeostat_apply_respects_bounds` compares against
    # exactly these fields.
    entry = WORM_REGISTRY["Lilith"]
    defaults = (entry["cooldown_seconds"], entry["priority"])
    try:
        for _ in range(HomeostatWorm.MIN_SAMPLES + 1):
            sr.record_outcome("xenomorph", "Lilith", False, 5.0, error="x")
        post_reflex_cd = entry["cooldown_seconds"]
        post_reflex_pr = entry["priority"]
        proposals = HomeostatWorm().cycle(apply=False)
        assert entry["cooldown_seconds"] == post_reflex_cd  # no further change
        assert entry["priority"] == post_reflex_pr
        assert any(p["worm"] == "Lilith" for p in proposals["proposals"])
        assert not any(e["actor"] == "homeostat"
                       for e in sr.get_policy_journal(20))
    finally:
        entry["cooldown_seconds"], entry["priority"] = defaults


def test_homeostat_apply_respects_bounds():
    """The homeostat's OWN scaling must land inside its configured clamp.

    The baseline is read AFTER `record_outcome`, because the REFLEX arc inside it
    is a different actor that mutates the same entry at the 4th consecutive
    failure. Reading `base_cd` first conflated the two: the assertion compared
    the homeostat's output against a pre-reflex value, so it held only while the
    reflex's own cooldown factor stayed <= COOLDOWN_MAX_SCALE / 2. That is why
    this test failed intermittently and only in-suite -- the reflex's factor
    depends on consecutive-failure state left by the tests before it.

    Both fields are restored in a `finally`, so a failure cannot leave a drifted
    registry behind for every later test (which is also how the drift spread).
    """
    sr.homeostat_reset()
    discover_worms()
    from CORE.WORMS.expansion.spine import HomeostatWorm
    entry = WORM_REGISTRY["Lilith"]
    defaults = (entry["cooldown_seconds"], entry["priority"])
    try:
        for _ in range(HomeostatWorm.MIN_SAMPLES + 1):
            sr.record_outcome("xenomorph", "Lilith", False, 5.0, error="x")
        # AFTER the reflex, so this measures the homeostat's factor alone.
        base_cd = entry["cooldown_seconds"]
        HomeostatWorm().cycle(apply=True)
        assert base_cd * HomeostatWorm.COOLDOWN_MIN_SCALE <= entry["cooldown_seconds"] \
               <= base_cd * HomeostatWorm.COOLDOWN_MAX_SCALE
        assert 1 <= entry["priority"] <= 10
    finally:
        # restore registry defaults for other tests
        entry["cooldown_seconds"], entry["priority"] = defaults


def test_homeostat_reinforces_synapse_q():
    sr.homeostat_reset()
    from CORE.WORMS.expansion.spine import HomeostatWorm
    hw = HomeostatWorm()
    assert hw._synapse is not None
    # Failing worm -> negative reward -> Q-weight must drop below baseline.
    # Sample count derives from MIN_SAMPLES (a TUNED value: live=8, fallback=5),
    # so this holds under any calibration. A literal 6 passed at 5 and broke the
    # moment the tuner raised the floor (audit 2026-09-22, section 7.3 class).
    for _ in range(HomeostatWorm.MIN_SAMPLES + 1):
        sr.record_outcome("mesh", "MeshRouterWorm", False, 5.0, error="x")
    before = hw._synapse.weight("mesh", "MeshRouterWorm")
    result = hw.cycle(apply=False)
    after = hw._synapse.weight("mesh", "MeshRouterWorm")
    assert after < before          # failing edge penalized
    assert result["q_rewarded"] >= 1


# ── RedQueenWorm ─────────────────────────────────────────────────────

def test_red_queen_standdown_when_follower():
    """Red Queen never probes without a queen claim (safety rail)."""
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    result = rq.execute()
    body = result["result"]
    # On this Windows box claim is queen_interim or follower; either way,
    # execute() must never raise and must report stand-down state honestly.
    assert body["stood_down"] == (
        not body["queen_role"].startswith("queen"))
    if body["stood_down"]:
        assert body["probes"] == [] and body["quarantined"] == []


def test_red_queen_quorum_quarantine():
    """Single process must NOT self-quarantine (quorum=2 votes needed)."""
    from CORE.WORMS.expansion.spine import RedQueenWorm
    sr.homeostat_reset()
    rq = RedQueenWorm()
    for _ in range(rq.QUARANTINE_MIN_FAILURES):
        sr.record_outcome("mesh", "BadWorm", False, 1.0, error="boom")
    ordered = rq.order_quarantine()
    assert ordered == []  # one vote cast, quorum(2) unmet -> stand down


def test_quorum_path_quarantines_when_decided():
    """The quarantine mechanism itself works when quorum IS decided
    (simulated: two envs voting the same verdict)."""
    from CORE.WORMS.expansion.consensus import QuorumVoterWorm
    from CORE.WORMS.expansion.immune import QuarantineWorm
    voter = QuorumVoterWorm(quorum=2)
    qz = QuarantineWorm()
    finding = "quarantine:TestWorm"
    v1 = voter.vote(finding, "windows", "quarantine")
    assert not v1["decided"]
    v2 = voter.vote(finding, "wsl2", "quarantine")
    assert v2["decided"]
    q = qz.quarantine("TestWorm", "fail_rate=1.00")
    assert q["success"] is True and q["quarantined"] >= 1


def test_spine_module_health():
    h = sr.get_module_health()
    assert h["version"] == "1.0.0"
    assert set(h) >= {"spine_nodes", "spine_events",
                      "homeostat_worms", "outcome_events"}


# ═════════════════════════════════════════════════════════════════
# LEAP A — Q-weight brainstem
# ═════════════════════════════════════════════════════════════════

def test_q_weights_auto_reinforce_via_record_outcome():
    """record_outcome() automatically moves Q-weights (brainstem reflex)."""
    sr.spine_reset()
    try:
        for _ in range(3):
            sr.record_outcome("mesh", "QHealthy", True, 5.0)
        for _ in range(3):
            sr.record_outcome("mesh", "QBroken", False, 5.0, error="x")
        q = sr.get_q_weights()
        assert q["mesh:QHealthy"] > 0.0
        assert q["mesh:QBroken"] < 0.0
        # EMA direction sanity: repeated success trends toward +1
        assert sr.get_q_weight("mesh", "QHealthy") < 1.0
    finally:
        sr.spine_reset()


def test_q_order_healthy_first_cold_middle():
    """q_order(): positive-Q first, cold (0.0) middle, negative-Q last."""
    sr.spine_reset()
    try:
        for _ in range(3):
            sr.record_outcome("synapse", "Good", True, 1.0)
        for _ in range(3):
            sr.record_outcome("synapse", "Bad", False, 1.0, error="x")
        ordered = sr.q_order("synapse", ["Bad", "Cold1", "Good", "Cold2"])
        assert ordered[0] == "Good"
        assert set(ordered[1:3]) == {"Cold1", "Cold2"}   # stable cold order
        assert ordered[-1] == "Bad"
    finally:
        sr.spine_reset()


def test_q_weights_clamped_and_reset():
    """Q clamps to [-1, 1]; spine_reset clears the table."""
    sr.spine_reset()
    try:
        for _ in range(50):
            sr.record_outcome("mesh", "Always", True, 1.0)
        assert sr.get_q_weight("mesh", "Always") <= 1.0
        sr.spine_reset()
        assert sr.get_q_weights() == {}
    finally:
        sr.spine_reset()


def test_q_weighted_execution_ordering():
    """Q-weights order execution before priority (LEAP A wiring).

    U3: worm names come from the flat registry (was ClusterWORMZ("mesh")).
    The ordering rule under test is unchanged.
    """
    import math
    discover_worms()
    from CORE.worm_registry import get_worms_by_category
    names = sorted(get_worms_by_category("mesh"))
    if len(names) < 3:
        pytest.skip("need >= 3 mesh worms")
    sr.spine_reset()
    try:
        # Penalize the highest-priority worm so Q must override priority.
        top = sorted(names, key=lambda n: WORM_REGISTRY.get(n, {}).get("priority", 5),
                     reverse=True)[0]
        for _ in range(6):
            sr.record_outcome("mesh", top, False, 1.0, error="penalized")
        qmap = sr.get_q_weights()
        assert qmap.get(f"mesh:{top}", 0.0) < 0.0
        # Replicate execute()'s ordering rule and assert the demotion.
        pri = {n: WORM_REGISTRY.get(n, {}).get("priority", 5) for n in names}
        ordered = sorted(names, key=lambda n: (
            -qmap.get(f"mesh:{n}", 0.0), -pri[n]))
        assert ordered.index(top) >= len(ordered) - 1 or \
            all(qmap.get(f"mesh:{n}", 0.0) >= 0.0 for n in ordered[:ordered.index(top)])
    finally:
        sr.spine_reset()


# ═════════════════════════════════════════════════════════════════
# LEAP B — persistence
# ═════════════════════════════════════════════════════════════════

def test_spine_state_save_load_roundtrip(tmp_path, monkeypatch):
    """save_spine_state()/load_spine_state() round-trip via a tmp file."""
    sr.spine_reset()
    try:
        sr.record_spine_node("testenv", "SomeWorm", {"category": "mesh"})
        sr.record_outcome("mesh", "LedgerWorm", True, 3.0)
        monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
        saved = sr.save_spine_state()
        assert saved["saved"] is True
        sr.spine_reset()  # wipe memory
        assert sr.get_q_weights() == {}
        loaded = sr.load_spine_state()
        assert loaded["restored"] is True
        assert sr.get_q_weight("mesh", "LedgerWorm") > 0.0
        assert any("SomeWorm" in k for k in sr.get_spine_map())
    finally:
        sr.spine_reset()


def test_spine_state_missing_file_is_noop():
    """load_spine_state() with no state file returns a clean no-op."""
    import pathlib
    real = sr._STATE_FILE
    sr._STATE_FILE = pathlib.Path("Z:/nonexistent/spine_state.json")
    try:
        res = sr.load_spine_state()
        assert res == {"restored": False, "reason": "no_state_file"}
    finally:
        sr._STATE_FILE = real


# ═════════════════════════════════════════════════════════════════
# LEAP C — SpineCycleWorm
# ═════════════════════════════════════════════════════════════════

def test_spine_cycle_runs_and_never_raises():
    """One entry point runs map -> homeostat -> queen -> persist safely."""
    discover_worms()
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    cyc = SpineCycleWorm(min_interval_seconds=0.0)
    rep = cyc.run_cycle(apply=False)
    assert rep["success"] is True
    for stage in ("map", "homeostat", "red_queen"):
        assert stage in rep
    assert rep["map"]["success"] is True


def test_spine_cycle_throttle_and_reentry_guard():
    """min-interval throttle + non-reentrant busy guard both skip safely."""
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    cyc = SpineCycleWorm(min_interval_seconds=60.0)
    first = cyc.run_cycle(apply=False)          # runs, sets last_run_at
    second = cyc.run_cycle(apply=False)         # throttled
    assert second.get("skipped") == "min_interval"
    # Reentry: hold the busy lock externally and expect a clean skip.
    assert cyc._busy.acquire(blocking=False)
    try:
        third = cyc.run_cycle(apply=False)
        assert third.get("skipped") == "cycle_already_running"
    finally:
        cyc._busy.release()


def test_spine_cycle_registered_as_orchestration():
    """SpineCycleWorm is orchestration-category so the queen never probes it."""
    discover_worms()
    info = WORM_REGISTRY.get("SpineCycleWorm")
    assert info is not None
    assert info["category"] == "orchestration"
    from CORE.WORMS.expansion.spine import RedQueenWorm
    assert "orchestration" not in RedQueenWorm.PROBE_NATURES


# ═════════════════════════════════════════════════════════════════
# Regressions — 3.14t concurrency fixes found during the batch failure
# ═════════════════════════════════════════════════════════════════

def test_discovery_thread_safe_under_concurrency():
    """Concurrent discover_worms() callers produce a full registry (no 142/17 downgrade)."""
    import concurrent.futures
    results = []
    def _run():
        discover_worms()
        return len(WORM_REGISTRY)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda _: _run(), range(4)))
    discover_worms()  # settle
    assert len(WORM_REGISTRY) >= 170
    assert len({i["category"] for i in WORM_REGISTRY.values()}) >= 25


def test_save_cache_atomic_no_torn_file():
    """save_cache() writes via tmp+replace; readers never see an empty cache."""
    from CORE import worm_registry_cache as wrc
    discover_worms()
    wrc.save_cache(dict(WORM_REGISTRY), str(wrc.Path(__file__).resolve().parent.parent / "src" / "CORE" / "WORMS"))
    with open(wrc.CACHE_FILE, "rb") as fh:
        head = fh.read(16)
    assert len(head) == 16  # non-empty -> a reader would never hit EOFError
    # leftover tmp file must be gone (os.replace consumed it)
    assert not wrc.CACHE_FILE.with_suffix(".pkl.tmp").exists()


# ═════════════════════════════════════════════════════════════════
# Gossip — cross-env state merge + exchange transport
# ═════════════════════════════════════════════════════════════════

def _gossip_payload(**overrides):
    base = {
        "gossip_from": "wsl2",
        "spine_map": {},
        "homeostat": {},
        "q_weights": {},
        "spine_events": [],
    }
    base.update(overrides)
    return base


def test_gossip_merge_recency_wins(monkeypatch):
    """merge_spine_gossip: newer remote wins, older remote loses, new keys merge."""
    # This test asserts MERGE semantics. Signature enforcement has its own
    # tests below; pin the policy so this stays deterministic whether or not
    # the cryptography backend is importable in the current environment.
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "warn")
    sr.spine_reset()
    try:
        sr.record_spine_node("windows", "WA", {"category": "mesh",
                                                "updated_at": "2026-09-13T10:00:00"})
        sr.record_spine_node("wsl2", "WB", {"category": "synapse",
                                             "updated_at": "2026-09-13T10:00:00"})
        payload = _gossip_payload(
            spine_map={
                "windows:WA": {"category": "mesh", "updated_at": "2026-09-13T12:00:00"},  # newer -> wins
                "wsl2:WB": {"category": "synapse", "updated_at": "2026-09-13T09:00:00"},  # older -> loses
                "bare:WC": {"category": "immune", "updated_at": "2026-09-13T11:00:00"},   # new -> merges
            },
            homeostat={"mesh:WA": {"successes": 3, "failures": 0,
                                   "last_at": "2026-09-13T12:00:00", "hist_ms": [1.0]}},
            q_weights={"mesh:WA": 0.9, "mesh:NEW": -0.5},
            spine_events=[{"kind": "probe", "at": "2026-09-13T12:00:00"}],
        )
        res = sr.merge_spine_gossip(payload)
        assert res["merged"] is True
        m = sr.get_spine_map()
        assert m["windows:WA"]["updated_at"] == "2026-09-13T12:00:00"
        assert m["wsl2:WB"]["updated_at"] == "2026-09-13T10:00:00"
        assert "bare:WC" in m
        assert sr.get_q_weight("mesh", "NEW") == -0.5  # unknown locally -> adopted
        assert any(e["kind"] == "probe" for e in sr.get_spine_events())
    finally:
        sr.spine_reset()


def test_gossip_merge_idempotent_and_defensive(monkeypatch):
    """Re-merging the same payload is a no-op; malformed payloads never raise."""
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "warn")
    sr.spine_reset()
    try:
        payload = _gossip_payload(
            spine_map={"wsl2:X": {"category": "mesh", "updated_at": "2026-09-13T10:00:00"}},
            q_weights={"mesh:X": 0.4},
        )
        first = sr.merge_spine_gossip(payload)
        assert first["merged"] is True
        second = sr.merge_spine_gossip(payload)
        assert second["spine_nodes"] == 0 and second["q_weights"] == 0
        # malformed inputs: no exception, no state change
        assert sr.merge_spine_gossip("not-a-dict")["merged"] is False
        assert sr.merge_spine_gossip(None)["merged"] is False
        assert sr.merge_spine_gossip({"spine_map": "x", "homeostat": 5,
                                      "q_weights": [1]})["merged"] is True  # tolerated
        assert sr.get_spine_map()["wsl2:X"]["category"] == "mesh"
    finally:
        sr.spine_reset()


def test_gossip_exchange_roundtrip(tmp_path, monkeypatch):
    """gossip_exchange publishes locally and merges a peer's drop file."""
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "warn")
    sr.spine_reset()
    try:
        monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path)
        sr.record_spine_node("windows", "LocalW", {"category": "mesh",
                                                    "updated_at": "2026-09-13T10:00:00"})
        r1 = sr.gossip_exchange()
        assert r1["exchanged"] is True and r1["peers"] == []
        # simulate a peer's drop (fresh mtime)
        peer_payload = _gossip_payload(
            spine_map={"bare:PeerW": {"category": "immune",
                                       "updated_at": "2026-09-13T11:00:00"}},
            q_weights={"immune:PeerW": 0.7},
        )
        (tmp_path / "bare.json").write_text(json.dumps(peer_payload), encoding="utf-8")
        r2 = sr.gossip_exchange()
        assert r2["exchanged"] is True and "bare" in r2["peers"]
        assert "bare:PeerW" in sr.get_spine_map()
        assert sr.get_q_weight("immune", "PeerW") == 0.7
    finally:
        sr.spine_reset()


def test_gossip_sync_worm_registered_in_sync_category():
    """SpineGossipWorm fills the sync category and round-trips through execute().

    Regression guard: the spine worm is named SpineGossipWorm precisely so it
    does NOT collide with mesh.py's pre-existing GossipSyncWorm (registry keys
    by class name; a collision silently overwrites the original).
    """
    discover_worms()
    info = WORM_REGISTRY.get("SpineGossipWorm")
    assert info is not None
    assert info["category"] == "sync"
    # the in-mesh epidemic gossip worm must still be mesh-category, un-replaced
    orig = WORM_REGISTRY.get("GossipSyncWorm")
    assert orig is not None and orig["category"] == "mesh"
    assert orig["source_file"].endswith("mesh.py")
    from CORE.WORMS.expansion.spine import SpineGossipWorm
    res = SpineGossipWorm().execute()
    assert res["success"] is True
    assert res["result"]["exchanged"] is True


# ═════════════════════════════════════════════════════════════════
# Startup restore — spine state wired into startup_warm
# ═════════════════════════════════════════════════════════════════

def test_startup_restore_spine_state(tmp_path, monkeypatch):
    """restore_spine_state() loads persisted Q-weights into a wiped process."""
    from CORE import startup_warm
    sr.spine_reset()
    try:
        sr.record_outcome("mesh", "PersistW", True, 2.0)
        monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
        assert sr.save_spine_state()["saved"] is True
        sr.spine_reset()  # simulate fresh process
        res = startup_warm.restore_spine_state()
        assert res["restored"] is True
        assert sr.get_q_weight("mesh", "PersistW") > 0.0
        status = startup_warm.get_spine_restore_status()
        assert status is not None and status["restored"] is True
    finally:
        sr.spine_reset()


def test_startup_restore_missing_state_is_clean_noop(tmp_path, monkeypatch):
    """No state file -> {restored: False, reason: no_state_file}, no raise."""
    from CORE import startup_warm
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "missing.json")
    res = startup_warm.restore_spine_state()
    assert res == {"restored": False, "reason": "no_state_file"}
    assert startup_warm.get_spine_restore_status() == res


# ═════════════════════════════════════════════════════════════════
# Nervous system — reflex arc, scars, policy journal, route learning
# ═════════════════════════════════════════════════════════════════

@pytest.fixture()
def _reflex_worm():
    from CORE.worm_registry import register_worm
    @register_worm(category="mesh", priority=8, cooldown_seconds=30.0,
                   description="reflex test worm")
    class ReflexTestWorm:
        VERSION = "1.0.0"
        def execute(self):
            return {"success": True}
    yield "ReflexTestWorm"


def test_reflex_arc_fires_and_scars(_reflex_worm):
    """4 consecutive failures -> cooldown x3, priority -1, scar, journal entry."""
    worm = _reflex_worm
    sr.spine_reset()
    try:
        for i in range(sr._REFLEX_FAIL_THRESHOLD):
            sr.record_outcome("mesh", worm, False, 5.0, error=f"e{i}")
        info = WORM_REGISTRY[worm]
        assert info["cooldown_seconds"] == 90.0      # 30 * 3
        assert info["priority"] == 7                  # 8 - 1
        assert f"mesh:{worm}" in sr.get_scars()
        j = sr.get_policy_journal(1)
        assert j and j[0]["actor"] == "reflex"
        # scarred worm demoted below an unscarred peer in q_order
        assert sr.q_order("mesh", [worm, "SomeHealthyWorm"])[-1] == worm
    finally:
        sr.spine_reset()


def test_reflex_scar_heals_on_success(_reflex_worm):
    """G7: Graduated scar healing — heals after HEAL_THRESHOLD consecutive successes."""
    worm = _reflex_worm
    sr.spine_reset()
    try:
        for i in range(sr._REFLEX_FAIL_THRESHOLD):
            sr.record_outcome("mesh", worm, False, 5.0, error="x")
        assert f"mesh:{worm}" in sr.get_scars()
        # G7: first success fades the scar (heal_count=1), doesn't remove it
        sr.record_outcome("mesh", worm, True, 5.0)
        assert f"mesh:{worm}" in sr.get_scars()  # still scarred after 1 success
        assert sr.get_scars()[f"mesh:{worm}"].get("heal_count", 0) == 1
        # Second success fades more
        sr.record_outcome("mesh", worm, True, 5.0)
        assert sr.get_scars()[f"mesh:{worm}"].get("heal_count", 0) == 2
        # Third success fully heals
        sr.record_outcome("mesh", worm, True, 5.0)
        assert f"mesh:{worm}" not in sr.get_scars()  # fully healed
    finally:
        sr.spine_reset()


def test_revert_policy_undoes_reflex(_reflex_worm):
    """revert_policy() restores pre-reflex cooldown/priority from the journal."""
    worm = _reflex_worm
    sr.spine_reset()
    try:
        for i in range(sr._REFLEX_FAIL_THRESHOLD):
            sr.record_outcome("mesh", worm, False, 5.0, error="x")
        reverted = sr.revert_policy(actor="test", count=5)
        assert reverted, "expected at least one reverted entry"
        info = WORM_REGISTRY[worm]
        assert info["cooldown_seconds"] == 30.0
        assert info["priority"] == 8
    finally:
        sr.spine_reset()


def test_route_learning_adapts_weights():
    """MeshRouterWorm route weights move with recorded outcomes."""
    from CORE.WORMS.expansion.mesh import MeshRouterWorm
    sr.spine_reset()
    try:
        r = MeshRouterWorm()
        assert r.route("wsl2", {"x": 1})["effective_weight"] == 1.0  # cold
        for _ in range(6):
            r.record_route_result("wsl2", False, 50.0)
        bad = r.route("wsl2", {"x": 1})
        assert bad["q"] < 0.0 and bad["effective_weight"] < 1.0
        for _ in range(6):
            r.record_route_result("bare", True, 5.0)
        good = r.route("bare", {"x": 1})
        assert good["q"] > 0.0 and good["effective_weight"] > 1.0
    finally:
        sr.spine_reset()


@pytest.mark.slow  # LIVE heart running real beats: solo process (see --slow-solo).
def test_spine_heart_starts_and_stops(monkeypatch):
    # Opts out of the suite-wide heart pen (needs a LIVE heart).
    monkeypatch.delenv("AZAZAEL_NO_HEART", raising=False)
    """The autonomous heartbeat starts, beats at least once, and stops.

    The assertion is on a RECORDED BEAT, not on a completed cycle, and that is a
    correction rather than a relaxation (2026-09-21).

    Why the old assertion could not be satisfied. The heart calls
    `run_cycle(apply=True)`, and with `apply=True` the stage-0 proprioception
    gate refuses the cycle outright when the self-image is stale - which it is
    whenever a NEURAL_MESH artifact has aged out. Measured live: four were stale
    (`NEURAL_MESH_FUNCTION_TODO.md`, `NEURAL_MESH_WIRE_OR_CUT.json`,
    `NEURAL_MESH_INTEGRATION_MAP.json`, `NEURAL_MESH_INTEROP.mmd`), so every beat
    returned `success=False` before running a stage.

    That refusal is CORRECT - an applying cycle on a stale map of itself is what
    the 2026-09-18 audit removed. The defect was that it was INVISIBLE:
    `spine_cycle` was written only on the success path, and the worker logged
    nothing on a non-success report, so a heart refused on every single beat was
    indistinguishable from a heart that was never scheduled. Zero `spine_cycle`
    events existed on a running heart.

    So the property under test is what its name says - the heart beats, the beat
    is observable, and it stops - and a refused beat must name its reason. A
    completed cycle is a separate property, and asserting it here made this test
    a hostage to artifact freshness rather than to the heartbeat.
    """
    import time
    from CORE import startup_warm
    sr.spine_reset()                    # a beat must be produced by THIS test
    startup_warm.stop_spine_heart()     # ensure clean slate
    try:
        assert startup_warm.start_spine_heart(interval=1.5) is True
        # 60s, not 15s, and the reason is measured rather than generous: this
        # test takes ~13s when it runs ALONE (the first cycle is the expensive
        # one -- it loads state, then refuses or runs), so a 15s deadline was
        # inside its own noise. Under the aggregate suite's load the first cycle
        # passed 15s and the test failed with `beats == []` -- a liveness property
        # graded by a stopwatch, which is the Error #201 class again (Error #204).
        #
        # The property under test is "the heart beats and the beat is observable",
        # not "the first beat is fast", so the deadline only has to outlive the
        # first cycle. A heart that stopped is the real defect here, and it is
        # caught early and by name rather than by a timeout.
        deadline = time.time() + 60
        beats = []
        while time.time() < deadline:
            beats = [e for e in sr.get_spine_events()
                     if e.get("kind") == "spine_cycle"]
            if beats:
                break
            status = startup_warm.spine_heart_status()
            assert status.get("beating") is True, (
                "the heart stopped instead of beating, so no deadline would have "
                "helped: %r" % (status,))
            time.sleep(0.5)
        assert beats, "heart did not record a beat"

        detail = beats[0].get("detail") or {}
        assert detail.get("phase") in ("refused", "complete", "skipped"), \
            "a beat must say which phase it reached"
        assert "ran" in detail, "a beat must say whether it acted"
        if detail.get("phase") == "refused":
            assert detail.get("reason"), \
                "a refused beat must name why it refused, not fail silently"
    finally:
        startup_warm.stop_spine_heart()
    assert startup_warm.spine_heart_status()["beating"] is False


def test_dream_reinforce_moves_q_without_ledger():
    """dream_reinforce() moves Q but must not create ledger entries."""
    sr.spine_reset()
    try:
        assert sr.dream_reinforce("dream", "SleepyWorm", 0.8) is True
        assert sr.get_q_weight("dream", "SleepyWorm") > 0.0
        assert sr.get_homeostat_view() == {}   # simulations != executions
    finally:
        sr.spine_reset()


def test_is_idle_true_when_quiet_false_when_busy():
    """is_idle() gates the sleep phase on recent live outcomes."""
    sr.spine_reset()
    try:
        assert sr.is_idle(window_seconds=60.0) is True
        sr.record_outcome("mesh", "BusyWorm", True, 1.0)
        assert sr.is_idle(window_seconds=60.0) is False
    finally:
        sr.spine_reset()


def test_morphogenesis_dry_run_detects_chronic_pattern():
    """MorphogenesisWorm sees a chronic (worm, signature) failure pattern."""
    from CORE.WORMS.expansion.spine import MorphogenesisWorm
    sr.spine_reset()
    try:
        for i in range(8):
            sr.record_outcome("mesh", "ChronicWorm", False, 5.0,
                              error="FileNotFoundError: quota")
        m = MorphogenesisWorm().morph(dry_run=True)
        assert m["success"] is True and m["patterns"] >= 1
        assert m["generated"] == 0    # dry run never writes
    finally:
        sr.spine_reset()


def test_consolidation_refuses_when_not_idle():
    """The sleep phase is gated: no dreaming while the mesh is busy."""
    from CORE.WORMS.expansion.spine import ConsolidationWorm
    sr.spine_reset()
    try:
        sr.record_outcome("mesh", "BusyWorm", True, 1.0)
        res = ConsolidationWorm().consolidate()
        assert res.get("skipped") == "not_idle"
    finally:
        sr.spine_reset()


# ═════════════════════════════════════════════════════════════
# 2026-09-14 audit remediation — G1 wiring, G2 reward, F1 engram, G9 trust
# ═════════════════════════════════════════════════════════════

def test_wired_synapse_worms_have_explicit_consumers():
    """G1: the synapse layer must be DRIVEN, not merely constructed.

    Note the trap this test is designed around: `ClusterWORMZ.execute()` does
    `get_worms_by_category(...)` -> `_get_or_create_instance()` -> `cls()`, so
    every registered worm is constructed by the generic fleet sweep and a
    static "never constructed anywhere" scan is meaningless. What G1 was
    actually about is an explicit consumer that supplies data and reads the
    result. These three now have one: SpineCycleWorm.
    """
    from CORE.WORMS.expansion import spine as spine_mod
    from CORE.WORMS.expansion.synapse import (
        MoEDispatcherWorm, AttentionRouterWorm, SSMSequenceWorm,
    )
    cycle = spine_mod.SpineCycleWorm(min_interval_seconds=0)
    assert isinstance(cycle._attention, AttentionRouterWorm)
    assert isinstance(cycle._sequence, SSMSequenceWorm)
    assert isinstance(cycle._moe, MoEDispatcherWorm)


def test_spine_cycle_drives_synapse_layer_with_real_data():
    """G1: the cycle feeds the outcome stream in and reports the outputs."""
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    sr.spine_reset()
    try:
        for i in range(10):
            sr.record_outcome("immune", "ImmuneSentinelWorm", i % 2 == 0, 9.0)
            sr.record_outcome("mesh", "MeshRouterWorm", True, 3.0)
        rep = SpineCycleWorm(min_interval_seconds=0).run_cycle()
        layer = rep.get("synapse_routing") or {}
        assert layer.get("success") is True

        attn = layer["attention"]
        assert attn["success"] is True and attn["top_natures"]
        assert "mesh" in attn["attention"]

        seq = layer["sequence"]
        assert seq["success"] is True and seq["tokens"] >= 10
        assert seq["params_updated"] >= 1      # the readout was actually fitted

        moe = layer["moe"]
        assert moe["resolved"] is True and moe["experts"]
    finally:
        sr.spine_reset()


def test_synapse_update_preserves_reward_magnitude():
    """G2: magnitude reaches the Q-table — it used to collapse to a sign bit."""
    from CORE.WORMS.expansion.synapse import SynapseWeightWorm
    sr.spine_reset()
    try:
        s = SynapseWeightWorm(lr=0.5, gamma=0.9)
        s.update("win", "big", 1.0)
        big = s.weight("win", "big")
        s.update("win", "small", 0.05)
        small = s.weight("win", "small")
        assert big > small > 0.0, (big, small)
        # gamma must be real: a non-zero successor value changes the TD error
        s.update("win", "gamma_a", 0.0, next_value=0.0)
        s.update("win", "gamma_b", 0.0, next_value=1.0)
        assert s.weight("win", "gamma_b") > s.weight("win", "gamma_a")
        st = s.stats()
        assert st["gamma"] == 0.9 and st["lr"] == 0.5 and st["updates"] == 4
    finally:
        sr.spine_reset()


def test_engram_semantic_search_beats_dict_lookup():
    """F1: recall is by similarity, not by hash bucket."""
    from CORE.WORMS.expansion.engram import VectorShardWorm
    store = VectorShardWorm()
    store.add("d1", text=("pyflakes reports an undefined name when the import "
                          "is hidden inside a docstring"))
    store.add("d2", text="f-string code generator forgot to escape curly braces")
    store.add("d3", text="the database connection pool was exhausted under load")
    res = store.search("undefined name import hidden in a docstring", top_k=3)
    assert res["success"] is True and res["hits"]
    assert res["hits"][0]["mem"] == "d1"
    assert res["hits"][0]["score"] > res["hits"][-1]["score"]
    assert store.stats()["index"]["size"] == 3


def test_engram_shard_placement_stable_and_recall_routes_to_owner():
    """Placement is deterministic; recall routes to the owning shard."""
    from CORE.WORMS.expansion.engram import (
        VectorShardWorm, RecallRouterWorm,
    )
    a, b = VectorShardWorm(), VectorShardWorm()
    assert a.shard_for("mem-xyz") == b.shard_for("mem-xyz")

    store = VectorShardWorm()
    store.add("hit", text="the barrier certificate rejection was filed late")
    routed = RecallRouterWorm().route_recall(
        "late filing of a rejection certificate", index=store)
    assert routed["mode"] == "semantic"
    assert routed["route"] in VectorShardWorm.ENVS

    # id fast path needs no embedding at all
    fast = RecallRouterWorm().route_recall("hit", shard_map={"hit": "wsl2"})
    assert fast["mode"] == "owner_lookup" and fast["route"] == "wsl2"

    empty = RecallRouterWorm().route_recall("nothing is indexed here",
                                            index=VectorShardWorm())
    assert empty["fallback"] is True and empty["route"] == "g_archive"


def test_engram_coherence_detects_near_duplicate_drift():
    """F1: exact-hash equality still works, and drift is now classified."""
    from CORE.WORMS.expansion.engram import CoherenceValidatorWorm
    cv = CoherenceValidatorWorm()
    assert cv.check("m", ["aaaa", "aaaa"])["mode"] == "exact"

    drift = cv.check("m2", ["h1", "h2"], replica_texts=[
        "the quick brown fox jumps over the lazy dog",
        "the quick brown fox jumped over the lazy dog",
    ], threshold=0.5)
    assert drift["mode"] == "drift" and drift["coherent"] is True

    div = cv.check("m3", ["h1", "h2"], replica_texts=[
        "database connection pool exhausted under load",
        "sunflower gardening tips for dry climates",
    ], threshold=0.9)
    assert div["coherent"] is False

    rep = cv.report()
    assert rep["mismatched"] == 2 and rep["near_duplicate_drift"] == 1


def test_moe_dispatcher_resolves_real_experts():
    """G1: the dispatcher ranks only experts it can actually import."""
    from CORE.WORMS.expansion.synapse import MoEDispatcherWorm
    m = MoEDispatcherWorm(top_k=3)
    avail = m.available_experts()
    res = m.dispatch()
    assert res["resolved"] is True
    assert set(res["experts"]).issubset({k for k, v in avail.items() if v})
    assert m.get_expert("InferenceWorm") is not None
    assert m.get_expert("NoSuchExpertWorm") is None


def test_moe_feedback_moves_the_shared_q_table():
    """G1/G2: feedback lands in the spine ledger and routing follows it."""
    from CORE.WORMS.expansion.synapse import MoEDispatcherWorm
    sr.spine_reset()
    try:
        m = MoEDispatcherWorm(top_k=1)
        assert sr.get_q_weight("moe", "NimInferenceWorm") == 0.0
        for _ in range(5):
            m.feedback("NimInferenceWorm", 1.0)
        assert sr.get_q_weight("moe", "NimInferenceWorm") > 0.0
        assert m.dispatch()["experts"] == ["NimInferenceWorm"]
    finally:
        sr.spine_reset()


def test_moe_cascade_does_not_read_an_unbound_answer():
    """B6: every expert raising must not become an UnboundLocalError.

    `answer` is bound only on a successful attempt, but the below-threshold
    fall-through read it unconditionally, so the documented "best effort"
    outcome was a crash on the one path it was written for (adversarial audit
    2026-09-16, B6).
    """
    from CORE.WORMS.expansion.synapse import MoEDispatcherWorm
    m = MoEDispatcherWorm(top_k=3)
    m.dispatch = lambda *a, **k: {"experts": ["A", "B", "C"]}
    m.get_expert = lambda name: object()
    calls = []

    def always_raises(inst, task):
        calls.append(task)
        raise RuntimeError("boom")

    res = m.dispatch_cascade("t", always_raises)
    assert res["success"] is False
    assert res["error"] == "all_experts_failed"
    assert len(calls) == 3, "every ranked expert should have been attempted"
    assert len(res["trail"]) == 3
    assert all("error" in row for row in res["trail"])


def test_moe_cascade_reports_the_best_attempt_not_the_last():
    """B6 second half: `best_effort_last_expert` did not deliver its own claim.

    After the loop `name` is whatever was ranked last, which is unrelated to
    which attempt scored best, so the note asserted a selection the code did
    not perform.
    """
    from CORE.WORMS.expansion.synapse import MoEDispatcherWorm
    m = MoEDispatcherWorm(top_k=3)
    m.dispatch = lambda *a, **k: {"experts": ["A", "B", "C"]}
    m.get_expert = lambda name: object()
    conf = {"A": 0.10, "B": 0.90, "C": 0.20}

    def scored(inst, task):
        name = scored.seen.pop(0)
        return ("ans-" + name, conf[name])
    scored.seen = ["A", "B", "C"]

    res = m.dispatch_cascade("t", scored, threshold=0.99)
    assert res["success"] is True
    assert res["via"] == "B", "expected the highest-confidence attempt, got %r" % res["via"]
    assert res["answer"] == "ans-B"
    assert res["note"] == "best_effort_best_expert"


def test_ssm_fits_an_input_dependent_readout():
    """G5: B/C are input-dependent and the readout is actually fitted."""
    from CORE.WORMS.expansion.synapse import SSMSequenceWorm
    ssm = SSMSequenceWorm(state_dim=8)
    init_c = [list(w) for w in ssm._W_c]
    init_b = [list(w) for w in ssm._W_b]
    res = ssm.ingest([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
    assert res["tokens"] == 8
    summary = ssm.summarize()
    assert summary["params_updated"] >= 1
    assert ssm.train_error() >= 0.0
    assert ssm._W_c != init_c       # readout moved off its random init
    assert ssm._W_b != init_b       # Hebbian approximation moved too


def test_dream_consolidation_indexes_kept_dreams():
    """F1: a kept dream becomes recallable instead of evaporating."""
    from CORE.WORMS.expansion.engram import (
        DreamConsolidatorWorm, VectorShardWorm,
    )
    store = VectorShardWorm()
    dc = DreamConsolidatorWorm()
    res = dc.consolidate([
        {"salience": 0.9, "id": "dream-1",
         "text": "throttle the scanner when the disk queue backs up"},
        {"salience": 0.1, "text": "low salience, dropped"},
    ], index=store)
    assert res["kept"] == 1 and res["indexed"] == 1
    hits = store.search("disk queue throttling for the scanner", top_k=1)["hits"]
    assert hits and hits[0]["mem"] == "dream-1"


def test_gossip_export_is_sequenced():
    """G9: every export carries a strictly increasing sequence number."""
    p1 = sr.export_spine_gossip()
    p2 = sr.export_spine_gossip()
    assert p2["seq"] == p1["seq"] + 1
    assert "signature" in p2 and "signer_pub" in p2
    state = sr.get_gossip_trust_state()
    assert state["policy"] in ("strict", "warn", "off")
    if state["crypto_available"]:
        assert p2["signature"] and p2["signer_pub"]


def test_gossip_rejects_replayed_sequence(monkeypatch):
    """G9: a payload at or below the accepted high-water mark is refused."""
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "off")
    sr.spine_reset()
    try:
        assert sr.merge_spine_gossip(_gossip_payload(seq=5))["merged"] is True
        stale = sr.merge_spine_gossip(_gossip_payload(
            seq=3,
            spine_map={"windows:X": {"category": "mesh",
                                     "updated_at": "2026-09-13T12:00:00"}}))
        assert stale["merged"] is False
        assert stale["rejected"] == "replayed_seq"
        assert "windows:X" not in sr.get_spine_map()
        # equal seq stays admitted: merge is idempotent by contract
        assert sr.merge_spine_gossip(_gossip_payload(seq=5))["merged"] is True
    finally:
        sr.spine_reset()


def test_gossip_rejects_env_key_change(monkeypatch):
    """G9: an env may not swap its identity key after being pinned."""
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "off")
    sr.spine_reset()
    try:
        ok = sr.merge_spine_gossip(_gossip_payload(seq=1, signer_pub="KEY_A"))
        assert ok["merged"] is True
        bad = sr.merge_spine_gossip(_gossip_payload(seq=2, signer_pub="KEY_B"))
        assert bad["merged"] is False and bad["rejected"] == "key_changed"
    finally:
        sr.spine_reset()


def test_gossip_strict_policy_contract(monkeypatch):
    """G9: strict requires a signature when crypto works, and admits the
    degradation out loud when it does not (never silently 'authenticated')."""
    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "strict")
    crypto = sr.get_gossip_trust_state()["crypto_available"]
    res = sr.check_gossip_trust({"gossip_from": "peer", "seq": 1})
    if crypto:
        assert res["ok"] is False and res["reason"] == "unsigned_payload"
    else:
        assert res["ok"] is True and res["signature"] == "unavailable"


def test_gossip_signature_plumbing_with_stubbed_crypto(monkeypatch):
    """G9: sig sign/verify/pin/reject plumbing is covered without real crypto.

    The machine's `cryptography` backend is currently broken (see the audit's
    G9 note), which would leave this code path entirely unverified. Stubbing
    the crypto accessor makes the PLUMBING deterministic — canonical form,
    signature attachment, verification, strict-mode refusal of unsigned
    payloads, and tamper detection — independent of the environment.
    """
    import hashlib as _hl

    def _sign(canonical):
        return _hl.sha256(("s::" + canonical).encode("utf-8")).hexdigest()

    def _verify(canonical, sig, pub):
        return sig == _hl.sha256(("s::" + canonical).encode("utf-8")).hexdigest()

    monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "strict")
    monkeypatch.setattr(sr, "_gossip_crypto",
                        lambda: (_sign, _verify, "FAKEPUB"))
    sr.spine_reset()
    try:
        assert sr.get_gossip_trust_state()["crypto_available"] is True
        payload = sr.export_spine_gossip()
        assert payload["signature"] and payload["signer_pub"] == "FAKEPUB"
        assert sr.merge_spine_gossip(payload)["signature"] == "verified"

        # strict mode + working crypto => an unsigned peer is refused
        unsigned = sr.merge_spine_gossip(_gossip_payload(seq=99))
        assert unsigned["merged"] is False
        assert unsigned["rejected"] == "unsigned_payload"

        # editing ANY signed collection invalidates the signature
        tampered_payload = sr.export_spine_gossip()
        tampered_payload["q_weights"]["mesh:evil"] = 0.99
        tampered = sr.check_gossip_trust(tampered_payload)
        assert tampered["ok"] is False and tampered["reason"] == "bad_signature"

        # ...including editing the sequence number itself
        seq_tampered = sr.export_spine_gossip()
        seq_tampered["seq"] = seq_tampered["seq"] + 10
        assert sr.check_gossip_trust(seq_tampered)["reason"] == "bad_signature"
    finally:
        sr.spine_reset()


def test_gossip_signed_roundtrip_and_tamper_detection(monkeypatch):
    """G9: a real export verifies end-to-end, and tampering invalidates it."""
    sr.spine_reset()
    try:
        if not sr.get_gossip_trust_state()["crypto_available"]:
            pytest.skip("cryptography backend unavailable on this machine "
                        "(see neural mesh audit, G9 remediation note)")
        monkeypatch.setenv("AZAZAEL_GOSSIP_POLICY", "strict")
        payload = sr.export_spine_gossip()
        assert sr.merge_spine_gossip(payload)["signature"] == "verified"
        payload["q_weights"]["mesh:tampered"] = 0.99
        tampered = sr.check_gossip_trust(payload)
        assert tampered["ok"] is False
        assert tampered["reason"] == "bad_signature"
    finally:
        sr.spine_reset()


def test_stop_spine_heart_waits_for_an_in_flight_beat(monkeypatch):
    """`stop` must mean STOPPED, not merely "flag cleared".

    MEASURED 2026-09-21: clearing the flag alone left a beat inside
    `run_cycle()`, holding the process-global cycle gate. The next same-process
    caller -- a test, the neural loop, a CLI -- was refused with
    `skipped: cycle_already_running_global`, by a heartbeat it had just been told
    was stopped. `spine_heart_status()["beating"] is False` was true of the FLAG
    and false of the PROCESS.

    The beat is held open deliberately, so this cannot pass by being early.
    """
    import threading as _t
    from CORE import startup_warm
    from CORE.WORMS.expansion import spine as spine_mod

    # Live-heart test: opt out of the suite-wide pen.
    monkeypatch.delenv("AZAZAEL_NO_HEART", raising=False)
    entered = _t.Event()
    release = _t.Event()

    class _FakeCycle:
        def __init__(self, *a, **k):
            pass

        def run_cycle(self, *a, **k):
            entered.set()
            release.wait(20)
            return {"success": True}

    monkeypatch.setattr(spine_mod, "SpineCycleWorm", _FakeCycle)
    startup_warm.stop_spine_heart()
    try:
        assert startup_warm.start_spine_heart(interval=0.5) is True
        assert entered.wait(10), "the heartbeat never started a beat"

        stopped = _t.Event()

        def _stop():
            startup_warm.stop_spine_heart()
            stopped.set()

        stopper = _t.Thread(target=_stop)
        stopper.start()
        # A beat is in flight, so the stop must NOT have returned yet.
        assert not stopped.wait(0.4), \
            "stop returned while a beat was still running"
        release.set()
        assert stopped.wait(10), "stop never returned after the beat finished"
        stopper.join(10)
        assert startup_warm.spine_heart_status()["thread_alive"] is False, \
            "stop claimed to have stopped a heartbeat whose thread is alive"
    finally:
        release.set()
        startup_warm.stop_spine_heart()
