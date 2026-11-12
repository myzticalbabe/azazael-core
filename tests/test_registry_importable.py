# coding: utf-8
"""Unit tests for worm registry importability validation (startup check).

Covers:
  - validate_registry_importable(): all-pass on healthy registry
  - _resolve_worm_import_module(): package-qualified, bare WORMS, bare src-root,
    worms.* package, and empty info
  - validate_registry_importable() with a broken entry (unimportable module,
    missing class) → flagged in `broken` and `valid=False`
  - startup_warm wiring: _run_registry_validation stores a result;
    get_registry_validation() returns it; cache_status() surfaces it
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.worm_registry import (
    WORM_REGISTRY,
    _registry_lock,
    _resolve_worm_import_module,
    register_worm,
    repair_registry,
    reset_registry,
    validate_registry_importable,
)


def _clear_registry_contents():
    """Empty the registry WITHOUT purging worm modules from `sys.modules`.

    The purge half of `reset_registry()` is process-global in a way this file
    cannot undo: every `CORE.WORMS.*` module object created afterwards is a
    DIFFERENT object from the one every other test module imported at collection
    time. MEASURED consequence, found 2026-09-21 by running this file before
    `tests/test_wiring_cycle.py`: 7 failures and 2 errors there, because its
    `monkeypatch.setattr(wg, "OBSERVABLE_MUTATIONS", ...)` patches the object the
    test module imported while a reloaded `wiring_cycle` imports a new one -- so
    the gate tests were silently exercising the REAL mutation operators and
    passing or failing for a reason other than the one they name.

    `purge_modules=False` (added 2026-09-21) keeps module identity while still
    re-arming discovery. Clearing the dict alone is NOT enough: discover_worms()
    short-circuits on a still-True `_discovery_done` and returns 0, so the
    module-scope restore fixture below was a silent no-op and every LATER test
    module inherited an EMPTY registry it could not rebuild (measured: 9
    failures in tests/test_spine_mesh.py, which passes 62/62 on its own).

    The tests here only need a SMALL registry, not fresh module objects: what
    they assert is `total == 2` and the shape of a validation report.
    """
    reset_registry(purge_modules=False)


# ── _resolve_worm_import_module ─────────────────────────────────────

def test_resolve_package_qualified_passthrough():
    """CORE.WORMS.X and worms.* module refs pass through unchanged."""
    assert _resolve_worm_import_module({"module": "CORE.WORMS.SYSTEMZ"}) == "CORE.WORMS.SYSTEMZ"
    assert _resolve_worm_import_module({"module": "worms.nmap_scanner"}) == "worms.nmap_scanner"


def test_resolve_bare_worms_module():
    """Bare module name + CORE/WORMS source file → CORE.WORMS.<name>."""
    info = {
        "module": "CLUSTERWORMS",
        "source_file": "C:/Azazael/azazael/src/CORE/WORMS/CLUSTERWORMS.py",
    }
    assert _resolve_worm_import_module(info) == "CORE.WORMS.CLUSTERWORMS"


def test_resolve_bare_core_module():
    """Bare module name + CORE/ source file → CORE.<name>."""
    info = {
        "module": "SOMEWORM",
        "source_file": "C:/Azazael/azazael/src/CORE/SOMEWORM.py",
    }
    assert _resolve_worm_import_module(info) == "CORE.SOMEWORM"


def test_resolve_bare_src_root_module():
    """src-root module (TRIMZ.py) stays bare — imports from src root."""
    assert _resolve_worm_import_module({"module": "TRIMZ"}) == "TRIMZ"


def test_resolve_empty_info():
    """No module and no source_file → empty string (validation will flag it)."""
    assert _resolve_worm_import_module({}) == ""


def test_resolve_bare_with_source_stem():
    """Bare module with only a source file infers the module name from the stem."""
    info = {"module": "", "source_file": "C:/Azazael/azazael/src/CORE/WORMS/HEALTHZ.py"}
    assert _resolve_worm_import_module(info) == "CORE.WORMS.HEALTHZ"


# ── validate_registry_importable ────────────────────────────────────

# Module-level probe worms so the standalone runner (module == "__main__")
# can resolve them via getattr() — function-local classes would not be
# attributes of the test module and would be flagged as broken.


@register_worm(category="diagnostic", priority=5, cooldown_seconds=30)
class _ProbeWormA:
    VERSION = "1.0.0"


@register_worm(category="monitor", priority=6, cooldown_seconds=30)
class _ProbeWormB:
    VERSION = "1.0.0"


def _fresh_registry():
    """Reset the registry and re-register the module-level probe worms."""
    _clear_registry_contents()
    register_worm(category="diagnostic", priority=5, cooldown_seconds=30)(_ProbeWormA)
    register_worm(category="monitor", priority=6, cooldown_seconds=30)(_ProbeWormB)


def test_validate_all_pass():
    """Healthy registry → valid=True, checked == total, no broken entries."""
    _fresh_registry()
    result = validate_registry_importable()
    assert result["valid"] is True
    assert result["total"] == 2
    assert result["checked"] == 2
    assert result["broken"] == []
    assert "validated_at" in result


def test_validate_flags_broken_module():
    """Entry whose module cannot be imported is reported as broken."""
    _fresh_registry()
    with _registry_lock:
        WORM_REGISTRY["_GhostWorm"] = {
            "class": object,  # not a real registered worm
            "module": "no_such_module_xyz",
            "category": "utility",
            "priority": 5,
            "cooldown_seconds": 30,
            "source_file": "",
        }
    try:
        result = validate_registry_importable()
        assert result["valid"] is False
        assert result["total"] == 3
        assert result["checked"] == 2
        names = [b["name"] for b in result["broken"]]
        assert "_GhostWorm" in names
        b = next(x for x in result["broken"] if x["name"] == "_GhostWorm")
        assert "no_such_module_xyz" in b["module"]
        assert b["error"]
    finally:
        _clear_registry_contents()


def test_validate_flags_missing_class():
    """Entry whose module imports but lacks the registered class is broken."""
    _fresh_registry()
    with _registry_lock:
        WORM_REGISTRY["_NotThere"] = {
            "class": object,
            "module": "CORE.worm_registry",  # imports fine, class absent
            "category": "utility",
            "priority": 5,
            "cooldown_seconds": 30,
            "source_file": "",
        }
    try:
        result = validate_registry_importable()
        assert result["valid"] is False
        b = next(x for x in result["broken"] if x["name"] == "_NotThere")
        assert "not found" in b["error"].lower()
    finally:
        _clear_registry_contents()


# ── repair_registry (auto-repair) ──────────────────────────────────

def test_repair_registry_noop_when_valid():
    """Healthy registry → repair is a no-op (attempt=False, nothing repaired)."""
    _fresh_registry()
    try:
        result = repair_registry()
        assert result["attempt"] is False
        assert result["repaired"] == 0
        assert result["valid"] is True
        assert result["still_broken"] == []
    finally:
        _clear_registry_contents()


def test_repair_registry_runs_and_revalidates(monkeypatch):
    """Broken entries trigger a re-discovery repair attempt (never raises).

    THE PURGE IS REPLACED BY A COUNTER, and that is the point of this test's
    shape. `repair_registry()` invalidates the pickle cache, purges every
    imported `CORE.WORMS.*` module from `sys.modules`, and rebuilds -- and that
    purge is process-global and IRREVERSIBLE: every module object any other test
    already imported becomes stale. MEASURED, 2026-09-21: running this file
    before `tests/test_wiring_cycle.py` produced 7 failures and 2 errors there,
    because its `monkeypatch.setattr(wg, "OBSERVABLE_MUTATIONS", ...)` patched an
    object the reloaded cycle no longer used, so its gate tests silently
    exercised the REAL mutation operators. The purge itself is verified in
    `test_repair_purges_and_rebuilds_in_a_fresh_process`, where it can do no
    damage; here the assertion is stronger and narrower -- the repair must
    REQUEST the purge.
    """
    import CORE.worm_registry as wr

    purges = []
    # `*a` because the purge is now told WHICH modules host worms: the caller
    # captures the registry's module set before clearing it, so the purge can
    # spare the helpers. The seam this test defends is unchanged -- the repair
    # must REQUEST a re-import from source.
    monkeypatch.setattr(wr, "_purge_worm_modules",
                        lambda *a, **k: purges.append(1))
    _fresh_registry()
    with _registry_lock:
        WORM_REGISTRY["_GhostWorm"] = {
            "class": object,
            "module": "no_such_module_xyz",
            "category": "utility",
            "priority": 5,
            "cooldown_seconds": 30,
            "source_file": "",
        }
    try:
        before = validate_registry_importable()
        assert before["valid"] is False  # pre-condition
        result = repair_registry()
        assert result["attempt"] is True
        assert "repaired_at" in result
        # Report shape: still_broken is a list, total present, no raise.
        assert isinstance(result["still_broken"], list)
        assert isinstance(result["total"], int)
        assert isinstance(result["checked"], int)
        assert purges == [1], "the repair must re-import from source"
    finally:
        _clear_registry_contents()


def test_repair_purges_and_rebuilds_in_a_fresh_process():
    """The destructive half of `repair_registry()`, verified where it is safe.

    In its own interpreter, because the purge is the one operation in this
    module that cannot be undone inside a shared one. Asserts the rebuild is
    FAITHFUL (not merely non-empty and not a shrunken `dir()`-fallback registry)
    and that the phantom entry is gone rather than tolerated.
    """
    import os
    import subprocess

    code = (
        "from CORE import worm_registry as wr;"
        "wr.discover_worms();"
        "full=sum(len(v) for v in wr.WORM_REGISTRY.values());"
        "wr.WORM_REGISTRY['_GhostWorm']={'class':object,"
        "'module':'no_such_module_xyz','category':'utility','priority':5,"
        "'cooldown_seconds':30,'source_file':''};"
        "r=wr.repair_registry();"
        "print('REPAIR', full, r['attempt'], r.get('repaired'),"
        "'_GhostWorm' in wr.WORM_REGISTRY,"
        "sum(len(v) for v in wr.WORM_REGISTRY.values()))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    proc = subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    line = next((ln for ln in proc.stdout.splitlines()
                 if ln.startswith("REPAIR")), None)
    assert line, proc.stdout[-400:]
    _full, attempt, repaired, ghost_present, after = line.split()[1:]
    assert attempt == "True"
    assert int(repaired) >= 1
    assert ghost_present == "False", "the phantom entry must be gone"
    assert int(after) > 100, \
        "a purge that rebuilds a shrunken registry is a regression, not a repair"


# ── startup_warm wiring ─────────────────────────────────────────────

def test_run_registry_validation_stores_result():
    """_run_registry_validation() populates the module-level result."""
    import CORE.startup_warm as sw

    # `force=True` because a validation is now deduped to once per process: the
    # unforced call is a warm-up that may return a stored result, and this test's
    # intent is explicitly "force a re-run against the live registry".
    sw._run_registry_validation(force=True)
    result = sw.get_registry_validation()
    assert result is not None
    assert "valid" in result
    assert "total" in result
    assert "broken" in result


def test_cache_status_surfaces_validation():
    """cache_status() includes registry_valid + registry_validation keys."""
    import CORE.startup_warm as sw

    sw._run_registry_validation(force=True)
    status = sw.cache_status()
    assert "registry_valid" in status
    assert "registry_validation" in status


# ── per-category importability breakdown ────────────────────────────

def test_breakdown_healthy():
    """Healthy registry → each category has checked == total, broken == 0."""
    import CORE.startup_warm as sw

    _fresh_registry()  # resets registry and registers _ProbeWormA (diagnostic) + _ProbeWormB (monitor)
    try:
        validation = validate_registry_importable()
        bd = sw.registry_importability_by_category(validation)
        assert "diagnostic" in bd
        assert "monitor" in bd
        for cat, row in bd.items():
            assert row["total"] == row["checked"]
            assert row["broken"] == 0
        # roll-up matches the validation totals
        assert sum(r["total"] for r in bd.values()) == validation["total"]
    finally:
        _clear_registry_contents()


def test_breakdown_counts_broken_by_category():
    """A broken worm is attributed to its category with broken >= 1."""
    import CORE.startup_warm as sw

    _fresh_registry()
    with _registry_lock:
        WORM_REGISTRY["_GhostWorm"] = {
            "class": object,
            "module": "no_such_module_xyz",
            "category": "diagnostic",
            "priority": 5,
            "cooldown_seconds": 30,
            "source_file": "",
        }
    try:
        validation = validate_registry_importable()
        bd = sw.registry_importability_by_category(validation)
        assert bd["diagnostic"]["broken"] >= 1
        assert bd["diagnostic"]["checked"] == bd["diagnostic"]["total"] - 1
        # total broken across categories == validation broken count
        assert sum(r["broken"] for r in bd.values()) == len(validation["broken"])
    finally:
        _clear_registry_contents()


def test_breakdown_empty_registry():
    """Empty/unavailable registry → empty-safe {} (no raise)."""
    import CORE.startup_warm as sw

    _clear_registry_contents()
    try:
        assert sw.registry_importability_by_category(None) == {}
        assert sw.registry_importability_by_category({}) == {}
    finally:
        _clear_registry_contents()


def test_cache_status_includes_breakdown():
    """cache_status() exposes registry_by_category even on an empty registry."""
    import CORE.startup_warm as sw

    _clear_registry_contents()
    try:
        status = sw.cache_status()
        assert "registry_by_category" in status
        assert status["registry_by_category"] == {}
    finally:
        _clear_registry_contents()


# ── registry state save / restore (2026-09-21) ───────────────────────
#
# These exist because a test file that needs a small registry must be able to
# hand the process back a FULL one. A purge-based rebuild breaks class
# identity and a bare dict clear cannot repopulate at all, so the properties
# asserted here are contents, identity, and an honest discovery flag.


def test_reset_registry_without_purge_clears_and_rearms_discovery():
    """purge_modules=False empties the registry AND re-arms discovery.

    Re-arming is exactly what a bare `WORM_REGISTRY.clear()` misses: with
    `_discovery_done` left True, discover_worms() short-circuits and returns
    0, so the registry can never be repopulated.
    """
    import CORE.worm_registry as wr

    _fresh_registry()
    wr._discovery_done = True          # simulate a completed pass
    try:
        wr.reset_registry(purge_modules=False)
        assert WORM_REGISTRY == {}
        assert wr.discovery_completed() is False
    finally:
        _clear_registry_contents()


def test_restore_registry_round_trips_contents_and_identity():
    """A restore brings every entry back with the SAME class objects."""
    import CORE.worm_registry as wr

    _fresh_registry()
    before = dict(WORM_REGISTRY)
    snapshot = wr.snapshot_registry()
    try:
        _clear_registry_contents()
        assert WORM_REGISTRY == {}
        n = wr.restore_registry(snapshot)
        assert n == len(before)
        assert set(WORM_REGISTRY) == set(before)
        for name, entry in before.items():
            assert WORM_REGISTRY[name]["class"] is entry["class"]
            assert WORM_REGISTRY[name]["category"] == entry["category"]
            assert WORM_REGISTRY[name]["priority"] == entry["priority"]
    finally:
        _clear_registry_contents()


def test_restore_registry_is_isolated_from_later_entry_mutation():
    """Mutating a live entry must not reach into the snapshot."""
    import CORE.worm_registry as wr

    _fresh_registry()
    with _registry_lock:
        WORM_REGISTRY["_ProbeWormA"]["tags"] = {"original"}
    snapshot = wr.snapshot_registry()
    try:
        with _registry_lock:
            WORM_REGISTRY["_ProbeWormA"]["tags"].add("mutated")
            WORM_REGISTRY["_ProbeWormA"]["priority"] = 1
        wr.restore_registry(snapshot)
        assert WORM_REGISTRY["_ProbeWormA"]["tags"] == {"original"}
        assert WORM_REGISTRY["_ProbeWormA"]["priority"] == 5
    finally:
        _clear_registry_contents()


def test_non_empty_registry_is_not_necessarily_the_fleet():
    """The predicate that would have caught the 9-failure restore.

    Before the first discovery pass the registry can be NON-EMPTY and still
    not be the fleet -- here it holds only this module's two probe worms. A
    save/restore that trusts contents alone passes that state on to the next
    module, which then has no way to rebuild it.
    """
    import CORE.worm_registry as wr

    _fresh_registry()
    wr._discovery_done = False
    try:
        snap = wr.snapshot_registry()
        assert len(snap) == 2
        assert wr.discovery_completed() is False
    finally:
        _clear_registry_contents()


@pytest.fixture(scope="module", autouse=True)
def _restore_full_registry():
    """Put the WHOLE registry back when this module is done.

    Every test above runs against a two-worm probe registry (or an empty one).
    A later module that expects the production fleet -- `tests/test_wiring_cycle.py`
    builds its eligibility list from one -- must not inherit that, so the real
    contents are snapshotted on the way in and restored on the way out.

    Contents, not modules: `reset_registry()` would purge `CORE.WORMS.*` /
    `expansion.*` from sys.modules so later imports are different class objects,
    and a no-purge re-discovery cannot rebuild `expansion.*` at all (its
    decorators stay cached). MEASURED 2026-09-21, both ways: the purge-based
    restore cost `tests/test_wiring_cycle.py` 7 failures + 2 errors, and the
    re-discovery restore left `tests/test_spine_mesh.py` with an EMPTY fleet
    (9 failures, 62/62 alone). `restore_registry()` is neither: same objects,
    full contents.
    """
    import CORE.worm_registry as wr

    try:
        # Save BEFORE anything is cleared, but only when the process has a
        # FLEET to save. `_discovery_done` is the predicate, not "is the
        # registry empty": MEASURED 2026-09-21 with this file first, the
        # registry at setup held exactly the two probe worms this module
        # registers at import time and `_discovery_done` was False — so an
        # emptiness test snapshotted 2 worms as the fleet and the next module
        # saw a 2-worm registry (9 failures).
        if wr.discovery_completed():
            snapshot = wr.snapshot_registry()
        else:
            # No fleet to inherit yet: build the real one. The module purge is
            # acceptable ONLY here, because nothing has imported the worm
            # modules yet, so there is no pre-existing class reference to
            # invalidate.
            wr.reset_registry()
            wr.discover_worms()
            snapshot = wr.snapshot_registry()
    except Exception as e:  # noqa: BLE001 — surfaced, not swallowed
        print("test_registry_importable: snapshot failed: %r" % (e,), flush=True)
        snapshot = None
    yield
    if not snapshot:
        print("test_registry_importable: NO snapshot — registry left as-is",
              flush=True)
        return
    n = wr.restore_registry(snapshot)
    if n == 0:
        print("test_registry_importable: registry restore produced NOTHING",
              flush=True)
    if n != len(snapshot):
        print("test_registry_importable: restore mismatch %d/%d" % (n, len(snapshot)),
              flush=True)


# ── the purge's two-sided contract (2026-09-22) ────────────────────
#
# `_purge_worm_modules` has to satisfy two constraints that pull in opposite
# directions, and the fix for one is the regression risk of the other:
#
#   * purge the WORM HOSTS, because a cached module cannot re-fire its
#     `@register_worm` decorators, and a rebuild without them is a shrunken
#     `dir()`-fallback fleet;
#   * KEEP the helpers, because deleting a helper replaces its module object,
#     and every holder then has a different `wiring_genome` than the next
#     importer (`wg0 is wg1 -> False`) — which silently disarms every
#     `monkeypatch`/`mock.patch` that names it.
#
# Both halves are asserted here, because a test that pinned only one of them
# would have accepted the code before the fix.

def test_a_purge_reset_keeps_helper_module_identity():
    """A full reset must NOT replace a module that hosts no worm.

    `wiring_genome` lives under `CORE.WORMS.expansion/` and is therefore inside
    the package this purge walks — it is excluded because nothing in it
    registers a worm, not because it is outside the tree.
    """
    import importlib

    import CORE.worm_registry as wr

    helper = importlib.import_module("CORE.WORMS.expansion.wiring_genome")
    host = importlib.import_module("CORE.WORMS.expansion.spine")
    try:
        wr.reset_registry()
        helper_after = importlib.import_module(
            "CORE.WORMS.expansion.wiring_genome")
        host_after = importlib.import_module("CORE.WORMS.expansion.spine")
        assert helper_after is helper, (
            "a helper module was purged: every holder now has a different "
            "object than the next importer, which disarms mock.patch targets")
        assert host_after is not host, (
            "a worm host was NOT purged: its @register_worm decorators cannot "
            "re-fire, so the rebuild will be a shrunken dir()-fallback fleet")
    finally:
        _clear_registry_contents()


def test_the_purge_still_rebuilds_a_full_fleet_including_expansion():
    """The narrowing must not have bought identity by losing the fleet.

    `expansion.*` is the documented casualty of the lossy path: when the
    expansion modules stay cached, SpineCycleWorm / MeshArchitectWorm /
    GossipSyncWorm are absent from the rebuilt registry (measured 2026-09-21).
    So the assertion is not just "non-empty" — it names the group that
    disappears when the purge is made too narrow.
    """
    import CORE.worm_registry as wr

    def rebuild():
        """One purge + discovery from an empty registry, returning the fleet."""
        wr.reset_registry()
        assert wr.WORM_REGISTRY == {}
        n = wr.discover_worms()
        fleet = dict(wr.WORM_REGISTRY)
        # `n` is how many worms THIS PASS imported, not how many exist: measured
        # 47 for a registry of 232, because the bare-name and package-qualified
        # import paths register the same classes. Pinned so a reader does not
        # read 47 as "the fleet shrank to 47".
        assert 0 < n <= len(fleet), (
            "discover_worms() returned %d for a registry of %d" % (n, len(fleet)))
        return fleet

    try:
        # Compared purge-to-purge rather than against whatever the earlier tests
        # left behind: a `before` collected from a shared registry carries their
        # probe worms, so a set difference would measure the file's own history
        # instead of the purge. Two identical purges answering with the same
        # fleet is the actual contract.
        first = rebuild()
        second = rebuild()
        assert set(first) == set(second), (
            "the purge is not reproducible: %d name(s) lost, %d gained"
            % (len(set(first) - set(second)), len(set(second) - set(first))))
        assert len(first) > 100, (
            "the rebuild is shrunken (%d): the purge is too narrow and "
            "re-discovery is serving cached no-op modules" % len(first))
        expansion = [name for name, entry in first.items()
                     if str(entry.get("module") or "").startswith(
                         "CORE.WORMS.expansion.")]
        assert expansion, (
            "the rebuild lost every expansion worm -- the documented casualty "
            "of a purge that leaves worm hosts cached")
    finally:
        _clear_registry_contents()


# ── standalone runner (pytest segfaults on 3.14t — Error #48) ───────

if __name__ == "__main__":
    failures = 0
    tests = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            tests += 1
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{tests - failures}/{tests} tests passed")
    sys.exit(1 if failures > 0 else 0)
