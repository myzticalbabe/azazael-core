"""Tests for the L1 wiring cycle: the gate, the canary, and the effector.

WHY THIS FILE EXISTS (2026-09-21)
---------------------------------
`wiring_genome.py` (rung L1) had a genotype and a fitness but no cycle and no
consumer, which is the defect this project already found once at the
hyperparameter layer: memory -> hyperparameters -> training -> memory, never
conditioned on what was learned, so the loop closed inside the trainer and the
system's behaviour never changed.

The wiring genome closes it because `worm_registry.decide_execution_order` READS
the promoted genome. So the tests below are ordered by what they defend:

1. **The effector** -- that a promoted genome actually changes dispatch order.
   Without this the whole rung is a report, so it is tested first.
2. **The gate** -- that it accumulates evidence instead of crowning one lucky
   draw, and refuses to promote on evidence too thin to carry a verdict.
3. **The canary** -- that a promotion which does not hold is reverted.
4. **Operator observability** -- the defect the first live run found: two of
   three mutation operators produced children scoring IDENTICALLY to their
   parent (delta exactly 0.0), i.e. search dimensions that did not exist.

Run:
    /c/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/test_wiring_cycle.py -q
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from CORE.WORMS.expansion import wiring_genome as wg  # noqa: E402


def _load_wiring_cycle(tmp_path, monkeypatch):
    """Import `scripts/wiring_cycle.py` against a throwaway state dir.

    The module computes its state paths at import time, so the env var must be
    set BEFORE the import — the same set-at-collection-time reason `conftest.py`
    gives for the memory store. Reimported per test so no test can leak a
    promotion into another, or into the developer's real `state/`.
    """
    monkeypatch.setenv("AZAZAEL_WIRING_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AZAZAEL_WIRING_PROMOTED", raising=False)
    monkeypatch.setenv("AZAZAEL_WIRING_DISCOVER", "0")  # ~38s cold start
    spec = importlib.util.spec_from_file_location(
        "wiring_cycle_under_test", _ROOT / "scripts" / "wiring_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REGISTRY = {
    # Dramatis personae, fourteen total — the true minimum for a floor-13
    # world: thirteen to cover the floor, one (Djinn) so the winner differs
    # from the all-active seed and the canary stays falsifiable. Thirteen
    # ghosts (Thir13en Ghosts, 2001 — the Black Zodiac twelve plus the Broken
    # Heart thirteenth) plus Djinn, who carries best-credit 0.9; DireMother
    # carries worst-credit and the priority fixture's last-place ranking.
    "Djinn": {"priority": 5, "category": "monitor"},
    "SpineCycleWorm": {"priority": 5, "category": "mesh"},   # infra: not evolvable
    # The 13 ghosts (Thir13en Ghosts, 2001 — the Black Zodiac twelve plus the
    # Broken Heart thirteenth). Fixture personas for the 13-world: the forced
    # winner is all thirteen, which differs from the 19-organ seed (so the
    # canary stays falsifiable) and covers exactly the L1_MIN_OBSERVED=13
    # floor. Mid-table rewards: Djinn stays best (0.9), DireMother worst
    # (0.40).
    "FirstBornSon": {"priority": 5, "category": "monitor"},
    "Torso": {"priority": 5, "category": "monitor"},
    "BoundWoman": {"priority": 5, "category": "monitor"},
    "WitheredLover": {"priority": 5, "category": "monitor"},
    "TornPrince": {"priority": 5, "category": "monitor"},
    "AngryPrincess": {"priority": 5, "category": "monitor"},
    "Pilgrimess": {"priority": 5, "category": "monitor"},
    "GreatChild": {"priority": 5, "category": "monitor"},
    "DireMother": {"priority": 5, "category": "monitor"},
    "Hammer": {"priority": 5, "category": "monitor"},
    "Jackal": {"priority": 5, "category": "monitor"},
    "Juggernaut": {"priority": 5, "category": "monitor"},
    "BrokenHeart": {"priority": 5, "category": "monitor"},
}

GHOSTS = ["FirstBornSon", "Torso", "BoundWoman", "WitheredLover",
          "TornPrince", "AngryPrincess", "Pilgrimess", "GreatChild",
          "DireMother", "Hammer", "Jackal", "Juggernaut", "BrokenHeart"]

CREDIT = {"Djinn": 0.9,
          "FirstBornSon": 0.55, "Torso": 0.45, "BoundWoman": 0.5,
          "WitheredLover": 0.48, "TornPrince": 0.52, "AngryPrincess": 0.46,
          "Pilgrimess": 0.44, "GreatChild": 0.42, "DireMother": 0.40,
          "Hammer": 0.58, "Jackal": 0.8, "Juggernaut": 0.60,
          "BrokenHeart": 0.57}
ELIGIBLE = (["Djinn"] + GHOSTS)


@pytest.fixture
def wc(tmp_path, monkeypatch):
    mod = _load_wiring_cycle(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "load_registry", lambda: dict(REGISTRY))
    monkeypatch.setattr(mod, "eligible_organs", lambda r: list(ELIGIBLE))
    # The 13-world: the gate under test demands thirteen observed organs.
    # Read live everywhere else (MIN_WINS, margin, replace ratio); only the
    # floor is pinned, because the tuned value (6) belongs to the 6-organ
    # fixture era and this file now houses thirteen ghosts.
    monkeypatch.setattr(mod, "L1_MIN_OBSERVED", 13)

    def fake_credit():
        return SimpleCredit(CREDIT)

    monkeypatch.setattr(mod, "load_credit", fake_credit)
    return mod


class SimpleCredit:
    """Stand-in carrying only what `credit_snapshot` reads."""

    def __init__(self, rewards):
        self._rewards = dict(rewards)
        self.outcomes = {w: SimpleOutcome(w, 12) for w in rewards}
        self.n_outcome_rows = len(rewards) * 12
        self.n_choice_rows = len(rewards)
        self.attribution_mode = "per-worm-type (statistical)"

    @property
    def rewards(self):
        return dict(self._rewards)


class SimpleOutcome:
    def __init__(self, worm_type, n):
        self.worm_type = worm_type
        self.n = n


# --------------------------------------------------------------------------
# 1. The effector -- without this the rung is a report
# --------------------------------------------------------------------------

def test_a_promoted_genome_changes_dispatch_order(tmp_path, monkeypatch):
    """THE test for this rung: state written here must alter behaviour there.

    A learner whose output nothing consumes is the defect this project already
    paid for once. So this pins the actual causal link:
    `state/wiring_promoted.json` -> `decide_execution_order` ordering.
    """
    from CORE import worm_registry as wr

    promoted = tmp_path / "wiring_promoted.json"
    monkeypatch.setattr(wr, "WIRING_PROMOTED_PATH", str(promoted))
    monkeypatch.setattr(wr, "_wiring_cache", None)
    monkeypatch.setattr(wr, "_wiring_cache_stamp", None)
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        "Djinn": {"priority": 5}, "Jackal": {"priority": 5},
        "DireMother": {"priority": 5}, "SpineCycleWorm": {"priority": 5},
    })

    engine = wr.SpawnDiversityEngine()
    monkeypatch.setattr(engine, "_record_pams", lambda et, data: None)
    monkeypatch.setattr(engine, "record_spawn", lambda *a, **k: None)

    # No promotion file: ranking falls back to registry priority then name.
    baseline, _ = engine.decide_execution_order("monitor", ["Djinn", "Jackal", "DireMother"])
    assert baseline == ["DireMother", "Djinn", "Jackal"]

    # A promoted genome that promotes DireMother and DEMOTES Djinn.
    promoted.write_text(json.dumps({
        "active": ["Jackal", "DireMother"],
        "priority": {"DireMother": 10, "Jackal": 9},
    }), encoding="utf-8")

    ordered, decision_id = engine.decide_execution_order(
        "monitor", ["Djinn", "Jackal", "DireMother"])

    assert decision_id, "the decision must still be recorded joinably"
    assert ordered == ["DireMother", "Jackal", "Djinn"], ordered

    # THE INVARIANT: ordering only. A demoted organ still runs.
    assert set(ordered) == {"Djinn", "Jackal", "DireMother"}


def test_the_effector_is_inert_without_a_promotion(tmp_path, monkeypatch):
    """No promotion must mean no behaviour change, not a crash or a default."""
    from CORE import worm_registry as wr

    monkeypatch.setattr(wr, "WIRING_PROMOTED_PATH",
                        str(tmp_path / "does-not-exist.json"))
    monkeypatch.setattr(wr, "_wiring_cache", None)
    monkeypatch.setattr(wr, "_wiring_cache_stamp", None)

    assert wr._load_promoted_wiring() is None
    engine = wr.SpawnDiversityEngine()
    monkeypatch.setattr(engine, "_record_pams", lambda et, data: None)
    monkeypatch.setattr(engine, "record_spawn", lambda *a, **k: None)
    monkeypatch.setattr(wr, "WORM_REGISTRY", {"Jackal": {"priority": 9},
                                              "Djinn": {"priority": 1}})

    ordered, _ = engine.decide_execution_order("monitor", ["Djinn", "Jackal"])
    assert ordered == ["Jackal", "Djinn"], "registry priority must still decide"


def test_the_effector_survives_a_corrupt_promotion_file(tmp_path, monkeypatch):
    """Dispatch must never be blocked by state."""
    from CORE import worm_registry as wr

    bad = tmp_path / "wiring_promoted.json"
    bad.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(wr, "WIRING_PROMOTED_PATH", str(bad))
    monkeypatch.setattr(wr, "_wiring_cache", None)
    monkeypatch.setattr(wr, "_wiring_cache_stamp", None)

    assert wr._load_promoted_wiring() is None


def test_the_effector_notices_a_new_promotion_without_a_restart(tmp_path,
                                                               monkeypatch):
    """Cache is keyed on mtime, not a TTL.

    A TTL would either serve a stale genome right after a promotion or re-read
    the file on every hot dispatch call.
    """
    from CORE import worm_registry as wr

    path = tmp_path / "wiring_promoted.json"
    monkeypatch.setattr(wr, "WIRING_PROMOTED_PATH", str(path))
    monkeypatch.setattr(wr, "_wiring_cache", None)
    monkeypatch.setattr(wr, "_wiring_cache_stamp", None)

    assert wr._load_promoted_wiring() is None
    path.write_text(json.dumps({"active": ["Djinn"]}), encoding="utf-8")
    assert wr._load_promoted_wiring() is not None

    # Rewrite with different content and a distinct mtime.
    import os
    import time
    time.sleep(0.01)
    path.write_text(json.dumps({"active": ["Jackal"]}), encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert wr._load_promoted_wiring()["active"] == ["Jackal"]


# --------------------------------------------------------------------------
# 2. The gate
# --------------------------------------------------------------------------

def _cycle_wg(mod):
    """The `wiring_genome` object the cycle under test ACTUALLY calls.

    Patching the module imported at the top of this file is not the same thing:
    `worm_registry._purge_worm_modules()` deletes every `CORE.WORMS.*` entry from
    `sys.modules` (so re-discovery can re-fire the `@register_worm` decorators),
    and the re-import installs a SECOND module object under the same name. After
    any `reset_registry()` in the session, this file's `wg` and the cycle's `wg`
    are different objects — measured 2026-09-22: a forced winner was ignored, the
    gate reported `no candidate beat the incumbent`, and eight gate tests failed
    in-suite while passing alone. The cycle is what runs, so the cycle's module is
    what gets patched.
    """
    return getattr(mod, "wg", wg)


def _force_winner(mod, monkeypatch, genome=None, delta=0.5):
    """Make every cycle score one fixed candidate at a fixed delta.

    Patches `OBSERVABLE_MUTATIONS`, because the cycle calls the OPERATORS
    directly, not the `mutate` helper. The first version patched `wg.mutate`,
    which the cycle never calls — so those gate tests were passing by the
    accident that a fixed RNG seed produces the same child each cycle, rather
    than because the streak logic worked. A test that passes for a reason other
    than the one it names is worse than no test.
    """
    # NOTE the composition: all thirteen ghosts, not the full nineteen. A
    # forced winner identical to the seed genome has the SAME fingerprint,
    # because `fingerprint()` is over composition only (generation/parent/
    # origin are excluded so lineage cannot masquerade as novelty). A fixture
    # that happened to equal the seed made the canary's revert assertion
    # unfalsifiable. Thirteen (not five): the 13-world evidence floor is
    # L1_MIN_OBSERVED=13, so a smaller winner could never clear the gate it
    # is meant to exercise — which is exactly how the 5-organ fixture died
    # when the tuned floor moved 5 -> 6.
    target = _cycle_wg(mod)
    winner = genome if genome is not None else target.WiringGenome(
        active=frozenset(GHOSTS),
        origin="forced")
    monkeypatch.setattr(target, "OBSERVABLE_MUTATIONS",
                        (lambda g, e, r: winner,))
    monkeypatch.setattr(target, "paired_compare", lambda c, i, r, e, margin=0.0: {
        "ok": True, "delta": delta, "accept": delta > margin,
        "candidate": {"observed": 13, "coverage": 1.0,
                      "evidence_ratio": 1.0, "n_active": 13},
        "incumbent": {},
        "candidate_fingerprint": c.fingerprint(),
        "incumbent_fingerprint": i.fingerprint(),
    })
    return winner


def test_one_winning_draw_does_not_promote(wc, monkeypatch):
    """The measured defect: 159/400 rows (39.8%) cleared a single gate."""
    # `L1_MIN_WINS` is read from the hyper-optimizer and only FALLS BACK to the
    # shipped literal, so the count is asserted against the live threshold rather
    # than against 3. Hardcoding the literal made these tests fail the moment a
    # tuned value landed (measured 2026-09-22: ``_hyp`` returned min_wins=2 while
    # the tests still expected 3).
    assert wc.L1_MIN_WINS > 1, "a single win must never be enough to promote"
    _force_winner(wc, monkeypatch)
    report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)

    assert report["ok"] is True
    assert report["promoted"] is False
    assert report["accepted"] == 1, "the candidate must actually be accepted"
    assert report["streak"]["wins"] == 1
    assert ("awaiting %d consecutive wins" % wc.L1_MIN_WINS) in report["reason"]


def test_the_same_candidate_must_win_consecutively(wc, monkeypatch):
    _force_winner(wc, monkeypatch)
    for expected in range(1, wc.L1_MIN_WINS):
        report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
        assert report["streak"]["wins"] == expected
        assert report["promoted"] is False

    report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert report["streak"]["wins"] == wc.L1_MIN_WINS
    assert report["promoted"] is True
    # The streak resets after a promotion, so the next one must earn it again.
    assert wc._streak_state()["wins"] == 0

    promoted = json.loads(Path(wc.PROMOTED_JSON).read_text(encoding="utf-8"))
    assert promoted["fingerprint"]
    assert promoted["promoted_by"] == "wiring_cycle"


def _other_winner():
    # Thirteen organs, so it still clears the evidence floor: a 5-organ
    # candidate would be rejected as thin evidence before the streak is ever
    # consulted. Twelve ghosts plus Djinn: same size as the elite, different
    # composition, so displacement restarts the count on a real difference.
    return wg.WiringGenome(active=frozenset(GHOSTS[:12] + ["Djinn"]),
                           origin="forced-other")


def test_a_noise_level_improvement_cannot_be_promoted(wc, monkeypatch):
    """The measured defect: promotions won by 0.09% of the signal's spread.

    Live, 2026-09-21, with the gate's margin at 0.0: three genomes were promoted
    on paired deltas of +0.000555, +0.000272 and +0.000111, while the credit
    signal they were scored on had a population spread of 0.1228. A streak does
    not filter that, because the search offers twelve fresh children per cycle;
    a handful of near-ties sitting just above the incumbent assembles the streak
    out of noise. This pins the floor: a sub-margin improvement must never
    promote, however many cycles it is offered.
    """
    assert wc.L1_MARGIN > 0.0, "a zero margin is the bug this test guards"
    _force_winner(wc, monkeypatch, delta=wc.L1_MARGIN / 4.0)
    reports = [wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
               for _ in range(wc.L1_MIN_WINS + 2)]
    assert not any(r.get("promoted") for r in reports), \
        "a sub-margin improvement was promoted"


def test_an_improvement_above_the_margin_still_promotes(wc, monkeypatch):
    """The floor must not make the gate unreachable."""
    _force_winner(wc, monkeypatch, delta=max(0.5, wc.L1_MARGIN * 10))
    reports = [wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
               for _ in range(wc.L1_MIN_WINS)]
    assert reports[-1].get("promoted") is True


def test_carrying_the_elite_is_what_makes_the_gate_reachable(wc, monkeypatch):
    """THE fix for an unreachable gate.

    Live measurement 2026-09-21: two wins on one fingerprint, never a third, in
    hours of running -- because every cycle generated fresh random children and
    discarded them, so the same winner had to be re-invented by chance three
    times in a row. Carrying the winner forward makes the third win a matter of
    the candidate still being good, and `best.label` is asserted to be the
    carried elite rather than a lucky newcomer.
    """
    _force_winner(wc, monkeypatch)
    assert wc.L1_MIN_WINS >= 2, "carrying an elite only matters when >1 win is needed"

    r1 = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert r1["streak"]["wins"] == 1 and r1["promoted"] is False
    assert r1["elite_carried"] is False, "nothing exists to carry on the first cycle"

    r2 = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert r2["elite_carried"] is True, \
        "the winner was not carried into the next cycle"

    final = r2
    for _ in range(wc.L1_MIN_WINS + 1):
        if final["promoted"]:
            break
        final = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert final["promoted"] is True, "the gate never reached its threshold"
    assert final["best"]["label"] == "elite_carried", \
        "the streak accumulated on a fresh child, not on the carried elite"


def test_a_BETTER_winner_takes_over_and_restarts_the_streak(wc, monkeypatch):
    """The streak must still not accumulate across DIFFERENT candidates.

    The delta is a function of who is being scored, not a constant: the carried
    elite stays at 0.5 while the newcomer scores 0.9, i.e. a real improvement
    rather than a tie. (`_force_winner` cannot express this -- it patches
    `paired_compare` to one value for every candidate, including the elite it is
    meant to be displacing, so both arms of the comparison move together.)
    """
    _force_winner(wc, monkeypatch)
    assert wc.L1_MIN_WINS >= 2, "the streak must need more than one win"
    for _ in range(wc.L1_MIN_WINS - 1):
        wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    elite_fp = wc._streak_state()["fingerprint"]
    assert elite_fp and wc._streak_state()["wins"] == wc.L1_MIN_WINS - 1

    other = _other_winner()
    assert other.fingerprint() != elite_fp, "fixture must differ from the elite"

    # The newcomer must clear the LIVE elite-replace floor (`elite_delta *
    # (1 + L1_ELITE_REPLACE_RATIO)`), not the shipped fallback: the ratio is
    # hyper-optimized (measured 2026-09-22: 0.8625, i.e. a floor of 0.93125), so
    # a hardcoded 0.9 failed to displace a 0.5 elite.
    better = 0.5 * (1.0 + wc.L1_ELITE_REPLACE_RATIO) + 0.01

    def _by_fingerprint(c, i, r, e, margin=0.0):
        d = better if c.fingerprint() == other.fingerprint() else 0.5
        return {"ok": True, "delta": d, "accept": d > margin,
                "candidate": {"observed": 13, "coverage": 1.0,
                              "evidence_ratio": 1.0, "n_active": 13},
                "incumbent": {"value": 0.5}}

    target = _cycle_wg(wc)
    monkeypatch.setattr(target, "OBSERVABLE_MUTATIONS", (lambda g, e, r: other,))
    monkeypatch.setattr(target, "paired_compare", _by_fingerprint)
    rep = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)

    assert rep["best"]["fingerprint"] == other.fingerprint(), \
        "a genuinely better candidate failed to displace the carried elite"
    assert rep["streak"]["wins"] == 1, "a different winner must restart the count"
    assert rep["streak"]["fingerprint"] == other.fingerprint()


def test_thin_evidence_is_reported_but_never_promoted(wc, monkeypatch):
    """The `alert`-pool defect: oracle EQUALLED the best constant, and it promoted anyway.

    Two organs carry observations and the candidate touches both, so it covers
    100% of the available evidence — and that is still not a verdict, because a
    cover ratio is not a sample size. This is the distinction the earlier
    pipeline missed when it promoted on a per-state oracle that equalled the
    best constant.
    """
    # Eleven ghosts plus both credited organs: ratio 1.0 on a 2-sample —
    # full coverage, still thin. (Thirteen ghosts would touch nothing here.)
    thin_full = wg.WiringGenome(
        active=frozenset(GHOSTS[:11] + ["Djinn"]),
        origin="forced-thin-full")
    _force_winner(wc, monkeypatch, genome=thin_full)
    monkeypatch.setattr(wc, "load_credit",
                        lambda: SimpleCredit({"Djinn": 0.9, "Jackal": 0.8}))
    for _ in range(5):
        report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert report["promoted"] is False
    assert "evidence too thin" in report["reason"], report["reason"]
    assert report["best_evidence_ratio"] == pytest.approx(1.0)
    assert report["best_observed"] == 2
    assert report["streak"]["wins"] == 0


def test_a_registry_reset_spares_helpers_and_the_gate_still_gates(tmp_path, monkeypatch):
    """Regression for the in-suite failure that was invisible when run alone.

    `worm_registry.reset_registry()` must re-import the modules that HOST worms
    so the `@register_worm` decorators re-fire — but it must not replace the
    HELPER modules. When it did (measured 2026-09-22), this file's `wg` and the
    cycle's were different objects, so patching this file's `wg` patched
    something the cycle never calls: the forced winner was ignored, the gate
    reported `no candidate beat the incumbent`, and eight tests here failed
    in-suite while passing alone.

    The cycle is loaded AFTER the reset, which is the in-suite order.
    """
    import importlib

    from CORE import worm_registry as wr

    mod_name = "CORE.WORMS.expansion.wiring_genome"
    before = importlib.import_module(mod_name)
    wr.reset_registry()
    after = importlib.import_module(mod_name)
    assert after is before, \
        "a reset must not replace a helper module's identity"

    post_reset = _load_wiring_cycle(tmp_path, monkeypatch)
    monkeypatch.setattr(post_reset, "load_registry", lambda: dict(REGISTRY))
    monkeypatch.setattr(post_reset, "eligible_organs", lambda r: list(ELIGIBLE))
    monkeypatch.setattr(post_reset, "load_credit",
                        lambda: SimpleCredit(CREDIT))
    assert _cycle_wg(post_reset) is wg, \
        "the cycle and this file must hold the same helper module"

    _force_winner(post_reset, monkeypatch)
    report = post_reset.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert report["accepted"] == 1, report.get("reason")
    assert report["streak"]["wins"] == 1


def test_apply_switch_blocks_a_promotion_the_gate_approved(wc, monkeypatch):
    """AZAZAEL_WIRING_L1_APPLY=0 stops writes while still recording trials."""
    _force_winner(wc, monkeypatch)
    monkeypatch.setattr(wc, "L1_APPLY", False)
    for _ in range(wc.L1_MIN_WINS):
        report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert report["promoted"] is False
    assert "L1_APPLY=0" in report["reason"]
    assert not Path(wc.PROMOTED_JSON).exists()


def test_without_apply_the_cycle_still_scores_and_records(wc, monkeypatch):
    """Shadow mode must still produce the evidence a review needs."""
    _force_winner(wc, monkeypatch)
    for _ in range(wc.L1_MIN_WINS):
        report = wc.run_cycle(apply_promotion=False, candidates=2, seed=1)
    assert report["promoted"] is False
    assert "--apply not given" in report["reason"]
    assert wc._lineage_size() > 0


# --------------------------------------------------------------------------
# 3. The canary
# --------------------------------------------------------------------------

def test_canary_reverts_a_promotion_that_does_not_hold(wc, monkeypatch):
    _force_winner(wc, monkeypatch)
    for _ in range(wc.L1_MIN_WINS):
        report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert report["promoted"] is True
    assert Path(wc.PROMOTED_JSON).exists()
    assert Path(wc.PREV_JSON).exists(), "rollback must be one file restore"
    # REBORN CANARY, first-promotion path: with no prior promotion the
    # rollback point is the seed genome itself (`pre-first-promotion`), so
    # the very first promotion already has somewhere to fall back to.
    prev = json.loads(Path(wc.PREV_JSON).read_text(encoding="utf-8"))
    seed = wg.seed_genome(REGISTRY, origin="init")
    assert prev["fingerprint"] == seed.fingerprint(), \
        "first rollback point is not the seed genome"
    assert prev.get("origin") == "pre-first-promotion"

    # The promoted genome decays: its realised score collapses.
    genome = wg.WiringGenome.from_dict(
        json.loads(Path(wc.PROMOTED_JSON).read_text(encoding="utf-8")))
    status = _decayed_canary(wc, genome, CREDIT, ELIGIBLE, {})

    assert status["status"] == "reverted"
    # The revert restored the pre-promotion genome, not merely reported a problem.
    restored = json.loads(Path(wc.PROMOTED_JSON).read_text(encoding="utf-8"))
    assert restored["fingerprint"] != genome.fingerprint()

    rows = Path(wc.LINEAGE_JSONL).read_text(encoding="utf-8").splitlines()
    assert any("canary-revert" in r for r in rows), \
        "a revert with no lineage row is indistinguishable from no revert"


def _decayed_canary(wc, genome, rewards, eligible, counts):
    """`canary_check` against a collapsed fitness, without patching globals."""
    target = _cycle_wg(wc)
    real_fitness = target.fitness
    try:
        # observed=13 clears the 13-world floor: the stub simulates a
        # well-measured collapse, not a thin sample (which the canary must
        # visibly skip, never revert on).
        target.fitness = lambda g, c, e=None: {"ok": True, "value": -1.0,
                                               "observed": 13, "coverage": 1.0}
        return wc.canary_check(genome, rewards, eligible, counts)
    finally:
        target.fitness = real_fitness


def test_canary_ignores_a_genome_that_was_not_promoted(wc):
    """The canary only judges the genome it promoted."""
    state = {"fingerprint": "wg-x", "baseline": 0.5}
    Path(wc.CANARY_JSON).write_text(json.dumps(state), encoding="utf-8")
    genome = wg.WiringGenome(active=frozenset({"Djinn", "Jackal", "DireMother", "Torso"}))
    # fingerprint mismatch -> not the promoted genome at all
    assert wc.canary_check(genome, CREDIT, ELIGIBLE, {})["status"] == "not-promoted"


# --------------------------------------------------------------------------
# 4. Operator observability -- the defect the first live run found
# --------------------------------------------------------------------------

def test_inert_operators_are_excluded_from_the_cycle(wc, monkeypatch):
    """40 live mutations produced 0 accepted candidates with all three operators.

    Cause: `fitness` read only `active` and the effector read only `priority`, so
    a cooldown mutation returned a child scoring IDENTICALLY to its parent.
    A search dimension nothing can observe is not a dimension.
    """
    report = wc.run_cycle(apply_promotion=False, candidates=4, seed=1)

    assert report["operators"] == ["mutate_flip_active", "mutate_bump_priority"]
    assert report["operators_excluded"] == ["mutate_adjust_cooldown"]
    assert set(report["operators"]) == {
        op.__name__ for op in wg.OBSERVABLE_MUTATIONS}


def test_every_used_operator_can_actually_move_the_score(wc, monkeypatch):
    """The invariant behind the exclusion: a used operator must not be a no-op.

    Measured per operator on a credit table where every organ is observed, so a
    null result is the operator's fault rather than the observation gap's.
    """
    incumbent = wg.seed_genome(REGISTRY)
    for op in wg.OBSERVABLE_MUTATIONS:
        import random
        moved = 0
        for i in range(60):
            child = op(incumbent, ELIGIBLE, random.Random(i))
            if wg.paired_compare(child, incumbent, CREDIT, ELIGIBLE)["delta"] != 0.0:
                moved += 1
        assert moved > 0, f"{op.__name__} cannot move the objective at all"


def test_priority_is_in_the_objective(wc):
    """Without this, the effector's ordering dimension was unselectable."""
    g = wg.WiringGenome(active=frozenset(ELIGIBLE))
    low = wg.WiringGenome(active=frozenset(ELIGIBLE), priority={"DireMother": 1})
    high = wg.WiringGenome(active=frozenset(ELIGIBLE), priority={"DireMother": 10})

    # Priority is a WEIGHT, and DireMother is the worst organ (0.40): the
    # objective must reward putting it LAST, which is exactly what the
    # effector's ordering does.
    assert (wg.fitness(low, CREDIT, ELIGIBLE)["value"]
            > wg.fitness(high, CREDIT, ELIGIBLE)["value"])
    # And it must prefer the opposite for the best organ (Djinn, 0.9).
    best_high = wg.WiringGenome(active=frozenset(ELIGIBLE), priority={"Djinn": 10})
    best_low = wg.WiringGenome(active=frozenset(ELIGIBLE), priority={"Djinn": 1})
    assert (wg.fitness(best_high, CREDIT, ELIGIBLE)["value"]
            > wg.fitness(best_low, CREDIT, ELIGIBLE)["value"])
    # With uniform weights it reduces to the plain mean, so no baseline shifts.
    base = wg.fitness(g, CREDIT, ELIGIBLE)
    assert base["ranked_credit"] == pytest.approx(base["mean_credit"])
    assert base["ranked_credit"] == pytest.approx(
        sum(CREDIT.values()) / len(CREDIT))


