# coding: utf-8
"""Tests for promotion_pack / promotion_gate — EP363 LOGOS living logic.

The bundle is untrusted at construction; only the gate flips `trusted`,
and only on stated evidence. Skills route to the commit-gate shape;
unknown kinds are rejected, never trusted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.skill_library import (
    PROMOTION_KINDS, promotion_gate, promotion_pack)


def test_pack_untrusted_at_construction():
    """promotion_pack never trusts: trusted is always False here."""
    pack = promotion_pack("prompt", "Router-v3", 2,
                          {"template": "route {q}"},
                          {"proof": "ledger:99", "scope_note": "dispatch only"})
    assert pack["trusted"] is False
    assert pack["kind"] == "prompt"
    assert pack["version"] == 2


def test_unknown_kind_rejected_not_trusted():
    """Kinds outside PROMOTION_KINDS are rejected with a remediation."""
    out = promotion_gate(promotion_pack("model", "X", 1, {}, {"proof": "p"}),
                         record=False)
    assert out["trusted"] is False
    assert out["gate"]["accepted"] is False
    assert out["gate"]["remediations"]


def test_prompt_gate_needs_proof_and_scope():
    """Non-skill kinds require both proof and scope_note."""
    bare = promotion_gate(promotion_pack("workflow", "W", 1, {}), record=False)
    assert bare["gate"]["accepted"] is False
    assert "no-evidence-proof" in bare["gate"]["reasons"]
    assert "no-scope-note" in bare["gate"]["reasons"]
    full = promotion_gate(promotion_pack(
        "memory", "M", 1, {}, {"proof": "ledger:7", "scope_note": "n/a: read-only"}),
        record=False)
    assert full["gate"]["accepted"] is True
    assert full["trusted"] is True


def test_all_kinds_gateable():
    """Every PROMOTION_KINDS member reaches a verdict (accept or remediate)."""
    for kind in PROMOTION_KINDS:
        if kind == "skill":
            pack = promotion_pack(kind, "S", 1, {"family": "dispatch"},
                                  {"proof": "ledger:1",
                                   "transfer_masking": "n/a: single-domain",
                                   "version": 1})
        else:
            pack = promotion_pack(kind, "N", 1, {},
                                  {"proof": "ledger:1",
                                   "scope_note": "dispatch only"})
        out = promotion_gate(pack, record=False)
        assert out["gate"]["accepted"] is True, kind
        assert out["trusted"] is True, kind


def test_skill_without_family_rejected():
    """Skill packs inherit the commit-gate family requirement."""
    out = promotion_gate(promotion_pack("skill", "S", 1, {},
                                        {"proof": "ledger:1",
                                         "transfer_masking": "masked"}),
                         record=False)
    assert out["gate"]["accepted"] is False
    assert out["trusted"] is False
