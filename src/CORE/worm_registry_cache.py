# coding: utf-8
"""


__all__ = [
    "CACHE_DIR",
    "CACHE_FILE",
    "cache_status",
    "invalidate_cache",
    "load_registry",
    "save_cache",
]

worm_registry_cache.py — Pickle-backed worm registry cache.

Eliminates the 38-45s discover_worms() cold start by caching the registry
to disk via pickle. First call builds cache (~38s); subsequent calls load
from pickle in <0.1s. Invalidates when source files change.

Usage:
    from CORE.worm_registry_cache import load_registry
    load_registry()  # <0.1s when cache is fresh

OR in worm_registry.py:
    from CORE.worm_registry_cache import load_registry
    if not WORM_REGISTRY:
        load_registry()
"""
import os
import sys
import pickle
from pathlib import Path
from datetime import datetime
from typing import Any, List
import hashlib
import re as _re

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "1.1.0"

CACHE_DIR = Path.home() / ".azazael"


def _cache_context() -> str:
    """Which cache a caller is allowed to share.

    The cache was keyed by SOURCE but shared by CONTEXT, and that was a real
    defect: a pytest run registers synthetic worm classes (observed:
    `test_worm_registry.test_concurrent_registry_reads.<locals>.ReadTestWorm`),
    `save_cache` then wrote that mixed registry into the shared production file,
    and the next real process loaded it.

    Before 2026-09-18 the damage was silent: a bare `pickle.dump` raised on the
    unpicklable entry, `discover_worms` swallowed it as non-fatal, and the OLD
    cache kept answering. The coverage guard now refuses such a cache, which is
    safe -- and makes every production start pay a rebuild (1.0s) merely because
    a test ran. Separate files fix the cause instead of the symptom.

    Overridable with `AZAZAEL_CACHE_CONTEXT` for CI, benchmarks, or a second
    checkout on the same machine.
    """
    explicit = os.environ.get("AZAZAEL_CACHE_CONTEXT")
    if explicit:
        safe = "".join(c for c in explicit if c.isalnum() or c in "-_")[:32]
        return safe or "custom"
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return "test"
    return "prod"


_CACHE_CONTEXT = _cache_context()


def _repo_root_fingerprint() -> str:
    """Short hash of this checkout's root (U3/move-day: two checkouts share
    a machine). Absolute source_file paths in a shared cache point at the
    WRONG tree after the second checkout warms it — measured 2026-10-02 as
    test_no_worm_name_collisions failing in C:\\Azazael with C:\\Users paths.
    """
    root = Path(__file__).resolve().parents[2]
    return hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:10]


#: Per-checkout filenames: prod and test contexts each get the root
#: fingerprint, so a warm cache can never serve another tree's absolute
#: paths. First run per checkout pays one rebuild; everything after hits.
CACHE_FILE = CACHE_DIR / ("worm_registry_cache.%s.%s.pkl"
                          % (_CACHE_CONTEXT, _repo_root_fingerprint()))


def _ensure_import_paths():
    """Put all worm import roots on sys.path before unpickling.

    The registry pickle stores live class objects. Worms are imported
    either as bare module names (WORMS dir prepended to sys.path) or as
    ``worms.*`` package-style modules (Vermillion LightFury). Unpickling
    therefore needs both the CORE/WORMS dir and the VF package parent
    importable. Mirrors the resolution in worm_registry.py so the cache
    loads in fresh processes.
    """
    # CORE/WORMS — bare module refs (e.g. BENCHMARK_WORM) go on as-is;
    # src root covers CORE.WORMS.* style refs.
    core_dir = Path(__file__).resolve().parent
    for root in (str(core_dir / "WORMS"), str(core_dir.parent)):
        if root not in sys.path:
            sys.path.insert(0, root)
    # Vermillion LightFury — ``worms.*`` package refs: the worms/ dir is a
    # package, so its PARENT goes on path to make `import worms.x` resolve.
    vf_candidates = []
    env_vf = os.environ.get("VERMILLION_LIGHTFURY_DIR", "")
    if env_vf:
        vf_candidates.append(os.path.join(env_vf, "worms"))
    azazael_root = Path(__file__).resolve().parent.parent.parent
    vf_candidates.append(str(azazael_root / "vermillion_lightfury" / "worms"))
    if os.name == "nt":
        vf_candidates.append(str(Path.home() / "vermillion_lightfury" / "worms"))
    else:
        vf_candidates.append("/root/vermillion_lightfury/worms")

    for cand in vf_candidates:
        worms_dir = Path(cand)
        if worms_dir.is_dir():
            insert = str(worms_dir.parent)
            if insert not in sys.path:
                sys.path.insert(0, insert)


