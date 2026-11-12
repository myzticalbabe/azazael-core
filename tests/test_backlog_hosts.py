#!/usr/bin/env python3
"""Oct-10 backlog churn: every new host, live. No doubles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_policy_ceiling_denies():
    from CORE.compliance.policy import PolicyEngine, default_policy_rules
    eng = PolicyEngine()
    eng.add_rules(default_policy_rules())
    assert eng.check_ceiling("x", "read-only", "write")["allowed"] is False
    assert eng.check_ceiling("x", "admin", "write")["allowed"] is True


def test_policy_attenuate_drops_one_rung_per_depth():
    from CORE.compliance.policy import PolicyEngine
    eng = PolicyEngine()
    assert eng.attenuate("admin", 3)["delegated"] == "none"
    assert eng.attenuate("admin", 2)["delegated"] == "read-only"
    assert eng.attenuate("admin", 0)["delegated"] == "admin"


def test_policy_obligate_requires_met():
    from CORE.compliance.policy import PolicyEngine, default_policy_rules
    eng = PolicyEngine()
    eng.add_rules(default_policy_rules())
    met = eng.obligate("str_replace", role="developer", obligations={"cite": True})
    assert met["obligated"] is True
    unmet = eng.obligate("str_replace", role="developer", obligations={"cite": False})
    assert unmet["obligated"] is False
    assert unmet["unmet"] == ["cite"]


def test_effect_row_declares_writes():
    from CORE.structured_action import effect_row
    assert effect_row({"verb": "push", "target": "main"})["effect"] == "write"
    assert effect_row({"verb": "run", "target": "tests"})["effect"] == "read"
    assert effect_row({"verb": "run", "target": "tests"})["typed_trace"] == ["run:tests"]


def test_authority_ledger_reads_live_store():
    from CORE.structured_action import authority_ledger
    out = authority_ledger()
    assert set(out) == {"granted", "exercised", "gap"}


def test_carrier_shared_mean():
    from CORE.MemoryBus import HiddenStateCarrier
    car = HiddenStateCarrier()
    assert car.put("a", {"x": 1.0}) is True
    assert car.put("", {}) is False
    car.put("b", {"x": 3.0})
    view = car.shared_view()
    assert view["reasoners"] == 2
    assert view["mean"]["x"] == 2.0


def test_prefix_reuse_roundtrip():
    from CORE.dispatch_plan import record_backtrack, reuse_prefix
    record_backtrack("test-branch-xyz", "lesson-one")
    back = reuse_prefix("test-branch-xyz")
    assert back["reusable"] is True
    assert back["lesson"] == "lesson-one"
    assert reuse_prefix("no-such-branch")["reusable"] is False


def test_resolve_conflicts_precedence_over_recency():
    from CORE.local_memory import resolve_conflicts
    out = resolve_conflicts([
        {"id": "f", "source": "engram", "value": "old", "recency": 0.9},
        {"id": "f", "source": "local_memory", "value": "new", "recency": 0.1},
    ])
    assert out["resolved"]["f"]["source"] == "local_memory"
    assert out["n_conflicts"] == 1
    assert out["conflicts"][0]["reason"] == "declared-precedence-over-recency"


def test_repair_tree_names_nodes_and_resync():
    from CORE.verification_gate import repair_tree
    t = repair_tree([{"id": "plan", "confidence": 0.92}, {"id": "edit", "confidence": 0.64}])
    assert t["resync"] == "plan"
    assert [n["id"] for n in t["nodes"]] == ["edit"]
    assert t["passed"] is False


def test_capsule_sealed():
    from CORE.audit_trail import graph_capsule
    cap = graph_capsule({"k": "v"}, "abc123")
    assert cap["prev_hash"] == "abc123"
    assert cap["delta"] == {"k": "v"}
    assert isinstance(cap["sealed"], bool)


def test_route_hierarchy_nests_domains():
    from CORE.skill_library import route_hierarchy
    r = route_hierarchy([{"name": "W", "family": "dispatch", "domain": "edit"},
                         {"name": "L", "family": "audit"}])
    assert r["depth"] == 2
    assert r["tree"]["dispatch"]["domains"] == {"edit": ["W"]}
    assert r["lazy_manifests"] is True


def test_stratified_fallback_payload():
    from CORE.WORMS.expansion.engram import RecallRouterWorm
    route = RecallRouterWorm().route_recall("technique runtime no-such-memory-xyz")
    assert route["stratified_payload"]["mode"] == "fallback"


def test_ablation_harness_measures_variants():
    from CORE.WORMS.expansion.learning_pipeline import DataPipelineWorm
    p = DataPipelineWorm()
    out = p.ablate_sources(exclude_variants=[[], ["SystemZ"]])
    assert out["n_variants"] == 2
    assert "excluded:none" in out["variants"]


def test_adapters_invoke_new_hosts():
    import CORE.technique_wiring._l2_skills as l2
    import CORE.technique_wiring._l3_verify as l3
    import CORE.technique_wiring._b01_05 as b01
    import CORE.technique_wiring._l1_memory as l1
    import CORE.technique_wiring._l6_evolve as l6
    assert l2._ep34()["rendered_steps"] == 4
    assert l2._ep427()["skill_md_chars"] > 0
    assert l2._ep427()["delta"] == ""
    assert l2._ep363()["skill_accepted"] is True
    assert l2._ep363()["prompt_accepted"] is True
    assert l2._ep363()["packs_untrusted_at_build"] is True
    assert l2._ep364()["depth"] == 2
    assert l2._ep458()["accepted"] is True
    assert l2._ep458()["invoked"] == "CORE.skill_library:commit_gate"
    assert l3._ep269()["ceiling_denies"] is True
    assert l3._ep289()["attenuated_to"] == "none"
    assert l3._ep413()["repair_nodes"] == 1
    assert l3._ep327()["residual"] == ""
    assert b01._ep345()["prefix_reusable"] is True
    assert b01._ep442()["carrier_mean_x"] == 2.0
    assert b01._ep412()["gap"] == ""
    assert l1.ep463()["demo_winner"] == "local_memory"
    assert l6._ep294()["delta"].startswith("calibration-error-weighted")
