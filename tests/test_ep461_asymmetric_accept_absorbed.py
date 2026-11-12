"""EP461 AsymSpec: the compressed verifier view must be BUILT, not assumed.

Absorption test for engram #632 Phase 2, slice 6 (technique lane).

The defect this locks down: `verification_gate.divergence_gate` (EP461's
registered mechanism, `technique_wiring/_l3_verify.py`) grades two
caller-supplied views and therefore never builds the asymmetry the
technique is named for — "full-context drafter steers compressed-view
verifier; divergence gate" (`state/SOTA_FULL_REVIEW_2026-10-09.md`, EXTEND
row: "compressed verifier view + contrastive fusion"). The technique was
registered while its named mechanism, `asymmetric_accept`, did not exist.

This adds it in the host (`verification_gate.py`): budget the verifier's
view from the FULL draft (confidence-ordered, first-seen ties), gate the
compressed coverage, gate full coverage, and fuse — with any claim the
verifier "confirmed" without its view ever granting it reported as the
tamper signal `unseen_verified`. The live gate consumes it via the
`verifier_budget` opt-in; without it the plain divergence gate is
byte-for-byte unchanged (no regressions on existing callers).

Per this repo's standard (tests/test_simulate_vs_spawn.py): a mechanism
nothing calls is EXERCISE-ONLY by construction, so the live-gate wiring is
part of the contract.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def test_the_compressed_view_is_built_from_draft_and_budget():
    """The builder must exist and must slice by budget, not pass through."""
    from CORE.verification_gate import asymmetric_accept

    out = asymmetric_accept(
        draft_claims=["a", "b", "c", "d"],
        verifier_claims=["a", "b"],
        verifier_budget=2)

    assert out["verifier_view"] == ["a", "b"], out
    assert out["n_verifier_view"] == 2 and out["n_draft"] == 4, out


def test_confidence_alignment_orders_the_verifier_view():
    """confidences (index-aligned, first-seen tie-break) pick the slice.

    The ordering is load-bearing, not cosmetic: the verifier's coverage
    ("a", "c") lands *inside* the confidence-ordered view, so the
    compressed gate passes — while a first-seen view ("a", "b") would
    have missed it. Fusion still owes the FULL draft (2 of 4 shown ⇒
    divergence 0.5), so the top-level accept stays False here.
    """
    from CORE.verification_gate import asymmetric_accept

    kw = {"draft_claims": ["a", "b", "c", "d"],
          "verifier_claims": ["a", "c"],
          "verifier_budget": 2}

    ordered = asymmetric_accept(confidences=[0.9, 0.2, 0.8, 0.1], **kw)

    assert ordered["verifier_view"] == ["a", "c"], ordered  # top-2 by confidence
    assert ordered["compressed"]["accepted"] is True, ordered
    assert ordered["full"]["accepted"] is False, ordered    # draft 4 ≠ shown 2
    assert ordered["accepted"] is False, ordered

    plain = asymmetric_accept(**kw)                         # no confidences

    assert plain["verifier_view"] == ["a", "b"], plain      # first-seen slice
    assert plain["compressed"]["accepted"] is False, plain  # coverage misses


def test_fusion_rejects_when_the_compressed_view_is_uncovered():
    """A verifier that covers nothing cannot carry the accept."""
    from CORE.verification_gate import asymmetric_accept

    out = asymmetric_accept(
        draft_claims=["a", "b", "c", "d"],
        verifier_claims=[],
        verifier_budget=2)

    assert out["accepted"] is False, out
    assert out["compressed"]["accepted"] is False, out


def test_fusion_rejects_when_full_divergence_stays_high():
    """Compression cuts the view; acceptance still owes the FULL draft."""
    from CORE.verification_gate import asymmetric_accept

    out = asymmetric_accept(
        draft_claims=["a", "b", "c", "d"],
        verifier_claims=["a", "b"],       # view covered, draft not
        verifier_budget=2)

    assert out["compressed"]["accepted"] is True, out
    assert out["accepted"] is False, out
    assert out["dropped_claims"] == ["c", "d"], out


def test_unseen_verified_claims_are_the_tamper_signal():
    """Verifying a claim the view never granted is evidence tampering."""
    from CORE.verification_gate import asymmetric_accept

    out = asymmetric_accept(
        draft_claims=["a", "b"],
        verifier_claims=["a", "b", "c"],
        verifier_budget=2)

    assert out["unseen_verified"] == ["c"], out
    assert out["accepted"] is False, out


def test_empty_draft_fails_open_like_the_divergence_gate():
    from CORE.verification_gate import asymmetric_accept

    out = asymmetric_accept(draft_claims=[], verifier_claims=[],
                            verifier_budget=2)

    assert out["accepted"] is True, out
    assert out["n_draft"] == 0, out


def test_the_live_gate_paths_through_asymmetric_accept(monkeypatch):
    """`verifier_budget` must engage the builder; its absence must not."""
    from CORE import verification_gate as vg

    calls: list = []
    real = vg.asymmetric_accept

    def _spy(draft_claims, verifier_claims=None, confidences=None,
             verifier_budget=2, max_divergence=0.1):
        calls.append({"draft": list(draft_claims), "budget": verifier_budget})
        return real(draft_claims, verifier_claims=verifier_claims,
                    confidences=confidences, verifier_budget=verifier_budget,
                    max_divergence=max_divergence)

    monkeypatch.setattr(vg, "asymmetric_accept", _spy, raising=True)

    out = vg.verify_before_accept(
        "ep461-live",
        draft_claims=["a", "b", "c", "d"],
        verified_claims=["a", "b"],
        verifier_budget=2,
        record=False)

    assert len(calls) == 1, calls                       # the seam engaged once
    assert calls[0]["budget"] == 2, calls
    assert out["accepted"] is False, out
    assert any(r.startswith("asym-blocked(") for r in out["reasons"]), out
    # The house rule: a rejection carries a remediation with repair targets.
    remediations = [m for m in out["remediations"] if "reconcile dropped" in m]
    assert remediations and "c, d" in remediations[0], out


def test_no_budget_keeps_the_plain_divergence_gate(monkeypatch):
    """Opt-in guard: existing divergence-gate callers see zero change."""
    from CORE import verification_gate as vg

    calls: list = []

    def _spy(*args, **kwargs):                          # must never run
        calls.append(1)
        raise AssertionError("asymmetric_accept must not run without budget")

    monkeypatch.setattr(vg, "asymmetric_accept", _spy, raising=True)

    out = vg.verify_before_accept(
        "ep461-plain",
        draft_claims=["a", "b", "c", "d"],
        verified_claims=["a", "b"],
        record=False)

    assert calls == [], calls
    assert out["accepted"] is False, out
    assert any(r.startswith("divergence-high(") for r in out["reasons"]), out