def test_search_resolution_is_reported_not_implied(wc, monkeypatch):
    """The binding constraint is observation coverage, so the cycle says so."""
    monkeypatch.setattr(wc, "load_credit", lambda: SimpleCredit({"Djinn": 0.9}))
    report = wc.run_cycle(apply_promotion=False, candidates=2, seed=1)

    assert report["visible_organs"] == 1
    # abs tolerance: the cycle rounds to 6 decimals for reporting.
    assert report["search_resolution"] == pytest.approx(
        1 / len(ELIGIBLE), abs=1e-6)


# --------------------------------------------------------------------------
# 5. Cache priming + promotion-quality readout (perf/outer-loop 2026-10-01)
# --------------------------------------------------------------------------

def test_prime_caches_fills_without_raising(tmp_path, monkeypatch):
    """Startup prewarm must never fail a boot: best-effort, tuple-shaped.

    Uses a fresh module WITHOUT the `wc` fixture: `wc` stubs `load_registry`
    to a fixture dict (no discovery runs), so priming through it can fill
    nothing by construction. The real loader path is exercised here.
    """
    mod = _load_wiring_cycle(tmp_path, monkeypatch)
    monkeypatch.setenv("AZAZAEL_WIRING_DISCOVER", "1")
    reg_ok, cred_ok = mod.prime_caches()
    assert isinstance(reg_ok, bool) and isinstance(cred_ok, bool)
    assert reg_ok is True, "real discovery must leave a cached registry"