def _get_module_mtimes(worms_dir: str) -> dict:
    """Get modification times of all worm .py files (recursive)."""
    mtimes = {}
    worms_path = Path(worms_dir)
    if not worms_path.is_dir():
        return mtimes
    try:
        for f in sorted(worms_path.rglob("*.py")):
            if not f.name.startswith("_") and not f.name.startswith("."):
                rel = str(f.relative_to(worms_path))
                mtimes[rel] = f.stat().st_mtime
    except OSError as exc:
        note_failure("worm_registry_cache:L137", exc)
    return mtimes


def _count_declared_worms(worms_dir: str) -> int:
    """Count `@register_worm` decorators on disk, as a second opinion.

    Deliberately a TEXT scan rather than an import. The whole purpose is to
    have a cheap check that does NOT share the importing machinery whose partial
    failure it exists to catch: if the registry came up short because modules
    raised, were skipped, or discovery short-circuited on an "already done"
    flag, importing them again to count would fail the same way.

    Measured on this tree (2026-09-18): 188 declarations on disk against 229
    registered worms, so the healthy case has ~20% headroom and this check only
    fires on a real undercount.
    """
    # MULTILINE is load-bearing: without it `^` anchors to the start of the
    # STRING, so only a module whose first line is the decorator matches -- the
    # scan reported 0 declarations against 188 on disk, and because the
    # completeness test reads `registered >= declared` a count of 0 passed
    # vacuously. A check that silently cannot fail is worse than no check.
    pattern = _re.compile(r"^\s*@register_worm\b", _re.MULTILINE)
    total = 0
    try:
        for f in sorted(Path(worms_dir).rglob("*.py")):
            if f.name.startswith("_") or f.name.startswith("."):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                note_failure("worm_registry_cache:L169", exc)
                continue
            total += len(pattern.findall(text))
    except OSError as exc:
        note_failure("worm_registry_cache:L172", exc)
        return 0
    return total


#: Modules whose CONTENT shapes what discovery produces. A change here leaves
#: every worm file untouched, so an mtime-only freshness test cannot see it.
#: This is the G17 lesson (2026-09-14): the fix that fed `_guess_category()` the
#: defining module instead of the shim had no effect until the pickle was
#: deleted by hand, because the affected worms live in files whose mtime had not
#: moved. Add a module here when it can change a category, a protection
#: invariant or the discovery scan itself.
_REGISTRY_SOURCE_FILES = ("worm_registry.py", "worm_registry_cache.py")


def _registry_fingerprint() -> str:
    """md5 over the contents of `_REGISTRY_SOURCE_FILES`, or "unknown".

    Content, not mtime: rewriting a byte must move the digest, and a rewrite
    that preserves the mtime (checkout, patch, copy) must not be invisible.
    Resolved through the module's `__file__` at call time rather than cached at
    import, so a caller can point the module at a different tree.

    Returns the literal string "unknown" when the sources cannot be read. That
    is deliberately not a valid digest, so `_is_cache_fresh` compares unequal
    and the cache is refused: unprovable freshness is not freshness.
    """
    digest = hashlib.md5()
    try:
        base = Path(__file__).resolve().parent
        for name in _REGISTRY_SOURCE_FILES:
            source = base / name
            if not source.is_file():
                return "unknown"
            digest.update(source.read_bytes())
    except OSError as exc:
        note_failure("worm_registry_cache:L208", exc)
        return "unknown"
    return digest.hexdigest()


