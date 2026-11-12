# coding: utf-8
"""
worm_registry — Centralized Worm Registry with Auto-Discovery & Spawn Diversity

Provides:
- @register_worm decorator for automatic class registration
- Global WORM_REGISTRY dict with full metadata
- Auto-discovery from CORE/WORMS directory
- SpawnDiversityEngine: category rotation, cooldown, weighted selection,
  time-based spawning patterns, and all-registered-worm coverage

Usage:
    >>> from CORE.worm_registry import register_worm, WORM_REGISTRY, SpawnDiversityEngine
    >>> @register_worm(category="diagnostic", priority=7, cooldown_seconds=30)
    ... class NetworkDiag:
    ...     VERSION = "1.0.0"
    ...
    >>> sde = SpawnDiversityEngine()
    >>> next_spawn = sde.pick_next_spawn(fleet_status, history)
"""

import os
import sys
import time
import importlib
import inspect

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Callable, Iterable, Tuple
from dataclasses import dataclass
import threading

from CORE.uuid7 import uuid7

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.PamsRecorder import get_recorder  # singleton accessor (PamsRecorder.py:112-125)
except ImportError as exc:
    # PAMS is optional, but its absence is still a degradation: memory recording
    # is unavailable for this process. Recorded rather than silently defaulted.
    get_recorder = None
    note_failure("worm_registry:optional-pams-absent", exc)

# NOTE: CORE.worm_registry_cache is intentionally NOT imported here at module
# level. worm_registry_cache lazily imports CORE.worm_registry (for
# get_all_categories / WORM_REGISTRY / discover_worms), so a module-level
# import here creates a real import cycle. Both cache functions are only needed
# inside discover_worms(), so they are imported lazily at their call sites via
# _get_cache_funcs() (see below).

# --- Global Registry -----------------------------------------------

# Structured logger with correlation IDs (per ALL 5 inference models' finding)
import logging
_log = logging.getLogger("worm_registry")

# Simple thread-local correlation ID for traceability across operations
# (set dynamically in _get_correlation_id / set_correlation_id — mypy
# rejects attribute annotations on non-self attribute assignment).
_thread_local = threading.local()


def _get_correlation_id() -> str:
    """Get the current thread's correlation ID, generating one if needed.

    Uses the full `uuid7().hex` (32 chars). Slicing to the first 12 hex chars
    would keep only the 48-bit millisecond timestamp — identical across threads
    created within the same millisecond under 3.14t free-threading. The full
    hex includes the monotonic counter and random bits, guaranteeing uniqueness
    per thread/event.
    """
    if not hasattr(_thread_local, "correlation_id") or _thread_local.correlation_id is None:
        _thread_local.correlation_id = uuid7().hex
    return _thread_local.correlation_id


_LOG_RECORD_RESERVED = frozenset({
    'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
    'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
    'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
    'thread', 'threadName', 'process', 'processName',
})


def _log_event(event: str, **context: Any):
    """Log a structured event with correlation ID and context.

    Automatically filters out keys that conflict with Python's
    reserved LogRecord attributes (e.g. 'name', 'msg', 'args')
    to prevent "Attempt to overwrite 'name' in LogRecord" crashes
    when worm module imports fire @register_worm decorators.
    """
    safe_context = {k: v for k, v in context.items()
                    if k not in _LOG_RECORD_RESERVED}
    extra = {"cid": _get_correlation_id(), **safe_context}
    _log.info(f"[registry] {event}", extra=extra)


WORM_REGISTRY: Dict[str, Dict[str, Any]] = {}
"""Global worm type registry.
Key: worm class name (e.g. "NetworkDiag")
Value: {
    "class": the actual class,
    "module": module name,
    "category": str,
    "priority": int (1-10),
    "cooldown_seconds": float,
    "description": str,
    "registered_at": iso timestamp,
    "spawn_count": int,
    "last_spawned": iso timestamp or None,
    "tags": set of tags,
    "source_file": str,
}
"""

_registry_lock = threading.RLock()
_discovery_done = False

# --- VF (Vermillion LightFury) worm discovery — cross-platform Quartet paths ---
_vf_worms_dir = None
_vf_candidates = []
# Env var override for Quartet cross-environment flexibility
_env_vf = os.environ.get('VERMILLION_LIGHTFURY_DIR', '')
if _env_vf:
    _vf_candidates.append(os.path.join(_env_vf, 'worms'))
# Sibling directory: azazael/../vermillion_lightfury/worms
_azazael_root = Path(__file__).resolve().parent.parent.parent
_vf_candidates.append(str(_azazael_root / 'vermillion_lightfury' / 'worms'))
# Platform-specific canonical paths
if os.name == 'nt':
    _vf_candidates.append(str(Path.home() / 'vermillion_lightfury' / 'worms'))
else:
    _vf_candidates.append('/root/vermillion_lightfury/worms')
# Find first existing candidate
for _candidate in _vf_candidates:
    if os.path.isdir(_candidate):
        _vf_worms_dir = _candidate
        break
_vf_already_discovered = False


# --- Decorator ------------------------------------------------------

# -- Valid categories for input validation ---------------------------
_VALID_CATEGORIES = frozenset({
    "monitor", "diagnostic", "analysis", "defense",
    "optimization", "orchestration", "sync", "repair", "utility",
    "learning", "ml", "data", "visualization",
    "container", "communication", "migration",
    "security", "config", "backup",
    "profiling", "telemetry",
    "expansion", "network", "system",
    # Neural mesh fabric (2026-09-13: 7 new mesh classes, 25 worms)
    "mesh", "synapse", "consensus", "engram",
    "federated", "servemesh", "immune",
    # Exotic hive (2026-09-13: xenomorph lifecycle, 7 worms)
    "xenomorph",
    # Neural mesh v2 (2026-09-14: 8 new classes, 32 worms — dendrite/axon signal,
    # glia support, plasticity learning, thalamus relay, consolidation sleep,
    # envmesh 5-way env incl wsl2-ubuntu vs wsl2-kali split + bare passthrough,
    # metabolic budgets)
    "dendrite", "axon", "glia", "plasticity",
    "thalamus", "consolidation", "envmesh", "metabolic",
})

#: Public alias for the vocabulary `register_worm` actually enforces.
#: Added 2026-09-21 because `tests/test_worm_regression.py` kept its own copy of
#: this set, frozen at 17 categories from before the mesh/immune/xenomorph
#: expansions, so the suite failed against a registry that was correct: measured
#: 40 valid categories in the registry and 0 off-vocabulary worms, while the test
#: reported five "invalid category" failures. A duplicated constant can only
#: drift, so consumers read this one.
VALID_CATEGORIES = _VALID_CATEGORIES


# ── FLEET-AUTHORITY INVARIANT (audit G16, 2026-09-14) ────────────────
# RedQueenWorm runs hostile read-only probes against worms in
# RedQueenWorm.PROBE_NATURES == ("mesh", "synapse", "immune") and writes the
# results into the homeostat ledger — the same ledger HomeostatWorm and
# SynapseWeightWorm *regulate*. `SpineCycleWorm` escaped that loop only by
# being registered `orchestration` (outside the probe set): one accidental
# protection, in one place, documented nowhere.
#
# Measured 2026-09-14: FOUR regulators currently sit INSIDE probed natures —
# HomeostatWorm(synapse), SpineMapperWorm(mesh), MeshRouterWorm(mesh),
# AutoencoderAnomalyWorm(immune). `pressure_probe()` takes the first N worms
# of a nature in registration order, so the queen was probing the organs that
# regulate it. The instinct was right; the invariant was missing.
#
# Two declarations make it explicit and enforced at registration:
#
#   PROTECTED_NATURES — natures whose worms are NEVER probed (they host
#                       fleet-regulatory or self-referential logic).
#   FLEET_GOVERNORS   — class name -> the ONE nature it may be registered
#                       under. A governor may keep living in a *functional*
#                       nature (HomeostatWorm really is `synapse`), so the
#                       rule is not "move it out"; it is "it may not be
#                       re-filed". `_guess_category()`'s long keyword chains
#                       are exactly the mechanism that would silently re-file
#                       one, so registration now refuses a mismatch.
#
# Registration auto-tags declared governors `fleet_governor`, and
# RedQueenWorm skips tagged worms regardless of their nature — which is the
# part that actually stops the circular feedback.
PROTECTED_NATURES = frozenset({"orchestration", "xenomorph", "plasticity"})

FLEET_GOVERNORS = {
    # class name            -> sanctioned nature (verified against the live
    #                          registry, 2026-09-14 — not aspirational)
    "SpineCycleWorm":       "orchestration",
    "HomeostatWorm":        "synapse",
    "SpineMapperWorm":      "mesh",
    "MeshRouterWorm":       "mesh",
    "MorphogenesisWorm":    "engram",
    "ConsolidationWorm":    "learning",
    "AutoencoderAnomalyWorm": "immune",
    "RedQueenWorm":         "xenomorph",
    "PlasticityEngineWorm": "plasticity",
    "Lilith":               "xenomorph",
    # F8: a worm that reshapes the fleet graph must never be probed by the
    # fleet's own adversarial loop.
    "MeshArchitectWorm":    "orchestration",
}

FLEET_GOVERNOR_TAG = "fleet_governor"


def get_heuristic_registrations() -> Dict[str, Dict[str, Any]]:
    """Worms whose nature was guessed rather than declared (audit G17).

    These entries have a `category` that no author asserted. Their cooldown and
    nature-cluster membership are heuristic output, so consumers that care
    about scheduling fidelity can treat them as lower-confidence instead of
    trusting them equally with decorated worms.
    """
    return {name: dict(info) for name, info in WORM_REGISTRY.items()
            if isinstance(info, dict)
            and info.get("category_source") == "heuristic"}


def _validate_fleet_authority(name: str, category: str) -> None:
    """Enforce the fleet-authority invariant for a registering class.

    Raises ValueError if a declared fleet governor is registered under any
    nature other than its sanctioned one. Non-governors are unconstrained.
    """
    sanctioned = FLEET_GOVERNORS.get(name)
    if sanctioned is not None and category != sanctioned:
        raise ValueError(
            f"Fleet-authority invariant (G16): {name} is a declared fleet "
            f"governor and must be registered under '{sanctioned}', not "
            f"'{category}'. Governors regulate the ledger that RedQueen "
            f"probes feed, so re-filing one into a probed nature "
            f"reintroduces the feedback loop. Update FLEET_GOVERNORS "
            f"deliberately if this is intentional."
        )
    if category in PROTECTED_NATURES and sanctioned is None:
        # Not an error — PROTECTED_NATURES is about probing, not about which
        # worms may live there. Recorded so the invariant stays auditable.
        return


def is_protected_worm(info: Optional[dict]) -> bool:
    """True if `info` describes a worm the Red Queen must never probe."""
    if not isinstance(info, dict):
        return False
    if info.get("fleet_governor"):
        return True
    if info.get("category") in PROTECTED_NATURES:
        return True
    tags = info.get("tags") or ()
    try:
        return FLEET_GOVERNOR_TAG in tags
    except TypeError as exc:          # noqa: BLE001 - tags may not be a container
        note_failure("worm_registry:fleet-governor-tag-type", exc)
        return False


