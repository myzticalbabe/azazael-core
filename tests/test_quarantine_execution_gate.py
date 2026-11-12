# coding: utf-8
"""A quarantine ORDER must gate execution, not merely be recorded.

Followup #7 (2026-09-28): the global quarantine item store was restored to
`spine_registry` (see FORENSIC_FOLLOWUPS_2026-09-28.md section 0), but nothing
consulted it -- `RedQueenWorm.order_quarantine()` wrote verdicts into a store
the scheduler never read. That is the same decorative-gate defect the module's
own docstring names for retirements ("a retired worm that the queen keeps
probing").

These tests drive the Red Queen's candidate scheduler
(`RedQueenWorm._probe_candidates`) directly, because it is the stage that
decides which members get `execute()`d: a quarantined member must not survive
it, and a released member must come back.
"""
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE import spine_registry as sr  # noqa: E402
from CORE.worm_registry import WORM_REGISTRY, is_protected_worm  # noqa: E402
from CORE.WORMS.expansion.spine import RedQueenWorm  # noqa: E402


def _eligible_member():
    """(nature, name) of a real probed member the scheduler is allowed to run."""
    for nature in RedQueenWorm.PROBE_NATURES:
        for name, info in WORM_REGISTRY.items():
            if (info.get("category") == nature
                    and info.get("lifecycle_state") != "retired"
                    and not is_protected_worm(info)):
                return nature, name
    return None, None


def _queen():
    """A Red Queen with the deterministic (scout-disabled) selector."""
    queen = RedQueenWorm()
    queen._scout = None
    return queen


def _names(queen, nature, limit=4096):
    chosen, *_rest = queen._probe_candidates(nature, limit)
    return [n for n, _info in chosen]


@pytest.fixture(autouse=True)
def _clean_quarantine():
    sr.spine_reset()
    yield
    sr.spine_reset()


def test_quarantined_member_is_held_out_of_the_probe_set():
    nature, name = _eligible_member()
    assert name, "no eligible probed member found in this registry"

    queen = _queen()
    assert name in _names(queen, nature), "setup: member should be selectable"

    sr.quarantine_item(name, "test: chronically failing")
    chosen, _prot, _hung, _ret, qskip = queen._probe_candidates(nature, 4096)
    assert name not in [n for n, _info in chosen]
    assert name in qskip, "the hold-out must be reported, not silent"


def test_released_member_returns_to_the_probe_set():
    nature, name = _eligible_member()
    sr.quarantine_item(name, "test")
    queen = _queen()
    assert name not in _names(queen, nature)
    sr.release_item(name)
    assert name in _names(queen, nature)


def test_quarantine_worm_delegates_to_the_shared_store():
    """A FRESH instance must see the pin -- the record is not instance-local."""
    from CORE.WORMS.expansion.immune import QuarantineWorm

    QuarantineWorm().quarantine("GatewayWorm", "test: poisoned shard")
    assert QuarantineWorm().is_quarantined("GatewayWorm") is True
    assert sr.is_quarantined("GatewayWorm") is True
    assert "GatewayWorm" in QuarantineWorm().active()

    result = QuarantineWorm().release("GatewayWorm")
    assert result["shared"] is True
    assert QuarantineWorm().is_quarantined("GatewayWorm") is False
    assert sr.quarantine_count() == 0


def test_protection_still_takes_its_own_precedence():
    """A protected governor is reported under protection, not quarantine."""
    nature, _name = _eligible_member()
    queen = _queen()
    _chosen, prot, _hung, _ret, qskip = queen._probe_candidates(nature, 4096)
    assert not (set(prot) & set(qskip))
