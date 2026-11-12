"""EP435 Refl-distill: the registered adapter must RUN the chain, not surface it.

Absorption test for engram #632 (MIMO SHELFWARE ABSORB).

The defect this locks down: `_l6_evolve._ep435` was registered under EP435 while
doing nothing but `_surface(...)` -- reporting that `observe`, `analyze_patterns`
and `adapt_strategy` EXIST on `CORE.WORMS.expansion.meta_cognition`. The
technique's actual mechanism, `meta_cognition.internalize` (the
analyse -> recommend -> adapt sequence that IS the EP435 Internalize phase), had
zero callers anywhere in src/. Registered-but-not-absorbed.

Per this repo's own standard (see tests/test_simulate_vs_spawn.py): "A pure
function nothing calls is the exact shape this repo keeps producing (flaws
S1/S3: EXERCISE-ONLY by construction), so the wiring is part of the contract."
So these tests assert BEHAVIOUR of the live adapter and the registration, not
the presence of a def.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

#: Keys only `internalize` produces. A fabricated dict cannot fake the chain.
_CHAIN_KEYS = {"error_rate", "recommended", "confidence", "adopted",
               "strategy", "reason"}


def _adapter():
    from CORE.technique_wiring import _l6_evolve as l6

    return l6._ep435


def _direct(obs):
    from CORE.WORMS.expansion import meta_cognition as mc

    return mc.internalize(obs)


def test_the_adapter_runs_the_real_internalize_chain():
    """`chain_ran` must reflect an actual call into `internalize`."""
    out = _adapter()()

    assert out["engaged"] is True, out
    assert out.get("chain_ran") is True, (
        "the EP435 adapter did not run meta_cognition.internalize: %r" % (out,))
    assert "error" not in out, out


def test_the_verdict_came_from_the_chain_not_a_stub():
    """The reported keys must be `internalize`'s, not a hand-written dict."""
    out = _adapter()()

    keys = set(out.get("verdict_keys") or [])
    assert keys & _CHAIN_KEYS, (
        "adapter reported no chain verdict keys; got %r" % (sorted(keys),))
    assert "confidence" in out, out


def test_the_adapter_matches_a_direct_chain_call():
    """Same observation shape in, same chain out -- the seam is the real one."""
    direct = _direct({"worm_type": "ep435_probe", "action": "internalize_chain",
                      "outcome": "success", "context": {"probe": "l6_ep435"}})

    assert set(direct.keys()) & _CHAIN_KEYS, (
        "internalize itself no longer returns a chain verdict: %r" % (direct,))
    out = _adapter()()
    assert out.get("confidence") == direct.get("confidence"), (out, direct)


def test_a_self_probe_never_adopts_a_strategy():
    """Read-mostly guard: a probe must not churn the active strategy."""
    out = _direct({"worm_type": "ep435_probe", "action": "internalize_chain",
                   "outcome": "success", "context": {"probe": "l6_ep435"}})

    assert isinstance(out, dict), out
    assert out.get("adopted") in (False, None), (
        "a self-test probe adopted a strategy on thin evidence: %r" % (out,))


def test_fail_open_when_the_host_symbol_is_missing(monkeypatch):
    """Missing mechanism must degrade, never raise, from the live adapter."""
    from CORE.WORMS.expansion import meta_cognition as mc

    monkeypatch.delattr(mc, "internalize", raising=False)
    out = _adapter()()

    assert out["engaged"] is False, out
    assert "internalize" in out.get("reason", ""), out


def test_ep435_is_actually_registered_in_the_technique_runtime():
    """Wiring is the contract: EP435 must be a live registry entry."""
    from CORE import technique_runtime as tr

    hits = []
    for attr in dir(tr):
        val = getattr(tr, attr, None)
        if isinstance(val, dict):
            for key in val:
                if isinstance(key, str) and key.upper() == "EP435":
                    hits.append(attr)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                text = str(item)
                if "EP435" in text:
                    hits.append(attr)
                    break
    assert hits, "EP435 is not registered in technique_runtime"
