"""EP266 (ALAR): the neural loop decided WHAT to run and never HOW.

`_stage_route` is the loop's effector -- it turns the just-updated weight table
into a dispatch order. What it had no concept of is the per-turn MODE: latent
reasoning versus explicit reasoning. The latent carriers themselves belong to
their own modules; the missing piece was the SELECTOR, and these tests pin the
two halves that matter:

1. With a decider injected, it is consulted once per `route` stage with the
   order it is deciding about, and its answer is recorded (not just used), so a
   cycle's mode is visible in the evidence the report prints.
2. With NO decider injected, the stage's evidence is exactly the pre-EP266 dict
   and no mode is recorded -- an injected default cannot move an existing
   caller's output.

A decider that raises must not fail the effector: the order it was consulted
about is already computed, so the failure is recorded and the order still ships.
"""
import sys
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE import neural_loop  # noqa: E402


class _FakeSpine:
    """Stand-in for CORE.spine_registry: the route stage's only dependency."""

    @staticmethod
    def q_order(nature, names):
        # Deliberately not the sorted order, so a test can tell the registry's
        # answer apart from the stage's own fallback.
        return sorted(names, reverse=True)


@pytest.fixture()
def fake_spine(monkeypatch):
    """Replace the registry on BOTH lookup paths.

    `_stage_route` does `from CORE import spine_registry as sr`. That resolves
    the attribute on the CORE package first and only falls back to importing the
    submodule, so patching sys.modules alone is not enough once the real
    registry has been imported by another test in the same process.
    """
    import CORE

    fake = _FakeSpine()
    monkeypatch.setitem(sys.modules, "CORE.spine_registry", fake)
    monkeypatch.setattr(CORE, "spine_registry", fake, raising=False)
    return fake


def _ctx(**kw):
    ctx = neural_loop.LoopContext()
    ctx.categories = {"a": "analysis", "b": "analysis", "c": "build"}
    for key, value in kw.items():
        setattr(ctx, key, value)
    return ctx


def test_route_without_a_decider_is_unchanged(fake_spine):
    ctx = _ctx()

    res = neural_loop._stage_route(ctx)

    assert res.ok is True and res.stage == "route"
    # Choosing a mode is not a write: `changed` must stay False or the verify
    # stage reports a durable change the weights do not show.
    assert res.changed is False
    assert res.evidence == {"ordered": len(ctx.order), "head": ctx.order[:8]}
    assert "mode" not in res.evidence
    assert ctx.modes == []
    # The turn clock still advances, so a later decision sees turn 1 -- but no
    # decider means nothing is recorded as a choice.
    assert ctx.turns == 1


def test_injected_decider_is_consulted_and_its_answer_recorded(fake_spine):
    seen = []

    def decider(turn_ctx):
        seen.append(turn_ctx)
        return "latent" if len(seen) == 1 else "explicit"

    ctx = _ctx(mode_decider=decider)

    first = neural_loop._stage_route(ctx)
    assert first.evidence["mode"] == "latent"
    assert first.evidence["turn"] == 0
    assert ctx.modes == ["latent"]
    assert ctx.turns == 1
    # The decider is handed the order it is deciding about, not an empty dict.
    assert seen[0]["order"] == ctx.order
    assert seen[0]["turn"] == 0

    second = neural_loop._stage_route(ctx)
    assert second.evidence["mode"] == "explicit"
    assert second.evidence["turn"] == 1
    assert ctx.modes == ["latent", "explicit"]
    assert ctx.turns == 2


def test_a_raising_decider_does_not_fail_the_route_stage(fake_spine):
    def boom(_turn_ctx):
        raise RuntimeError("mode policy unavailable")

    ctx = _ctx(mode_decider=boom)

    res = neural_loop._stage_route(ctx)

    assert res.ok is True          # the effector still delivered its order
    assert ctx.order               # ...and the order is real, not empty
    assert ctx.modes == []         # nothing was recorded as a choice
    assert "mode" not in res.evidence
    assert ctx.turns == 1


def test_a_decider_returning_none_records_no_choice(fake_spine):
    ctx = _ctx(mode_decider=lambda _turn_ctx: None)

    res = neural_loop._stage_route(ctx)

    assert res.ok is True
    assert "mode" not in res.evidence
    assert ctx.modes == []
    assert ctx.turns == 1


def test_context_fields_are_appended_with_defaults():
    """Construction compatibility: no caller positionally builds this context."""
    ctx = neural_loop.LoopContext()

    assert ctx.mode_decider is None
    assert ctx.modes == []
    assert ctx.turns == 0
    # The engine still builds with all its real stages -- appending fields did
    # not disturb the stage/STAGES validation in `Loop.__init__`.
    assert sorted(neural_loop.Loop().stages) == sorted(neural_loop.STAGES)
