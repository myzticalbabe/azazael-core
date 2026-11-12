# coding: utf-8
"""


__all__ = [
    "cache_status",
    "ensure_registry_warm",
    "get_registry_validation",
    "registry_importability_by_category",
    "restore_spine_state",
    "get_spine_restore_status",
    "warm_caches_background",
    "warm_caches_blocking",
    "warm_caches_now",
    "warm_caches_ready",
]

startup_warm.py — Pre-warm caches and lazy init at system startup.

Prevents the 38-45s discover_worms() cold start from blocking session
recording or other time-sensitive operations (Error #57 permanent fix).

The worm registry cache is warmed in a background daemon thread so that
the first session recording call finds a ready cache in <0.1s.

Usage:
    # At the top of your startup script or entry point:
    from CORE.startup_warm import warm_caches_background

    # Non-blocking: returns immediately, cache warms in background
    warm_caches_background()

    # Or block until ready (for startup where you have time):
    # from CORE.startup_warm import warm_caches_blocking
    # warm_caches_blocking(timeout=60)

Created: June 11, 2026 — Error #57 permanent fix
"""

import sys
import os
import time
import logging
import threading
from pathlib import Path
from typing import Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_log = logging.getLogger("startup_warm")


# ── Lazy cache warm (background thread, non-blocking) ──────────────

_warmed = threading.Event()
_warm_thread: (threading.Thread | None) = None
_warm_lock = threading.Lock()  # Guard for thread creation race
_last_warm_time: (float | None) = None  # time.monotonic() when warm completed

# Registry importability validation result (filled once after warm).
_registry_validation: (dict | None) = None
_validation_lock = threading.Lock()
#: Both events are load-bearing. A validation must run AT MOST ONCE per process
#: (`_validation_done`), and must never run on a caller that promised not to
#: block (`_validation_started`). See `_ensure_registry_validation_async`.
_validation_started = threading.Event()
_validation_done = threading.Event()
_validation_start_lock = threading.Lock()
_validation_run_lock = threading.Lock()


def _ensure_registry_validation_async() -> bool:
    """Validate the registry ONCE, off the caller's thread.

    THE DEFECT THIS FIXES, measured 2026-09-21 under pytest: a warm-up
    `DirectMemory()` returned in 71 ms and the very next one blocked for 1837 ms,
    because between the two calls the background warm thread finished discovery
    and populated `WORM_REGISTRY`. `ensure_registry_warm()` is documented as
    "non-blocking if already warm" and its first branch called
    `_run_registry_validation()` INLINE whenever the registry was populated, so
    whether `__init__` met its <100 ms contract depended on a race with another
    thread. A caller that has promised not to block must not run an 1.8 s
    validation merely because a different thread happened to finish first.

    Returns True when this call launched the validation.
    """
    if _validation_started.is_set() or _validation_done.is_set():
        return False
    with _validation_start_lock:
        if _validation_started.is_set() or _validation_done.is_set():
            return False
        _validation_started.set()
        threading.Thread(target=_run_registry_validation,
                         name="registry-validation", daemon=True).start()
        return True


def _run_registry_validation(force: bool = False):
    """Validate all registered worms are importable, auto-repairing failures.

    Runs after registry warm so that every module is already imported in this
    process (imports resolve via sys.modules — no cold-start cost). If broken
    entries are found (typically a stale pickle cache after module renames),
    re-runs discover_worms() to rebuild from current source, then re-validates.
    Always non-fatal: startup must not block on a broken worm.

    Stored result keys:
        repair_attempted: bool       — whether a repair was attempted
        repaired: int                — broken entries fixed by re-discovery
        repair_still_broken: list    — broken entries that remain after repair

    Idempotent by default: a second concurrent or later call returns the stored
    result instead of walking 232 entries again. `force=True` re-runs it, which
    only `warm_caches_now()` (an explicit operator action) does. Previously every
    call re-validated, so a hot path calling this once per `DirectMemory()` paid
    the full cost every time and the cost was indistinguishable from slowness.
    """
    global _registry_validation
    if not force:
        if _validation_done.is_set():
            return _registry_validation
        if not _validation_run_lock.acquire(blocking=False):
            # Another thread is mid-validation. Returning the current (possibly
            # absent) result is correct: this call is a warm-up, not a query.
            return _registry_validation
    else:
        _validation_run_lock.acquire()
    _validation_started.set()
    try:
        from CORE.worm_registry import repair_registry, validate_registry_importable
        try:
            result = validate_registry_importable()
        except Exception as exc:
            note_failure("startup_warm:L131", exc)
            result = {"valid": True, "total": 0, "checked": 0, "broken": []}

        if result.get("broken"):
            _log.warning(
                "Registry validation: %d/%d worms not importable; attempting repair",
                len(result.get("broken") or []), result.get("total", 0),
            )
            try:
                repair = repair_registry()
                result["repair_attempted"] = repair.get("attempt", False)
                result["repaired"] = repair.get("repaired", 0)
                result["repair_still_broken"] = repair.get("still_broken") or []
                if repair.get("valid"):
                    result["valid"] = True
                    result["broken"] = []
                    for k in ("total", "checked"):
                        if k in repair:
                            result[k] = repair[k]
                    _log.info("...re-discovery repaired %d broken registry entries",
                              repair.get("repaired", 0))
                else:
                    _log.warning(
                        "...%d registry entries still not importable after repair",
                        len(repair.get("still_broken") or []),
                    )
            except Exception as rexc:
                _log.warning("Registry auto-repair failed (non-fatal): %s", rexc)

        with _validation_lock:
            _registry_validation = result
        _validation_done.set()
    except Exception as exc:
        _log.warning("Registry validation failed (non-fatal): %s", exc)
    finally:
        _validation_run_lock.release()


