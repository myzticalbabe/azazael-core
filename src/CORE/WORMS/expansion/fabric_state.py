# coding: utf-8
"""Fabric state persistence helper — the mesh's operational memory.

Audit 2026-09-15 (finding #7): ``save_spine_state()`` persisted the spine's
*learning* (topology map, homeostat ledger, Q-weights, scars, policy journal,
gossip trust, anomaly model) but nothing persisted the fabric natures'
*operational* state — routing tables, elected leader + term, quorum tallies,
federated client submissions, model shard catalogue, endpoint loads. All of it
lived in per-instance dicts cached in ``ClusterWORMZ.instances``, so it was
lost on every new ``ClusterManager()`` (i.e. every separate CLI/script
process) and on restart. The mesh remembered how it felt and forgot what it
knew.

Why a helper instead of a base class: the fabric worms are plain classes with
existing ``__init__`` bodies and no common base. Requiring them to inherit a
mixin would mean touching MRO and ``super().__init__`` across 13 classes;
two one-line calls per class is smaller and more obvious at the call site.

Why the store lives in ``CORE.spine_registry``: the fabric modules live in
``CORE.WORMS.expansion.*`` and ``spine_registry`` must not import them (cycle).
The worms publish *through* the store instead, which also means fabric state
rides the existing persistence + signed-gossip payload for free.

Usage in a fabric worm::

    from CORE.WORMS.expansion.fabric_state import hydrate, publish

    PROVIDER = "MeshRouterWorm"
    _STATE_ATTRS = ("_routes",)

    def __init__(self):
        self._routes = {}
        hydrate(self, PROVIDER, _STATE_ATTRS)

    def add_route(self, ...):
        ...
        publish(self, PROVIDER, _STATE_ATTRS)

Hydration is additive (``dict.update`` / ``list.extend`` / ``set.update``) so a
half-populated instance keeps what it already had; publish is a full snapshot.
Both are best-effort and never raise — losing persistence must not break the
mesh's ability to run.
"""

from typing import Any, Iterable, Optional, List
import os

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["hydrate", "publish", "fabric_state_enabled", "cap_list", "cap_map"]


def cap_list(seq: Any, limit: int) -> Any:
    """Trim an in-place list to its NEWEST `limit` items. Returns the list.

    Audit 2026-09-15 (finding #8): the spine bounded its own collections
    (``_SPINE_MAP_MAX``, ``_SPINE_EVENTS_MAX``) but the fabric bounded
    nothing, so a long-lived heartbeat process grew DLQs, partition
    histories, route tables and endpoint registers without limit. This is
    the shared eviction helper; ``limit <= 0`` is a no-op (callers that
    want an unbounded list just pass 0). Never raises.
    """
    try:
        if isinstance(seq, list) and limit > 0 and len(seq) > limit:
            del seq[:len(seq) - limit]
    except Exception as exc:
        note_failure("fabric_state:L63", exc)
    return seq


def cap_map(d: Any, limit: int) -> Any:
    """Trim an in-place dict to its NEWEST-INSERTED `limit` keys.

    Eviction is insertion-order (Python dicts are ordered), which is the
    right policy for append-shaped stores (routes, shards, endpoints,
    beacons): the oldest observation is the least informative. Never raises.
    """
    try:
        if isinstance(d, dict) and limit > 0 and len(d) > limit:
            for k in list(d)[:len(d) - limit]:
                d.pop(k, None)
    except Exception as exc:
        note_failure("fabric_state:L79", exc)
    return d


def _spine() -> Optional[Any]:
    try:
        from CORE import spine_registry
        return spine_registry
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("fabric_state:L88", exc)
        return None


def fabric_state_enabled() -> bool:
    """Fabric persistence is on unless AZAZAEL_NO_FABRIC_STATE is set.

    The escape hatch exists for test suites that assert on a worm's *empty*
    initial state: set the variable before import to disable both directions.
    """
    return not os.environ.get("AZAZAEL_NO_FABRIC_STATE")


def _jsonable(obj: Any, depth: int = 0) -> Any:
    """Coerce state into JSON-safe primitives (sets/tuples -> lists)."""
    if depth > 6:
        return str(obj)[:200]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)[:200]


def hydrate(instance: Any, provider: str, attrs: Iterable[str]) -> int:
    """Restore named attributes from the persisted fabric state.

    Returns the number of attributes restored (0 when nothing was saved).
    Additive: existing dict/list/set contents are kept, saved contents are
    merged in. Never raises.
    """
    if not fabric_state_enabled():
        return 0
    spi = _spine()
    if spi is None:
        return 0
    try:
        state = spi.get_fabric_state(str(provider))
    except Exception as exc:
        note_failure("fabric_state:L129", exc)
        return 0
    if not isinstance(state, dict):
        return 0
    restored = 0
    for attr in attrs:
        if attr not in state:
            continue
        try:
            cur = getattr(instance, attr, None)
            saved = state[attr]
            if isinstance(cur, dict) and isinstance(saved, dict):
                cur.update(saved)
            elif isinstance(cur, set) and isinstance(saved, (list, set, tuple)):
                cur.update(saved)
            elif isinstance(cur, list) and isinstance(saved, list):
                cur.extend(saved)
            else:
                setattr(instance, attr, saved)
            restored += 1
        except Exception as exc:
            note_failure("fabric_state:L149", exc)
            continue
    return restored


def publish(instance: Any, provider: str, attrs: Iterable[str]) -> bool:
    """Snapshot named attributes into the persisted fabric state.

    Returns True on success. Never raises.
    """
    if not fabric_state_enabled():
        return False
    spi = _spine()
    if spi is None:
        return False
    state: dict = {}
    for attr in attrs:
        try:
            val = getattr(instance, attr, None)
        except Exception as exc:
            note_failure("fabric_state:L168", exc)
            continue
        if val is None:
            continue
        if isinstance(val, (dict, list, set, frozenset, tuple)):
            state[attr] = _jsonable(val)
        elif isinstance(val, (str, int, float, bool)):
            state[attr] = val
    try:
        spi.publish_fabric_state(str(provider), state)
        return True
    except Exception as exc:
        note_failure("fabric_state:L179", exc)
        return False
