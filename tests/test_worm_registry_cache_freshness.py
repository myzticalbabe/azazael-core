# coding: utf-8
"""
2026-09-14 — the discovery cache was keyed on the wrong thing.

`worm_registry_cache` invalidated on the mtimes of the ``WORMS/`` tree only.
The G17 fix (feeding ``_guess_category()`` the module that *defines* a class
instead of the shim it was being scanned through) had **no effect** until the
pickle was deleted by hand, because every affected worm lives in a file whose
mtime had not changed. The same is true of any change to the category
heuristics, the ``PROTECTED_NATURES``/``FLEET_GOVERNORS`` invariants or the
auto-discovery scan: the cache stayed "fresh" while the semantics moved.

These tests pin the contract: freshness now requires BOTH the tree to be
unchanged AND the registry modules' contents to match the fingerprint they
were built from.
"""

import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE import worm_registry_cache as wrc

WORMS_DIR = str(Path(wrc.__file__).resolve().parent / "WORMS")


def test_fingerprint_is_stable_and_not_unknown():
    fp = wrc._registry_fingerprint()
    assert fp != "unknown", "registry sources unreadable — freshness cannot be proven"
    assert fp == wrc._registry_fingerprint(), "fingerprint is not deterministic"
    assert len(fp) == 32


def test_fingerprint_covers_the_registry_modules():
    """The tuple is the contract; the modules that shape discovery must be in it."""
    assert "worm_registry.py" in wrc._REGISTRY_SOURCE_FILES
    assert "worm_registry_cache.py" in wrc._REGISTRY_SOURCE_FILES


def test_fingerprint_changes_when_a_source_changes(tmp_path, monkeypatch):
    """Content, not mtime: rewriting a byte must move the digest."""
    core = Path(wrc.__file__).resolve().parent
    real = core / "worm_registry.py"
    original = real.read_bytes()

    fake_core = tmp_path
    (fake_core / "worm_registry.py").write_bytes(original)
    (fake_core / "worm_registry_cache.py").write_bytes(
        (core / "worm_registry_cache.py").read_bytes())
    monkeypatch.setattr(wrc, "__file__", str(fake_core / "worm_registry_cache.py"))

    before = wrc._registry_fingerprint()
    assert before != "unknown"

    # Same length, different content, mtime deliberately untouched.
    mutated = bytearray(original)
    marker = b"CORE " if b"CORE " in original else b"import"
    at = original.index(marker)
    mutated[at] = ord("X") if original[at] != ord("X") else ord("Y")
    (fake_core / "worm_registry.py").write_bytes(bytes(mutated))

    after = wrc._registry_fingerprint()
    assert after != before, "content change did not move the fingerprint"


def test_cache_roundtrip_is_fresh_then_stale_on_semantics_drift(tmp_path, monkeypatch):
    """Save a cache, confirm fresh; then move the fingerprint and confirm the
    cache is rejected — the exact failure that hid the G17 fix."""
    real_cached = wrc.CACHE_FILE
    monkeypatch.setattr(wrc, "CACHE_FILE", tmp_path / "cache.pkl")
    try:
        wrc.save_cache({"FakeWorm": {"category": "mesh"}}, WORMS_DIR)
        assert wrc._is_cache_fresh(WORMS_DIR) is True

        # Simulate a semantics revision after the cache was written.
        monkeypatch.setattr(wrc, "_registry_fingerprint", lambda: "revised")
        assert wrc._is_cache_fresh(WORMS_DIR) is False
    finally:
        monkeypatch.setattr(wrc, "CACHE_FILE", real_cached)


def test_legacy_cache_without_fingerprint_is_treated_as_stale(tmp_path, monkeypatch):
    """A pickle written before this contract existed has no fingerprint key."""
    real_cached = wrc.CACHE_FILE
    legacy = tmp_path / "legacy.pkl"
    payload = {
        "_mtimes": wrc._get_module_mtimes(WORMS_DIR),
        "_built_at": "2026-09-13T00:00:00",
        "_total_registered": 1,
        "FakeWorm": {"category": "mesh"},
    }
    with open(legacy, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

    monkeypatch.setattr(wrc, "CACHE_FILE", legacy)
    try:
        assert wrc._is_cache_fresh(WORMS_DIR) is False
        status = wrc.cache_status()
        assert status["registry_fingerprint"] is None
        assert status["semantics_current"] is False
    finally:
        monkeypatch.setattr(wrc, "CACHE_FILE", real_cached)


def test_cache_status_reports_semantics_currency():
    status = wrc.cache_status()
    if not status.get("exists"):
        return  # nothing built yet in this environment
    assert "registry_fingerprint" in status
    assert "current_fingerprint" in status
    assert "semantics_current" in status


# ── Currency and safety are different questions (2026-09-18) ─────────────
# Both below are additions to the 09-14 contract, not replacements. The
# fingerprint answers "does this cache match the sources"; these answer "may
# this cache be served at all".

def test_incomplete_registry_is_refused_even_when_current(tmp_path, monkeypatch):
    """A current cache can still be missing the fleet, and must not load.

    Observed on the shared cache: 111 of 229 worms (24 of 35 categories) with
    sources unchanged, so the mtime AND fingerprint checks both passed and the
    fleet came up missing `axon`, `dendrite`, `glia`, `envmesh`, `ml` and
    `network`. Every consumer -- the queens, the wire-or-cut census -- inherited
    the undercount silently.
    """
    real = wrc.CACHE_FILE
    monkeypatch.setattr(wrc, "CACHE_FILE", tmp_path / "cache.pkl")
    try:
        cache = {
            "FakeWorm": {"category": "mesh"},
            "_mtimes": wrc._get_module_mtimes(WORMS_DIR),
            "_fingerprint": wrc._registry_fingerprint(),
            "_coverage": {"declared_on_disk": 185, "registered": 111,
                          "complete": False},
        }
        (tmp_path / "cache.pkl").write_bytes(
            pickle.dumps(cache, protocol=pickle.HIGHEST_PROTOCOL))
        assert wrc._is_cache_fresh(WORMS_DIR) is True, "sources are unchanged"
        assert wrc._load_cache_readonly(WORMS_DIR) is None, \
            "an incomplete registry must never be served"
    finally:
        monkeypatch.setattr(wrc, "CACHE_FILE", real)


def test_unpicklable_entry_is_excluded_not_fatal(tmp_path, monkeypatch):
    """One local class must not cost the whole cache, and must be declared.

    The original failure mode was worse than a missing entry: `pickle.dump`
    raised, `discover_worms` swallows that as non-fatal, and the cache silently
    kept the registry it held BEFORE -- an older fleet answering for a tree it
    no longer described, with nothing reported.
    """
    real = wrc.CACHE_FILE
    monkeypatch.setattr(wrc, "CACHE_FILE", tmp_path / "cache.pkl")

    class LocalWorm:            # defined inside a function: not picklable
        pass

    try:
        report = wrc.save_cache(
            {"GoodWorm": {"category": "mesh", "class": dict},
             "LocalWorm": {"category": "mesh", "class": LocalWorm}},
            WORMS_DIR)
        assert report["excluded"] == ["LocalWorm"], report
        assert report["complete"] is False, (
            "a cache missing entries is not complete, however good the reason")
        assert wrc._load_cache_readonly(WORMS_DIR) is None
    finally:
        monkeypatch.setattr(wrc, "CACHE_FILE", real)