def get_registry_validation() -> (dict | None):
    """Return the last registry importability validation result (or None)."""
    with _validation_lock:
        return _registry_validation


_spine_restored: (dict | None) = None
_spine_restore_lock = threading.Lock()


def restore_spine_state() -> dict:
    """Restore the neural-mesh spine state saved by a previous session.

    Loads ~/.azazael/spine_state.json (spine map, homeostat ledger,
    Q-weights) into CORE.spine_registry via merge-on-load semantics, so
    learned worm health and topology survive restarts. Called from the
    warm worker AFTER registry discovery (worm classes must exist before
    consumers read the map). Always non-fatal.

    Returns the load_spine_state() result dict, or {"restored": False,
    "error": ...} on failure. Idempotent: safe to call repeatedly.
    """
    global _spine_restored
    try:
        from CORE.spine_registry import load_spine_state
        res = load_spine_state()
        with _spine_restore_lock:
            _spine_restored = res
        if res.get("restored"):
            _log.info(
                "Spine state restored: %s spine nodes, %s ledger entries, "
                "%s Q-weights",
                res.get("spine_nodes", 0), res.get("homeostat_worms", 0),
                res.get("q_weights", 0),
            )
        else:
            _log.debug("Spine state not restored: %s",
                       res.get("reason") or res.get("error"))
        return res
    except Exception as exc:
        _log.warning("Spine state restore failed (non-fatal): %s", exc)
        with _spine_restore_lock:
            _spine_restored = {"restored": False, "error": str(exc)[:120]}
        return _spine_restored


def get_spine_restore_status() -> (dict | None):
    """Return the last spine-state restore result (or None if not run)."""
    with _spine_restore_lock:
        return _spine_restored


