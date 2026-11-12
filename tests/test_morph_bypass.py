# coding: utf-8
"""Novel-candidate bypass: pause judges old trials, never new genes."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.WORMS.expansion.proteus_hooks import (
    morph_candidate_gene,
    morph_evidence_from_ledger,
    morph_rotation_paused,
)


def _ledger(path, n=10):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({
                "cycle_index": i, "gene": "use_schema_features",
                "dimension": 5, "fitness": 22.0 + i * 0.01,
                "baseline_fitness": 21.0,
                "extra": {"use_schema_features": 0.05}}) + "\n")
    return path


def test_old_ledger_pauses(tmp_path):
    hist = _ledger(str(tmp_path / "h.jsonl"))
    paused, reading = morph_rotation_paused(hist)
    assert paused is True
    assert reading.get("verdict") == "free growth"


def test_novel_candidate_has_no_evidence(tmp_path):
    hist = _ledger(str(tmp_path / "h.jsonl"))
    ev = morph_evidence_from_ledger(hist) or {}
    # Retired rotation (ed2bfc25): MORPH_CANDIDATES holds only by_session;
    # the round-robin machinery stays so a future candidate re-opens it
    # with no code change. The test pins the CURRENT pool, not the retired
    # lr_ep* pool it asserted before the retirement.
    names = {morph_candidate_gene(i)["name"] for i in range(10)}
    assert names == {"by_session"}
    novel = [g for g in names if g not in ev]
    assert novel, "every candidate already measured — nothing novel exists"
    # The bypass condition the cycle checks: paused AND candidate novel.
    paused, _ = morph_rotation_paused(hist)
    assert paused is True
    assert all(g not in ev for g in novel)


def test_round_robin_covers_new_genes():
    cands = [("lr_ep0", "float", 0.0, 0.3, "lr"),
             ("lr_ep15", "float", 0.0, 0.3, "lr"),
             ("lr_ep30", "float", 0.0, 0.3, "lr")]
    names = [morph_candidate_gene(i, cands)["name"] for i in range(5)]
    assert len(set(names)) > 2  # rotation actually rotates
