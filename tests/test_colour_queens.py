# coding: utf-8
"""Acceptance tests for the colour queens, teams and gates (audit §5, 09-16).

Findings covered:
- R1/R2/R4  hostile probes + rotating coverage. The SCOUT QUOTA half is tested
            here (`ScoutBeeWorm`), because it is now a policy object with a
            named consumer (`RedQueenWorm._probe_candidates`) instead of an
            inline sort. Unit-testing it in isolation is the point.
- R5        quarantine is enforced in the execution pipeline (tested in
            test_spine_mesh_telemetry-style isolation below, see the gate test).
- R6        `order_quarantine(force=True)` is the single-env operator channel.
- §5.1      `BlackQueenWorm` — adaptive loss; a retirement is quorum-gated AND
            enforced by `ClusterWORMZ.execute_all` (a decorated table is not a
            retirement).
- §5.2      `GrayQueenWorm` — entrenchment detector over the PF16 census delta,
            snapshot persisted per session, delta reported instead of totals.
- §5.6      Yellow — `AttestationWorm` gates `MorphogenesisWorm.promote()`, so
            an uncompilable candidate cannot become discoverable.
- §5.7      White — `WormGovernor.ratify_policy()` is the runtime consumer of
            1042 lines of governance: a journalled policy change must be
            ratified before it is replayed into a live registry.

Invariants carried over from `rainbow_queens_test`: base ABC has NO queen, and
`requeen` is NOT an operator.

Everything is isolated: spine state to tmp, census/snapshot via AZAZAEL_*
env overrides, generated source via AZAZAEL_GENERATED_DIR. The developer's real
`~/.azazael/` state and the real repo census are never read or written.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr
from CORE.worm_registry import WORM_REGISTRY


@pytest.fixture(autouse=True)
def isolated_spine_state(tmp_path, monkeypatch):
    """Keep the suite off the developer's real dot-directory and repo files."""
    monkeypatch.setattr(sr, "_STATE_FILE", tmp_path / "spine_state.json")
    monkeypatch.setattr(sr, "_GOSSIP_DIR", tmp_path / "gossip")
    monkeypatch.setenv("AZAZAEL_GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("AZAZAEL_ENTRENCHMENT_PATH",
                       str(tmp_path / "entrenchment.json"))
    sr.spine_reset()
    yield
    sr.spine_reset()


