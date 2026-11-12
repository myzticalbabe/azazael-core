"""Tests for the L1 wiring genome.

The load-bearing test is `test_genome_cannot_contain_infrastructure`. Everything
else is arithmetic; that one is the difference between a self-improving system
and a system that can quietly rewire the check on its own improvements.

Run:
    /c/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/test_wiring_genome.py -q
"""

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.wiring_genome import (  # noqa: E402
    COVERAGE_PENALTY,
    INFRA_EXCLUDED,
    MIN_ACTIVE,
    MIN_COVERAGE,
    MUTATIONS,
    WiringGenome,
    fitness,
    is_evolvable_organ,
    mutate,
    mutate_adjust_cooldown,
    mutate_bump_priority,
    mutate_flip_active,
    paired_compare,
    seed_genome,
    validate,
)

REGISTRY = {
    "PortAuditor": {"priority": 6},
    "SpanTracer": {"priority": 5},
    "CodeProfiler": {"priority": 5},
    "ConfigLinter": {"priority": 4},
    # infrastructure that must never enter a genome
    "EvolutionIntegrityWorm": {},
    "MorphogenesisWorm": {},
    "SpineCycleWorm": {},
    "GrayQueenWorm": {},
    "QuarantineWorm": {},
    "AttestationWorm": {},
}

# Hardcoded on purpose. A pool DERIVED from the filter makes every test about
# the filter vacuous: the filter would define its own ground truth.
ELIGIBLE = ["CodeProfiler", "ConfigLinter", "PortAuditor", "SpanTracer"]
EXPECTED_EXCLUDED = ["EvolutionIntegrityWorm", "MorphogenesisWorm",
                     "SpineCycleWorm", "GrayQueenWorm", "QuarantineWorm",
                     "AttestationWorm"]
CREDIT = {"PortAuditor": 0.95, "SpanTracer": 0.90,
          "CodeProfiler": 0.20, "ConfigLinter": 0.10}


def rng(seed=7):
    return random.Random(seed)


# --------------------------------------------------------------------------
# The invariant: the modifier is not evolvable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(INFRA_EXCLUDED))
def test_named_infrastructure_is_not_evolvable(name):
    assert not is_evolvable_organ(name)


@pytest.mark.parametrize("name", ["EvolutionIntegrityWorm", "MorphogenesisWorm",
                                  "SpineCycleWorm", "GrayQueenWorm",
                                  "QuarantineWorm", "AttestationWorm",
                                  "WiringAuditWorm", "EvolutionAuditWorm",
                                  "AnythingGovernance", "MetaSentinel"])
def test_infra_name_patterns_are_excluded_by_rule(name):
    """A newly added gate must be excluded by DEFAULT, not by remembering."""
    assert not is_evolvable_organ(name)


def test_domain_auditors_stay_evolvable():
    """The boundary: an organ that audits a DOMAIN may evolve; an organ that
    audits THE LOOP may not. Only the second kind can invalidate the verdict on
    a mutation."""
    assert is_evolvable_organ("PortAuditor")     # audits ports
    assert is_evolvable_organ("NewAuditWorm")    # audits its own domain
    assert not is_evolvable_organ("EvolutionAuditWorm")   # audits the loop
    assert not is_evolvable_organ("AuditWorm")            # generic meta-auditor


@pytest.mark.parametrize("name", ELIGIBLE)
def test_ordinary_organs_are_evolvable(name):
    assert is_evolvable_organ(name)


def test_the_eligible_pool_is_what_the_filter_actually_yields():
    assert sorted(n for n in REGISTRY if is_evolvable_organ(n)) == ELIGIBLE


def test_filter_does_not_exclude_by_substring():
    """Over-exclusion guard. `PortAuditor` contains "Auditor", and a rule that
    matched bare `audit` silently deleted it from every generation - the search
    space closing without anyone being told, which is the very defect this
    module exists to undo."""
    assert is_evolvable_organ("PortAuditor")
    assert is_evolvable_organ("SpanTracer")
    assert is_evolvable_organ("ConfigLinter")
    # ...while genuine infrastructure is still caught.
    for name in EXPECTED_EXCLUDED:
        assert not is_evolvable_organ(name)