def _warm_worker(on_complete: (Callable | None) = None,
                 force_validation: bool = False):
    """Background worker: pre-warms the worm registry cache.

    Runs discover_worms() which checks the pickle cache first.
    - If cache is fresh: returns in <0.1s, no-op.
    - If cache is stale/missing: runs full discovery (38-45s) and saves cache.
    """
    try:
        # Ensure src directory is on sys.path
        src_dir = str(Path(__file__).resolve().parent.parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        # Import and run registry discovery (uses cache internally)
        from CORE.worm_registry import discover_worms

        start = time.time()
        count = discover_worms()
        elapsed = time.time() - start

        if elapsed > 1.0:
            _log.info(
                "Cache warm completed in %.1fs (discovered %d worms)",
                elapsed, count,
            )
        else:
            _log.debug("Cache warm: ready in %.2fs (cache hit)", elapsed)

    except Exception as exc:
        _log.warning("Cache warm failed (non-fatal): %s", exc)
    finally:
        global _last_warm_time
        _last_warm_time = time.monotonic()
        _warmed.set()
        # `force_validation` is what keeps `warm_caches_now()`'s "ALWAYS runs a
        # fresh warm" contract true: a validation deduped to once-per-process is
        # reused by the background path, but an explicit re-warm must re-validate
        # against whatever discovery just produced.
        _run_registry_validation(force=force_validation)
        # Restore the spine (learned Q-weights, topology, feedback ledger)
        # only after the registry is warm — consumers assume worm classes
        # exist. Non-fatal by design.
        restore_spine_state()
        # Then start the autonomic heart: the self-improvement cycle
        # now beats on its own (map → homeostat → queen → persist →
        # gossip). Daemon thread; non-fatal.
        try:
            start_spine_heart()
        except Exception as exc:
            _log.warning("Spine heart start failed (non-fatal): %s", exc)
        # Prime the wiring cycle's discovery/credit caches so the first
        # user-facing run_cycle skips the ~3s cold load (measured 2026-10-01:
        # repeat cycles run 0.01s). Loaded by path — scripts/ is not a
        # package — and fully guarded: absence or failure just means the
        # first cycle pays cold cost, exactly as before.
        try:
            import importlib.util as _ilu
            _wiring_path = (Path(__file__).resolve().parent.parent.parent
                            / "scripts" / "wiring_cycle.py")
            if _wiring_path.is_file():
                _spec = _ilu.spec_from_file_location(
                    "wiring_cycle_warm", str(_wiring_path))
                if _spec is not None and _spec.loader is not None:
                    _wmod = _ilu.module_from_spec(_spec)
                    _spec.loader.exec_module(_wmod)
                _prime = getattr(_wmod, "prime_caches", None)
                if callable(_prime):
                    _prime()
        except Exception as exc:
            _log.warning("Wiring prime failed (non-fatal): %s", exc)
        if on_complete:
            try:
                on_complete()
            except Exception as exc:
                note_failure("startup_warm:L294", exc) # scanner:ignore


def warm_caches_background(on_complete: (Callable | None) = None) -> threading.Thread:
    """Start cache warming in a background daemon thread. Non-blocking.

    Thread-safe: uses a lock to prevent duplicate warm threads.
    Safe to call multiple times — only one warm thread is started.
    Call warm_caches_ready() later to check if warming completed.

    Args:
        on_complete: Optional callback invoked when warming finishes
            (called from the background thread).

    Returns:
        The background threading.Thread instance.
    """
    global _warm_thread

    # Return type is Thread; the module global may be None pre-warm, so
    # narrow with a local (mypy: Optional is not Thread).
    t = _warm_thread
    if _warmed.is_set():
        _log.debug("Caches already warm, skipping background warm")
        return t  # type: ignore[return-value]

    with _warm_lock:
        if _warmed.is_set():
            return _warm_thread  # type: ignore[return-value]
        if _warm_thread is not None and _warm_thread.is_alive():
            _log.debug("Cache warm already in progress")
            return _warm_thread

        _warm_thread = threading.Thread(
            target=_warm_worker,
            args=(on_complete,),
            name="cache-warm",
            daemon=True,
        )
        _warm_thread.start()
        _log.debug("Cache warm started in background thread")
        return _warm_thread


def warm_caches_blocking(timeout: float = 60.0) -> bool:
    """Warm caches in foreground, blocking until ready or timeout.

    Args:
        timeout: Max seconds to wait for warm completion. Default 60.

    Returns:
        True if caches are ready, False if timeout or failure.
    """
    global _warm_thread

    if _warmed.is_set():
        return True

    if _warm_thread is None or not _warm_thread.is_alive():
        # No thread running — start one synchronously
        _warm_worker()
        return _warmed.is_set()

    # Wait for existing thread
    return _warmed.wait(timeout=timeout)


def warm_caches_ready() -> bool:
    """Check if cache warming has completed.

    Returns:
        True if warming finished (or was already done).
    """
    return _warmed.is_set()


def warm_caches_now() -> bool:
    """Force immediate synchronous cache warm. Blocks until done.

    Unlike warm_caches_blocking(), this ALWAYS runs a fresh warm
    regardless of previous thread state. Useful after cache invalidation.

    Returns:
        True if warming succeeded.
    """
    global _warmed, _warm_thread

    _warmed.clear()
    _warm_worker(force_validation=True)
    return _warmed.is_set()


# ── SPINE HEARTBEAT (autonomic heart) ────────────────────────────
# The self-improvement cycle must run WITHOUT a caller: map → homeostat
# (apply) → red queen → persist → gossip, every SPINE_BEAT_INTERVAL s,
# on a daemon thread, started automatically after the cache warm. A
# heartbeat that needs an operator is not a heart. Idempotent: one
# thread per process; non-fatal on every error.

_SPINE_HEART_ALIVE = threading.Event()
_spine_heart_thread: (threading.Thread | None) = None
SPINE_BEAT_INTERVAL = 600.0  # seconds between self-improvement beats


def _spine_heart_worker(interval: float = SPINE_BEAT_INTERVAL):
    """Beat forever: run one SpineCycleWorm cycle per interval. Daemon."""
    try:
        from CORE.WORMS.expansion.spine import SpineCycleWorm
        cycle = SpineCycleWorm(min_interval_seconds=max(30.0, interval * 0.5))
    except Exception as exc:
        _log.warning("Spine heart could not start (non-fatal): %s", exc)
        return
    while _SPINE_HEART_ALIVE.is_set():
        try:
            # degrade_to_observe: the heart is a CONTINUOUS caller, so a refused
            # write must not end the beat. Without it, a stale map meant the
            # heartbeat did nothing at all - no outcomes, no refreshed maps, no
            # way to clear the gate that refused it - and the refusal became
            # self-perpetuating. Observation is explicitly always allowed.
            rep = cycle.run_cycle(apply=True, degrade_to_observe=True)
            if rep.get("success"):
                _log.debug("spine beat ok: proposals=%s peers=%s",
                           len(rep.get("homeostat", {}).get("proposals") or []),
                           len(rep.get("gossip", {}).get("peers") or []))
            else:
                # A refused beat used to be logged NOWHERE - the line above is
                # success-only - so a heartbeat that could never run looked
                # exactly like one that was never scheduled. Measured
                # 2026-09-21: the stage-0 proprioception gate refused every beat
                # because four NEURAL_MESH artifacts were stale, silently.
                _log.warning(
                    "spine beat did not run: skipped=%s reason=%s",
                    rep.get("skipped"), str(rep.get("reason"))[:200])
        except Exception as exc:
            _log.warning("spine beat failed (non-fatal): %s", exc)
        # sleep in small slices so stop requests are honored promptly
        slept = 0.0
        while _SPINE_HEART_ALIVE.is_set() and slept < interval:
            time.sleep(min(5.0, interval - slept))
            slept += 5.0


def start_spine_heart(interval: float = SPINE_BEAT_INTERVAL) -> bool:
    """Start the autonomous self-improvement heartbeat. Idempotent."""
    global _spine_heart_thread
    if os.environ.get("AZAZAEL_NO_HEART") == "1":
        # Test pen (see root conftest.py): a beating heart runs full worm
        # cycles on a daemon thread; under a test suite that races torch
        # construction on worker threads and AVs on 3.15 (measured).
        # Production never sets this; tests that NEED a live heart delete it.
        return False
    if _SPINE_HEART_ALIVE.is_set():
        return True
    _SPINE_HEART_ALIVE.set()
    _spine_heart_thread = threading.Thread(
        target=_spine_heart_worker, args=(interval,),
        name="spine-heart", daemon=True)
    _spine_heart_thread.start()
    _log.info("Spine heart started (interval=%.0fs)", interval)
    return True


def stop_spine_heart(timeout: float = 30.0) -> bool:
    """Stop the heartbeat (tests / explicit operator action).

    Clears the stop flag AND WAITS for an in-flight beat to finish.

    Clearing the flag alone used to be the entire implementation, so this
    returned while a beat could still be inside `run_cycle()`. Measured
    2026-09-21: that beat holds the process-global cycle gate, so the next
    same-process caller -- a test, the neural loop, a CLI -- was refused with
    `skipped: cycle_already_running_global`, by a heartbeat it had just been
    told was stopped. `spine_heart_status()["beating"] is False` was true of the
    FLAG and false of the PROCESS, which is the same defect as reporting a
    completed cycle that never ran. Joining is what makes the claim true.

    The worker checks the flag after each beat, so it exits within one 5s sleep
    slice; the timeout only has to cover a beat already in flight.
    """
    global _spine_heart_thread
    if not _SPINE_HEART_ALIVE.is_set():
        return False
    _SPINE_HEART_ALIVE.clear()
    thread = _spine_heart_thread
    if (thread is not None and thread.is_alive()
            and thread is not threading.current_thread()):
        thread.join(timeout=timeout)
        if thread.is_alive():
            # Honest rather than silent: the flag is cleared, but a beat is
            # still running, so a same-process `run_cycle()` may still be
            # refused. Saying so beats letting the caller discover it.
            _log.warning(
                "spine heart stop timed out after %.0fs; a beat is still "
                "in flight and may refuse a same-process run_cycle()", timeout)
    if not (thread is not None and thread.is_alive()):
        _spine_heart_thread = None
    return True


def spine_heart_status() -> dict:
    """Heart introspection for doctor/health tooling."""
    t = _spine_heart_thread
    return {"beating": _SPINE_HEART_ALIVE.is_set(),
            "thread_alive": bool(t and t.is_alive()),
            "interval_s": SPINE_BEAT_INTERVAL}


# ── DirectMemory short-circuit guard ────────────────────────────────

# DirectMemory's session recording should NOT trigger discover_worms().
# This flag lets DirectMemory check if the registry is ready without
# blocking on a cold start. DirectMemory should import this module and
# check warm_caches_ready() before attempting registry-dependent ops.

def cache_status() -> dict:
    """Report the current state of the cache warm system.

    Returns a dict with:
        - ready (bool): Whether warming has completed.
        - registry_size (int): Number of worms in WORM_REGISTRY (0 if not warm).
        - last_warm_time_ago (float or None): Seconds since last warm completed,
          or None if never warmed.
        - thread_alive (bool): Whether the warm thread is currently running.

    This is a diagnostic/convenience function. No blocking — returns
    immediately with whatever state is currently available.
    """
    ready = _warmed.is_set()
    registry_size = 0
    last_warm_ago = None

    if ready:
        try:
            from CORE.worm_registry import WORM_REGISTRY
            # 2026-09-29: count worms, not info fields. sum(len(v)) summed
            # each entry's field count (5-8x the worm count).
            registry_size = len(WORM_REGISTRY) if WORM_REGISTRY else 0
        except (ImportError, AttributeError) as exc:
            note_failure("startup_warm:L532", exc) # scanner:ignore

    if _last_warm_time is not None:
        last_warm_ago = time.monotonic() - _last_warm_time

    thread_alive = _warm_thread is not None and _warm_thread.is_alive()

    validation = get_registry_validation()
    by_category = registry_importability_by_category(validation)

    return {
        "ready": ready,
        "registry_size": registry_size,
        "last_warm_time_ago": last_warm_ago,
        "thread_alive": thread_alive,
        "registry_valid": bool(validation and validation.get("valid")),
        "registry_validation": validation,
        "registry_by_category": by_category,
    }


def registry_importability_by_category(
    validation: (dict | None) = None,
) -> dict:
    """Per-category importability breakdown of registered worms.

    Groups WORM_REGISTRY entries by category and reports, for each category:
        - total: number of registered worms in this category
        - checked: number confirmed importable
        - broken: number that failed import validation
    Broken worms are matched by name so a stale registry entry with a removed
    source still lands in the right category. Empty-safe: returns {} when the
    registry or categories are unavailable.

    Args:
        validation: Optional result from validate_registry_importable()
            (or the cached registry_validation). If None, derives broken names
            by running a fresh importability check lazily.

    Returns:
        dict mapping lowercase category -> {total, checked, broken}.
    """
    try:
        from CORE.worm_registry import WORM_REGISTRY, validate_registry_importable
    except (ImportError, AttributeError) as exc:
        note_failure("startup_warm:L577", exc)
        return {}

    if not WORM_REGISTRY:
        return {}

    # Name -> category lookup from the registry itself.
    name_to_cat: dict = {}
    per_cat_total: dict = {}
    for name, info in list(WORM_REGISTRY.items()):
        cat = str(info.get("category", "utility")).lower()
        name_to_cat[name] = cat
        per_cat_total[cat] = per_cat_total.get(cat, 0) + 1

    # Broken names: prefer the provided validation report, else compute it.
    broken_names = set()
    if validation and isinstance(validation.get("broken"), list):
        broken_names = {b.get("name") for b in validation["broken"] if b.get("name")}
    else:
        try:
            res = validate_registry_importable()
            broken_names = {b.get("name") for b in (res.get("broken") or []) if b.get("name")}
        except Exception as exc:
            note_failure("startup_warm:L599", exc)
            broken_names = set()

    breakdown: dict = {}
    for cat, total in sorted(per_cat_total.items()):
        broken_in_cat = sum(1 for name, c in name_to_cat.items() if c == cat and name in broken_names)
        breakdown[cat] = {
            "total": total,
            "broken": broken_in_cat,
            "checked": total - broken_in_cat,
        }
    return breakdown


def ensure_registry_warm() -> bool:
    """Ensure the worm registry is loaded (non-blocking if already warm).

    Safe to call before any session recording. If the cache isn't warm
    yet, starts background warming and returns False (caller should
    proceed without registry-dependent features).

    Returns:
        True if registry is available now, False if warming in background.
    """
    # Try quick import — if already imported, registry is warm
    try:
        from CORE.worm_registry import WORM_REGISTRY
        if WORM_REGISTRY:
            _warmed.set()
            # NOT inline: this branch is the one an already-warm process takes on
            # every `DirectMemory()`, and it used to run the full validation
            # synchronously here (1837 ms measured) while the function's own
            # docstring promised it was non-blocking.
            _ensure_registry_validation_async()
            return True
    except ImportError as exc:
        note_failure("startup_warm:L634", exc) # scanner:ignore

    # Start background warm if not already started
    if not _warmed.is_set():
        warm_caches_background()

    return _warmed.is_set()