def _write_census(path: Path, statuses: dict) -> None:
    import json
    declared = {k: {"module": k.split(":")[0], "qualname": k,
                    "line": 1, "status": v, "reason": ""}
                for k, v in statuses.items()}
    path.write_text(json.dumps({"generated_at": "2026-09-17T00:00:00",
                                "probe": "PF16",
                                "counts": {}, "total": len(declared),
                                "declared": declared}), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# Scout Bee — queenless exploration quota
# ═══════════════════════════════════════════════════════════════════

def test_scout_has_no_queen_and_no_requeen_operator():
    """Base ABC has no queen; `requeen` is not an operator (toy invariant)."""
    from CORE.WORMS.expansion import queens
    scout = queens.ScoutBeeWorm()
    assert not hasattr(scout, "queen"), "base ABC must not have a queen"
    assert not hasattr(queens, "requeen"), "requeen is not an operator"
    assert not hasattr(queens.ScoutBeeWorm, "requeen")


def test_scout_reserves_slots_for_the_ignorant():
    import math
    from CORE.WORMS.expansion.queens import ScoutBeeWorm
    scout = ScoutBeeWorm()
    eligible = [(f"W{i}", {}) for i in range(8)]
    # Nothing probed yet: a fresh member is 'ignorant', so the reserved share
    # must be filled from the unpicked set (and the total must be exact).
    out = scout.select(eligible, 4, last_probed={})
    assert len(out["chosen"]) == 4
    expected = max(1, int(math.ceil(4 * scout.SCOUT_FRACTION)))
    assert len(out["scout"]) == expected, (out, expected)
    assert set(out["scout"]) <= {n for n, _ in out["chosen"]}


def test_scout_rotates_coverage_and_satisfies_the_quota():
    """Over K passes, no eligible member starves — the R4 fix, as a policy."""
    from CORE.WORMS.expansion.queens import ScoutBeeWorm
    scout = ScoutBeeWorm()
    eligible = [(f"W{i}", {}) for i in range(6)]
    last: dict = {}
    for tick in range(12):
        out = scout.select(eligible, 2, last_probed=last)
        assert len(out["chosen"]) == 2
        for name, _ in out["chosen"]:
            last[name] = float(tick + 1)
    assert scout.coverage_quota_met(eligible, last), \
        "every eligible member must be probed at least once"
    assert len(last) == len(eligible)


def test_scout_selection_is_not_frozen_to_registration_order():
    """A fixed subset must not be re-picked forever (the R2 popularity pump)."""
    from CORE.WORMS.expansion.queens import ScoutBeeWorm
    scout = ScoutBeeWorm()
    eligible = [(f"W{i}", {}) for i in range(6)]
    last: dict = {}
    passes = []
    for tick in range(6):
        out = scout.select(eligible, 2, last_probed=last)
        passes.append(tuple(sorted(n for n, _ in out["chosen"])))
        for name, _ in out["chosen"]:
            last[name] = float(tick + 1)
    assert len(set(passes)) > 1, "coverage is frozen: same subset every pass"


def test_red_queen_delegates_selection_to_the_scout_policy():
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    assert rq._scout is not None, "RedQueen must hold a ScoutBeeWorm policy"
    out = rq.pressure_probe("immune", limit=2)
    assert "scouts" in out and "retired_skipped" in out


# ═══════════════════════════════════════════════════════════════════
# Gray Queen — entrenchment detector (delta, not totals)
# ═══════════════════════════════════════════════════════════════════

def test_gray_reports_unknown_when_the_census_is_unreadable(tmp_path):
    from CORE.WORMS.expansion.queens import GrayQueenWorm
    gq = GrayQueenWorm()
    out = gq.detect(census_path=tmp_path / "nope.json",
                    snapshot_path=tmp_path / "snap.json")
    assert out["success"] is False and out["reason"] == "census_unreadable", \
        "a missing census is UNKNOWN, never clean"


def test_gray_names_the_delta_not_the_total(tmp_path):
    from CORE.WORMS.expansion.queens import GrayQueenWorm
    gq = GrayQueenWorm()
    census = tmp_path / "census.json"
    snap = tmp_path / "snap.json"

    # Session 1: three orphans.
    _write_census(census, {"A:f": "pending", "B:g": "pending", "C:h": "pending"})
    first = gq.detect(census_path=census, snapshot_path=snap, persist=True)
    assert first["success"] and first["counts"]["new"] == 3
    assert first["counts"]["entrenched"] == 0, "no baseline yet: nothing entrenched"

    # Session 2: B gained a caller (resolved); D lost its last caller (new).
    _write_census(census, {"A:f": "pending", "B:g": "wired",
                           "C:h": "pending", "D:i": "pending"})
    second = gq.detect(census_path=census, snapshot_path=snap, persist=True)
    assert second["new"] == ["D:i"], "newly entrenched loss must appear same session"
    assert second["resolved"] == ["B:g"], "a gained caller must leave the list"
    assert second["entrenched"] == ["A:f", "C:h"], "the ratchet: orphan both sessions"
    assert second["counts"]["delta"] == 0  # 3 -> 3, but composition changed


def test_gray_execute_never_writes_the_snapshot(tmp_path, monkeypatch):
    from CORE.WORMS.expansion.queens import GrayQueenWorm
    census = tmp_path / "census.json"
    snap = tmp_path / "snap.json"
    _write_census(census, {"A:f": "pending"})
    gq = GrayQueenWorm()
    gq.execute()
    assert not snap.exists(), "a probe must never mutate the snapshot"


def test_red_queen_probes_surface_the_retired_skip():
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    for nat in rq.PROBE_NATURES:
        out = rq.pressure_probe(nat, limit=1)
        assert isinstance(out.get("retired_skipped"), list)


# ═══════════════════════════════════════════════════════════════════
# Black Queen — adaptive loss + enforced retirement
# ═══════════════════════════════════════════════════════════════════

class _TwinAlpha:
    VERSION = "1.0.0"

    def execute(self):
        return {"success": True}

    def run(self):
        return 1

    def tick(self):
        return 2

    def helper(self):
        return 3


class _TwinBeta:
    """Same method signature set => a community-provided capability."""

    VERSION = "1.0.0"

    def execute(self):
        return {"success": True}

    def run(self):
        return 1

    def tick(self):
        return 2

    def helper(self):
        return 3


@pytest.fixture
def twin_registry():
    """A registry holding ONLY two structurally identical worms, then restore.

    The candidate set is deliberately reduced to the pair under test: the real
    fleet already has many thinly-evidenced, twin-covered worms, so a shared
    registry would make `candidates()` order-dependent and the assertions
    would be measuring the live fleet instead of the mechanism.
    """
    saved = dict(WORM_REGISTRY)
    WORM_REGISTRY.clear()
    WORM_REGISTRY.update({
        "TwinAlphaWorm": {
            "class": _TwinAlpha, "category": "analysis", "priority": 5,
            "cooldown_seconds": 30.0, "version": "1.0.0", "tags": set(),
            "source_file": "queens_test.py", "lifecycle_state": None},
        "TwinBetaWorm": {
            "class": _TwinBeta, "category": "analysis", "priority": 5,
            "cooldown_seconds": 30.0, "version": "1.0.0", "tags": set(),
            "source_file": "queens_test.py", "lifecycle_state": None},
    })
    try:
        yield WORM_REGISTRY
    finally:
        WORM_REGISTRY.clear()
        WORM_REGISTRY.update(saved)


def test_black_requires_a_twin_and_thin_evidence(twin_registry):
    from CORE.WORMS.expansion.queens import BlackQueenWorm
    bw = BlackQueenWorm()
    cands = {c["worm"] for c in bw.candidates()}
    assert "TwinAlphaWorm" in cands and "TwinBetaWorm" in cands, \
        "a twin-covered, evidence-free worm is a retirement candidate"
    assert all(c["leak"]["similarity"] >= bw.MIN_SIMILARITY for c in bw.candidates())


def test_black_never_retires_a_fleet_governor(twin_registry):
    from CORE.worm_registry import is_protected_worm
    from CORE.WORMS.expansion.queens import BlackQueenWorm
    WORM_REGISTRY["TwinAlphaWorm"]["fleet_governor"] = True
    assert is_protected_worm(WORM_REGISTRY["TwinAlphaWorm"])
    cands = {c["worm"] for c in BlackQueenWorm().candidates()}
    assert "TwinAlphaWorm" not in cands, "a regulator is a wiring gap, not a loss"


def test_black_quorum_gate_holds_on_a_lone_env(twin_registry):
    from CORE.WORMS.expansion.queens import BlackQueenWorm
    out = BlackQueenWorm().propose(apply=False)
    assert out["retired"] == 0, "one env must not self-retire"
    assert all(h["reason"] == "awaiting_quorum" for h in out["pending"])


def test_black_force_channel_is_audited_and_effective(twin_registry):
    from CORE.WORMS.expansion.queens import BlackQueenWorm
    out = BlackQueenWorm().propose(force=True, apply=True)
    assert out["retired"] >= 1
    rec = out["proposals"][0]
    assert rec["forced"] is True and rec["applied"] is True
    assert WORM_REGISTRY[rec["worm"]]["lifecycle_state"] == "retired"

    # …and the retirement is ENFORCED, not merely recorded.
    # U3: enforcement moved from ClusterWORMZ.execute to ClusterWORMZ.
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
    cluster = ClusterWORMZ.build_from_registry([rec["worm"]])
    res = cluster.execute_all(worm_names=[rec["worm"]])
    agg = cluster.aggregate_results()
    assert rec["worm"] in agg.retired_skipped
    assert rec["worm"] not in agg.worm_results


def test_black_marks_census_rows_retired(tmp_path, monkeypatch, twin_registry):
    from CORE.WORMS.expansion.queens import BlackQueenWorm
    census = tmp_path / "census.json"
    monkeypatch.setenv("AZAZAEL_WIRE_OR_CUT_PATH", str(census))
    _write_census(census, {"TwinAlphaWorm:foo": "pending",
                           "TwinAlphaWorm:bar": "wired"})
    out = BlackQueenWorm().propose(force=True, apply=True)
    rows = __import__("json").loads(census.read_text(encoding="utf-8"))["declared"]
    assert rows["TwinAlphaWorm:foo"]["status"] == "retired"
    assert rows["TwinAlphaWorm:foo"]["reason"], "a retirement must explain itself"
    assert rows["TwinAlphaWorm:bar"]["status"] == "wired", \
        "a wired row is a real caller and is never overridden"
    assert out["retired"] >= 1


# ═══════════════════════════════════════════════════════════════════
# R6 — single-env operator channel for quarantine
# ═══════════════════════════════════════════════════════════════════

def test_quarantine_force_channel_reaches_a_lone_env():
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    for _ in range(4):
        sr.record_outcome("immune", "ChronicWorm", False, 1.0, "boom")
    assert rq.order_quarantine() == [], "no peers => quorum unreachable"
    ordered = rq.order_quarantine(force=True)
    assert ordered and ordered[0]["worm"] == "ChronicWorm"
    assert ordered[0]["forced"] is True
    assert sr.is_quarantined("ChronicWorm"), \
        "force must actually gate the worm, not just report it"


def test_quarantine_still_cannot_be_reached_by_one_env_without_force():
    from CORE.WORMS.expansion.spine import RedQueenWorm
    rq = RedQueenWorm()
    for _ in range(4):
        sr.record_outcome("consensus", "LonelyWorm", False, 1.0, "boom")
    assert rq.order_quarantine() == []
    assert not sr.is_quarantined("LonelyWorm"), "the safety intent is preserved"


def test_quarantine_gate_holds_a_worm_out_of_the_pipeline(twin_registry):
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
    cluster = ClusterWORMZ.build_from_registry(["TwinAlphaWorm", "TwinBetaWorm"])
    victim = sorted(c.name for c in cluster.configs)[0]
    sr.quarantine_item(victim, "test:gate")
    try:
        res = cluster.execute_all(worm_names=[victim])
        agg = cluster.aggregate_results()
        assert agg.quarantined_skipped == [victim]
        assert not agg.worm_results
    finally:
        sr.release_item(victim)
    res2 = cluster.execute_all(worm_names=[victim])
    agg2 = cluster.aggregate_results()
    assert agg2.worm_results and not agg2.quarantined_skipped


# ═══════════════════════════════════════════════════════════════════
# Yellow — attestation gates promotion
# ═══════════════════════════════════════════════════════════════════

def _stage(name: str, source: str, tmp_path: Path) -> Path:
    pen = tmp_path / "generated" / "_staging"
    pen.mkdir(parents=True, exist_ok=True)
    p = pen / name
    p.write_text(source, encoding="utf-8")
    return p


def test_yellow_refuses_an_uncompilable_candidate(tmp_path):
    from CORE.WORMS.expansion.spine import MorphogenesisWorm
    bad = _stage("gen_broken.py", "def oops(:\n    pass\n", tmp_path)
    out = MorphogenesisWorm().promote(force=True)
    assert out["promoted"] == 0
    held = [h for h in out["pending"] if h["file"] == bad.name]
    assert held and held[0]["reason"] == "attestation_failed", \
        "an uncompilable candidate must not become discoverable"
    assert bad.exists(), "a refused file stays in the containment pen"
    assert not (tmp_path / "generated" / bad.name).exists()


def test_yellow_promotes_and_records_a_digest(tmp_path):
    from CORE.WORMS.expansion.spine import MorphogenesisWorm
    good = _stage("gen_ok.py", "VALUE = 1\n", tmp_path)
    out = MorphogenesisWorm().promote(force=True)
    assert out["promoted"] == 1, out
    rec = out["promotions"][0]
    assert rec["file"] == good.name and rec["sha"], "digest must be recorded"
    assert (tmp_path / "generated" / good.name).exists()
    assert not good.exists()


# ═══════════════════════════════════════════════════════════════════
# White — policy ratification (the governance consumer)
# ═══════════════════════════════════════════════════════════════════

def _entry(worm: str, action: str = "policy_adjust", **changes) -> dict:
    return {"actor": "homeostat", "action": action, "worm": worm,
            "changes": changes, "at": "2026-09-17T00:00:00"}


def test_white_refuses_policy_for_an_unregistered_worm():
    from CORE.worm_governance import WormGovernor
    verdict = WormGovernor().ratify_policy(
        _entry("NoSuchWormEver"), registry={})
    assert verdict["approved"] is False
    assert "worm_not_registered" in verdict["reasons"]


def test_white_refuses_policy_for_a_retired_worm(twin_registry):
    from CORE.worm_governance import WormGovernor
    WORM_REGISTRY["TwinAlphaWorm"]["lifecycle_state"] = "retired"
    verdict = WormGovernor().ratify_policy(
        _entry("TwinAlphaWorm"), registry=WORM_REGISTRY)
    assert verdict["approved"] is False
    assert "worm_retired" in verdict["reasons"], \
        "a Black-Queen retirement must not be undone by a stale policy journal"


def test_white_refuses_out_of_range_values(twin_registry):
    from CORE.worm_governance import WormGovernor
    bad = _entry("TwinAlphaWorm",
                 priority={"from": 5, "to": 99},
                 cooldown_seconds={"from": 30.0, "to": -1.0})
    verdict = WormGovernor().ratify_policy(bad, registry=WORM_REGISTRY)
    assert verdict["approved"] is False
    assert "priority_out_of_range" in verdict["reasons"]
    assert "cooldown_out_of_range" in verdict["reasons"]


def test_white_approves_a_valid_policy(twin_registry):
    from CORE.worm_governance import WormGovernor
    ok = _entry("TwinAlphaWorm",
                priority={"from": 5, "to": 7},
                cooldown_seconds={"from": 30.0, "to": 90.0})
    verdict = WormGovernor().ratify_policy(ok, registry=WORM_REGISTRY)
    assert verdict["approved"] is True, verdict
    assert verdict["reasons"] == []


def test_white_journal_entries_survive_journaling(twin_registry):
    """End-to-end: a journalled change is readable back and ratifiable."""
    sr.journal_policy("homeostat", "policy_adjust", "TwinAlphaWorm",
                      {"priority": {"from": 5, "to": 7}})
    entries = sr.get_policy_journal()
    assert entries and entries[0]["worm"] == "TwinAlphaWorm"
    from CORE.worm_governance import WormGovernor
    assert WormGovernor().ratify_policy(entries[0],
                                        registry=WORM_REGISTRY)["approved"] is True


# ═══════════════════════════════════════════════════════════════════
# §5.1b — the comparator validates itself before the queen may act
#
# Every Black-Queen candidate is justified SOLELY by DedupAnalyzer's score, so
# the score is an instrument, and an instrument that ships unvalidated is how
# 35 worms -- including EvolutionEngine, DreamingModule and GeneticOptimizer --
# came to be proposed for retirement on the strength of a shared generic verb.
# These tests hold both directions of the calibration and the fail-closed rule.
# ═══════════════════════════════════════════════════════════════════

def test_dedup_comparator_passes_its_own_control_pairs():
    """The instrument is checked on cases whose answer is already known."""
    from CORE.worm_governance import DedupAnalyzer
    report = DedupAnalyzer().self_check()
    assert report["trustworthy"] is True, report
    assert report["failures"] == [], report
    controls = report["controls"]
    assert controls["interface_only"]["matched"] is False
    assert controls["same_name_different_work"]["matched"] is False
    assert controls["genuine_twins"]["matched"] is True
    assert controls["genuine_twins"]["similarity"] == 1.0


def test_interface_only_twins_are_not_a_duplicate():
    """The degenerate pair is pinned directly, without going through `_twin`.

    Two classes sharing only `execute` are IDENTICAL to a name-only Jaccard
    (1.0, `exact_signature`), because `Jaccard({x}, {x})` is 1.0. They must be
    different to the comparator: a shared call that most of the fleet defines is
    evidence of the interface, not of duplication.
    """
    from CORE.worm_governance import DedupAnalyzer

    class OnlyExecuteA:
        def execute(self):
            return {"cert_days_left": 3}

    class OnlyExecuteB:
        def execute(self):
            return {"firewall_rules": 41}

    match = DedupAnalyzer().compare_worms(
        "A", {"class": OnlyExecuteA, "category": "x"},
        "B", {"class": OnlyExecuteB, "category": "x"})
    assert match is None, (
        "a shared `execute` cannot carry a duplication match -- acting on it is "
        "what proposed retiring the learning subsystem")


def test_a_shared_distinctive_name_with_unrelated_bodies_is_not_a_duplicate():
    """A name-level match is necessary but never sufficient."""
    from CORE.worm_governance import DedupAnalyzer

    class SameNameA:
        def execute(self):
            return {"rules": 7}

        def audit(self):
            return {"hosts": ["a"], "rules": 7}

    class SameNameB:
        def execute(self):
            return {"rules": 7}

        def audit(self):
            return {"invoice_total": "unrelated"}

    match = DedupAnalyzer().compare_worms(
        "A", {"class": SameNameA, "category": "x"},
        "B", {"class": SameNameB, "category": "x"})
    assert match is None, "shared method names with unrelated bodies are a coincidence"


def test_identical_implementations_still_match():
    """The opposite failure: real copies must still be caught."""
    from CORE.worm_governance import DedupAnalyzer

    class CopyA:
        def execute(self):
            return {"rules": 7}

        def audit(self):
            return {"scanned": 3}

    class CopyB:
        def execute(self):
            return {"rules": 7}

        def audit(self):
            return {"scanned": 3}

    match = DedupAnalyzer().compare_worms(
        "A", {"class": CopyA, "category": "x"},
        "B", {"class": CopyB, "category": "x"})
    assert match is not None, "tightening the rules must not hide a real duplicate"
    assert match.similarity == 1.0
    assert match.match_type == "exact_signature"


def test_black_queen_fails_closed_on_an_unvalidated_comparator(
        monkeypatch, twin_registry):
    """A destructive verdict must not rest on an instrument that failed its check."""
    from CORE import worm_governance
    from CORE.WORMS.expansion.queens import BlackQueenWorm

    monkeypatch.setattr(
        worm_governance.DedupAnalyzer, "self_check",
        lambda self, live=False, registry=None: {
            "success": True, "trustworthy": False,
            "failures": ["genuine_twins"], "controls": {},
            "summary": "injected failure"})

    before = {n: (i or {}).get("lifecycle_state")
              for n, i in WORM_REGISTRY.items()}
    result = BlackQueenWorm().propose(force=True)
    assert result["success"] is False
    assert result["reason"] == "similarity_metric_failed_self_check"
    assert result["retired"] == 0 and result["proposals"] == []
    after = {n: (i or {}).get("lifecycle_state")
             for n, i in WORM_REGISTRY.items()}
    assert after == before, "a refused run must not mutate the registry"


# ═══════════════════════════════════════════════════════════════════
# Verification fields — a MISSING verdict must not read as PASS
#
# MEASURED (engram #569): `rainbow_round`'s `_finish` read each team verdict as
# `result.get("ok", True)`, and the gray/black teams read their producer as
# `d.get("success", True)` / `p.get("success", True)`. Any producer that dropped
# its field therefore reported a PASS it never verified — the `default_true`
# class the meta-evolution detector raises as HIGH against this module, and the
# candidate it had queued here. `_verification_ok` fail-closes on absence and
# RECORDS it, so a removed field becomes evidence instead of a silent pass.
# ═══════════════════════════════════════════════════════════════════

def test_verification_field_reads_a_present_verdict_both_ways():
    from CORE.WORMS.expansion.queens import _verification_ok
    assert _verification_ok({"ok": True}, "ok", "test") is True
    assert _verification_ok({"ok": False}, "ok", "test") is False
    assert _verification_ok({"success": 0}, "success", "test") is False


def test_absent_verification_field_is_not_a_pass():
    """The load-bearing direction: absence must not be credited as success."""
    from CORE.WORMS.expansion.queens import _verification_ok
    assert _verification_ok({}, "ok", "test") is False
    assert _verification_ok({"other": True}, "success", "test") is False
    assert _verification_ok(None, "ok", "test") is False
    assert _verification_ok("not-a-dict", "ok", "test") is False


def test_queens_module_has_no_default_true_verdict_reads():
    """Source guard: the vacuous defaults must not come back.

    The three sites were `result.get("ok", True)` (`rainbow_round._finish`),
    `d.get("success", True)` (GrayQueenWorm) and `p.get("success", True)`
    (BlackQueenWorm). Each one made DELETING the producer's field invisible.
    """
    import ast
    from CORE.WORMS.expansion import queens
    src = Path(queens.__file__).read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if len(node.args) == 2 and isinstance(node.args[1], ast.Constant) \
                and node.args[1].value is True:
            offenders.append(node.lineno)
    assert not offenders, \
        "a missing verification field reads as PASS again at line(s) %s" % offenders
    assert "_verification_ok" in src, "the fail-closed reader was removed"


def test_rainbow_round_reports_a_verdict_for_every_team(monkeypatch):
    """Every team row carries a verdict, and a broken round is not healthy.

    All six producers are replaced with ones that raise, so the round is
    exercised WITHOUT its live probes, the real census or the developer's
    memory store: a team that fails must land as `ok: False`, never as an
    absent key that the aggregate could mistake for a pass.
    """
    import CORE.local_memory as lm
    from CORE import worm_governance
    from CORE.WORMS.expansion import queens, spine

    class _Explode:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("injected team failure")

    # The failure ledger is a real file, so the round's own records are captured
    # here instead of being written to the developer's state from a test.
    monkeypatch.setattr(queens, "note_failure", lambda *a, **k: None)
    monkeypatch.setattr(lm, "get_memory_store", lambda *a, **k: None)
    monkeypatch.setattr(queens, "_rainbow_hook", lambda *a, **k: True)
    monkeypatch.setattr(queens, "_rainbow_reward", lambda *a, **k: True)
    for target, name in ((queens, "GrayQueenWorm"),
                         (queens, "BlackQueenWorm"),
                         (queens, "ScoutBeeWorm"),
                         (spine, "RedQueenWorm"),
                         (spine, "MorphogenesisWorm"),
                         (worm_governance, "WormGovernor")):
        monkeypatch.setattr(target, name, _Explode)

    out = queens.rainbow_round()
    assert set(out["teams"]) == set(queens.RAINBOW_TEAMS)
    assert all("ok" in row for row in out["teams"].values()), \
        "a team row must always carry its verdict"
    assert any(row["ok"] is False for row in out["teams"].values()), \
        "an injected failure must be reported as not-ok"
    assert out["ok"] is False, \
        "a round containing a failed team cannot report itself healthy"