def test_genome_cannot_contain_infrastructure():
    """The one that matters: a system able to evolve its own evaluator has no
    safety property at all."""
    genome = WiringGenome(active=frozenset({"PortAuditor", "SpineCycleWorm",
                                            "GrayQueenWorm"}))
    problems = validate(genome, REGISTRY)
    assert any("INFRASTRUCTURE ORGAN" in p for p in problems)
    assert any("SpineCycleWorm" in p for p in problems)


def test_seed_genome_contains_no_infrastructure():
    seed = seed_genome(REGISTRY)
    assert set(seed.active) == set(ELIGIBLE)
    assert validate(seed, REGISTRY) == []
    assert not (set(seed.active) & set(EXPECTED_EXCLUDED))


def test_mutations_never_introduce_infrastructure():
    """Even when infra names are offered in the pool."""
    genome = seed_genome(REGISTRY)
    r = rng(11)
    for _ in range(300):
        genome = mutate(genome, list(REGISTRY), r)   # full registry incl. infra
        assert all(is_evolvable_organ(n) for n in genome.active)


# --------------------------------------------------------------------------
# Validity
# --------------------------------------------------------------------------

def test_validate_flags_unknown_organs():
    genome = WiringGenome(active=frozenset({"PortAuditor", "NotARealOrgan"}))
    assert any("not in registry" in p for p in validate(genome, REGISTRY))


def test_validate_flags_empty_genome():
    assert any("no active organs" in p
               for p in validate(WiringGenome(active=frozenset()), REGISTRY))


def test_validate_flags_out_of_bounds_overrides():
    g = WiringGenome(active=frozenset({"PortAuditor"}),
                     priority={"PortAuditor": 99},
                     cooldown_s={"PortAuditor": 99999.0},
                     budget_ms={"PortAuditor": 1.0})
    problems = validate(g, REGISTRY)
    assert any("priority out of bounds" in p for p in problems)
    assert any("cooldown out of bounds" in p for p in problems)
    assert any("budget out of bounds" in p for p in problems)


def test_validate_flags_overrides_for_inactive_organs():
    g = WiringGenome(active=frozenset({"PortAuditor"}),
                     priority={"SpanTracer": 7})
    assert any("inactive organs" in p for p in validate(g, REGISTRY))


def test_every_mutation_of_the_seed_is_valid():
    """Bounded operators must not be able to emit an invalid genome."""
    genome = seed_genome(REGISTRY)
    r = rng(3)
    for _ in range(500):
        genome = mutate(genome, ELIGIBLE, r)
        assert validate(genome, REGISTRY) == [], genome.origin


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------

def test_flip_active_can_drop_but_never_below_the_minimum():
    genome = WiringGenome(active=frozenset({"PortAuditor"}))
    r = rng(5)
    for _ in range(50):
        genome = mutate_flip_active(genome, ELIGIBLE, r)
    assert len(genome.active) >= MIN_ACTIVE


def test_mutations_are_bounded():
    r = rng(13)
    g = seed_genome(REGISTRY)
    for _ in range(300):
        g = mutate_bump_priority(g, ELIGIBLE, r)
        g = mutate_adjust_cooldown(g, ELIGIBLE, r)
    assert all(1 <= v <= 10 for v in g.priority.values())
    assert all(5.0 <= v <= 3600.0 for v in g.cooldown_s.values())


def test_priority_mutations_only_target_the_active_fleet():
    """Regression guard: tuning an organ that is not running produced a genome
    that failed its own validity gate. An operator must not be able to emit an
    invalid candidate."""
    genome = WiringGenome(active=frozenset({"PortAuditor"}))
    r = rng(21)
    for _ in range(200):
        genome = mutate_bump_priority(genome, ELIGIBLE, r)
        genome = mutate_adjust_cooldown(genome, ELIGIBLE, r)
    assert set(genome.priority) <= set(genome.active)
    assert set(genome.cooldown_s) <= set(genome.active)
    assert validate(genome, REGISTRY) == []


