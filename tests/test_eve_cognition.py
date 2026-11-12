"""Eve cognition -- strata membership, honest audit, and the nexus chain.

The audit is the contract: a declared process with no live-log evidence
reads 'declared', never 'used'; a locus that does not exist reads
'absent'. Gaps are named, never hidden.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import pytest

from eve_unleashed import cognition as _c
from eve_unleashed import live_log as _l


def test_every_process_has_a_real_stratum_and_locus():
    rows = _c.audit()["processes"]
    assert rows
    for r in rows:
        assert r["stratum"] in _c.STRATA
        assert r["locus"].endswith(".py")
        assert r["state"] in ("used", "declared", "absent")


def test_loci_exist_so_no_process_is_absent():
    absent = [r["name"] for r in _c.audit()["processes"]
              if r["state"] == "absent"]
    assert absent == []


def test_evidence_promotes_declared_to_used(tmp_path):
    p = str(tmp_path / "l.jsonl")
    before = {r["name"]: r["state"] for r in _c.audit(p)["processes"]}
    assert before["intuition"] == "declared"
    _l.emit("fetch", "intuition", path=p, url="https://x.test/",
            predicted="plain")
    after = {r["name"]: r["state"] for r in _c.audit(p)["processes"]}
    assert after["intuition"] == "used"
    assert after["reflex"] == "declared"


def test_gaps_are_named_and_not_in_processes():
    rep = _c.audit()
    names = {g["name"] for g in rep["gaps"]}
    # consolidation / incubation / inhibition WERE gaps until b7ff4348
    # implemented them natively in intuition.py -- now processes, so the
    # old "still a gap" expectation is stale. What remains contracted:
    # (a) they are audited processes, (b) gaps never overlap processes,
    # (c) each maps to a real callable (a listing is not an implementation).
    from eve_unleashed import intuition as _i
    assert {"consolidation", "incubation", "inhibition"} <= \
        {r["name"] for r in rep["processes"]}
    assert not (names & {r["name"] for r in rep["processes"]})
    assert callable(_i.consolidate)
    assert callable(_i.incubate)
    assert callable(_i.inhibited_rungs)


def test_paradigm_membership_and_unknown():
    cons = _c.paradigm("conscious")
    assert "fetch-ladder" in cons["members"]
    assert "deliberate" in cons["paradigm"]
    with pytest.raises(ValueError):
        _c.paradigm("metaconscious")


def test_nexus_returns_three_strata_in_order():
    nx = _c.nexus("https://unseen.test/page",
                  log_path="state/does-not-exist.jsonl")
    assert list(nx)[:1] == ["request"]
    for s in _c.STRATA:
        assert s in nx
    assert nx["subconscious"]["guess"] == "unknown"
    assert "live_log" in nx["unconscious"]["locus"]