def _replay_policy_journal() -> int:
    """Re-apply persisted homeostat policy adaptations into WORM_REGISTRY.

    Homeostat apply=True mutates the registry in-process; without replay,
    every fresh process silently reverted to decorator defaults (adaptations
    evaporated on restart — found 2026-09-14). Re-applies latest journaled
    cooldown/priority per worm. Returns number of worms adjusted.
    """
    adjusted = 0
    try:
        from CORE import spine_registry as _spine_reg
        try:
            _spine_reg.load_spine_state()
        except Exception as exc:  # noqa: BLE001 - replay is best-effort
            note_failure("worm_registry:spine-load-state", exc)
        # G10: the journal is a bounded ring, so replaying it directly meant a
        # worm whose last adaptation had been evicted silently reverted to its
        # decorator default — a regression that only shows up once the fleet is
        # busy enough to wrap the ring. The compacting per-worm map is the
        # authoritative state; the ring is only the audit log. Fall back to the
        # ring for state files written before the map existed.
        latest: Any = None
        getter = getattr(_spine_reg, "get_policy_latest", None)
        if callable(getter):
            try:
                latest = getter()
            except Exception as exc:      # noqa: BLE001 - fall back to the ring
                note_failure("worm_registry:policy-latest-getter", exc)
                latest = None
        if not latest:
            _journal = getattr(_spine_reg, "_POLICY_JOURNAL", None) or []
            if not _journal:
                return 0
            # Fold the ring in order so the newest entry per worm wins, which
            # is the same semantics the latest-map already has.
            latest = {}
            for _e in _journal:
                _w = _e.get("worm") if isinstance(_e, dict) else None
                if _w:
                    latest[_w] = _e
        with _registry_lock:
            for entry in latest.values():
                try:
                    name = entry.get("worm", "")
                    changes = entry.get("changes", {}) or {}
                    info = WORM_REGISTRY.get(name)
                    if not info:
                        continue
                    # White team (audit §5.7): replay consults the ratifier.
                    # An entry the governor refuses (retired worm, naming
                    # violation, out-of-range values) is skipped, never
                    # replayed — otherwise a Black-Queen retirement could be
                    # undone by a stale journal. Fail-closed on ratifier
                    # error: an unvalidated instrument admits nothing.
                    try:
                        from CORE.worm_governance import WormGovernor
                        _verdict = WormGovernor().ratify_policy(
                            entry, registry=WORM_REGISTRY)
                    except Exception as exc:
                        note_failure("worm_registry:policy-ratify", exc)
                        continue
                    if not _verdict.get("approved"):
                        continue
                    cd = (changes.get("cooldown_seconds") or {}).get("to")
                    pr = (changes.get("priority") or {}).get("to")
                    touched = False
                    if isinstance(cd, (int, float)) and cd > 0:
                        info["cooldown_seconds"] = float(cd)
                        touched = True
                    if isinstance(pr, int) and 1 <= pr <= 10:
                        info["priority"] = pr
                        touched = True
                    adjusted += 1 if touched else 0
                except Exception as exc:  # noqa: BLE001 - skip the malformed entry
                    note_failure("worm_registry:policy-entry-apply", exc)
                    continue
    except Exception as exc:          # noqa: BLE001 - never break registration
        note_failure("worm_registry:policy-journal-replay", exc)
    return adjusted


def _validate_registry_params(category: str, priority: int, cooldown_seconds: float, tags: Optional[Set[str]]):
    """Validate register_worm() parameters. Raises ValueError on invalid input.

    Added per ALL 5 inference models' production audit finding on input validation.
    """
    if category not in _VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category '{category}'. Must be one of: {sorted(_VALID_CATEGORIES)}"
        )
    if not isinstance(priority, int) or priority < 1 or priority > 10:
        raise ValueError(f"Priority must be int 1-10, got {priority}")
    if not isinstance(cooldown_seconds, (int, float)) or cooldown_seconds <= 0:
        raise ValueError(f"cooldown_seconds must be positive, got {cooldown_seconds}")
    if tags is not None:
        if not isinstance(tags, set):
            raise ValueError(f"tags must be a set, got {type(tags).__name__}")
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                raise ValueError(f"Each tag must be a non-empty string, got {tag!r}")
            if any(c in tag for c in "<>\"'&;`|"):
                raise ValueError(f"Tag '{tag}' contains dangerous characters")


def _ensure_summary_contract(cls: type) -> str:
    """Guarantee every registered class can report its state. Returns provenance.

    2026-09-21: measured 174 of 218 registered classes had NO `get_summary` at
    all — 53 did. There is no single place to add it to the 145 that are plain
    `object` subclasses, so the choice was to redefine which entries the contract
    covers, invent 174 interfaces, or have the registry enforce what it already
    documents. The registry is the only thing that knows what is registered, and
    it already normalises on the way in (`@register_worm` fixes bare module names
    and refuses a governor re-filed into another nature), so it enforces this too.

    The default is `BaseWormMixin.get_summary`, which is SAFE by construction for
    this use: with no `__summary_method__`/`__summary_fields__` declared it takes
    the metadata branch and calls nothing. That matters — `tests/test_worm_smoke.py`
    skips `RLTrainLoop` and `ScheduledTrainingWorm` precisely because calling into
    them is expensive, so a default that invoked `run()` would convert 174 quick
    failures into 174 hangs.

    Returns "declared" when the class (or a base) already implemented it, else
    "default". The distinction is recorded in the registry entry rather than
    discarded, because a fleet where 174 organs report an identical stub is not
    the same fleet as one where they report real state, and the provenance column
    is what keeps that visible after the test goes green.
    """
    try:
        existing = getattr(cls, "get_summary", None)
        if callable(existing):
            return "declared"
    except Exception as exc:          # noqa: BLE001 - absent method is the normal case
        note_failure("worm_registry:summary-declared-probe", exc)
    try:
        # The mixin's implementation reads its two schema attributes, and a class
        # that does not inherit the mixin has neither — attaching only the method
        # produced `AttributeError: 'AmassWorm' object has no attribute
        # '__summary_method__'`. Both are dunders with trailing underscores, so
        # neither name is mangled. Existing values are left alone: a class that
        # already declares a schema keeps it.
        if not hasattr(cls, "__summary_method__"):
            cls.__summary_method__ = ""       # type: ignore[attr-defined]
        if not hasattr(cls, "__summary_fields__"):
            cls.__summary_fields__ = []       # type: ignore[attr-defined]
        cls.get_summary = BaseWormMixin.get_summary  # type: ignore[attr-defined]
        return "default"
    except Exception as exc:
        # Builtin / C-extension types refuse attribute assignment. Nothing to do
        # here, and refusing to register a worm over it would be worse.
        note_failure("worm_registry:summary-attach-refused", exc)
        return "unavailable"


def register_worm(
    category: str = "utility",
    priority: int = 5,
    cooldown_seconds: float = 30.0,
    description: str = "",
    tags: Optional[Set[str]] = None,
):
    """
    Decorator to register a worm class in the global WORM_REGISTRY.

    Args:
        category: One of "monitor", "diagnostic", "analysis", "defense",
                  "optimization", "orchestration", "sync", "repair", "utility"
        priority: Spawn priority 1-10 (10 = highest)
        cooldown_seconds: Minimum seconds between spawns of this type
        description: Human-readable description
        tags: Optional set of tags for filtered selection

    Raises:
        ValueError: If any parameter is invalid (per security hardening).

    Example:
        @register_worm(category="diagnostic", priority=8, cooldown_seconds=60)
        class NetworkDiag:
            VERSION = "1.0.0"
    """
    # -- Input validation (per ALL 5 inference models' finding) --
    _validate_registry_params(category, priority, cooldown_seconds, tags)

    def decorator(cls):
        if not isinstance(cls, type):
            raise TypeError(f"@register_worm requires a class, got {type(cls).__name__}")

        name = cls.__name__
        # G16: refuse to let a declared fleet governor be re-filed into a
        # different nature (the `_guess_category` keyword-chain hazard).
        _validate_fleet_authority(name, category)
        module = cls.__module__
        try:
            source_file = inspect.getfile(cls)
        except (OSError, TypeError, Exception) as exc:
            note_failure("worm_registry:source-file-probe", exc)
            source_file = ""  # exec()-defined classes or REPL classes have no source file

        # Normalize bare module names to their package-qualified import path.
        # When a worm file is imported by its bare filename (e.g. discover_worms()
        # prepends the WORMS dir to sys.path), cls.__module__ is just "CLAWBOT"
        # — which only imports if that directory stays on sys.path. Consumers
        # (health-check worker, execution matrix) import from the src root, so
        # store the path that works there: CORE.WORMS.<name> for files under
        # CORE/WORMS/, CORE.<name> for files directly under CORE/.
        if "." not in module and source_file:
            d = os.path.dirname(source_file)
            if d.endswith("WORMS"):
                module = "CORE.WORMS." + module
            elif d.endswith("CORE"):
                module = "CORE." + module

        with _registry_lock:
            if name in WORM_REGISTRY:
                # Loud, structured warning: registry keys by class NAME, so a
                # second registration silently replaces the first. Happens
                # when two modules define same-named worm classes (found via
                # the GossipSyncWorm collision between expansion/mesh.py and
                # expansion/spine.py). Not an exception — loading continues —
                # but it is visible in logs and the event stream.
                _log_event("worm_registered_overwrite", worm_name=name,
                           old_module=WORM_REGISTRY[name].get("module", ""),
                           new_module=module)
            WORM_REGISTRY[name] = {
                "class": cls,
                "module": module,
                "category": category,
                "priority": priority,
                "cooldown_seconds": cooldown_seconds,
                "description": description or (cls.__doc__ or "").split("\n")[0].strip(),
                "registered_at": datetime.now().isoformat(),
                "spawn_count": 0,
                "last_spawned": None,
                "tags": tags or set(),
                "source_file": source_file,
                "version": getattr(cls, "VERSION", "0.0.0"),
                # Declared fleet-authority membership. RedQueenWorm consults
                # this (via is_protected_worm) so a regulator is never probed,
                # even when its functional nature IS in PROBE_NATURES.
                "fleet_governor": name in FLEET_GOVERNORS,
                # G17: provenance of the nature. "declared" means an author
                # asserted it via @register_worm(category=...).
                "registration_source": "decorator",
                "category_source": "declared",
                # "declared" = the class implements get_summary itself;
                # "default" = the registry attached BaseWormMixin's metadata
                # summary; "unavailable" = a C type refused the assignment.
                # Recorded so "every worm can be summarized" cannot be mistaken
                # for "every worm reports real state".
                "summary_source": _ensure_summary_contract(cls),
            }

        _log_event("worm_registered", worm_name=name, category=category, priority=priority)
        return cls

    return decorator


# --- Auto-Discovery -------------------------------------------------