def test_dropping_an_organ_clears_its_tuning():
    """Keeping dead overrides would change the fingerprint for a no-op and
    leave a genome that fails validation."""
    genome = WiringGenome(active=frozenset({"PortAuditor", "SpanTracer"}),
                          priority={"SpanTracer": 9},
                          cooldown_s={"SpanTracer": 15.0})
    r = rng(8)
    for _ in range(100):
        genome = mutate_flip_active(genome, ELIGIBLE, r)
        assert validate(genome, REGISTRY) == [], genome.origin


def test_mutations_record_lineage_and_bump_generation():
    genome = seed_genome(REGISTRY)
    mutated = mutate_flip_active(genome, ELIGIBLE, rng(1))
    assert mutated.generation == genome.generation + 1
    assert mutated.parent == genome.fingerprint()
    assert mutated.origin


def test_mutation_is_deterministic_for_a_seed():
    a = mutate(seed_genome(REGISTRY), ELIGIBLE, rng(42))
    b = mutate(seed_genome(REGISTRY), ELIGIBLE, rng(42))
    assert a.fingerprint() == b.fingerprint()


def test_all_mutations_are_registered():
    assert set(MUTATIONS) == {mutate_flip_active, mutate_bump_priority,
                             mutate_adjust_cooldown}


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def test_fingerprint_ignores_lineage():
    """Same composition reached two ways is the SAME genotype, or lineage
    reads as novelty and selection chases its own history."""
    a = WiringGenome(active=frozenset({"PortAuditor"}), generation=0,
                     origin="init")
    b = WiringGenome(active=frozenset({"PortAuditor"}), generation=9,
                     parent="wg-deadbeef", origin="mutate")
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_changes_with_composition():
    a = seed_genome(REGISTRY)
    b = mutate_flip_active(a, ELIGIBLE, rng(2))
    assert a.fingerprint() != b.fingerprint()


def test_round_trip_serialisation():
    g = WiringGenome(active=frozenset({"PortAuditor", "SpanTracer"}),
                     priority={"PortAuditor": 9},
                     cooldown_s={"SpanTracer": 12.5},
                     budget_ms={"PortAuditor": 250.0},
                     generation=4, parent="wg-x", origin="mutate")
    restored = WiringGenome.from_dict(json.loads(g.to_json()))
    assert restored.fingerprint() == g.fingerprint()
    assert restored.generation == 4 and restored.parent == "wg-x"


# --------------------------------------------------------------------------
# Fitness: grounded, never authored
# --------------------------------------------------------------------------

def test_unobserved_organ_is_neutral_not_penalised():
    """Adding an unmeasured organ must not move the score at all."""
    observed = WiringGenome(active=frozenset({"PortAuditor"}))
    plus_unmeasured = WiringGenome(
        active=frozenset({"PortAuditor", "CodeProfiler"}))
    credit = {"PortAuditor": 1.0}          # CodeProfiler unobserved

    assert fitness(observed, credit, ELIGIBLE)["mean_credit"] == \
        pytest.approx(1.0)
    assert fitness(plus_unmeasured, credit, ELIGIBLE)["mean_credit"] == \
        pytest.approx(1.0)


def test_dropping_an_unobserved_organ_is_not_a_free_improvement():
    """Regression guard from live measurement.

    With unobserved organs counted as 0.0 in the denominator, the first 300
    mutations 'improved' by dropping an unmeasured organ (+0.000276). A fitness
    that rises by forgetting what has not been measured shrinks the fleet to
    the handful with data instead of exploring the codebase.
    """
    # One organ in the pool has measurements; dropping an organ that HAS
    # measurements is allowed to improve the score (that is real selection).
    # The bias is that dropping an organ with NO measurements also improved it.
    pool = ELIGIBLE + ["BaselineScanner"]         # 5 organs, one unmeasured
    incumbent = WiringGenome(active=frozenset(pool))
    dropped = WiringGenome(active=frozenset(ELIGIBLE))   # drop the unmeasured

    result = paired_compare(dropped, incumbent, CREDIT, pool)
    assert result["delta"] == pytest.approx(0.0)
    assert result["accept"] is False
    # Sanity: dropping a MEASURED underperformer is still a real improvement.
    better = WiringGenome(active=frozenset({"PortAuditor", "SpanTracer"}))
    assert paired_compare(better, incumbent, CREDIT, pool)["delta"] > 0