def test_promotion_quality_tracks_holds_and_reverts(wc, monkeypatch):
    """The outer-loop readout: promotions standing vs reverted by canary."""
    assert wc.promotion_quality() == {"promotions": 0, "reverts": 0,
                                      "held_rate": None}
    _force_winner(wc, monkeypatch)
    for _ in range(wc.L1_MIN_WINS):
        report = wc.run_cycle(apply_promotion=True, candidates=1, seed=1)
    assert report["promoted"] is True
    assert wc.promotion_quality()["promotions"] == 1
    assert wc.promotion_quality()["held_rate"] == 1.0

    genome = wg.WiringGenome.from_dict(
        json.loads(Path(wc.PROMOTED_JSON).read_text(encoding="utf-8")))
    assert _decayed_canary(wc, genome, CREDIT, ELIGIBLE, {})["status"] \
        == "reverted"
    assert wc.promotion_quality() == {"promotions": 1, "reverts": 1,
                                      "held_rate": 0.0}


def test_promotion_gate_negative_controls(wc):
    """R2: the gate's three controls — empty/all-held never block."""
    assert wc.promotion_gate_ok(quality={"promotions": 0, "reverts": 0,
                                         "held_rate": None})["block"] is False
    assert wc.promotion_gate_ok(quality={"promotions": 4, "reverts": 0,
                                         "held_rate": 1.0})["block"] is False
    assert wc.promotion_gate_ok(quality={"promotions": 4, "reverts": 3,
                                         "held_rate": 0.25})["block"] is True