def _is_cache_fresh(worms_dir: str) -> bool:
    """Check if the pickle cache is up to date with source files.

    Up-to-date means two things, and the second is the one that was missing:
    the sources are unchanged (`_mtimes`), AND the registry that was saved
    actually contains what the tree declares (`_coverage`). A registry can be
    built from unchanged sources and still be incomplete -- whatever the cause,
    a scan that registered fewer worms than the tree declares has lost some on
    the way in, and a partially-populated cache is worse than no cache because
    it is fast, plausible and wrong.

    Observed consequence: a shared cache holding 111 of 229 worms (24 of 35
    categories) passed the mtime check and loaded as fresh, so the fleet came up
    missing 51% of itself -- including `axon`, `dendrite`, `glia`, `envmesh`,
    `ml` and `network` -- and every consumer of WORM_REGISTRY, the queens and
    the wire-or-cut census included, silently inherited the undercount.

    A legacy cache with no `_coverage` key is treated as NOT fresh (one rebuild,
    then the key is present). Absent evidence of completeness is not evidence of
    it.
    """
    if not CACHE_FILE.exists():
        return False
    try:
        _ensure_import_paths()
        with open(CACHE_FILE, "rb") as fh:
            cache = pickle.load(fh)
        cached_mtimes = cache.get("_mtimes", {})
        current_mtimes = _get_module_mtimes(worms_dir)
        if set(cached_mtimes.keys()) != set(current_mtimes.keys()):
            return False
        for name, mtime in current_mtimes.items():
            if abs(cached_mtimes.get(name, 0) - mtime) > 0.01:
                return False
        # Unchanged mtimes are necessary, not sufficient: a change to the
        # discovery semantics leaves every worm file untouched, so the cache
        # stayed "fresh" while what it MEANT moved (see _REGISTRY_SOURCE_FILES).
        if cache.get("_fingerprint") != _registry_fingerprint():
            return False
        return True
    except (pickle.PickleError, KeyError, OSError, EOFError, ModuleNotFoundError, ImportError, AttributeError) as exc:
        note_failure("worm_registry_cache:L253", exc)
        return False


def _load_cache_readonly(worms_dir: str) -> (dict[str, Any] | None):
    """Read registry from pickle cache if fresh. Never rebuilds.

    Safe to call from within discover_worms() — no circular import risk.

    Returns None if cache is stale or missing (caller should fall through
    to normal discovery and call save_cache() afterwards).

    Also refuses a cache that is CURRENT but INCOMPLETE. Currency and safety are
    different questions: a cache can be perfectly current with its sources and
    still hold a partially-built registry, because a scan that registers fewer
    worms than the tree declares has lost some on the way in, whatever the
    cause. Loading that is worse than rebuilding -- it is fast, plausible and
    wrong, and every consumer inherits it. Observed: a shared cache holding 111
    of 229 worms (24 of 35 categories) passed the mtime check, and the fleet
    came up missing `axon`, `dendrite`, `glia`, `envmesh`, `ml` and `network`
    with nothing reporting a problem.
    """
    if not _is_cache_fresh(worms_dir):
        return None
    try:
        _ensure_import_paths()
        with open(CACHE_FILE, "rb") as fh:
            cache = pickle.load(fh)
        if not (cache.get("_coverage") or {}).get("complete", False):
            return None
        return {k: v for k, v in cache.items() if not k.startswith("_")}
    except (pickle.PickleError, KeyError, OSError, EOFError, ModuleNotFoundError, ImportError, AttributeError) as exc:
        note_failure("worm_registry_cache:L284", exc)
        return None