def discover_worms(
    worms_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> int:
    """Auto-discover worms (lock-guarded public entry point).

    Thin wrapper: holds _registry_lock for the ENTIRE discovery pass.
    Under Python 3.14t free-threading, unsynchronized concurrent
    discovery (e.g. startup_warm background thread racing a caller)
    produced partial registries (top-level worms only, ~142/17 cats)
    or double-registered ones (~199). With the lock, racers queue and
    the latecomer sees _discovery_done=True and returns immediately.
    RLock is reentrant, so the implementation's register_worm() calls
    do not deadlock.
    """
    with _registry_lock:
        return _discover_worms_impl(worms_dir=worms_dir,
                                    progress_callback=progress_callback)


#: Stdlib top-level module names. The heuristic below registers any class
#: with a VERSION attribute that a scanned module happens to EXPOSE, and a
#: wildcard import exposes whatever it dragged through `dir()` — measured
#: 2026-09-27: stdlib `random.Random` (VERSION is the INT 3) was registered as
#: a worm and crashed the governance audit's version bucketing. A worm is a
#: class a worm MODULE defines (re-exports included, that is the G17 case);
#: a stdlib class is never one. VF worms live outside this tree by design and
#: are not stdlib, so the boundary is "stdlib", not "outside src".
_STDLIB_ROOTS = frozenset(getattr(sys, "stdlib_module_names", ()))


def _discover_worms_impl(
    worms_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> int:
    """
    Auto-discover and import all worm modules from CORE/WORMS directory.

    Uses per-checkout pickle cache (~/.azazael/worm_registry_cache.<ctx>.<root>.pkl) to avoid the
    38-45s cold start. If cache is fresh, loads registry in <0.2s.
    If cache is stale or missing, runs full discovery and rebuilds cache.

    Args:
        worms_dir: Optional path to WORMS directory (default: CORE/WORMS).
        progress_callback: Optional callback(processed, total, module_name).

    Returns number of newly discovered worm classes.
    """
    global _discovery_done
    
    # Short-circuit: if discovery already completed, skip redundant work.
    # Thread-safe: _discovery_done is only set True after WORM_REGISTRY
    # is fully populated. Workers see True → registry is valid.
    if _discovery_done:
        return 0

    if worms_dir is None:
        worms_dir = str(Path(__file__).resolve().parent / "WORMS")

    if not os.path.isdir(worms_dir):
        return 0

    # -- Try pickle cache first (avoids 38-45s cold start) --
    # Uses cache._load_cache_readonly() — NEVER rebuilds from within
    # discover_worms(). Calling cache.load_registry() here would create an
    # infinite loop:  discover_worms -> load_registry -> discover_worms -> ...
    _load_cache_readonly, save_cache = _get_cache_funcs()
    if not WORM_REGISTRY:
        try:
            registry = _load_cache_readonly(worms_dir)
            if registry:
                with _registry_lock:
                    WORM_REGISTRY.update(registry)
                _discovery_done = True
                _log_event("discovery_cache_hit", count=len(WORM_REGISTRY), worms_dir=worms_dir)
                _replay_policy_journal()
                return len(WORM_REGISTRY)
        except Exception as exc:
            note_failure("worm_registry:discovery-cache-read", exc)
            _log_event("discovery_cache_miss", error=str(exc)[:80])
            pass  # Cache unavailable — fall through to full discovery

    before_count = len(WORM_REGISTRY)

    # Add WORMS dir to sys.path temporarily
    if worms_dir not in sys.path:
        sys.path.insert(0, worms_dir)

    # Pre-compute already-registered filenames for faster lookup.
    # Use os.path.basename() for robust cross-platform path handling.
    registered_files = {
        os.path.basename(info.get("source_file", ""))
        for info in WORM_REGISTRY.values()
        if info.get("source_file")
    }

    # Build the module candidate list ONCE via a recursive walk.
    # Top-level modules import as bare names ("NetworkDiag"); package
    # subdir modules (e.g. WORMS/expansion/*.py) import as dotted bare
    # names ("expansion.mesh") with worms_dir itself on sys.path.
    # Only directories with __init__.py are descended into (proper
    # packages); "_"/"."-prefixed files and dirs are skipped.
    py_modules = []
    for dirpath, dirnames, filenames in os.walk(worms_dir):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(("_", "."))
            and os.path.isfile(os.path.join(dirpath, d, "__init__.py"))
        )
        rel_dir = os.path.relpath(dirpath, worms_dir)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, ".") + "."
        for f in sorted(filenames):
            if f.endswith(".py") and not f.startswith(("_", ".")):
                py_modules.append((prefix + f[:-3]))
    py_modules.sort()
    total = len(py_modules)

    for processed, module_name in enumerate(py_modules, 1):
        # Skip already-registered modules (fast basename lookup)
        if (module_name.split(".")[-1] + ".py") in registered_files:
            continue

        if progress_callback:
            progress_callback(processed, total, module_name)

        try:
            # Prefer the package-qualified name (CORE.WORMS.expansion.mesh):
            # safer under free-threading and avoids clobbering an unrelated
            # "expansion" module in sys.modules from another project. Fall
            # back to the bare dotted name for non-CORE layouts (VF, ad-hoc
            # worms_dir passed by tests).
            qualified = "CORE.WORMS." + module_name
            try:
                module = importlib.import_module(qualified)
            except ImportError as exc:
                note_failure("worm_registry:qualified-module-import", exc)
                module = importlib.import_module(module_name)

            # After import, skip dir() loop if @register_worm already registered this module
            src_file = getattr(module, "__file__", "")
            if src_file:
                src_name = os.path.basename(src_file)
                already_has_classes = any(
                    info.get("source_file", "").endswith(src_name)
                    for info in WORM_REGISTRY.values()
                )
                if already_has_classes:
                    continue

            # Find classes with VERSION attribute (fast isinstance check)
            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue
                if attr_name.endswith("Config"):
                    continue
                obj = getattr(module, attr_name, None)
                if not isinstance(obj, type):
                    continue
                if not hasattr(obj, "VERSION"):
                    continue

                # Skip if already registered (fast dict lookup)
                if attr_name in WORM_REGISTRY:
                    continue

                # A worm entry is spawned as `cls()` and nothing else — the
                # cluster runner and the DLQ replay driver
                # (mesh._registry_execute) both construct it with no arguments.
                # A class that REQUIRES constructor arguments is a library, not
                # a spawning unit, and filing it produces a worm that cannot be
                # spawned at all. Measured 2026-11-02: trainers_v2's three
                # HookAwareTrainer subclasses (GRPOTrainerV2, RLTrainLoopV2,
                # SB3TrainerV2) carry VERSION = "2.0.0", so the heuristic here
                # filed them as `utility` worms; every `orchestrate` run
                # reported exactly 3 failures, each a TypeError raised before
                # any work happened.
                if not _spawnable_without_args(obj):
                    _log_event("worm_auto_register_skipped",
                               worm_name=attr_name, module=module_name,
                               reason="constructor_requires_arguments")
                    continue

                # Auto-categorize and register.
                #
                # G17 root cause (measured 2026-09-14): the guess was fed the
                # module currently being SCANNED, not the module that DEFINES
                # the class. Those differ whenever a class is reached through a
                # re-export — and the deprecated `ECOSYSTEM_EXPANSION` shim
                # re-exports 50 worm classes. So `DataPipelineWorm` (defined in
                # `expansion/learning_pipeline.py`) was guessed from the name
                # "ECOSYSTEM_EXPANSION", matched no rule, and landed in the
                # `utility` default bucket. Six of the nine heuristic filings
                # were mis-natured by exactly this mechanism.
                hint_module = getattr(obj, "__module__", "") or module_name
                if hint_module.split(".")[0] in _STDLIB_ROOTS:
                    continue   # stdlib class, not a worm (see _STDLIB_ROOTS)
                auto_category = _guess_category(attr_name, hint_module)
                register_fn = register_worm(
                    category=auto_category,
                    priority=_guess_priority(attr_name, hint_module),
                    cooldown_seconds=_guess_cooldown(auto_category),
                    description=obj.__doc__ or "",
                )
                register_fn(obj)
                # G17: mark the entry as heuristic-derived. Nothing before this
                # distinguished a declared nature from a guessed one, so the
                # fleet's own scheduling metadata could be fabricated without
                # a trace. Now it is flagged on the record and logged, which
                # makes the guess population measurable (see
                # get_heuristic_registrations).
                _info = WORM_REGISTRY.get(attr_name)
                if isinstance(_info, dict):
                    _info["registration_source"] = "heuristic"
                    _info["category_source"] = "heuristic"
                    # Which module actually drove the guess. Kept separately
                    # from `module` (the defining file) because disagreement
                    # between the two is the exact signal that would have made
                    # this bug obvious a year ago.
                    _info["category_hint_module"] = hint_module
                _log_event("worm_auto_registered", worm_name=attr_name,
                           category=auto_category, module=module_name,
                           hint_module=hint_module)

        except Exception as exc:      # noqa: BLE001 - one bad module must not stop discovery
            # Module might have import issues or psutil segfaults
            note_failure("worm_registry:module-auto-register", exc)

    # --- VF (Vermillion LightFury) worm auto-discovery ---
    # vermillion_lightfury uses package-style imports (worms.nmap_scanner),
    # so we need the PARENT directory on sys.path (vermillion_lightfury/)
    # and a package prefix (worms.) for module names.
    global _vf_already_discovered
    if _vf_worms_dir and os.path.isdir(_vf_worms_dir) and not _vf_already_discovered:
        _vf_root = os.path.dirname(_vf_worms_dir)  # vermillion_lightfury/
        _vf_pkg = os.path.basename(_vf_worms_dir)   # "worms"
        if _vf_root not in sys.path:
            sys.path.insert(0, _vf_root)
        try:
            _vf_py_files = sorted(
                f for f in os.listdir(_vf_worms_dir)
                if f.endswith('.py') and not f.startswith('_') and not f.startswith('.')
            )
            for _vf_filename in _vf_py_files:
                _vf_mod_name = f"{_vf_pkg}.{_vf_filename[:-3]}"
                try:
                    importlib.import_module(_vf_mod_name)
                except Exception as exc:  # noqa: BLE001 - one bad VF module is skipped
                    note_failure("worm_registry:vf-module-import", exc)
            _vf_already_discovered = True
        except Exception as exc:      # noqa: BLE001 - VF tree is optional
            note_failure("worm_registry:vf-discovery", exc)

    # -- Replay persisted policy adaptations (2026-09-14, helper below) --
    _replay_policy_journal()

    _discovery_done = True

    # -- Save to pickle cache for next session --
    new_count = len(WORM_REGISTRY) - before_count
    if new_count > 0:
        try:
            # Lazy import to avoid module-level import cycle with cache module
            _, save_cache = _get_cache_funcs()
            report = save_cache(WORM_REGISTRY, worms_dir)
            if isinstance(report, dict) and report.get("excluded"):
                # Reported, not swallowed. Entries that cannot be pickled are
                # excluded so that one locally-defined class cannot cost the
                # whole cache; the names are what makes that actionable.
                _log_event("discovery_cache_entries_excluded",
                           excluded=report["excluded"],
                           registered=report.get("registered"),
                           declared=report.get("declared"))
        except Exception as _save_exc:
            # Non-fatal — discovery succeeded and the next process simply
            # rebuilds. Logged rather than silently swallowed, because a save
            # that fails every run leaves an older cache answering for a fleet
            # it no longer describes, with nothing reporting a problem.
            # Non-fatal — discovery succeeded and the next process simply
            # rebuilds. Logged rather than silently swallowed, because a save
            # that fails every run leaves an older cache answering for a fleet
            # it no longer describes, with nothing reporting a problem.
            note_failure("worm_registry:discovery-cache-write", _save_exc)
            _log_event("discovery_cache_save_failed",
                       error=repr(_save_exc)[:200])

    return new_count


