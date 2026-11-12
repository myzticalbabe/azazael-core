"""Tests for the three closures added 2026-09-21: real prune, proven
consolidation, and the genome adaptation audit.

Each one exists because a stage reported success while nothing happened:

* `prune` reported `blocked` every cycle because no public Q-edge prune existed
  -- and when one was added, `cap_q_tables` turned out to drop keys that the next
  save re-adopted, so the bound it reported did not hold.
* `consolidate` ran the dream cycle and could not have proved a delta, because
  the offline Q channel was never invoked: the live dream-count table was empty
  for the whole life of the loop.
* `l1` reported a genome, a generation and a fitness, none of which says whether
  the search is going anywhere.

The tests below pin the properties, not the implementation: an evidence gate that
REFUSES rather than trusting the caller, a durability that survives a save, a
delta read from somewhere other than the component's own report, and an effector
measured through the production ranking code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from CORE import neural_loop as nl          # noqa: E402
from CORE import spine_registry as sr       # noqa: E402


# ---------------------------------------------------------------------------
# purge_edges -- the evidence gate
# ---------------------------------------------------------------------------

def _seed(**tables):
    for name, values in tables.items():
        table = getattr(sr, name)
        table.clear()
        table.update(values)


def test_purge_edges_removes_an_unevidenced_key_and_says_so():
    _seed(_Q_WEIGHTS={"a:dead": 0.0, "b:live": 0.9},
          _Q_UPDATE_COUNTS={"b:live": 4},
          _Q_DREAM_COUNTS={})
    out = sr.purge_edges(["a:dead"], reason="test")
    assert out["removed"] == ["a:dead"]
    assert out["removed_count"] == 1
    assert "a:dead" not in sr._Q_WEIGHTS
    assert sr._PURGED_KEYS.get("a:dead") == "test"


def test_purge_edges_refuses_a_key_with_observed_evidence():
    """The safety property: a prune can never delete something that was learned.

    The edges `plasticity` creates start near the neutral value by construction,
    so "small" and "carries no signal" must not collapse into one test.
    """
    _seed(_Q_WEIGHTS={"b:live": 0.9}, _Q_UPDATE_COUNTS={"b:live": 4},
          _Q_DREAM_COUNTS={})
    out = sr.purge_edges(["b:live"])
    assert out["removed"] == []
    assert out["refused"] == ["b:live"]
    assert sr._Q_WEIGHTS["b:live"] == 0.9, "a refused key must be untouched"


def test_purge_edges_refuses_a_key_that_was_only_dreamed_about():
    """Dreams are weaker evidence, not no evidence."""
    _seed(_Q_WEIGHTS={"dream:x": 0.4}, _Q_UPDATE_COUNTS={},
          _Q_DREAM_COUNTS={"dream:x": 3})
    out = sr.purge_edges(["dream:x"])
    assert out["refused"] == ["dream:x"] and out["removed"] == []


def test_purge_edges_can_remove_evidence_on_an_explicit_override():
    """`require_no_evidence=False` is the only way to delete learning on purpose."""
    _seed(_Q_WEIGHTS={"b:live": 0.9}, _Q_UPDATE_COUNTS={"b:live": 4},
          _Q_DREAM_COUNTS={})
    out = sr.purge_edges(["b:live"], require_no_evidence=False)
    assert out["removed"] == ["b:live"]
    assert "b:live" not in sr._Q_UPDATE_COUNTS, "every table must be cleared"


def test_purge_edges_is_durable_and_records_the_removal_on_disk():
    _seed(_Q_WEIGHTS={"a:dead": 0.0, "b:live": 0.9},
          _Q_UPDATE_COUNTS={"b:live": 4}, _Q_DREAM_COUNTS={})
    sr.purge_edges(["a:dead"], reason="test", persist=True)
    disk = json.loads(Path(sr._STATE_FILE).read_text(encoding="utf-8"))
    assert "a:dead" not in (disk.get("q_weights") or {})
    assert "a:dead" in (disk.get("purged_keys") or {})


def test_cap_q_tables_drops_are_durable_not_re_adopted_by_the_next_save():
    """MEASURED DEFECT: the cap dropped 3 of 6 keys and the next save wrote 6.

    The save folds the disk in before writing, so an un-tombstoned drop is
    re-adopted and the bound silently reverts -- a cap that reports `dropped=3`
    while the table stays at 6 makes the bound look enforced.
    """
    _seed(_Q_WEIGHTS={"w:%d" % i: 0.5 for i in range(6)},
          _Q_UPDATE_COUNTS={"w:%d" % i: i + 1 for i in range(6)},
          _Q_DREAM_COUNTS={})
    sr.save_spine_state()                       # disk now holds all six
    original = sr._Q_KEYS_MAX
    # `_PURGED_KEYS` is a process-wide tombstone table and earlier tests in this
    # session have added to it, so the assertion is on the DELTA, not the total.
    tombstones_before = len(sr._PURGED_KEYS)
    sr._Q_KEYS_MAX = 3
    try:
        dropped = sr.cap_q_tables()
        assert dropped == 3
        sr.save_spine_state()                   # the fold-in happens here
    finally:
        sr._Q_KEYS_MAX = original
    disk = json.loads(Path(sr._STATE_FILE).read_text(encoding="utf-8"))
    assert len((disk.get("q_weights") or {})) == 3, \
        "the cap did not hold: the fold-in re-adopted the dropped keys"
    assert len(sr._PURGED_KEYS) - tombstones_before == 3


# ---------------------------------------------------------------------------
# the prune stage
# ---------------------------------------------------------------------------

def test_prune_deletes_unevidenced_edges_and_reports_the_removal():
    _seed(_Q_WEIGHTS={"a:dead": 0.0, "b:live": 0.9},
          _Q_UPDATE_COUNTS={"b:live": 4}, _Q_DREAM_COUNTS={})
    res = nl._stage_prune(nl.LoopContext(apply=True))
    assert res.ok is True
    assert res.changed is True
    assert res.evidence["removed"] == 1
    assert res.evidence["scanned"] == 2
    assert "a:dead" not in sr._Q_WEIGHTS


def test_prune_finds_nothing_to_forget_without_calling_it_a_block():
    """Nothing to forget is a SUCCESS. Reporting `blocked` every cycle trains a
    reader to skip the blocked column -- the permanently-refused-heartbeat shape.
    """
    _seed(_Q_WEIGHTS={"b:live": 0.9}, _Q_UPDATE_COUNTS={"b:live": 4},
          _Q_DREAM_COUNTS={})
    res = nl._stage_prune(nl.LoopContext(apply=True))
    assert res.ok is True and res.changed is False
    assert not res.blocked, "an empty prune must not be reported as blocked"
    assert res.evidence["candidates"] == 0


def test_prune_is_blocked_in_a_dry_run_and_deletes_nothing():
    _seed(_Q_WEIGHTS={"a:dead": 0.0}, _Q_UPDATE_COUNTS={}, _Q_DREAM_COUNTS={})
    res = nl._stage_prune(nl.LoopContext(apply=False))
    assert res.blocked and res.changed is False
    assert "a:dead" in sr._Q_WEIGHTS


# ---------------------------------------------------------------------------
# the consolidate stage -- proving a delta
# ---------------------------------------------------------------------------

def _fake_dream_worm(*, claim, write=None):
    """A ConsolidationWorm stand-in: reports `claim`, optionally writes."""
    class _Worm:
        def consolidate(self, force=False):
            if write is not None:
                write()
            return {"success": True, "dreams": 1, "reinforced": claim}
    return _Worm


def test_consolidate_proves_the_delta_from_the_dream_count_table(monkeypatch):
    from CORE.WORMS.expansion import spine as spine_mod
    monkeypatch.setattr(nl, "_run_script_stage",
                        lambda *a, **k: nl.StageResult("consolidate", True,
                                                       evidence={"rc": 0, "tail": "OK"}))
    monkeypatch.setattr(
        spine_mod, "ConsolidationWorm",
        _fake_dream_worm(claim=2,
                         write=lambda: [sr.dream_reinforce("dream", "W%d" % i, 0.6)
                                        for i in range(2)]))
    _seed(_Q_WEIGHTS={}, _Q_UPDATE_COUNTS={}, _Q_DREAM_COUNTS={})
    res = nl._stage_consolidate(nl.LoopContext(apply=True))
    assert res.ok is True
    assert res.evidence["dream_delta"] == 2
    assert res.evidence["dream_counts_after"] == 2
    assert res.changed is True
    # and it is durable, not just in-memory
    disk = json.loads(Path(sr._STATE_FILE).read_text(encoding="utf-8"))
    assert (disk.get("q_dream_counts") or {}), \
        "the dream moved Q and did not persist it"


def test_consolidate_does_not_believe_the_worm_over_the_evidence(monkeypatch):
    """A worm that says `reinforced=5` while nothing moved has argued with the
    evidence. Neither the count table nor the weights may be inferred from a
    component's own report -- that is the self-report shape this loop exists to
    stop trusting.
    """
    from CORE.WORMS.expansion import spine as spine_mod
    monkeypatch.setattr(nl, "_run_script_stage",
                        lambda *a, **k: nl.StageResult("consolidate", True,
                                                       evidence={"rc": 0, "tail": "OK"}))
    monkeypatch.setattr(spine_mod, "ConsolidationWorm",
                        _fake_dream_worm(claim=5, write=None))
    _seed(_Q_WEIGHTS={"b:live": 0.9}, _Q_UPDATE_COUNTS={"b:live": 4},
          _Q_DREAM_COUNTS={})
    res = nl._stage_consolidate(nl.LoopContext(apply=True))
    assert res.evidence["reinforced"] == 5      # its claim is reported
    assert res.evidence["dream_delta"] == 0     # and its claim is not believed
    assert res.changed is False


def test_consolidate_fails_when_the_offline_channel_cannot_run(monkeypatch):
    from CORE.WORMS.expansion import spine as spine_mod
    monkeypatch.setattr(nl, "_run_script_stage",
                        lambda *a, **k: nl.StageResult("consolidate", True,
                                                       evidence={"rc": 0, "tail": "OK"}))

    class _Boom:
        def __init__(self):
            raise RuntimeError("dreaming unavailable")

    monkeypatch.setattr(spine_mod, "ConsolidationWorm", _Boom)
    res = nl._stage_consolidate(nl.LoopContext(apply=True))
    assert res.ok is False and "dreaming unavailable" in res.error


# ---------------------------------------------------------------------------
# the genome adaptation audit
# ---------------------------------------------------------------------------

def _audit_env(monkeypatch, tmp_path, genome):
    from CORE import l1_wiring_audit as wa
    state = tmp_path / "wstate"
    state.mkdir()
    promoted = state / "wiring_promoted.json"
    if genome is not None:
        promoted.write_text(json.dumps(genome), encoding="utf-8")
    monkeypatch.setenv("AZAZAEL_WIRING_STATE_DIR", str(state))
    monkeypatch.setenv("AZAZAEL_WIRING_PROMOTED", str(promoted))
    import CORE.worm_registry as wr
    monkeypatch.setattr(wr, "WIRING_PROMOTED_PATH", str(promoted))
    return wa, state


def _objective_stub(monkeypatch, *, reachable, ok=True, pinned=()):
    """Pin the objective block so a verdict test does not read live telemetry.

    `objective_health()` measures the real spawn-credit store, and a healthy
    process accumulates rows until `coverage` and `success` sit at their clamps
    (measured 2026-09-21: after `test_chain_refactor` the terms read
    `pinned=[coverage, success]`, `reachable=False`). A verdict test that reads
    that state asserts on whatever ran before it in the same process, not on the
    ordering it claims to check. The branches are pinned explicitly instead.
    """
    from CORE import l1_wiring_audit as wa
    block = {
        "ok": ok,
        "reachable": reachable,
        "reason": ("at least one term can move" if reachable
                   else "every measured term is pinned at a bound: %s"
                        % ", ".join(pinned)),
        "value": 0.5,
        "movable_terms": [] if not reachable else ["coverage"],
        "pinned_terms": list(pinned),
        "unmeasured_terms": [],
        "warnings": [],
        "historical": {},
    }
    monkeypatch.setattr(wa, "objective_health", lambda: dict(block))
    return wa


def _genome(**over):
    base = {"fingerprint": "wg-test", "generation": 3, "parent": "wg-parent",
            "origin": "drop:SomeWorm", "paired_delta": 0.002,
            "active": ["A", "B"], "priority": {}, "budget_ms": 0,
            "cooldown_s": 0}
    base.update(over)
    return base


def test_dimensions_counts_axes_by_the_code_not_by_population(tmp_path, monkeypatch):
    """`priority` is LIVE even though the promoted genome carries an empty map.

    The first version judged an axis by whether the genome happened to populate
    it, so this genome -- active filled, priority empty -- reported three
    "dormant" axes. That misread a fact about the SEARCH (no priority child has
    won yet) as a fact about the AXIS. The dividing line is code: an axis is live
    when an observable operator exists AND something consumes the field. Verified
    by reading both consumers: `fitness` is a priority-weighted mean credit and
    `decide_execution_order` ranks on `priority`.

    So the three categories are distinct claims, and only the last two mean
    "waiting":
      live     -- `active`, `priority` (operator + objective + effector)
      excluded -- `cooldown_s` (operator exists; scores exactly its parent)
      unwired  -- `budget_ms` (no operator at all)
    """
    wa, _ = _audit_env(monkeypatch, tmp_path, _genome())
    d = wa.dimensions(_genome())
    assert d["live"] == ["active", "priority"]
    assert d["excluded"] == ["cooldown_s"]
    assert d["unwired"] == ["budget_ms"]
    assert d["dormant"] == ["budget_ms", "cooldown_s"]
    # The empty map is reported, but it does not demote the axis.
    assert d["values"]["priority"] == 0
    assert d["values"]["active"] == 2


def test_history_separates_dry_evaluations_from_real_cycles(tmp_path, monkeypatch):
    """The lineage append sits in a `finally`, so a dry evaluation writes a row
    too. Counting them together overstates how much search has happened.
    """
    wa, state = _audit_env(monkeypatch, tmp_path, _genome())
    rows = [
        {"cycle_kind": "l1", "at": "2026-01-01T00:00:00Z", "promoted": False,
         "apply": True, "reason": "no candidate beat the incumbent"},
        {"cycle_kind": "l1", "at": "2026-01-01T00:01:00Z", "promoted": False,
         "apply": False, "reason": "no candidate beat the incumbent"},
        {"cycle_kind": "l1", "at": "2026-01-01T00:02:00Z", "promoted": True,
         "apply": True, "best": "wg-x", "reason": "promoted"},
        {"cycle_kind": "l1", "at": "2026-01-01T00:03:00Z", "promoted": False,
         "reason": "pre-field row"},
    ]
    (state / "wiring_lineage.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    h = wa.history_summary(wa.load_lineage())
    assert h["rows"] == 4
    assert h["applied_rows"] == 2 and h["dry_rows"] == 1
    assert h["unknown_apply_rows"] == 1
    assert h["promotions"] == 1
    assert h["promoted_fingerprints"] == ["wg-x"]


def test_effector_measures_a_real_reorder_and_reports_no_ledger_writes(
        tmp_path, monkeypatch):
    from CORE import worm_registry as wr
    wa, _ = _audit_env(monkeypatch, tmp_path,
                       _genome(active=["Keep"], priority={}))
    registry = {
        "Keep": {"category": "mesh", "priority": 5},
        "Drop": {"category": "mesh", "priority": 5},
    }
    monkeypatch.setattr(wr, "WORM_REGISTRY", registry)
    written = []
    monkeypatch.setattr(wr.SpawnDiversityEngine, "_record_pams",
                        lambda self, ev, data: written.append(ev))
    monkeypatch.setattr(wr.SpawnDiversityEngine, "record_spawn",
                        lambda self, *a, **k: written.append("spawn"))
    eff = wa.effector_effect(q_weights={})
    assert eff["ok"] is True
    assert eff["natures_with_choices"] == 1
    assert eff["natures_reordered"] == 1, "the demotion must reorder the nature"
    assert eff["demoted_organs"] == 1
    assert written == [], "an audit must not write decisions into the ledger"


def test_effector_finds_no_effect_when_the_genome_covers_everything(
        tmp_path, monkeypatch):
    from CORE import worm_registry as wr
    wa, _ = _audit_env(monkeypatch, tmp_path,
                       _genome(active=["Keep", "Drop"]))
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        "Keep": {"category": "mesh", "priority": 5},
        "Drop": {"category": "mesh", "priority": 5},
    })
    monkeypatch.setattr(wr.SpawnDiversityEngine, "_record_pams",
                        lambda self, ev, data: None)
    monkeypatch.setattr(wr.SpawnDiversityEngine, "record_spawn",
                        lambda self, *a, **k: None)
    eff = wa.effector_effect(q_weights={})
    assert eff["demoted_organs"] == 0
    assert eff["positions_changed"] == 0


def test_verdict_is_MAINTENANCE_when_the_genome_changes_nothing(
        tmp_path, monkeypatch):
    from CORE import worm_registry as wr
    wa, _ = _audit_env(monkeypatch, tmp_path, _genome(active=["A", "B"]))
    _objective_stub(monkeypatch, reachable=True)
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        "A": {"category": "mesh", "priority": 5},
        "B": {"category": "mesh", "priority": 5},
    })
    monkeypatch.setattr(wr.SpawnDiversityEngine, "_record_pams",
                        lambda self, ev, data: None)
    monkeypatch.setattr(wr.SpawnDiversityEngine, "record_spawn",
                        lambda self, *a, **k: None)
    assert wa.audit(q_weights={})["verdict"] == "MAINTENANCE"


def test_verdict_is_OBJECTIVE_PINNED_before_the_effector_questions(
        tmp_path, monkeypatch):
    """A pinned objective outranks "the genome reordered N natures".

    The genome here DOES change what gets done -- it demotes `Extra` out of the
    active set, so the effector measures a real reorder. If the objective check
    were not ordered first, that reorder would be reported as adaptation while
    every candidate the search scores is judged against an objective that cannot
    move. The ordering is the property, so the stub makes the objective the only
    pinned variable.
    """
    from CORE import worm_registry as wr
    wa, _ = _audit_env(monkeypatch, tmp_path, _genome(active=["Keep", "Drop"]))
    _objective_stub(monkeypatch, reachable=False,
                    pinned=("coverage", "success"))
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        "Keep": {"category": "mesh", "priority": 5},
        "Drop": {"category": "mesh", "priority": 5},
        "Extra": {"category": "mesh", "priority": 5},
    })
    monkeypatch.setattr(wr.SpawnDiversityEngine, "_record_pams",
                        lambda self, ev, data: None)
    monkeypatch.setattr(wr.SpawnDiversityEngine, "record_spawn",
                        lambda self, *a, **k: None)
    assert wa.audit(q_weights={})["effector"]["positions_changed"] > 0, (
        "fixture precondition: the genome must move the effector")
    assert wa.audit(q_weights={})["verdict"] == "NOT_LEARNING_OBJECTIVE_PINNED"


def test_verdict_is_NO_GENOME_when_nothing_was_ever_promoted(
        tmp_path, monkeypatch):
    wa, _ = _audit_env(monkeypatch, tmp_path, None)
    assert wa.audit(q_weights={})["verdict"] == "NO_GENOME"


def test_record_false_ranks_without_writing_the_decision_ledger(monkeypatch):
    from CORE import worm_registry as wr
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        "Keep": {"category": "mesh", "priority": 5},
        "Drop": {"category": "mesh", "priority": 5},
    })
    written = []
    monkeypatch.setattr(wr.SpawnDiversityEngine, "_record_pams",
                        lambda self, ev, data: written.append(ev))
    monkeypatch.setattr(wr.SpawnDiversityEngine, "record_spawn",
                        lambda self, *a, **k: written.append("spawn"))
    engine = wr.SpawnDiversityEngine()
    ordered, _ = engine.decide_execution_order("mesh", ["Drop", "Keep"],
                                               record=False)
    assert sorted(ordered) == ["Drop", "Keep"]
    assert written == [], "record=False must write nothing"
    engine.decide_execution_order("mesh", ["Drop", "Keep"])
    assert written, "record=True must still write (the default is unchanged)"


def test_consolidate_surfaces_why_the_offline_channel_wrote_nothing(monkeypatch):
    """`reinforced: 0` alone cannot be told apart from a healthy quiet channel.

    MEASURED: the offline channel had written nothing for the life of the system
    (the live dream-count table was empty) while reporting `reinforced: 0` with
    no reason. The cause is structural -- `DreamingModule` emits
    `{type: pattern_grounded, source: 'pattern_memory'}` and a
    `{type: strategy_exploration, strategies: [...]}` suggestion, and NEITHER
    names an organ, so there is no `nature:organ` key to move. The stage must
    carry that reason rather than reporting a silent zero.
    """
    from CORE.WORMS.expansion import spine as spine_mod
    monkeypatch.setattr(nl, "_run_script_stage",
                        lambda *a, **k: nl.StageResult("consolidate", True,
                                                       evidence={"rc": 0, "tail": "OK"}))

    class _NoTargets:
        def consolidate(self, force=False):
            return {"success": True, "dreams": 3, "reinforced": 0,
                    "skipped": {"no_organ_target": 9}, "persisted": False}

    monkeypatch.setattr(spine_mod, "ConsolidationWorm", _NoTargets)
    _seed(_Q_WEIGHTS={}, _Q_UPDATE_COUNTS={}, _Q_DREAM_COUNTS={})
    res = nl._stage_consolidate(nl.LoopContext(apply=True))
    assert res.ok is True and res.changed is False
    assert res.evidence["skipped"] == {"no_organ_target": 9}
    assert res.evidence["dream_delta"] == 0


# ---------------------------------------------------------------------------
# the offline (dream) channel -- the organ JOIN
# ---------------------------------------------------------------------------

def test_miner_pairs_an_error_with_its_earliest_same_source_success():
    """Adjacency in the sampled list is the WRONG requirement.

    MEASURED: an anchored live sample holding 3 errors and 20 infos yielded ZERO
    pairs, because each error's immediate successor was itself an error -- the
    sparse sample was being asked to reproduce the stream's exact ordering. The
    documented intent is "an error was followed within a short window by a
    successful event from the same source", so that is what gets measured.
    """
    from CORE.WORMS.expansion.dreaming import DreamingModule

    def _rec(ts, sev, source, data):
        return {"timestamp": ts, "severity": sev, "source": source, "data": data}

    scenarios = [
        _rec("2026-09-21T10:00:00", "error", "ClusterWORMZ.monitor",
             {"error": "timeout after 13s", "worm": "DatabaseMonitorWorm",
              "nature": "monitor"}),
        # An UNRELATED record sits between the error and its successor, so the
        # pair is NOT adjacent. The old code required exactly this adjacency.
        _rec("2026-09-21T10:00:05", "info", "ClusterWORMZ.analysis", {"ok": 1}),
        _rec("2026-09-21T10:00:09", "info", "ClusterWORMZ.monitor",
             {"action": "retry_with_backoff"}),
    ]
    pats = DreamingModule()._mine_error_recovery_patterns(scenarios)
    assert len(pats) == 1, "an error/success pair was lost to list ordering"
    p = pats[0]
    assert p["recovery"] == "retry_with_backoff"
    # The ORGAN travels with the pattern, distinct from `source` (the emitter).
    assert p["worm"] == "DatabaseMonitorWorm"
    assert p["nature"] == "monitor"


def test_a_suggestion_from_mined_evidence_carries_the_organ():
    """`source` is the EMITTER, `data.worm`/`data.nature` are the ORGAN.

    MEASURED over the live store: 256 of 400 records carry both fields, and all
    256 name an edge that already exists in Q (230 distinct real organs, 0
    fabricated). Keying a Q edge on `source` would invent organisms like
    "ClusterWORMZ.monitor" and "pattern_memory".
    """
    from CORE.WORMS.expansion.dreaming import DreamingModule

    def _rec(ts, sev, source, data):
        return {"timestamp": ts, "severity": sev, "source": source, "data": data}

    scenarios = [
        _rec("2026-09-21T10:00:00", "error", "ClusterWORMZ.monitor",
             {"error": "timeout after 13s", "worm": "DatabaseMonitorWorm",
              "nature": "monitor"}),
        _rec("2026-09-21T10:00:09", "info", "ClusterWORMZ.monitor",
             {"action": "retry_with_backoff"}),
    ]
    dm = DreamingModule()
    sugg = dm._generate_policy_suggestions(scenarios, [], 0.5)
    mined = [s for s in sugg if s.get("type") == "error_mitigation"]
    assert mined, "no organ-bearing suggestion was produced"
    assert mined[0]["worm"] == "DatabaseMonitorWorm"
    assert mined[0]["nature"] == "monitor"
    assert mined[0]["source"] == "ClusterWORMZ.monitor"


def _dreamer(suggestions):
    """A DreamingModule stand-in returning one dream carrying `suggestions`."""
    class _Dream:
        quality_score = 0.9
        policy_suggestions = suggestions

    class _Dreamer:
        def recall_scenarios(self, *a, **k):
            return {"recalled": 0}

        def dream(self, num_episodes=1):
            return [_Dream()]
    return _Dreamer()


def test_replay_keys_the_organs_real_category_not_a_phantom(monkeypatch):
    """The replay used to pass the literal `"dream"` as the nature, writing
    `dream:<worm>`. `dream` is NOT a nature in the live table (measured: 38 real
    natures across 472 edges, no `dream:` key), so routing would never read what
    the channel wrote -- it reinforced a phantom namespace and reported success.
    """
    from CORE.WORMS.expansion import spine as spine_mod
    from CORE.WORMS.expansion import dreaming as dreaming_mod

    monkeypatch.setattr(dreaming_mod, "DreamingModule", lambda *a, **k: _dreamer([
        {"type": "error_mitigation", "worm": "AutoDownloaderWorm",
         "nature": "orchestration", "confidence": 0.6},
    ]))
    _seed(_Q_WEIGHTS={}, _Q_UPDATE_COUNTS={}, _Q_DREAM_COUNTS={})
    res = spine_mod.ConsolidationWorm().consolidate(force=True)
    assert res["reinforced"] == 1
    assert res["persisted"] is True
    assert sr._Q_DREAM_COUNTS == {"orchestration:AutoDownloaderWorm": 1}
    assert "dream:AutoDownloaderWorm" not in sr._Q_WEIGHTS, \
        "the channel wrote its own phantom namespace"
    assert sr._Q_WEIGHTS["orchestration:AutoDownloaderWorm"] > 0.0


def test_replay_refuses_an_organ_that_names_no_nature(monkeypatch):
    """A worm with no nature cannot be keyed, and must not default to one."""
    from CORE.WORMS.expansion import spine as spine_mod
    from CORE.WORMS.expansion import dreaming as dreaming_mod

    monkeypatch.setattr(dreaming_mod, "DreamingModule", lambda *a, **k: _dreamer([
        {"type": "error_mitigation", "worm": "SomeWorm", "nature": "",
         "confidence": 0.6},
    ]))
    _seed(_Q_WEIGHTS={}, _Q_UPDATE_COUNTS={}, _Q_DREAM_COUNTS={})
    res = spine_mod.ConsolidationWorm().consolidate(force=True)
    assert res["reinforced"] == 0
    assert res["skipped"] == {"no_organ_nature": 1}
    assert sr._Q_WEIGHTS == {}


def test_replay_refuses_a_suggestion_with_no_reward_signal(monkeypatch):
    """The reward must be the suggestion's own measured quantity.

    The old constant fallback (0.3) pushed every dreamed edge to the same value,
    which carries no ordering information for `q_effective` to rank on.
    """
    from CORE.WORMS.expansion import spine as spine_mod
    from CORE.WORMS.expansion import dreaming as dreaming_mod

    monkeypatch.setattr(dreaming_mod, "DreamingModule", lambda *a, **k: _dreamer([
        {"type": "error_mitigation", "worm": "SomeWorm", "nature": "monitor"},
    ]))
    _seed(_Q_WEIGHTS={}, _Q_UPDATE_COUNTS={}, _Q_DREAM_COUNTS={})
    res = spine_mod.ConsolidationWorm().consolidate(force=True)
    assert res["reinforced"] == 0
    assert res["skipped"] == {"no_reward_signal": 1}
    assert sr._Q_WEIGHTS == {}


def test_recall_stratifies_toward_errors_so_the_miner_has_material(monkeypatch):
    """MEASURED: the unfiltered 200-row window was 198 info and 2 warning with
    ZERO errors, while the store held 200 errors in the same 7-day window. The
    error->recovery miner was STARVED, not broken -- a pair cannot be mined from
    a sample that contains no errors, so the recall has to carry some.
    """
    from CORE.WORMS.expansion import dreaming as dreaming_mod
    from CORE import local_memory as lm

    class _Store:
        def query_memories(self, source=None, event_type=None, severity=None,
                           limit=200, max_age_days=None):
            if severity == "error":
                return [{"id": "e%d" % i, "severity": "error", "source": "S",
                         "timestamp": "2026-01-01T00:00:0%d" % i, "data": {}}
                        for i in range(3)]
            return [{"id": "i%d" % i, "severity": "info", "source": "S",
                     "timestamp": "2026-01-02T00:00:0%d" % i, "data": {}}
                    for i in range(5)]

    monkeypatch.setattr(lm, "get_memory_store", lambda: _Store())
    dm = dreaming_mod.DreamingModule()
    r = dm.recall_scenarios()
    assert r["errors_added"] == 3
    assert r["recalled"] == 8
    assert sum(1 for s in dm._scenario_memory
               if s["severity"] == "error") == 3


# ---------------------------------------------------------------------------
# Learning dynamics are calibratable -- and the band cannot invert
# ---------------------------------------------------------------------------

_DYNAMICS_PROBE = (
    "from CORE.WORMS.expansion.spine import HomeostatWorm as H;"
    "print('DYNAMICS', H.MIN_SAMPLES, H.FAIL_RATE_HIGH, H.FAIL_RATE_LOW,"
    " H.BACKOFF_FACTOR, H.RECOVER_FACTOR, H.PRIORITY_STEP)"
)


def _homeostat_dynamics(params):
    """Read the homeostat's learning dynamics from a FRESH interpreter.

    A subprocess because a class attribute is evaluated exactly once, at import:
    re-importing in-process would either be a no-op or would re-register 232
    worms into this process's registry and disturb every later test. The read
    path is the thing under test, and this is the only honest way to exercise it.
    """
    import os
    import subprocess
    import sys
    import tempfile

    path = Path(tempfile.mkdtemp()) / "hyperparams.json"
    path.write_text(json.dumps({"params": params}), encoding="utf-8")
    env = dict(os.environ)
    env["AZAZAEL_HYPEROPT_PARAMS"] = str(path)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run([sys.executable, "-c", _DYNAMICS_PROBE], env=env,
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    for line in proc.stdout.splitlines():
        if line.startswith("DYNAMICS"):
            parts = line.split()[1:]
            return [int(parts[0]), float(parts[1]), float(parts[2]),
                    float(parts[3]), float(parts[4]), int(parts[5])]
    raise AssertionError("probe printed no DYNAMICS line: %r" % proc.stdout[-400:])


def test_homeostat_defaults_are_the_shipped_literals():
    """With nothing promoted the dynamics are exactly what this class shipped
    with, because every reader falls back to its own literal -- so
    hyper-optimization can never be a new way for the loop to fail to start."""
    assert _homeostat_dynamics({}) == [5, 0.5, 0.1, 2.0, 0.75, 1]


def test_homeostat_learning_dynamics_are_read_from_the_param_space():
    """The evidence floor, the failure band and the size of the corrections
    decide HOW the system learns. As literals in `HomeostatWorm` they froze the
    learner's own sensitivity while everything it controlled kept moving -- the
    missing recursion the outer loop exists to close, one level down."""
    got = _homeostat_dynamics({
        "spine.homeostat_min_samples": 17,
        "spine.fail_rate_high": 0.8,
        "spine.fail_rate_low": 0.3,
        "spine.backoff_factor": 3.5,
        "spine.recover_factor": 0.55,
        "spine.priority_step": 2,
    })
    assert got == [17, 0.8, 0.3, 3.5, 0.55, 2]


def test_the_failure_band_cannot_invert_through_a_promoted_value():
    """`fail_rate_low` above `fail_rate_high` makes a 30%-failing organ read as
    HEALTHY and SHORTENS its cooldown -- strictly worse than the untuned literal.
    The ordering is therefore enforced at read time rather than trusted to two
    independent bounds that do not know about each other."""
    _min, high, low, _b, _r, _s = _homeostat_dynamics({
        "spine.fail_rate_high": 0.2,   # its own low bound
        "spine.fail_rate_low": 0.4,    # its own high bound
    })
    assert low < high, "an inverted band would punish health and reward failure"