def test_fitness_reports_how_thin_its_evidence_is():
    g = WiringGenome(active=frozenset(ELIGIBLE))
    scored = fitness(g, {"PortAuditor": 1.0}, ELIGIBLE)
    assert scored["observed"] == 1
    assert scored["evidence_ratio"] == pytest.approx(0.25)


def test_coverage_floor_prevents_starvation_dressed_as_fitness():
    """Mean credit alone prefers one reliable organ over the whole fleet."""
    tiny = WiringGenome(active=frozenset({"PortAuditor"}))          # mean 0.95
    full = WiringGenome(active=frozenset(ELIGIBLE))
    assert fitness(tiny, CREDIT, ELIGIBLE)["mean_credit"] > \
        fitness(full, CREDIT, ELIGIBLE)["mean_credit"]
    # ...but coverage is a FLOOR, so the one-organ genome is penalised.
    assert fitness(tiny, CREDIT, ELIGIBLE)["coverage_shortfall"] > 0
    assert fitness(full, CREDIT, ELIGIBLE)["coverage_shortfall"] == 0.0


def test_coverage_floor_value_is_the_declared_one():
    one = WiringGenome(active=frozenset({"PortAuditor"}))
    scored = fitness(one, CREDIT, ELIGIBLE)
    expected = max(0.0, MIN_COVERAGE - 1 / len(ELIGIBLE))
    assert scored["coverage_shortfall"] == pytest.approx(expected, abs=1e-6)
    assert COVERAGE_PENALTY > 0


def test_fitness_of_an_empty_genome_is_not_a_number():
    scored = fitness(WiringGenome(active=frozenset()), CREDIT, ELIGIBLE)
    assert scored["ok"] is False


# --------------------------------------------------------------------------
# Paired comparison
# --------------------------------------------------------------------------

def test_compare_is_paired_on_one_credit_snapshot():
    """Both sides scored on the SAME measurement, so noise cannot decide it."""
    incumbent = WiringGenome(active=frozenset(ELIGIBLE))
    candidate = WiringGenome(active=frozenset({"PortAuditor", "SpanTracer",
                                               "CodeProfiler"}))
    result = paired_compare(candidate, incumbent, CREDIT, ELIGIBLE)
    assert result["ok"] is True
    assert result["incumbent_fingerprint"] == incumbent.fingerprint()
    assert result["candidate_fingerprint"] == candidate.fingerprint()


def test_candidate_must_exceed_the_margin_to_be_accepted():
    incumbent = WiringGenome(active=frozenset(ELIGIBLE))
    candidate = WiringGenome(active=frozenset(ELIGIBLE))          # identical
    same = paired_compare(candidate, incumbent, CREDIT, ELIGIBLE)
    assert same["delta"] == pytest.approx(0.0)
    assert same["accept"] is False

    assert paired_compare(candidate, incumbent, CREDIT, ELIGIBLE,
                          margin=-1.0)["accept"] is True            # explicit bar


def test_a_better_composition_is_accepted():
    incumbent = WiringGenome(active=frozenset(ELIGIBLE))
    # Drop the two weakest organs, keeping coverage above the floor.
    candidate = WiringGenome(active=frozenset({"PortAuditor", "SpanTracer"}))
    result = paired_compare(candidate, incumbent, CREDIT, ELIGIBLE)
    assert result["delta"] > 0


def test_unscorable_sides_do_not_produce_a_verdict():
    result = paired_compare(WiringGenome(active=frozenset()),
                            WiringGenome(active=frozenset()), CREDIT, ELIGIBLE)
    assert result["ok"] is False and "accept" not in result
