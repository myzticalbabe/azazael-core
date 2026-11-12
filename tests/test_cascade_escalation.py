"""The fast strategy now escalates on low confidence instead of accepting anything.

`CORE/cascade.py::CascadeRouter` shipped complete with no production caller, so
EP201/EP208 scored EXERCISE-ONLY -- the router was only ever driven by the exercise
layer in `src/CORE/technique_wiring/`.

The real gap it closes: `SequentialAnalyzer.run(strategy="fast")` uses a SINGLE-step
chain, and `check_convergence` needs two responses, so convergence could never fire
and the cheap model's answer was final at ANY confidence. The deep tier was reachable
only by asking for `deep` up front. `run()` now delegates a one-step chain to
`_run_single_with_cascade`, which accepts the triage answer at or above
`CASCADE_THRESHOLD` and consults the deep-tier model below it.

Both halves are asserted, because a call that fires but changes nothing is not an
integration:
  * LOW confidence  -> the deep-tier model is called and its answer is the result;
  * HIGH confidence -> the deep-tier model is NOT called and the cheap answer stands.

Providers are stubbed: no network, no API keys. The stub replaces `_chat`, which is
the single place the analyzer calls a provider from this path.
"""


import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _analyzer_and_stub(cheap_confidence):
    """Return (analyzer, calls) with `_chat` stubbed; `calls` records every model."""
    from CORE.inference import sequential_analyzer as sa

    calls = []
    cheap_model = sa.FAST_CHAIN[0].model
    deep_model = sa.DEEP_CHAIN[1].model

    def fake_chat(self, step, prompt, max_tokens=None):
        calls.append(step.model)
        if step.model == cheap_model:
            return sa.ModelResponse(
                provider=step.provider, model=step.model,
                content="cheap answer", elapsed_ms=7, tokens_used=11,
                success=True, confidence=cheap_confidence)
        return sa.ModelResponse(
            provider=step.provider, model=step.model,
            content="deep answer", elapsed_ms=90, tokens_used=500,
            success=True, confidence=0.81)

    sa.SequentialAnalyzer._chat = fake_chat
    return sa.SequentialAnalyzer(), calls, cheap_model, deep_model


def test_low_triage_confidence_escalates_to_the_deep_tier():
    analyzer, calls, cheap_model, deep_model = _analyzer_and_stub(0.20)

    result = analyzer.run("a question the cheap tier is unsure about", strategy="fast")

    # The expensive call happened, exactly once, and its answer is what is returned.
    assert calls.count(deep_model) == 1, f"deep tier not escalated to: {calls}"
    assert calls[0] == cheap_model, "the cheap tier must be tried first"
    assert result.synthesis == "deep answer"
    assert result.confidence == 0.81
    assert len(result.models_used) == 2, result.models_used


def test_high_triage_confidence_does_not_escalate():
    analyzer, calls, cheap_model, deep_model = _analyzer_and_stub(0.90)

    result = analyzer.run("an easy question", strategy="fast")

    # The gate held: the expensive model was never called, and the cheap answer is
    # the result -- not merely logged and discarded.
    assert deep_model not in calls, f"escalated despite high confidence: {calls}"
    assert calls == [cheap_model]
    assert result.synthesis == "cheap answer"
    assert result.confidence == 0.90
    assert len(result.models_used) == 1, result.models_used


def test_multi_step_strategies_are_unchanged():
    """The cascade is scoped to one-step chains; `deep` keeps its original loop.

    This test previously stubbed `SequentialAnalyzer._chat`, which the MULTI-step
    loop never calls -- it calls `provider.chat` directly
    (`sequential_analyzer.py:720`). So `calls` was always `[]` on a deep run: the
    assertion could not fire for the reason it named, and the run reached the real
    providers. It now stubs the provider factory the loop actually uses and asserts
    the property itself -- every deep step ran, and the cascade was never entered.
    """
    from CORE.inference import sequential_analyzer as sa

    stepped = []

    class _StubProvider:
        def __init__(self, name):
            self.name = name

        def has_valid_key(self):
            return True

        def chat(self, **kwargs):
            stepped.append(kwargs.get("model", ""))
            return {"content": "a" * 200, "model": kwargs.get("model", ""),
                    "usage": {"total_tokens": 3}}

    cascade_entered = []

    def spy_cascade(self, prompt, step, strategy, max_tokens=None):
        cascade_entered.append(step.model)
        raise AssertionError("a multi-step chain must not route through the cascade")

    sa.get_provider = _StubProvider
    sa.SequentialAnalyzer._run_single_with_cascade = spy_cascade
    analyzer = sa.SequentialAnalyzer()
    result = analyzer.run("q", strategy="deep")

    assert not cascade_entered, cascade_entered
    assert len(result.models_used) == 3, result.models_used
    assert stepped == [s.model for s in sa.DEEP_CHAIN], stepped
