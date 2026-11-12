"""Tests for opportunity-driven generation in `MorphogenesisWorm`.

WHY THIS FILE EXISTS (2026-09-21)
---------------------------------
Generation fired on ONE condition:

    if fail_rate < self.MIN_FAIL_RATE:   # 0.75
        continue

so the pipeline only ever reacted to breakage. Measured consequence: a repo with
a complete morphogenesis apparatus - a containment pen, a quarantine, a quorum
vote, an attestation step, bounded stores, signature dedup - had produced exactly
ONE generated file. A generator that fires only on failure can bandage wounds; it
can never propose anything, and proposing is the whole point of morphogenesis.

The opportunity trigger needs no new instrumentation, because the two taps
already record both halves of a demand: `spawn_credit` joins the decisions
against the outcomes, so "chosen and never delivered" is measurable. Before the
`decision_id` work it was not even unimplementable-but-unimplemented - the two
halves could not be joined at all (13 chosen vs 28 executed had ONE worm in
common), so this trigger had nothing to read.

WHAT THESE TESTS DEFEND
-----------------------
1. **The new trigger fires on a gap** and names the gap, not a fabricated error.
2. **Breakage keeps priority and its own budget** — exploring must not be able to
   crowd out repairing.
3. **Both triggers use the SAME containment rails.** Opportunity-driven
   generation must not become a second, laxer route into the tree.
4. **The generated source states which trigger produced it**, so a file in the
   pen is self-describing.

Run:
    /c/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/test_morphogenesis_opportunity.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.spine import MorphogenesisWorm  # noqa: E402


CHOSEN_NEVER_RUN = [
    "FleetOrch", "NetDiag", "DivWorm",          # expansion: 3 members
    "ClockSync", "BinAnalysis", "SysMonitor",   # expansion: 6 members via category
]


class FakeCredit:
    """Only the two members `_opportunity_patterns` reads."""

    def __init__(self, chosen_never):
        self._chosen_never = list(chosen_never)

    def chosen_but_never_run(self):
        return sorted(self._chosen_never)


@pytest.fixture
def morph(tmp_path, monkeypatch):
    """A morphogenesis worm writing to a throwaway pen."""
    monkeypatch.setenv("AZAZAEL_GENERATED_DIR", str(tmp_path))
    m = MorphogenesisWorm()
    return m


@pytest.fixture
def with_gap(monkeypatch):
    """`spawn_credit` reporting a real capability gap."""
    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "live_spawn_credit",
                        lambda: FakeCredit(CHOSEN_NEVER_RUN))
    import CORE.worm_registry as wr
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        n: {"category": "expansion", "priority": 5}
        for n in CHOSEN_NEVER_RUN
    })


# --------------------------------------------------------------------------
# 1. The trigger
# --------------------------------------------------------------------------

def test_a_gap_produces_an_opportunity_pattern(morph, with_gap):
    """The finding, inverted: generation now fires WITHOUT any failure."""
    patterns = morph._opportunity_patterns()

    assert len(patterns) == 1
    p = patterns[0]
    assert p["trigger"] == "opportunity"
    assert p["gap"] == "chosen-not-run"
    assert p["nature"] == "expansion"
    assert p["n_members"] == len(CHOSEN_NEVER_RUN)
    assert "unrealised:chosen-not-run" in p["sig"]


def test_the_gap_names_what_was_requested_not_an_error_string(morph, with_gap):
    """A fabricated error signature would be a lie the scaffold then embeds."""
    p = morph._opportunity_patterns()[0]

    assert "Traceback" not in p["sig"]
    assert "unknown" != p["sig"]
    assert set(p["members"]) <= set(CHOSEN_NEVER_RUN)


def test_a_single_unrealised_worm_is_not_a_capability_gap(morph, monkeypatch):
    """One is a hiccup; the threshold exists so a fleet gap yields a proposal,
    not a proposal per worm."""
    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "live_spawn_credit",
                        lambda: FakeCredit(["FleetOrch"]))
    import CORE.worm_registry as wr
    monkeypatch.setattr(wr, "WORM_REGISTRY",
                        {"FleetOrch": {"category": "expansion"}})

    assert morph._opportunity_patterns() == []


def test_names_absent_from_the_registry_are_not_capability_gaps(morph, monkeypatch):
    """A corrupted RECORD must not be able to manufacture source.

    Measured 2026-09-21: every name in the live choice tap that was "chosen and
    never ran" (FleetOrch, NetDiag, DivWorm, ...) came from a unit test in
    `src/tests/` writing into the production store. `WORM_REGISTRY.get(name, {})`
    turned an absence into the `"expansion"` default, so the trigger reported
    them as a six-member gap in `expansion` and would have generated one
    specialist per phantom component - emitting source in response to a
    bookkeeping error, which is the most expensive possible reaction to it.
    """
    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "live_spawn_credit",
                        lambda: FakeCredit(CHOSEN_NEVER_RUN))
    import CORE.worm_registry as wr
    monkeypatch.setattr(wr, "WORM_REGISTRY", {})  # knows NONE of these names

    assert morph._opportunity_patterns() == []


def test_a_real_gap_beside_a_bad_record_is_still_reported(morph, monkeypatch):
    """The filter must not throw away a genuine gap standing next to noise."""
    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "live_spawn_credit",
                        lambda: FakeCredit(CHOSEN_NEVER_RUN))
    import CORE.worm_registry as wr
    real = CHOSEN_NEVER_RUN[:3]
    monkeypatch.setattr(wr, "WORM_REGISTRY",
                        {n: {"category": "expansion"} for n in real})

    patterns = morph._opportunity_patterns()

    assert len(patterns) == 1
    assert patterns[0]["n_members"] == 3, "only registered names count"
    assert set(patterns[0]["members"]) <= set(real)


def test_no_gap_means_no_proposal(morph, monkeypatch):
    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "live_spawn_credit", lambda: FakeCredit([]))
    assert morph._opportunity_patterns() == []


def test_a_broken_credit_source_is_silent_not_fatal(morph, monkeypatch):
    """Generation must not be able to take down a spine cycle."""
    import CORE.WORMS.expansion.spawn_credit as sc

    def boom():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(sc, "live_spawn_credit", boom)
    assert morph._opportunity_patterns() == []


def test_opportunity_can_be_switched_off(morph, with_gap, monkeypatch):
    monkeypatch.setattr(MorphogenesisWorm, "OPPORTUNITY_ENABLED", False)
    report = morph.morph(dry_run=False)

    assert report["patterns_opportunity"] == 0
    assert report["opportunity_enabled"] is False
    assert report["generated"] == 0


# --------------------------------------------------------------------------
# 2. Breakage keeps priority
# --------------------------------------------------------------------------

def test_chronic_runs_before_opportunity_and_each_keeps_a_budget(morph, monkeypatch):
    """A permanently-limping nature must not consume the whole allowance.

    If the two shared one cap, chronic patterns (which sort by failure count and
    are always plentiful) would fill it every cycle and the exploring path would
    be silently starved — the same "installed but can never act" outcome this
    change exists to remove, reintroduced through the budget.
    """
    chronic = [{"worm": f"Fail{i}", "nature": "monitor", "sig": f"err{i}",
                "fails": 10, "total": 10, "consec": 9} for i in range(9)]
    monkeypatch.setattr(morph, "_chronic_patterns", lambda: chronic)

    import CORE.WORMS.expansion.spawn_credit as sc
    monkeypatch.setattr(sc, "live_spawn_credit",
                        lambda: FakeCredit(CHOSEN_NEVER_RUN))
    import CORE.worm_registry as wr
    monkeypatch.setattr(wr, "WORM_REGISTRY", {
        n: {"category": "expansion"} for n in CHOSEN_NEVER_RUN})

    report = morph.morph(dry_run=False)

    triggers = [p["trigger"] for p in report["proposals"]]
    assert report["patterns_chronic"] == 9
    assert report["patterns_opportunity"] == 1
    assert triggers.count("chronic-failure") == MorphogenesisWorm.CHRONIC_WRITE_CAP
    assert triggers.count("opportunity") == 1
    # Chronicle went first.
    assert triggers[0] == "chronic-failure"


# --------------------------------------------------------------------------
# 3. One containment path for every trigger
# --------------------------------------------------------------------------

def test_opportunity_output_lands_in_the_pen_not_the_tree(morph, with_gap):
    """The containment boundary is a property of the mechanism, not the trigger."""
    report = morph.morph(dry_run=False)

    assert report["generated"] >= 1
    pen = Path(report["staging"])
    written = list(pen.glob("*.py"))
    assert written, "a proposal must land in the pen"

    # ...and NOT into the discovery-visible directory.
    discoverable = Path(__file__).resolve().parents[1] / "src" / "CORE" / "WORMS" / "expansion" / "generated"
    for p in written:
        assert not (discoverable / p.name).exists(), \
            "a staged candidate must not be importable before the gate"


def test_staged_opportunity_source_is_valid_and_self_describing(morph, with_gap):
    """A file in the pen must say WHY it exists."""
    report = morph.morph(dry_run=False)
    path = Path(report["proposals"][0]["file"])

    src = (Path(report["staging"]) / path.name).read_text(encoding="utf-8")
    compile(src, str(path), "exec")           # promotion attests by compiling
    assert "CAPABILITY GAP" in src
    assert "NON-AUTONOMOUS" in src
    assert "unrealised:chosen-not-run" in src


def test_chronic_source_still_says_it_is_a_repair(morph, monkeypatch):
    """The two triggers must not be confusable on inspection."""
    src_chronic = morph._scaffold("SYSTEMZ", "monitor", "disk timeout")
    src_gap = morph._scaffold("FleetOrch", "expansion",
                              "unrealised:chosen-not-run",
                              trigger="opportunity")

    assert "chronic failure pattern" in src_chronic
    assert "CAPABILITY GAP" not in src_chronic
    assert "CAPABILITY GAP" in src_gap
    compile(src_chronic, "<c>", "exec")
    compile(src_gap, "<g>", "exec")


def test_dedup_is_shared_so_a_gap_is_proposed_once(morph, with_gap):
    """Both triggers share `_stage`, so signature dedup covers both."""
    first = morph.morph(dry_run=False)
    second = morph.morph(dry_run=False)

    assert first["generated"] == 1
    assert second["generated"] == 0, "the same gap must not be proposed twice"
    assert second["patterns_opportunity"] == 1


def test_dry_run_neither_writes_nor_reports_writes(morph, with_gap):
    report = morph.morph(dry_run=True)

    assert report["dry_run"] is True
    assert report["generated"] == 0
    assert list(Path(report["staging"]).glob("*.py")) == []
    assert report["proposals"] == []
    # ...but it still reports the gap it WOULD act on.
    assert report["patterns_opportunity"] == 1