# G17 (2026-09-14): module-name prefixes are a STRONGER signal than
# class-name keywords when the module was named for its nature. Measured on
# the live registry, `DataPipelineWorm`, `FeatureEngineer` and `ModelVersioner`
# were all filed under the `utility` default branch despite living in
# `learning_pipeline`, `learning_features` and `learning_versioner` — the exact
# "right module, wrong nature" fabrication the heuristic risk describes. A
# declared prefix map fixes the class of problem instead of adding another
# keyword to the chain.
_MODULE_PREFIX_NATURES = {
    "learning_": "learning",
    "ml_": "ml",
    "viz_": "visualization",
    "telemetry_": "telemetry",
    "container_": "container",
    "backup_": "backup",
}


def _spawnable_without_args(cls: type) -> bool:
    """True when `cls()` is legal — the only construction the fleet performs.

    Auto-registration used to accept any class carrying a `VERSION` attribute,
    which is true of helper trainers that are meant to be handed a context by
    their caller. Such a class lands in the registry, the scheduler spawns it
    at the next cooldown, `cls()` raises TypeError, and the run reports a
    failure for a worm that never existed. Discovery asks the constructor
    instead of guessing from the name.

    An opaque signature (C extension, `__signature__` unavailable) is treated
    as spawnable: this filter removes only what it can prove is not.
    """
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        # No signature to read (C extension, or `__signature__` withheld). Silence
        # is not evidence of required arguments, so the class is let through and
        # the filter stays limited to what it can prove.
        sig = None
    if sig is None:
        return True
    for param in sig.parameters.values():
        if param.default is param.empty and param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return False
    return True


def _guess_category(class_name: str, module_name: str) -> str:
    """Heuristic category guessing for auto-discovered worms.

    Only used for classes that carry a `VERSION` attribute but were never
    decorated — i.e. worms whose nature was never *declared*. Their category
    drives scheduling (nature clusters) and cooldown, so a wrong answer is a
    real scheduling error, not a cosmetic one. The auto-registration path
    therefore records `category_source="heuristic"` on the registry entry and
    emits a `worm_auto_registered` event, so guessed metadata is countable
    instead of invisible.
    """
    name_upper = class_name.upper()
    module_upper = module_name.upper()

    # Module-prefix rule: checked FIRST, because the module name is declarative
    # where the class name is descriptive.
    base = str(module_name).rsplit(".", 1)[-1].lower()
    for prefix, nature in _MODULE_PREFIX_NATURES.items():
        if base.startswith(prefix):
            return nature

    # ── Neural mesh fabric natures (2026-09-13) — checked FIRST because
    # the generic rules below misfile them (Gossip->sync via "SYNC",
    # SelfHeal/PartitionHealer->defense via "HEAL").
    if any(kw in name_upper for kw in (
            "QUORUM", "LEADERELECT", "SPLITBRAIN")):
        return "consensus"
    if any(kw in name_upper for kw in (
            "ENGRAM", "VECTORSHARD", "RECALLROUTER", "COHERENCE",
            "DREAMCONSOLIDAT")):
        return "engram"
    if any(kw in name_upper for kw in (
            "FEDERATED", "NASMESH", "REWARDSHAPER")):
        return "federated"
    if any(kw in name_upper for kw in (
            "SERVEMESH", "LOADBALANCER", "FAILOVER", "MODELSHARD")):
        return "servemesh"
    if any(kw in name_upper for kw in (
            "IMMUNE", "QUARANTINE", "ATTESTATION", "SELFHEAL")):
        return "immune"
    if any(kw in name_upper for kw in (
            "SYNAPSE", "HOMEOSTAT", "MOEDISPATCH", "SSMSEQUENCE",
            "ACTIVATION")):
        return "synapse"
    if any(kw in name_upper for kw in (
            "XENOMORPH", "OVOMORPH", "FACEHUGGER", "CHESTBURSTER",
            "PRAETORIAN", "LILITH", "REDQUEEN", "RESIN", "DRONEWORM")):
        return "xenomorph"
    if any(kw in name_upper for kw in (
            "SPINE", "MESH", "GOSSIP", "PARTITION", "BEACON")):
        return "mesh"
    # Neural mesh v2 (2026-09-14) — 8 new classes
    if any(kw in name_upper for kw in (
            "DENDRITE", "ATTENTIONGATE", "CONTEXTCONCAT", "SENSORYFUSE")):
        return "dendrite"
    if any(kw in name_upper for kw in (
            "MYELIN", "SALTATORY", "SPIKEENCOD", "LATENCYCOMPENSAT")):
        return "axon"
    if any(kw in name_upper for kw in (
            "ASTROCYTE", "OLIGODENDROCYTE", "MICROGLIA", "EPENDYMAL", "GLIA")):
        return "glia"
    if any(kw in name_upper for kw in (
            "HEBBIAN", "STDP", "ELASTICCONSOLIDAT", "FORGETTINGCURVE", "PLASTICITY")):
        return "plasticity"
    if any(kw in name_upper for kw in (
            "THALAMIC", "SENSORYRELAY", "AROUSAL", "THALAMUS")):
        return "thalamus"
    if any(kw in name_upper for kw in (
            "SHARPWAVE", "SLEEPCONSOLIDAT", "ENGRAMINDEX", "DREAMCRITIC", "CONSOLIDAT")):
        return "consolidation"
    if any(kw in name_upper for kw in (
            "WINBRIDGE", "WSLUBUNTU", "WSLKALI", "BAREPASSTHROUGH", "GDRIVELOOM",
            "ENVBEACONV2", "ENVMESH")):
        return "envmesh"
    if any(kw in name_upper for kw in (
            "POWERBUDGET", "THERMALTHROTTLE", "QUOTAENFORC", "METABOLIC")):
        return "metabolic"

    if any(kw in name_upper for kw in ["DIAG", "SCAN", "INSPECT"]):
        return "diagnostic"
    if any(kw in name_upper for kw in ["ANALYSIS", "ANALYZE", "REVERSE"]):
        return "analysis"
    if any(kw in name_upper for kw in ["ERROR", "HEALTH", "HEAL", "REPAIR", "DEFENSE"]):
        return "defense"
    if any(kw in name_upper for kw in ["OPTIMIZE", "OPTIMIZ", "TRIM", "PRUNE"]):
        return "optimization"
    if any(kw in name_upper for kw in ["ORCHESTRATE", "ORCHESTRA", "META", "HYDRA"]):
        return "orchestration"
    if any(kw in name_upper for kw in ["SYNC", "CLOCK", "TIME"]):
        return "sync"
    if any(kw in name_upper for kw in ["SYSTEM", "PROCESS", "DISK", "NETWORK", "NET", "MONITOR"]):
        return "monitor"
    if any(kw in name_upper for kw in ["CLUSTER", "FLEET", "POOL", "SPAWN"]):
        return "orchestration"

    return "utility"


def _guess_priority(class_name: str, module_name: str) -> int:
    """Heuristic priority guessing for auto-discovered worms."""
    name_upper = class_name.upper()
    if any(kw in name_upper for kw in ["SYSTEM", "HEALTH", "ERROR"]):
        return 8
    if any(kw in name_upper for kw in ["PROCESS", "NETWORK", "DIAG"]):
        return 7
    if any(kw in name_upper for kw in ["ANALYSIS", "BINARY", "REVERSE"]):
        return 6
    if any(kw in name_upper for kw in ["DISK", "CLOCK", "SYNC"]):
        return 5
    if any(kw in name_upper for kw in ["OPTIMIZ", "TRIM"]):
        return 4
    return 3


def _guess_cooldown(category: str) -> float:
    """Heuristic cooldown guessing based on category."""
    cooldowns = {
        "monitor": 15.0,
        "diagnostic": 30.0,
        "analysis": 60.0,
        "defense": 30.0,
        "optimization": 45.0,
        "orchestration": 20.0,
        "sync": 30.0,
        "repair": 60.0,
        "utility": 30.0,
        # Neural mesh fabric natures (2026-09-13)
        "mesh": 15.0,
        "synapse": 20.0,
        "consensus": 45.0,
        "engram": 45.0,
        "federated": 120.0,
        "servemesh": 20.0,
        "immune": 45.0,
        "xenomorph": 120.0,
    }
    return cooldowns.get(category, 30.0)


# --- Lazy PAMS Recorder (thread-safe, defer import) ----------------
_pams_instance: Any = None
_pams_instance_lock = threading.Lock()


_cache_funcs_cache: Optional[tuple] = None
_cache_funcs_lock = threading.Lock()


def _get_cache_funcs() -> tuple:
    """Lazily import worm_registry_cache functions used by discover_worms().

    Avoids a module-level import cycle: worm_registry_cache itself imports
    CORE.worm_registry (get_all_categories / WORM_REGISTRY / discover_worms),
    so importing it at worm_registry's module level creates a circular import.

    Returns (load_cache_readonly, save_cache) once resolved, cached for reuse.
    """
    global _cache_funcs_cache
    if _cache_funcs_cache is not None:
        return _cache_funcs_cache
    with _cache_funcs_lock:
        if _cache_funcs_cache is None:
            from CORE.worm_registry_cache import _load_cache_readonly, save_cache
            _cache_funcs_cache = (_load_cache_readonly, save_cache)
    return _cache_funcs_cache


def _new_decision_id() -> Optional[str]:
    """Canonical decision id, minted by `spawn_credit` so both taps agree.

    Imported lazily: `worm_registry` is imported by the expansion package, so a
    module-level import would risk a cycle. Falls back to a local mint rather
    than returning None, because a missing id would silently degrade the join
    back to statistical.
    """
    try:
        from CORE.WORMS.expansion.spawn_credit import new_decision_id
        return new_decision_id()
    except Exception as exc:          # noqa: BLE001 - a local mint is the fallback
        note_failure("worm_registry:decision-id-mint", exc)
        try:
            return "d-%d" % time.time_ns()
        except Exception as exc:      # noqa: BLE001 - no id at all than a crash
            note_failure("worm_registry:decision-id-fallback", exc)
            return None


#: Where the L1 wiring cycle publishes an ACCEPTED genome. Absent means no
#: promotion has ever passed the gate, which is the correct initial state.
WIRING_PROMOTED_PATH = os.environ.get("AZAZAEL_WIRING_PROMOTED") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "state", "wiring_promoted.json")

_wiring_cache: Optional[Dict[str, Any]] = None
_wiring_cache_stamp: Optional[Tuple[float, int]] = None
_wiring_lock = threading.Lock()