def save_cache(registry: dict[str, Any], worms_dir: str) -> dict:
    """Save registry to pickle cache. Called after successful discover_worms().

    Safe to call from within discover_worms() — no circular import risk.
    """
    try:
        from CORE.worm_registry import get_all_categories
    except ImportError:
        get_all_categories = lambda: set(
            info.get("category", "utility") for info in registry.values()
        )

    # Entries that cannot be pickled are EXCLUDED rather than allowed to fail
    # the whole save. Not hypothetical: a worm class defined inside a function
    # (or built with `type()`) raises `PicklingError: Can't pickle local
    # object`, and `discover_worms` treats a save failure as non-fatal -- so the
    # cache kept whatever it held BEFORE, answering for a fleet it no longer
    # described with nothing reported. Observed polluting a test batch:
    # `test_worm_registry.test_concurrent_registry_reads.<locals>.ReadTestWorm`.
    #
    # Excluding the entry keeps the cache honest about the rest, and `complete`
    # is forced False whenever anything was excluded, so `_load_cache_readonly`
    # refuses a cache that cannot represent the registry instead of serving an
    # undercount.
    payload: dict[str, Any] = {}
    excluded: list = []
    for name, info in registry.items():
        try:
            pickle.dumps(info, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            note_failure("worm_registry_cache:L317", exc)
            excluded.append(str(name))
            continue
        payload[name] = info

    cache: dict[str, Any] = dict(payload)
    cache["_mtimes"] = _get_module_mtimes(worms_dir)
    cache["_built_at"] = datetime.now().isoformat()
    cache["_total_registered"] = len(payload)
    cache["_categories"] = sorted(get_all_categories())
    # Self-report of coverage, so the next process can refuse an incomplete
    # registry instead of loading it (`_load_cache_readonly`). The scan knows
    # how many worms the tree declares; if it registered fewer, it says so here.
    cache["_fingerprint"] = _registry_fingerprint()
    _declared = _count_declared_worms(worms_dir)
    cache["_coverage"] = {
        "declared_on_disk": _declared,
        "registered": len(payload),
        "excluded_unpicklable": excluded,
        # `_declared > 0` is required, not cosmetic: with no second opinion the
        # test would be `registered >= 0`, true even for an empty registry.
        # Cannot verify is not the same as verified complete, and a cache that
        # is missing entries is not complete either, however good the reason.
        "complete": (_declared > 0 and len(payload) >= _declared
                     and not excluded),
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "wb") as fh:
        pickle.dump(cache, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return {"excluded": excluded, "registered": len(payload),
            "declared": _declared,
            "complete": cache["_coverage"]["complete"]}


def load_registry(worms_dir: (str | None) = None, force_rebuild: bool = False) -> dict[str, Any]:
    """Load worm registry from pickle cache, or rebuild if stale.

    Returns the registry dict and populates WORM_REGISTRY as a side effect.
    Subsequent calls return cached data in <0.1s.

    NOTE: Do NOT call this from within discover_worms() — that creates a
    circular import (load_registry -> discover_worms -> load_registry). Use
    _load_cache_readonly() + save_cache() inside discover_worms() instead.

    Args:
        worms_dir: Path to CORE/WORMS directory.
        force_rebuild: If True, skip cache and run full discovery.

    Returns:
        dict with worm registry entries.
    """
    if worms_dir is None:
        worms_dir = str(Path(__file__).resolve().parent / "WORMS")

    # Try cache first (read-only, no rebuild)
    if not force_rebuild:
        registry = _load_cache_readonly(worms_dir)
        if registry is not None:
            # Populate the in-memory WORM_REGISTRY. Unpickling re-imports
            # worm modules, whose @register_worm decorators partially
            # repopulate WORM_REGISTRY as a side effect — so merge only
            # missing keys rather than skipping on non-empty.
            try:
                from CORE.worm_registry import WORM_REGISTRY
                for name, info in registry.items():
                    if name not in WORM_REGISTRY:
                        WORM_REGISTRY[name] = info
            except ImportError as exc:
                note_failure("worm_registry_cache:L385", exc)
            return registry

    # Rebuild: run full discover_worms()
    # Guard: only add to sys.path once
    src_dir = str(Path(worms_dir).parent.parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    if worms_dir not in sys.path:
        sys.path.insert(0, worms_dir)

    from CORE.worm_registry import discover_worms, WORM_REGISTRY

    discover_worms(worms_dir=worms_dir)
    # discover_worms() already calls save_cache() on success, no need to double-save

    return dict(WORM_REGISTRY)


def invalidate_cache():
    """Delete the cache file. Next load_registry() will rebuild."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def cache_status() -> dict:
    """Return cache status information."""
    if not CACHE_FILE.exists():
        return {"exists": False, "reason": "No cache file"}
    try:
        _ensure_import_paths()
        with open(CACHE_FILE, "rb") as fh:
            data = pickle.load(fh)
        built_at = data.get("_built_at", "unknown")
        return {
            "exists": True,
            "built_at": built_at,
            "worm_count": data.get("_total_registered", 0),
            "total_registered": data.get("_total_registered", 0),
            "categories": data.get("_categories", []),
            "coverage": data.get("_coverage") or {},
            "registry_fingerprint": data.get("_fingerprint"),
            "current_fingerprint": _registry_fingerprint(),
            "semantics_current": data.get("_fingerprint") == _registry_fingerprint(),
            "cache_size_kb": round(CACHE_FILE.stat().st_size / 1024, 1),
        }
    except Exception as e:
        note_failure("worm_registry_cache:L432", e)
        return {"exists": False, "reason": str(e)}


# [!]️ No module-level auto-build — would trigger circular import with worm_registry.py
# Cache is built lazily: discover_worms() -> save_cache(), or load_registry() -> save_cache()