def _load_promoted_wiring() -> Optional[Dict[str, Any]]:
    """The accepted L1 wiring genome, or None. NEVER raises, NEVER stale.

    This is the consumer that makes the wiring genome an effector rather than a
    report: `decide_execution_order` reads it to rank the fleet. Without a reader
    the genotype would be one more learner with nothing to act on - the defect
    that left the hyperparameter loop closing inside the trainer.

    Cache is keyed on (mtime_ns, size), not a TTL. A promotion must take effect
    on the next dispatch without a restart, and a TTL would either serve a stale
    genome after a promotion or re-read the file on every hot dispatch call.
    Fail-open everywhere: a corrupt or missing file yields None and the caller
    falls back to registry priority, so dispatch can never be blocked by state.
    """
    global _wiring_cache, _wiring_cache_stamp
    try:
        st = os.stat(WIRING_PROMOTED_PATH)
        stamp = (st.st_mtime, st.st_size)
    except OSError as exc:            # noqa: BLE001 - no promotion yet is the initial state
        note_failure("worm_registry:no-promoted-wiring-yet", exc)
        return None

    if _wiring_cache is not None and _wiring_cache_stamp == stamp:
        return _wiring_cache

    with _wiring_lock:
        if _wiring_cache is not None and _wiring_cache_stamp == stamp:
            return _wiring_cache
        try:
            import json as _json
            with open(WIRING_PROMOTED_PATH, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
            if not isinstance(data, dict):
                return None
            _wiring_cache = data
            _wiring_cache_stamp = stamp
            return data
        except Exception as exc:      # noqa: BLE001 - corrupt genome falls back to registry
            note_failure("worm_registry:wiring-unreadable", exc)
            return None


def _decision_versions() -> Dict[str, Any]:
    """Versions of every LEARNED artifact that produced this decision.

    Added 2026-09-21 for the same reason `decision_id` was: without it, a
    promoted calibration and its effect are indistinguishable in the ledger from
    any other change in the fleet. The choice rows carry `decision_id` and the
    outcome rows carry the same id, so a version block written here is inherited
    by every outcome that decision caused -- which is what makes "did the L2
    promotion help?" a question the recorded data can answer instead of one that
    only the loop's own average can assert.

    Three artifacts are stamped, because three things steer a dispatch and each
    can move independently of the others: the hyperparameter set (`hp`), the L1
    wiring genome (`wg`, plus its `wg_gen`), and the spine Q-table size.

    Fail-soft PER FIELD, and None means "not readable", never "absent": a
    missing genome is a real state (no promotion has passed the gate) and is
    reported as None in exactly the same slot, so a reader can tell that the
    field was written and had nothing to say.
    """
    out: Dict[str, Any] = {"hp": None, "wg": None, "wg_gen": None,
                           "spine_edges": None}
    try:
        from CORE.WORMS.expansion import hyperopt as _hyp
        out["hp"] = _hyp.fingerprint(_hyp.load())
    except Exception as exc:          # noqa: BLE001 - None means "not readable"
        note_failure("worm_registry:versions-hp", exc)
    try:
        data = _load_promoted_wiring() or {}
        out["wg"] = data.get("fingerprint")
        out["wg_gen"] = data.get("generation")
    except Exception as exc:          # noqa: BLE001 - None means "not readable"
        note_failure("worm_registry:versions-wg", exc)
    try:
        from CORE import spine_registry as _sr
        out["spine_edges"] = len(_sr.get_q_weights())
    except Exception as exc:          # noqa: BLE001 - None means "not readable"
        note_failure("worm_registry:versions-spine", exc)
    return out


def _get_pams_recorder():
    """Lazy PAMS recorder — double-checked locking for Python 3.14t free-threading."""
    global _pams_instance
    if _pams_instance is None:
        with _pams_instance_lock:
            if _pams_instance is None:
                if get_recorder is not None:
                    try:
                        _pams_instance = get_recorder()
                    except ImportError as exc:   # noqa: BLE001 - PAMS unavailable at runtime
                        note_failure("worm_registry:pams-instantiate", exc)
                        _pams_instance = False
                else:
                    _pams_instance = False
    return _pams_instance if _pams_instance else None


# --- BaseWormMixin — explicit schema registration (thread-safe for 3.14t) ----

class BaseWormMixin:
    """
    Mixin that provides default `get_summary()` for worm classes.

    Uses **explicit schema registration** instead of introspection,
    making it thread-safe under Python 3.14t free-threading.

    Worm classes declare their summary method and fields upfront:
        class MyWorm(BaseWormMixin):
            __summary_method__ = "scan_all"       # method to call for get_summary()
            __summary_fields__ = ["results"]      # instance attrs to include in summary

            def scan_all(self):
                return {"found": 42}

        w = MyWorm()
        print(w.get_summary())
        # {"success": True, "method": "scan_all", "result": {"found": 42}, ...}

    If no explicit schema is set, returns basic class info (name, module, version)
    instead of using introspection.
    """

    # -- Schema (override in subclass) --------------------------------
    __summary_method__: str = ""       # Method name to call for summary
    __summary_fields__: list[str] = []  # Instance attribute names to include

    # -- Standard method names to auto-discover if __summary_method__ is not set ---
    _DISCOVERY_CANDIDATES = ["execute", "run", "scan", "check", "analyze",
                             "audit", "collect", "probe", "profile", "inspect",
                             "monitor", "report", "validate", "detect", "measure",
                             "track", "fix", "heal", "start", "test", "verify",
                             "publish", "subscribe", "deliver", "negotiate",
                             "orchestrate", "handle_request", "register_model",
                             "register_schema", "register_peer", "register_method",
                             "check_latest", "check_usage", "check_outdated",
                             "check_package", "compare_versions", "compile_html",
                             "crawl_all", "predictive_crawl", "train_model",
                             "train_episode", "train_from_memory",
                             "discover_all", "discover_all_natures",
                             "execute_nature", "execute_all_natures",
                             "export_state", "import_state", "get_snapshot",
                             "inc_counter", "set_gauge", "observe",
                             "scan_all", "scan_all_modules", "scan_env_files",
                             "scan_requirements", "scan_local_ports",
                             "run_scheduler", "run_scan", "run_all",
                             "run_comprehensive_scan", "run_complete_analysis",
                             "audit_exposure", "audit_image",
                             "detect_bottleneck", "detect_anomalies",
                             "detect_breaking_changes",
                             "measure_contention", "measure_io",
                             "create_snapshot", "list_versions",
                             "rollback", "rotate_secret", "generate_password",
                             "generate_shim", "test_restore", "verify_archive",
                             "archive", "backup_data", "restore_data",
                             "render_svg", "to_dot", "to_timeline_html",
                             "build_hourly_heatmap",
                             "compare_snapshots", "take_snapshot",
                             "watch_file", "read_new_lines",
                             "register_webhook", "replay",
                             "find_overly_permissive", "get_firewall_rules",
                             "fix_file", "fix_bom", "fix_bad_syspath",
                             "heal_module", "apply_healing",
                             "serialize", "deserialize",
                             "check_bom", "check_syntax", "check_imports",
                             "is_enabled", "set_flag",
                             "crawl_file", "crawl_folder", "crawl_delta",
                             "find_python_files", "remove_bom",
                             "compute_manifest", "find_changes",
                             "start_service", "stop_service",
                             "predict_failure", "record_history",
                             "add_edge", "add_node", "add_section",
                             "add_span", "add_mapping",
                             "get_checksums", "format_report",
                             "parse_requirements",
                             "normalize", "impute_missing", "one_hot_encode",
                             "extract_worm_features",
                             "update_learned_patterns", "sample", "capture_sample"]

    def __init_subclass__(cls, **kwargs):
        """Auto-discover __summary_method__ if not explicitly set.

        Checks for common standard methods (execute, run, scan, etc.)
        and sets __summary_method__ to the first one found.
        This ensures 40 previously-skipped worms get auto-discovered.
        """
        super().__init_subclass__(**kwargs)
        # Only auto-discover if __summary_method__ is the default empty string
        if cls.__summary_method__:
            return
        for candidate in cls._DISCOVERY_CANDIDATES:
            if hasattr(cls, candidate) and callable(getattr(cls, candidate, None)):
                cls.__summary_method__ = candidate
                break

    def get_summary(self) -> Dict[str, Any]:
        """
        Return a summary dict using the declared schema.

        If `__summary_method__` is set, calls that method and returns its result.
        If `__summary_fields__` is set, includes those instance attributes.
        Falls back to basic class info if no schema is declared.

        Thread-safe: uses only class-level constants and instance __dict__ reads
        via explicit field names — no dir(), no getattr() for method discovery.

        Returns:
            Dict with "success", "method", "result" matching what executors expect.
        """
        # -- Explicit method call --
        method_name = self.__summary_method__
        if method_name:
            # Direct attribute lookup by explicit name (thread-safe, no dir())
            try:
                method = object.__getattribute__(self, method_name)
                if callable(method):
                    result = method()
                    if hasattr(result, "__dict__"):
                        result = result.__dict__
                    return {"success": True, "method": method_name, "result": result}
            except AttributeError as exc:  # noqa: BLE001 - fall through to fields
                note_failure("worm_registry:summary-method-attr", exc)
            except Exception as exc:        # noqa: BLE001 - summary is advisory
                note_failure("worm_registry:summary-method-call", exc)

        # -- Explicit field summary --
        fields = self.__summary_fields__
        if fields:
            result = {}
            for field_name in fields:
                try:
                    result[field_name] = object.__getattribute__(self, field_name)
                except AttributeError as exc:   # noqa: BLE001 - field is optional
                    note_failure("worm_registry:summary-field-attr", exc)
            return {"success": True, "method": "get_summary", "result": result}

        # -- Fallback: basic class info (no introspection) --
        return {
            "success": True,
            "method": "get_summary",
            "result": {
                "class": type(self).__name__,
                "module": type(self).__module__,
                "version": getattr(self, "VERSION", "?"),
            },
        }

    def get_status(self) -> Dict[str, Any]:
        """Default status — returns basic class info without introspection."""
        return {
            "class": type(self).__name__,
            "module": type(self).__module__,
            "version": getattr(self, "VERSION", "?"),
            "summary_method": self.__summary_method__ or "(default)",
            "summary_fields": self.__summary_fields__,
        }


# --- Spawn Diversity Engine -----------------------------------------

@dataclass
class SpawnRecord:
    """Record of a single spawn event."""
    worm_type: str
    category: str
    timestamp: float
    priority: int
    reason: str


class SpawnDiversityEngine:
    """
    Spawn diversity engine that prevents spawning the same worm types
    repeatedly and ensures broad ecosystem coverage.

    Features:
    - Category rotation: never spawn same category in consecutive picks
    - Cooldown enforcement: each type has minimum time between spawns
    - Weighted selection: priority × recency × diversity bonus
    - Time-based patterns: spawn different profiles based on hour of day
    - Staleness penalty: worms never spawned get increasing priority bonus
    - All-registered-worm coverage: ensures every worm type eventually gets spawned
    - PAMS memory recording: every spawn recommendation is logged to PAMS
    """

    def __init__(self):
        self.spawn_history: List[SpawnRecord] = []
        self._last_category: Optional[str] = None
        self._lock = threading.Lock()

    def _record_pams(self, event_type: str, data: Dict[str, Any]):
        """Record event to PAMS memory (non-blocking on failure)."""
        pams = _get_pams_recorder()
        if pams:
            try:
                pams.record_event(
                    event_type, data,
                    source="SpawnDiversityEngine",
                    severity="info",
                )
            except Exception as exc:        # noqa: BLE001 - recording is best-effort
                note_failure("worm_registry:pams-record", exc)

    def record_spawn(self, worm_type: str, category: str, priority: int, reason: str = ""):
        """Record a spawn event for diversity tracking."""
        with self._lock:
            self.spawn_history.append(SpawnRecord(
                worm_type=worm_type,
                category=category,
                timestamp=time.time(),
                priority=priority,
                reason=reason,
            ))
            self._last_category = category

            # Prune history (keep last 1000)
            if len(self.spawn_history) > 1000:
                self.spawn_history = self.spawn_history[-1000:]

    def pick_next_spawn(
        self,
        fleet_status: Any = None,
        worm_types_override: Optional[Dict[str, Dict]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Pick the best next worm type to spawn, considering diversity,
        cooldowns, category rotation, time-based patterns, and staleness.

        Also records the selection to PAMS for audit trail.

        Args:
            fleet_status: Optional FleetStatus-like object with cpu_percent, etc.
            worm_types_override: Override WORM_REGISTRY entries (for MetaWormSpawner compat)

        Returns:
            Dict with "type", "priority", "reason", "category" — or None if nothing to spawn
        """
        registry = worm_types_override or WORM_REGISTRY
        if not registry:
            return None

        now = time.time()
        candidates = []

        for worm_type, info in registry.items():
            # -- Cooldown check --
            cooldown = info.get("cooldown_seconds", 30)
            last_spawned = info.get("last_spawned")
            if last_spawned:
                try:
                    last_time = datetime.fromisoformat(last_spawned).timestamp()
                    if now - last_time < cooldown:
                        continue  # Still on cooldown
                except (ValueError, TypeError) as exc:   # noqa: BLE001 - unparseable timestamp
                    note_failure("worm_registry:cooldown-parse", exc)

            category = info.get("category", "utility")

            # -- Category rotation --
            if category == self._last_category:
                continue  # Skip same category as last spawn

            priority = info.get("priority", 5)

            # -- Staleness bonus --
            spawn_count = info.get("spawn_count", 0)
            staleness_bonus: float = 0.0
            if spawn_count == 0:
                staleness_bonus = 5.0  # Never spawned — big bonus
            elif last_spawned:
                try:
                    last_time = datetime.fromisoformat(last_spawned).timestamp()
                    hours_since = (now - last_time) / 3600
                    staleness_bonus = min(3.0, hours_since * 0.5)  # Up to 3.0 bonus
                except (ValueError, TypeError) as exc:   # noqa: BLE001 - unparseable timestamp
                    note_failure("worm_registry:staleness-parse", exc)

            # -- Time-based pattern bonus --
            hour = datetime.now().hour
            time_bonus = self._get_time_bonus(category, hour)

            # -- Fleet status bonus --
            fleet_bonus: float = 0.0
            if fleet_status:
                fleet_bonus = self._get_fleet_bonus(category, fleet_status)

            # -- Compute weighted score --
            # Higher = more likely to be picked
            score = (
                priority * 2.0           # Base priority (2x weight)
                + staleness_bonus * 3.0  # Staleness matters most (3x)
                + time_bonus * 1.5       # Time patterns (1.5x)
                + fleet_bonus * 2.0      # Fleet context (2x)
            )

            candidates.append({
                "type": worm_type,
                "priority": priority,
                "category": category,
                "score": score,
                "spawn_count": spawn_count,
                "staleness_bonus": staleness_bonus,
                "time_bonus": time_bonus,
                "reason": self._build_reason(worm_type, category, staleness_bonus, time_bonus, fleet_bonus),
            })

        if not candidates:
            return None

        # Sort by score descending, pick top
        candidates.sort(key=lambda x: -x["score"])
        best = candidates[0]

        pick = {
            "type": best["type"],
            "priority": best["priority"],
            "reason": best["reason"],
            "category": best["category"],
        }

        # Record selection to PAMS for audit trail
        self._record_pams("spawn_selected", {
            "worm_type": pick["type"],
            "category": pick["category"],
            "priority": pick["priority"],
            "reason": pick["reason"],
            "total_candidates": len(candidates),
        })

        return pick

    def decide_execution_order(
        self,
        nature: str,
        candidates: Iterable[str],
        q_weights: Optional[Dict[str, float]] = None,
        credit: Optional[Dict[str, float]] = None,
        reason: str = "nature-order",
        record: bool = True,
    ) -> Tuple[List[str], Optional[str]]:
        """THE DISPATCH DECISION: order a Nature's worms for execution.

        Until 2026-09-21 this engine was instantiated by `ClusterManager`
        and never called. It wrote 608 `spawn_selected` rows describing choices
        that nothing executed, while the executions that DID happen were
        recorded by a different component that never logged a choice. Measured
        consequence: of 13 chosen worms and 28 executed worms, they had ONE in
        common - so no join between the two logs existed at any level.

        This method supplies the missing half. It makes the decision, records
        it with a `decision_id`, and returns that same id to the caller so the
        executions it causes carry it too. Choice and outcome then describe one
        decision instead of two unrelated populations.

        ORDERING ONLY - every candidate is returned. A policy able to drop a
        worm could starve the fleet, including the worms that keep this loop
        alive, so demotion is the strongest action available here.

        Ranking, descending:
          1. grounded credit from observed outcomes (`credit`)
          2. spine Q-weight - EMA of this nature's execution history
          3. registry priority - the hand-authored fallback
          4. name - deterministic tie-break

        ``record=False`` ranks WITHOUT writing the `spawn_selected` row per worm
        (2026-09-21, added for the genome adaptation audit). A caller that is
        asking a HYPOTHETICAL -- "what order would this genome produce?" -- must
        not be able to answer it only by injecting phantom decisions into the one
        ledger that exists to join choices to executions. Replicating the ranking
        key outside would have been the other option and it is the worse one: two
        copies of one formula is how telemetry comes to disagree with the thing it
        describes. Default True, so every existing caller is unaffected, and a
        `record=False` call returns a decision_id that is deliberately NOT
        joinable because nothing was written for it to join to.

        Returns ``(ordered_names, decision_id)``. ``decision_id`` is None only
        when there is nothing to decide.
        """
        names = [n for n in dict.fromkeys(candidates) if n]  # dedupe, keep order
        if not names:
            return [], None

        q_weights = q_weights or {}
        credit = credit or {}
        decision_id = _new_decision_id()

        # ── L1 effector: the promoted wiring genome steers this ranking ────
        # 2026-09-21. Before this, the L1 genotype had no consumer: a promoted
        # wiring genome would have been written to state and read by nothing --
        # the same "learner with no effector" defect this repo already had at the
        # hyperparameter layer. The genome's `priority` replaces the hand-authored
        # registry priority, and an organ the genome deactivated keeps running but
        # is demoted to the tail.
        #
        # STILL ORDERING ONLY - every candidate is returned, and "inactive" is a
        # demotion rather than a deletion, because the invariant above holds: a
        # policy able to drop a worm could starve the fleet. A soft-disable is the
        # strongest action L1 is allowed to take.
        # Wrapped even though the loader documents itself as fail-open: a
        # learner that can raise into the dispatch path can stop the whole
        # fleet, and that is a strictly worse failure than an unordered one.
        # Found by the test that hands this a loader that raises -- the
        # docstring said "NEVER raises" and nothing enforced it on the caller.
        try:
            wiring = _load_promoted_wiring()
        except Exception as exc:          # noqa: BLE001 - ordering must not stop the fleet
            note_failure("worm_registry:promoted-wiring-load", exc)
            wiring = None
        wiring_priority = (wiring or {}).get("priority") or {}
        wiring_active = (wiring or {}).get("active")
        active_set = set(wiring_active) if isinstance(wiring_active, list) else None

        def _key(name: str) -> Tuple[int, float, float, float, str]:
            reg = WORM_REGISTRY.get(name, {}) or {}
            priority = reg.get("priority", 5) or 5
            if name in wiring_priority:
                try:
                    priority = int(wiring_priority[name])
                except (TypeError, ValueError) as exc:   # noqa: BLE001 - malformed genome
                    note_failure("worm_registry:wiring-priority-parse", exc)
            # Demoted-out organs rank below every ranked one, deterministically.
            demoted = 1 if (active_set is not None and name not in active_set) else 0
            return (
                demoted,
                -float(credit.get(name, 0.0) or 0.0),
                -float(q_weights.get(f"{nature}:{name}",
                                     q_weights.get(name, 0.0)) or 0.0),
                -float(priority),
                name,
            )

        ordered = sorted(names, key=_key)

        # One row per worm - the schema `spawn_selected` already used, plus the
        # decision_id and rank that make it joinable to the outcome tap.
        # Skipped entirely when `record=False`: a hypothetical ranking must not
        # appear in the ledger as a decision that was taken.
        if record:
            # Resolved ONCE per decision, not per worm: every row of one choice
            # must name the same versions, or the ledger would claim a decision
            # was partly made by one calibration and partly by another.
            versions = _decision_versions()
            for rank, name in enumerate(ordered):
                reg = WORM_REGISTRY.get(name, {}) or {}
                category = reg.get("category", nature)
                priority = int(reg.get("priority", 5) or 5)
                self._record_pams("spawn_selected", {
                    "worm_type": name,
                    "category": category,
                    "priority": priority,
                    "reason": reason,
                    "decision_id": decision_id,
                    "rank": rank,
                    "n_targets": len(ordered),
                    "nature": nature,
                    "credit": round(float(credit.get(name, 0.0) or 0.0), 4),
                    "versions": versions,
                })
                self.record_spawn(name, category, priority, reason)

        return ordered, decision_id

    def pick_diverse_batch(self, count: int, fleet_status: Any = None) -> List[Dict[str, Any]]:
        """
        Pick a diverse batch of 'count' worm types, each from different categories.

        Returns list of spawn recommendations, each from unique categories.
        """
        picks = []
        used_categories: Set[str] = set()
        used_types: Set[str] = set()

        for _ in range(count):
            next_pick = self.pick_next_spawn(fleet_status=fleet_status)
            if next_pick is None:
                break

            cat = next_pick.get("category", "")
            wtype = next_pick.get("type", "")

            if cat in used_categories or wtype in used_types:
                # Temporarily mark as spawned and try again
                self._last_category = cat
                continue

            picks.append(next_pick)
            used_categories.add(cat)
            used_types.add(wtype)
            self._last_category = cat

        return picks

    def get_staleness_report(self, worm_types_override: Optional[Dict] = None) -> List[Dict]:
        """Get report of which worm types haven't been spawned recently."""
        registry = worm_types_override or WORM_REGISTRY
        now = time.time()
        report = []

        for wtype, info in registry.items():
            spawn_count = info.get("spawn_count", 0)
            last_spawned = info.get("last_spawned")

            hours_since = None
            if last_spawned:
                try:
                    last_time = datetime.fromisoformat(last_spawned).timestamp()
                    hours_since = (now - last_time) / 3600
                except (ValueError, TypeError) as exc:   # noqa: BLE001 - unparseable timestamp
                    note_failure("worm_registry:staleness-report-parse", exc)

            staleness = float("inf") if spawn_count == 0 else (hours_since or float("inf"))

            report.append({
                "type": wtype,
                "category": info.get("category", "utility"),
                "spawn_count": spawn_count,
                "hours_since_last": round(hours_since, 1) if hours_since else None,
                "staleness": staleness,
            })

        # Sort: never-spawned first, then longest since last spawn
        report.sort(key=lambda x: (-x["spawn_count"] if x["spawn_count"] == 0 else 0,
                                    -(x["staleness"] if x["staleness"] != float("inf") else 0)))
        return report

    def get_category_rotation_order(self) -> List[str]:
        """Get the recommended rotation order for categories, accounting for recent spawns."""
        # All known categories
        all_categories = list(set(
            info.get("category", "utility") for info in WORM_REGISTRY.values()
        ))

        # Reorder: last-spawned category goes to the end
        if self._last_category and self._last_category in all_categories:
            all_categories.remove(self._last_category)
            all_categories.append(self._last_category)

        return all_categories

    def _get_time_bonus(self, category: str, hour: int) -> float:
        """Get time-of-day based bonus for a category."""
        # Morning (6-12): monitoring is more useful
        if 6 <= hour < 12:
            if category == "monitor":
                return 2.0
            if category == "sync":
                return 1.0

        # Afternoon (12-18): analysis and optimization
        elif 12 <= hour < 18:
            if category in ("analysis", "optimization"):
                return 2.0
            if category == "diagnostic":
                return 1.5

        # Evening (18-22): defense and diagnostic
        elif 18 <= hour < 22:
            if category in ("defense", "diagnostic"):
                return 2.0

        # Night (22-6): repair and sync
        else:
            if category in ("repair", "sync", "orchestration"):
                return 2.0
            if category == "optimization":
                return 1.5

        return 0.0

    def _get_fleet_bonus(self, category: str, fleet_status: Any) -> float:
        """Get fleet-status based bonus for a category."""
        bonus = 0.0
        try:
            cpu = getattr(fleet_status, "cpu_percent", 0)
            error_count = getattr(fleet_status, "total_failures", 0)
            exec_count = max(getattr(fleet_status, "total_executions", 1), 1)
            error_rate = error_count / exec_count

            # High CPU -> prefer monitors
            if cpu > 70 and category == "monitor":
                bonus += 2.0

            # High error rate -> prefer defense
            if error_rate > 0.1 and category == "defense":
                bonus += 3.0

            # Many failed worms -> prefer repair/diagnostic
            if getattr(fleet_status, "failed", 0) > 3 and category in ("repair", "diagnostic"):
                bonus += 2.0

            # Low activity -> prefer exploratory analysis
            if exec_count < 10 and category in ("analysis", "diagnostic"):
                bonus += 1.5

        except Exception as exc:          # noqa: BLE001 - fleet status may be partial
            note_failure("worm_registry:fleet-bonus-probe", exc)

        return bonus

    def _build_reason(self, worm_type: str, category: str,
                      staleness: float, time_bonus: float, fleet_bonus: float) -> str:
        """Build a human-readable reason string for a spawn recommendation."""
        parts = []
        if staleness > 2:
            parts.append("stale")
        if time_bonus > 1:
            parts.append("time_preferred")
        if fleet_bonus > 1:
            parts.append("fleet_needed")
        if not parts:
            parts.append("diversity_rotation")
        return f"{worm_type.lower()}_{'_'.join(parts)}"


# --- Convenience Functions ------------------------------------------

def get_worms_by_category(category: str) -> Dict[str, Dict]:
    """Get all registered worms in a category. Thread-safe: uses _registry_lock."""
    with _registry_lock:
        return {
            name: info for name, info in list(WORM_REGISTRY.items())
            if info.get("category") == category
        }


def get_all_categories() -> Set[str]:
    """Get all unique categories in the registry. Thread-safe: uses _registry_lock."""
    with _registry_lock:
        return set(info.get("category", "utility") for info in list(WORM_REGISTRY.values()))


def get_unspawned_worms() -> List[str]:
    """Get list of worm types that have never been spawned. Thread-safe: uses _registry_lock."""
    with _registry_lock:
        return [
            name for name, info in list(WORM_REGISTRY.items())
            if info.get("spawn_count", 0) == 0
        ]


def mark_spawned(worm_type: str):
    """Mark a worm type as having been spawned (increment counter, set timestamp).

    Uses `.get(..., 0)`, not `+= 1` on the raw key. Registry entries are not
    guaranteed to carry `spawn_count` -- `get_unspawned_worms()` above already
    reads it defensively for exactly that reason -- so `+= 1` raised KeyError
    for any entry that had never been spawned. That is the only state in which
    this function is ever called, which made it fail on the first real use
    (reproduced by tests/test_colour_queens.py's retire channel).
    """
    with _registry_lock:
        if worm_type in WORM_REGISTRY:
            entry = WORM_REGISTRY[worm_type]
            entry["spawn_count"] = entry.get("spawn_count", 0) + 1
            entry["last_spawned"] = datetime.now().isoformat()


# --- Importability Validation --------------------------------------


def _resolve_worm_import_module(info: Dict[str, Any]) -> str:
    """Resolve the importable module path for a registry entry.

    Mirrors the module-path normalization in register_worm() and the
    health-check worker: bare module names (e.g. "TRIMZ" for files at the
    src root, or "CLUSTERWORMS" for files under CORE/WORMS) are prefixed
    with their package so the import resolves from the src root in fresh
    processes. Package-style refs (CORE.WORMS.X, worms.*) pass through.

    Returns an empty string if no module path can be determined.
    """
    module = info.get("module") or ""
    if module and "." in module:
        return module  # Already package-qualified (CORE.WORMS.X, worms.X)

    # Bare name — infer the package from the source file location.
    src = info.get("source_file") or ""
    norm = src.replace("\\", "/")
    if "/CORE/WORMS/" in norm:
        return f"CORE.WORMS.{module or Path(src).stem}"
    if "/CORE/" in norm:
        return f"CORE.{module or Path(src).stem}"
    # src-root module (TRIMZ.py, ERRORZ.py, ...) or unknown — import as-is.
    return module or (Path(src).stem if src else "")


def validate_registry_importable() -> Dict[str, Any]:
    """Confirm every registered worm's module is importable and exposes its class.

    Iterates WORM_REGISTRY and, for each entry, imports the resolved module
    and verifies the registered class is present. Detects stale cache entries,
    moved/renamed modules, and broken imports before they surface at spawn time.

    Returns:
        dict with:
          - valid: bool — True if all worms confirmed importable
          - total: int — number of registry entries checked
          - checked: int — number successfully confirmed
          - broken: list of {name, module, error} for worms that failed
          - validated_at: ISO timestamp
    """
    with _registry_lock:
        entries = list(WORM_REGISTRY.items())

    # Vacuous-pass guard (2026-10-03). A validator that returns `valid: True`
    # over ZERO entries has validated nothing, yet `azazael-ctl status` read it
    # as "Import valid: True / Total entries: 0" - a passing report on an empty
    # registry. The registry is process-global and empty until discovery runs,
    # so an empty registry here means NOT YET DISCOVERED, not "nothing to
    # check". Trigger discovery so the verdict describes the real registry.
    discovered = False
    discovery_error: (str | None) = None
    if not entries:
        try:
            discover_worms()
            discovered = True
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            note_failure("worm_registry:discover-call", exc)
            discovery_error = f"{type(exc).__name__}: {exc}"
        with _registry_lock:
            entries = list(WORM_REGISTRY.items())

    broken: List[Dict[str, Any]] = []
    checked = 0

    for name, info in entries:
        module = _resolve_worm_import_module(info)
        try:
            if not module:
                raise ImportError("no module or source_file recorded")
            mod = importlib.import_module(module)
            cls = getattr(mod, name, None)
            if cls is None or not isinstance(cls, type):
                raise ImportError(f"class {name!r} not found in module {module!r}")
            checked += 1
        except Exception as exc:
            note_failure("worm_registry:worm-module-import", exc)
            broken.append({
                "name": name,
                "module": module,
                "error": f"{type(exc).__name__}: {exc}",
            })

    # `valid` requires a NON-EMPTY registry: an empty registry can never be a
    # pass, or the check silently stops meaning anything.
    result: Dict[str, Any] = {
        "valid": len(broken) == 0 and bool(entries),
        "total": len(entries),
        "checked": checked,
        "broken": broken,
        "empty": not entries,
        "validated_at": datetime.now().isoformat(),
    }
    if not entries:
        result["note"] = (
            "registry is empty after discovery" if discovered
            else "registry empty and discovery did not run")
    if discovery_error:
        result["discovery_error"] = discovery_error
    return result


def repair_registry() -> Dict[str, Any]:
    """Attempt to auto-repair broken registry entries by re-running discovery.

    Broken entries usually come from a stale pickle cache (modules renamed,
    moved, or removed since the cache was saved). This function:
      1. Validates the current registry.
      2. If no broken entries, returns immediately (no-op).
      3. Otherwise invalidates the pickle cache, purges imported worm
         modules from sys.modules, and resets discovery state — forcing
         discover_worms() to re-import every worm module fresh so
         @register_worm decorators fire and the rebuild is faithful
         (full recursion, exact categories, exact cooldowns).
      4. Re-validates and reports what was fixed.

    Returns:
        dict with:
            attempt: bool          — True if a repair was needed (broken entries found)
            repaired: int          — count of previously-broken entries now importable
            still_broken: list     — of {name, module, error} remaining after repair
            valid: bool            — registry importable after repair
            total: int             — registry entries after repair
            checked: int           — entries confirmed after repair
            repaired_at: str       — ISO timestamp
    """
    before = validate_registry_importable()
    broken_in = {b.get("name") for b in (before.get("broken") or [])}
    if not broken_in:
        return {
            "attempt": False,
            "repaired": 0,
            "still_broken": [],
            "valid": bool(before.get("valid")),
            "total": before.get("total", 0),
            "checked": before.get("checked", 0),
            "repaired_at": datetime.now().isoformat(),
        }

    # Force a fresh rebuild: drop the stale pickle cache and clear discovery
    # state so discover_worms() re-imports every module from source.
    try:
        from CORE.worm_registry_cache import invalidate_cache
        invalidate_cache()
    except Exception as exc:          # noqa: BLE001 - cache invalidation is best-effort
        note_failure("worm_registry:repair-cache-invalidate", exc)

    importlib.invalidate_caches()  # clear importlib metadata caches too

    # Captured BEFORE the clear, and outside the lock (snapshot takes it): the
    # fleet this call is about to throw away is the only thing that can be put
    # back if the rebuild fails. Class identity survives the round trip, so
    # callers holding worm classes across a failed repair keep working ones.
    _previous_fleet = snapshot_registry()

    with _registry_lock:
        # Captured BEFORE the clear: the purge needs to know which modules host
        # worms, and the registry is the only record of that.
        _module_targets = _registered_worm_modules()
        WORM_REGISTRY.clear()
        global _discovery_done
        _discovery_done = False
    _purge_worm_modules(_module_targets)

    try:
        discover_worms()
    except Exception as exc:          # noqa: BLE001 - repair reports what it found
        note_failure("worm_registry:repair-rediscover", exc)
        # Without this the process is left holding an EMPTY registry and no way
        # to rebuild it (the purge dropped the modules that would have re-fired
        # @register_worm, and re-discovery just failed). Restoring the captured
        # fleet is the state that is strictly better than none.
        restored = restore_registry(_previous_fleet)
        after_failure = validate_registry_importable()
        broken_failed = {b.get("name") for b in (after_failure.get("broken") or [])}
        return {
            "attempt": True,
            "error": str(exc)[:200],
            "repaired": len(broken_in - broken_failed),
            "still_broken": after_failure.get("broken") or [],
            "valid": bool(after_failure.get("valid")),
            "total": after_failure.get("total", 0),
            "checked": after_failure.get("checked", 0),
            "restored": restored,
            "repaired_at": datetime.now().isoformat(),
        }

    after = validate_registry_importable()
    broken_out = set(b.get("name") for b in (after.get("broken") or []))
    repaired = len(broken_in - broken_out)
    return {
        "attempt": True,
        "repaired": repaired,
        "still_broken": after.get("broken") or [],
        "valid": bool(after.get("valid")),
        "total": after.get("total", 0),
        "checked": after.get("checked", 0),
        "repaired_at": datetime.now().isoformat(),
    }


def _registered_worm_modules() -> set:
    """The modules that host a registered worm class.

    Derived from the registry, which stores the package-qualified module for
    every class it registered (`register_worm` normalizes bare names). This is
    the authority on which modules must be re-imported for the decorators to
    re-fire, so callers read it BEFORE they clear the registry.
    """
    with _registry_lock:
        return {str(entry.get("module") or "")
                for entry in WORM_REGISTRY.values() if entry.get("module")}


def _hosts_worms(mod_name: str, mod: Any, targets: Set[str]) -> bool:
    """Whether a cached module must be re-imported for the fleet to rebuild.

    Two ways to qualify, deliberately unioned:

    * the registry NAMES this module (precise), and
    * the module's SOURCE mentions `register_worm` — the safety net for a module
      whose registry entry was overwritten by a same-named class elsewhere, and
      for registrations made indirectly.

    A module matching neither is a HELPER (`wiring_genome`, `hyperopt`,
    `spawn_credit`, ...) and is left cached. That distinction is the fix for a
    measured defect: deleting helpers replaced the module object, so every
    holder kept a different `wiring_genome` than the next importer
    (`wg0 is wg1 -> False`, 2026-09-22), which silently disarmed every
    `monkeypatch`/`mock.patch` that named a helper — 8 gate tests failed in-suite
    while passing alone, and `test_nature_metacluster_unit`'s 13 failures came
    from `mock.patch("CORE.WORMS.CLUSTERWORMS.get_worms_by_category")`
    patching the re-imported module while the classes under test read the old
    one.
    """
    if mod_name in targets:
        return True
    source = getattr(mod, "__file__", None)
    if not source:
        # A synthetic/exec'd module with no file: nothing to re-read, and it
        # cannot be a worm host discovered from disk.
        return False
    try:
        with open(source, "r", encoding="utf-8", errors="ignore") as fh:
            return "register_worm" in fh.read()
    except OSError as exc:
        # Unreadable source: prefer re-import over serving a stale fleet.
        note_failure("worm_registry:unreadable-worm-source", exc)
        return True


def _purge_worm_modules(worm_modules: Optional[Set[str]] = None) -> None:
    """Purge imported WORM-HOSTING modules from sys.modules.

    Without the purge, re-discovery cannot re-fire @register_worm
    decorators (modules import as cached no-ops) and worms rebuild via
    the dir()-fallback with heuristic metadata — fewer natures, wrong
    categories, wrong cooldowns, and expansion/ subpackages dropped
    entirely. Shared by reset_registry() and repair_registry().

    `worm_modules` is the registry's module set captured BEFORE the caller
    cleared it; when omitted it is read from the live registry (the
    `repair_registry()` path, where the registry is still populated). Only
    modules that host worms are purged — see `_hosts_worms` for why the helpers
    must keep their identity.
    """
    targets = _registered_worm_modules() if worm_modules is None else set(worm_modules)
    # Every module the registry names is a target regardless of package, because
    # the fleet spans TWO roots: `CORE.WORMS.*` and a top-level `worms.*`
    # (measured 2026-09-22: KaliHealth / MetasploitBridge / NmapScanner live in
    # `worms.*`, and a prefix-only filter silently lost those three on every
    # reset). The source-mention fallback stays scoped to the worm packages.
    for mod_name in list(sys.modules):
        if mod_name in targets:
            del sys.modules[mod_name]
            continue
        if not (mod_name == "expansion" or mod_name.startswith("expansion.")
                or mod_name.startswith("CORE.WORMS.")
                or mod_name == "worms" or mod_name.startswith("worms.")):
            continue
        if _hosts_worms(mod_name, sys.modules.get(mod_name), targets):
            del sys.modules[mod_name]
    # Also bare-named worm modules (discovery imports WORMS dir flat names
    # when running against an ad-hoc worms_dir): heuristic — names that
    # match a .py file directly in the WORMS dir.
    try:
        worms_dir = Path(__file__).resolve().parent / "WORMS"
        bare = {f[:-3] for f in os.listdir(worms_dir)
                if f.endswith(".py") and not f.startswith(("_", "."))}
        for mod_name in list(sys.modules):
            if mod_name in bare and not mod_name.startswith("CORE."):
                # File-loaded modules whose __file__ lives OUTSIDE the WORMS
                # dir were not loaded by flat discovery (e.g. the CLUSTERWORMS
                # shim execs the repo-root file under its bare name). Purging
                # them orphans the entry: nothing re-execs the file, so the
                # dir()-fallback cannot rebuild it and the fleet shrinks by
                # exactly that worm on every cold rebuild (measured: ClusterWORMZ
                # lost on 2nd purge+discover). Spare them like helpers.
                try:
                    _mf = getattr(sys.modules.get(mod_name), "__file__", None)
                    if _mf and Path(_mf).resolve().parent != Path(worms_dir).resolve():
                        continue
                except OSError as exc:    # noqa: BLE001 - probe is advisory
                    note_failure("worm_registry:module-file-probe", exc)
                del sys.modules[mod_name]
    except OSError as exc:            # noqa: BLE001 - no WORMS dir is a real state
        note_failure("worm_registry:worms-dir-list", exc)


def reset_registry(purge_modules: bool = True):
    """Reset the registry (for testing).

    Clears WORM_REGISTRY, re-arms discovery, and (by default) ALSO purges
    already-imported worm modules from sys.modules (see
    _purge_worm_modules). Without the purge, re-discovery after a reset
    cannot re-fire @register_worm decorators (modules import as no-ops),
    and worms rebuild via the dir()-fallback with heuristic metadata —
    fewer natures, wrong categories, wrong cooldowns.

    trade-off (accepted): classes from the purged modules become
    distinct objects from pre-reset ones; any test holding a direct
    class reference across a reset must re-discover.

    `purge_modules=False` gives the other half of that trade-off: the
    registry is emptied and discovery re-armed, but every already-imported
    module object stays identical. Use it when a test needs a small or
    empty registry and other test modules in the same process hold direct
    references to worm classes. Two limits are real and measured:

      * Emptying WORM_REGISTRY WITHOUT re-arming `_discovery_done` is not a
        usable state. `discover_worms()` short-circuits on the still-True
        flag and returns 0, so the registry can never be repopulated
        (2026-09-21: a test file that cleared only the dict left the next
        module an EMPTY fleet it had no way to rebuild — 9 failures in
        tests/test_spine_mesh.py, which passes 62/62 alone).
      * A subsequent re-discovery with modules still cached is LOSSY: the
        `@register_worm` decorators in `expansion.*` never re-fire, so those
        worms are absent from the rebuilt registry (2026-09-21 measurement:
        SpineCycleWorm / MeshArchitectWorm / GossipSyncWorm all gone).
        To restore full contents without the purge use
        `snapshot_registry()` / `restore_registry()` instead.
    """
    global _discovery_done, _vf_already_discovered
    with _registry_lock:
        # Captured BEFORE the clear — see repair_registry().
        _module_targets = _registered_worm_modules()
        WORM_REGISTRY.clear()
        _discovery_done = False
        _vf_already_discovered = False
    # Drop the stale pickle cache like repair_registry() does (2026-10-06):
    # a reset means "rebuild from source". Without this, the next
    # discover_worms() serves the on-disk snapshot while a LATER purge in
    # the same process rebuilds live — the two fleets differ by whatever
    # the cache predates (measured: ClusterWORMZ lost between purges).
    try:
        from CORE.worm_registry_cache import invalidate_cache
        invalidate_cache()
    except Exception as exc:          # noqa: BLE001 - cache invalidation is best-effort
        note_failure("worm_registry:reset-cache-invalidate", exc)
    if purge_modules:
        _purge_worm_modules(_module_targets)


def discovery_completed() -> bool:
    """True once a full discovery pass has populated WORM_REGISTRY.

    Public because it is the predicate a caller needs BEFORE treating
    registry contents as the fleet. Before the first discovery pass the
    registry holds only whatever registered at import time (measured
    2026-09-21: two probe worms from a test module, with `_discovery_done`
    False) — and a save/restore pair that treated those two as the fleet
    then left the process with a two-worm registry and 9 failures in a
    later module.
    """
    return bool(_discovery_done)


def snapshot_registry() -> Dict[str, Dict[str, Any]]:
    """Copy WORM_REGISTRY contents for later `restore_registry()`.

    Entry dicts are copied — including the `tags` set — so mutating a live
    entry afterwards cannot reach into the snapshot. The `class` value is
    shared on purpose: identity is what makes a restore faithful.

    Contents are NOT necessarily the fleet: check `discovery_completed()`
    first if the snapshot is going to be restored as one.
    """
    with _registry_lock:
        return {
            name: {**entry, "tags": set(entry.get("tags") or ())}
            for name, entry in WORM_REGISTRY.items()
        }


def restore_registry(snapshot: Dict[str, Dict[str, Any]]) -> int:
    """Replace registry CONTENTS with a `snapshot_registry()` copy.

    Exists because both alternatives are unusable for an in-process save and
    restore:

      * `reset_registry()` empties the registry and purges `CORE.WORMS.*` and
        `expansion.*` from sys.modules, so every module imported afterwards
        sees a DIFFERENT class object than the one callers already hold.
      * Clearing WORM_REGISTRY alone cannot repopulate (see reset_registry's
        docstring), and a no-purge re-discovery is lossy for `expansion.*`.

    A contents restore does neither: class identity is preserved, nothing is
    re-imported, and `_discovery_done` is left True because the restored
    contents genuinely ARE the populated fleet that flag describes.

    Returns:
        Number of entries in the restored registry.
    """
    global _discovery_done
    with _registry_lock:
        WORM_REGISTRY.clear()
        WORM_REGISTRY.update({
            name: {**entry, "tags": set(entry.get("tags") or ())}
            for name, entry in snapshot.items()
        })
        _discovery_done = bool(WORM_REGISTRY)
        return len(WORM_REGISTRY)


# Auto-discover on import
def _auto_discover():
    try:
        discover_worms()
    except Exception as exc:          # noqa: BLE001 - import must never fail here
        note_failure("worm_registry:auto-discover", exc)


# Don't auto-discover at import time (let caller decide when)
