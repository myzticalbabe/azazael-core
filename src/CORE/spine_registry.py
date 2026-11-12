# coding: utf-8
"""
spine_registry — Process-wide shared state for the neural mesh spine.

Two shared organs, one module, no heavy imports:

1. SPINE_MAP  — the live topology snapshot that SpineMapperWorm publishes
   and any consumer (dashboard, CLI, Red Queen) can read. Keyed by
   "env:worm" so multi-env gossip can merge without collisions.

2. HOMEOSTAT — the cross-process feedback ledger. ClusterWORMZ.execute()
   records every worm outcome here (record_outcome()); HomeostatWorm consumes
   it and proposes cooldown/priority adjustments. This closes the loop that
   previously ended at "results returned to caller and dropped".

Design constraints:
- stdlib only, no PAMS/MemoryBus dependency at module level (avoid cycles)
- all mutable state behind threading.RLock (3.14t free-threading safe)
- bounded memory: outcome history and events are capped
- pure data: no worm classes registered here (registry hygiene)

Usage:
    from CORE.spine_registry import record_outcome, get_homeostat_view
    record_outcome("mesh", "MeshRouterWorm", True, 12.3)
    view = get_homeostat_view()
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "1.0.0"

# ── SPINE MAP (topology snapshot) ────────────────────────────────────
# key: "env:worm" -> node record
_SPINE_MAP: Dict[str, Dict[str, Any]] = {}
_SPINE_LOCK = threading.RLock()
_SPINE_MAP_MAX = 4096  # bound memory under gossip storms
_SPINE_EVENTS: List[Dict[str, Any]] = []
# Sized against the observed per-cycle volume, not picked as a round number.
# Measured 2026-09-21: ONE observe cycle emits ~200 spine events (175 of them
# `nature_edge` alone), so a cap of 200 held exactly one cycle of history -- and
# therefore the log could never show anything ACROSS cycles. That is not a
# theoretical concern: it evicted the heartbeat's own `spine_cycle` beat record
# within one cycle of writing it, which is why a refused beat stayed effectively
# invisible even after it started being recorded. 5000 holds ~25 cycles at the
# measured rate, and each entry is a small dict.
_SPINE_EVENTS_MAX = 5000

# ── HOMEOSTAT (feedback ledger) ──────────────────────────────────────
# key: "nature:worm" -> {"successes": int, "failures": int, ...}
_HOMEOSTAT: Dict[str, Dict[str, Any]] = {}
_HIST_MAX = 20  # per-worm rolling duration history
_OUTCOME_EVENTS_MAX = 200

# ── OUTCOME RING (striped, append-only, merged on read) ──────────────
# The ring used to be one shared list appended under the global lock — the
# purest LongAdder case in the module: append-only, never read-modify-write,
# order-insensitive for every consumer. It now has its own lock stripes, so
# a write takes an outcome lock only when another thread picked the same
# stripe (8 stripes — 8 consecutive thread idents, which the interpreter
# hands out sequentially, so in practice each thread keeps its own).
#
# Trade, stated plainly: rings are per-stripe, so worst-case in-memory
# history is 8 × _OUTCOME_EVENTS_MAX events instead of 200. Every *read*
# is still merged, re-sorted and trimmed to exactly _OUTCOME_EVENTS_MAX, so
# the public bound and the single-threaded history depth are unchanged
# (asserted in test_spine_mesh_ledger_striping.py).
_OUTCOME_STRIPE_COUNT = 8
_OUTCOME_LOCKS: tuple = tuple(threading.RLock() for _ in range(_OUTCOME_STRIPE_COUNT))
_OUTCOME_RINGS: tuple = tuple([] for _ in range(_OUTCOME_STRIPE_COUNT))


def _outcome_stripe() -> tuple:
    """``(lock, ring)`` for the calling thread's stripe."""
    idx = threading.get_ident() % _OUTCOME_STRIPE_COUNT
    return _OUTCOME_LOCKS[idx], _OUTCOME_RINGS[idx]


def _append_outcome_event(event: Dict[str, Any]) -> None:
    """Append to this thread's outcome ring, self-capping. Lock is per-stripe."""
    lock, ring = _outcome_stripe()
    with lock:
        ring.append(event)
        if len(ring) > _OUTCOME_EVENTS_MAX:
            del ring[:len(ring) - _OUTCOME_EVENTS_MAX]


def _merged_outcome_events() -> List[Dict[str, Any]]:
    """All rings merged, oldest-first, trimmed to _OUTCOME_EVENTS_MAX.

    The trim is what preserves the documented public bound: callers can
    never observe more than _OUTCOME_EVENTS_MAX events regardless of how
    many stripes are live.
    """
    merged: List[Dict[str, Any]] = []
    for lock, ring in zip(_OUTCOME_LOCKS, _OUTCOME_RINGS):
        with lock:
            merged.extend(ring)
    merged.sort(key=lambda e: e.get("at", ""))
    if len(merged) > _OUTCOME_EVENTS_MAX:
        del merged[:len(merged) - _OUTCOME_EVENTS_MAX]
    return merged


def _clear_outcome_events() -> None:
    """Drop every ring's contents (reset path)."""
    for lock, ring in zip(_OUTCOME_LOCKS, _OUTCOME_RINGS):
        with lock:
            ring.clear()


def _outcome_event_count() -> int:
    """Raw (untrimmed) event count across stripes, for health reporting."""
    return sum(len(ring) for ring in _OUTCOME_RINGS)


# ── STRIPED LEDGER LOCKS (F9: stop serialising the hot path) ────────
# Audit F9: "every outcome takes a global RLock, and the reflex arc does a
# registry lookup inside it." True — and it is a *scale* defect: at 224 worms
# it is fine, and it is the first thing that breaks when the fleet 10×'s.
#
# Instead of LongAdder-style per-thread counters (which would make the
# learning numbers order-dependent and therefore *different*), the ledger
# uses **key striping** — the ConcurrentHashMap shape. Every key
# ("nature:worm") still has exactly ONE authoritative record mutated under
# exactly one lock, so every learned value is bit-identical to the
# single-lock version (asserted in test_spine_mesh_ledger_striping.py), but
# two different worms no longer contend with each other. Only same-key
# updates serialise, which is not an option to remove: it is a
# read-modify-write.
#
# LOCK ORDER INVARIANT — "holding any Q stripe implies holding every
# homeostat stripe". Writers acquire homeostat-stripe(key) then
# Q-stripe(key); readers acquire the whole barrier (`_HOMEOSTAT_LOCK`,
# all homeostat stripes then all Q stripes). A writer can therefore only be
# blocked by a peer writer (bounded work) or by a reader that holds no
# homeostat stripe — never by a reader holding homeostat stripes. That is
# what makes this deadlock-free rather than merely unlikely.
_STRIPE_COUNT = 16
_HOMEOSTAT_STRIPES: tuple = tuple(threading.RLock() for _ in range(_STRIPE_COUNT))
_Q_STRIPES: tuple = tuple(threading.RLock() for _ in range(_STRIPE_COUNT))

# ── SCAR LOCK (reflex state, previously unguarded) ──────────────────
# _SCARS was mutated from record_outcome() and read from q_order()/views
# with no lock at all. That was survivable only because those mutations
# happened inside the global ledger lock. Now that writers use stripes, the
# scar table gets an explicit lock rather than an accidentally-inherited one.
_SCAR_LOCK = threading.RLock()


def _stripe_index(key: str) -> int:
    """Stable stripe for a ledger key.

    ``hash()`` on str is randomised per process (PYTHONHASHSEED) but stable
    within one, which is all a stripe assignment needs; no ordering and no
    persistence depends on it.
    """
    return hash(key) % _STRIPE_COUNT


def _homeostat_stripe(key: str) -> threading.RLock:
    """The one lock that guards ``_HOMEOSTAT[key]``."""
    return _HOMEOSTAT_STRIPES[_stripe_index(key)]


def _q_stripe(key: str) -> threading.RLock:
    """The one lock that guards the Q-table entries for ``key``."""
    return _Q_STRIPES[_stripe_index(key)]


class _LedgerBarrier:
    """Reader/resetter gate: acquires EVERY stripe, homeostat then Q.

    Keeps the old ``with _HOMEOSTAT_LOCK:`` spelling working — and meaning
    "no writer anywhere may run while I read" — without putting 32 acquires
    on the write path. Every multi-key read (views, snapshots, persist,
    reset, gossip merge) uses it; only the two hot writers bypass it.
    """

    __slots__ = ("_stripes",)

    def __init__(self, stripes: tuple) -> None:
        self._stripes = stripes

    def __enter__(self) -> "_LedgerBarrier":
        for lock in self._stripes:
            lock.acquire()
        return self

    def __exit__(self, *exc: Any) -> Literal[False]:
        for lock in reversed(self._stripes):
            lock.release()
        return False


# Barrier spanning both tables, in the canonical order.
_HOMEOSTAT_LOCK = _LedgerBarrier(_HOMEOSTAT_STRIPES + _Q_STRIPES)

# ── Q-WEIGHT BRAINSTEM ─────────────────────────────────────────────
# Shared, process-wide reinforcement table keyed "nature:worm". Every
# record_outcome() auto-reinforces here (the fast signal), and
# ClusterWORMZ.execute() reads it to order execution: healthy worms
# run earlier, failing ones later. Distinct from SynapseWeightWorm's
# private table, which is the batch-learned routing substrate; this is
# the always-on brainstem reflex.
_Q_WEIGHTS: Dict[str, float] = {}
_Q_ALPHA_BASE = 0.2
_Q_MIN, _Q_MAX = -1.0, 1.0
# Adaptive alpha: per-key variance tracking for G2
_Q_VARIANCE: Dict[str, float] = {}  # key -> running variance of Q-values
_Q_UPDATE_COUNTS: Dict[str, int] = {}  # key -> number of OBSERVED updates
# Key -> number of SIMULATED updates (dreams). Separate from the count above
# because the two are not the same claim, and the distinction became
# load-bearing the moment `q_effective` started ranking on the count.
# `dream_reinforce()` documents that it does not touch the live ledger -- which
# was true of `_HOMEOSTAT` and false of `_Q_UPDATE_COUNTS`, so a key that had
# only ever been DREAMED about reported `source="measured"` and earned a
# shrinkage weight from evidence that no worm ever produced. A simulation must
# not be able to create a routing preference out of nothing; it may only nudge
# a value that real observation already established.
_Q_DREAM_COUNTS: Dict[str, int] = {}

# ── EVIDENCE SHRINKAGE (playing-field TODO G1) ──────────────────────
# The EMA reaches its clip in ~22 pulls at the default adaptive alpha
# (variance 0.25 -> alpha 0.4), and the mesh's live keys carry 21-31 pulls.
# 29 of 76 keys were measured pinned at |q| > 0.999999, up from 3 at the
# previous save: the table saturates inside a single process lifetime, and
# `q_order()` -- which decides which worm a nature runs first -- was sorting
# primarily on that plateau. Ranking on `q * n/(n+K)` instead restores a
# strict ordering, because the factor scales with evidence: a 4-pull -1.0
# key contributes -0.33, a 40-pull -0.9 key contributes -0.75. Shrinkage is
# the standard Laplace/Jeffreys answer to "how much do I trust a mean from n
# observations", and it makes the pull count load-bearing rather than
# advisory -- so losing one is visibly a lost RANKING, not a silent 0.0.
_Q_SHRINK_K = 8.0

# ── FORGETTING CURVE (playing-field TODO G2, fixed 2026-09-16) ─────────
# A penalty with no timestamp is a life sentence: a route that failed 27/27
# during a since-fixed bug stays at the floor forever. Staleness shrinks the
# effective value toward neutral with a 7-day half-life, alongside the
# evidence shrinkage in q_effective_from. Fresh evidence re-asserts instantly
# (last_at updates on every outcome); unknown age counts as fresh so the
# G4 no-backfill keys are never punished for missing timestamps.
_Q_STALE_HALF_LIFE_S = 7.0 * 86400.0

# ── CONFIDENCE-AWARE SELECTION (audit 2026-09-16) ───────────────────
# `_Q_VARIANCE` has been maintained by Welford's algorithm on EVERY update
# since adaptive alpha was added, and read by nothing except the two lines
# that set alpha. It is the only uncertainty estimate in the module and it was
# treated as a tuning input rather than as information.
#
# That matters because the value scale saturates before the interesting
# range. Measured on live traffic (60 pulls each):
#
#     true rate 0.95 -> q_eff = +0.8791
#     true rate 1.00 -> q_eff = +0.8824     (gap: 0.0032 = 0.32% of the scale)
#
# so a 95% arm and a perfect arm are indistinguishable to any selector ranking
# on the value. The uncertainty separates them immediately: the applied signal
# for a Bernoulli(1.0) key is always 0 (sigma = 0.0000) while for Bernoulli(0.95)
# it is 0.436. The clip destroys the top of the VALUE range; the variance is
# what survives it.
#
# `c` is the bandit-style exploration constant: a lower confidence bound is the
# standard conservative score for choosing among arms, and it subsumes the
# shrinkage heuristic rather than competing with it (a thin-evidence key is
# also a wide-interval key).
_Q_LCB_C = 1.0

# Bound on the brainstem's own table growth. Every other table in this module
# carries a _MAX and prunes; the four Q tables did not, and they are persisted
# AND gossiped, so an unbounded key space grows the state file and every wire
# payload with it. Pruning is reported as a spine event, never silent.
_Q_KEYS_MAX = 4096

# ── TOMBSTONES (deletion in a monotone store) ───────────────────────
# Every read path in this module MERGES (adopt-if-absent; counters take MAX)
# and every write merges the disk in first. That is deliberate -- it is what
# makes N save-then-exit processes compose instead of clobbering each other --
# but it means the store is MONOTONE: it can express "this key has a value"
# and cannot express "this key is gone".
#
# Observed, not theorised. `purge_fixtures()` removed 56 entries across five
# tables and the very next `save_spine_state()` restored all 14 fixture keys,
# because the save folds the disk in before writing and the disk still had
# them. So deletion was impossible by construction, and a "purge" reported
# success while changing nothing durable -- which is the same defect class as
# the four silent defects from the previous audit, in the store itself.
#
# A tombstone is the standard answer for a monotone CRDT-like store: record the
# removal as a fact, so every merge that sees an absent key can ask whether it
# was never written or deliberately removed. Bounded, persisted, and checked on
# both read paths.
_PURGED_KEYS: Dict[str, str] = {}
_PURGED_MAX = 4096

# Evidence FLOORS, the same shape of fact as a tombstone but for a REDUCTION.
#
# Every read path merges counters with `max()`, which is what makes N parallel
# hourly jobs composable instead of clobbering each other. It also means a
# counter can never go down. `invalidate_q()` exists to discount evidence whose
# cause was fixed -- and it was a complete no-op end-to-end: it set the count
# in memory, journalled `q_invalidated`, emitted a spine event, and then
# `save_spine_state()`'s `_merge_disk_state()` folded the old number back over
# it. Reproduced 2026-09-29 on an isolated store:
#
#     invalidate_q said : evidence_to = 0
#     in memory now     : n = 0
#     after save (mem)  : n = 100
#     on DISK           : n = 100
#
# So the journal and the monitors counted invalidations that did not happen.
# A tombstone is the standard answer for a monotone store, and it already
# exists for removal; this is the same idea for a reduction. Bounded, persisted,
# and honoured on all three read paths (disk, load, gossip) so a peer cannot
# resurrect a discounted count either.
_Q_EVIDENCE_FLOOR: Dict[str, int] = {}
_Q_EVIDENCE_FLOOR_MAX = 4096

# The same idea one level down, for the homeostat's own counters.
#
# 2026-09-29. Three monotone stores, three resets that reported success and
# did not happen: `purge_fixtures` (fixed by tombstoning), `invalidate_q`
# (fixed by the evidence floor above), and now `successes` / `failures` --
# still bare. `_merge_disk_state` adopts those two with `max()`, so a reset to
# zero is less than the value already on disk and loses on the very next save.
# MEASURED on the live ledger: the 09-29 purge set
# `route:windows>mesh` to 0/0, journalled "successes: 5, failures: 0" as
# removed, and the next load reported 5/0 again with the journal still
# claiming the reset. The false data survived its own removal AND the audit
# trail asserted it had gone, which is worse than either alone.
#
# A watermark on the two counter fields, mirroring `_Q_EVIDENCE_FLOOR` rather
# than inventing a second mechanism. When a key has an entry here, the
# incoming disk value for those fields is not admissible: the floor is a
# record that the counter was deliberately reset, and max()-ing against a
# value the operator has already disowned is how the reset disappears.
# A genuine new observation still raises the counter, because
# `record_outcome()` writes through and never consults this.
_COUNTER_FLOOR: Dict[str, Dict[str, int]] = {}
_COUNTER_FLOOR_MAX = 4096


def _adopt_counter(key: str, field: str, incoming: int, current: int) -> int:
    """Monotone counter adoption, clamped by any recorded counter floor.

    Same shape as `_adopt_evidence` and for the same reason: the fold-in runs
    before every write, so without a recorded floor a reset is indistinguishable
    from stale data and the larger of the two always wins.
    """
    try:
        n = int(incoming)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L357", exc)
        return current
    cur = max(int(current), n)
    floor = (_COUNTER_FLOOR.get(key) or {}).get(field)
    if floor is not None and cur > int(floor):
        cur = int(floor)
    return cur


def _adopt_evidence(key: str, incoming: int) -> int:
    """Monotone count adoption, clamped by any recorded evidence floor.

    Returns the count the store should hold for `key`. The floor is a high
    WATERMARK on how far the count may be restored, not a lock: a genuinely
    newer observation raises the count above it on the next real update,
    because `record_outcome()` writes through `_bump_evidence` rather than
    through here.
    """
    try:
        n = int(incoming)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L377", exc)
        return _Q_UPDATE_COUNTS.get(key, 0)
    cur = max(_Q_UPDATE_COUNTS.get(key, 0), n)
    floor = _Q_EVIDENCE_FLOOR.get(key)
    if floor is not None and cur > int(floor):
        cur = int(floor)
    return cur


def _purged_blocked(key: str) -> bool:
    """True when `key` was deliberately removed and must not be re-adopted."""
    return key in _PURGED_KEYS


# A tombstone was enforced on every MERGE path (both disk loads, the gossip
# merge, and `audit_state`) -- and only there. That made the deletion hold
# against the filesystem and against peers but NOT against this process: a
# live `record_outcome` for a purged key re-created it in the very tables the
# purge had cleared, and the next save wrote it back out. The guard therefore
# belongs on the WRITE path too, one per table at the single choke point each
# table has -- `_q_observe_unlocked` for Q, `record_outcome` for the homeostat.
#
# Blocked writes are COUNTED, never silently dropped. A purge that keeps having
# to refuse writes is a fixture still being exercised, which is exactly the
# signal an operator wants (the same reasoning as `silence.py`: a swallowed
# no-op is indistinguishable from a healthy key).
_PURGED_BLOCKED_WRITES: Dict[str, int] = {}


def _note_blocked_write(key: str) -> None:
    """Record that an observation was refused by a tombstone. Never raises."""
    try:
        _PURGED_BLOCKED_WRITES[key] = _PURGED_BLOCKED_WRITES.get(key, 0) + 1
    except Exception as exc:
        note_failure("spine_registry:L410", exc)
        pass


def purged_blocked_writes() -> Dict[str, int]:
    """Keys whose writes a tombstone refused, and how many times each.

    Non-zero means a declared-removed key is still being exercised by live
    code. That is not a fault in the tombstone; it is a finding about the
    caller.
    """
    return dict(_PURGED_BLOCKED_WRITES)

# ── ROUTE WEIGHT BOUNDS (playing-field TODO G5) ─────────────────────
# `route()` and `get_route_usage()` each hardcoded `clamp(base_w * (1+q),
# 0.1, 5.0)`. With q in [-1, 1] the factor is [0, 2], so for the only
# registered base_w (1.0) the reachable ceiling is 2.0 -- the declared 5.0
# was unreachable and both docstrings advertised a 50x range the arithmetic
# cannot visit. The bounds now live here and the ceiling is DERIVED from the
# inputs (see `route_weight_range`), so the two cannot drift apart again.
_ROUTE_WEIGHT_MIN = 0.1
_ROUTE_WEIGHT_MAX = 5.0

# ── FIXTURE DECLARATION (playing-field TODO G3) ─────────────────────
# The live store at ~/.azazael/spine_state.json carries synthetic keys in the
# SAME namespaces as production worms (mesh:BadWorm, mesh:W0..W3, mesh:T0..T2,
# immune:W0/W1, learning:W0/W1). tests/conftest.py documents the cause -- a
# test run against a live 76-record homeostat replaced it with fixtures -- and
# the fixture fixed the mechanism for FUTURE runs. Nothing purged the damage,
# and nothing distinguishes a fabricated key from a measured one, so the
# question "is my brainstem trustworthy" is unanswerable after the fact.
#
# Declared, not inferred. A name-shape heuristic is the wrong tool here: a
# first cut flagged diagnostic:ChaosWorm and learning:EvolutionEngine on the
# grounds that they had no pull count -- both are real worms (expansion/chaos.py,
# expansion/evolution.py). And a "not in WORM_REGISTRY" probe over-flags 48
# keys, because synapse:/route:/aoa_route:/monitor: key on EDGES and SITES, not
# worms. Importing the registry here is also forbidden (this module is
# stdlib-only by design). So the set is explicit, and a test asserts every
# declared name still appears under tests/ -- a declaration with no fixture
# behind it is rot.
_FIXTURE_WORMS = frozenset({
    "BadWorm", "W", "W0", "W1", "W2", "W3", "T0", "T1", "T2",
    "ReflexTestWorm",
})

# The subset the suite STILL creates -- i.e. the ones a reader can go and look
# at. Kept separate from the purge set on purpose, because the two are
# different claims and only one of them is checkable:
#
#   `_FIXTURE_WORMS`          -> "purge these" (an act)
#   `_FIXTURE_WORMS_WITNESSED` -> "the test suite creates these" (a fact)
#
# The difference is the interesting part. `mesh:T0/T1/T2` sit in the live store
# at +/-0.9217 -- an exact mirrored triple, which is a fixture signature if
# anything is -- and grep finds no test or script that creates them: their
# fixture is gone and the keys are ORPHANED SYNTHETIC DATA, which is precisely
# the state `audit_state()` exists to expose. Asserting that every declared
# name has a living fixture would have failed, and forcing the list to match
# the grep would have left the orphans in the brainstem. Test-backing is
# asserted for the witnessed subset only.
_FIXTURE_WORMS_WITNESSED = frozenset({"BadWorm", "ReflexTestWorm"})

# ── REFLEX ARC + SCARS (autonomic response, no worm in the loop) ────
# record_outcome() reacts INSTANTLY to consecutive failures: demotes
# priority, inflates cooldown, and opens a "scar" that demotes the worm
# below all healthy peers in q_order() until it succeeds again. This is
# the spinal reflex — HomeostatWorm is the slower deliberative layer.
_SCARS: Dict[str, Dict[str, Any]] = {}   # "nature:worm" -> {"since", "reason"}
_REFLEX_FAIL_THRESHOLD = 4               # consecutive failures before reflex
_REFLEX_COOLDOWN_MULT = 3.0              # cooldown inflation on reflex
_REFLEX_COOLDOWN_CAP = 3600.0            # absolute cooldown ceiling

# ── POLICY JOURNAL (endocrine memory) ─────────────────────────────
# Every programmatic change to worm priority/cooldown is journalled so
# it can be audited and reverted as a whole. The delibrate layer
# (HomeostatWorm apply=True, SpineCycleWorm) writes here; the reflex
# arc writes too (actor="reflex") so a revert undoes emergency measures
# as well. Bounded; thread-safe.
_POLICY_JOURNAL: List[Dict[str, Any]] = []
_POLICY_JOURNAL_MAX = 100
_POLICY_LOCK = threading.RLock()

# ── POLICY LATEST (compacting adaptation memory) ─────────────────────
# Audit G10 (2026-09-14): the journal above is a bounded FIFO ring, and
# `worm_registry._replay_policy_journal()` reconstructs adapted cooldowns by
# replaying it on restart. That makes the *audit trail* load-bearing for
# *state*, so once the ring wraps past 100 entries a worm whose last
# adaptation has been evicted silently reverts to its decorator default —
# a scale-dependent regression that only appears once the fleet is busy.
#
# This map keeps exactly one entry per worm (its latest adaptation), so the
# replay source is bounded by the number of worms rather than by journal
# traffic. The ring keeps growing/shrinking as the human-readable audit log;
# this is the authoritative state. Keyed by worm name.
_POLICY_LATEST: Dict[str, Dict[str, Any]] = {}


def _remember_policy(entry: Dict[str, Any]) -> None:
    """Fold one journal entry into the compacting latest-per-worm map.

    Last writer wins, which is exactly the semantics the replay needs
    (`later adaptation supersedes the earlier one`). Caller holds no lock
    requirement: safe to call with or without `_POLICY_LOCK` held.
    """
    worm = entry.get("worm")
    if not worm:
        return
    with _POLICY_LOCK:
        _POLICY_LATEST[str(worm)] = dict(entry)


def get_policy_latest() -> Dict[str, Dict[str, Any]]:
    """Latest journalled adaptation per worm (the replay source). Copy."""
    with _POLICY_LOCK:
        return {k: dict(v) for k, v in _POLICY_LATEST.items()}

# ── QUARANTINE VOTE LEDGER (cross-env quorum that can actually decide) ──
# Audit 2026-09-15: RedQueenWorm.order_quarantine() built a FRESH
# QuorumVoterWorm(quorum=2) on every call and cast exactly ONE vote from the
# local env, so `decided` (distinct count >= 2) could never become True — the
# quarantine gate was structurally dead code. Two changes make it live while
# preserving the original safety intent (one env must never quarantine alone):
#   1. votes are counted per DISTINCT ENV, not per call, and
#   2. the ledger is persisted AND gossiped, so a peer env's vote arrives
#      in-process instead of dying in another process's memory.
# A single env still cannot reach quorum. Two envs can.
_QUARANTINE_VOTES: Dict[str, Dict[str, Dict[str, Any]]] = {}
_QUARANTINE_LOCK = threading.RLock()
_QUARANTINE_FINDINGS_MAX = 256


def cast_quarantine_vote(finding: str, env: str, verdict: str = "quarantine",
                         quorum: int = 2) -> Dict[str, Any]:
    """Record one env's vote on `finding`; decide on DISTINCT envs.

    Later votes from the same env replace that env's earlier vote (an env
    changing its mind must not stuff the ballot). Returns the tally plus
    `decided` = the leading verdict has >= `quorum` distinct envs.
    Thread-safe; never raises.
    """
    finding = str(finding or "")[:160]
    env = str(env or "")[:40]
    verdict = str(verdict or "quarantine")[:40]
    if not finding or not env:
        return {"success": False, "error": "finding and env are required"}
    # Fold any persisted votes in first, so a second env's vote that arrived
    # via gossip (or a previous session) counts toward this quorum.
    _ensure_loaded()
    with _QUARANTINE_LOCK:
        box = _QUARANTINE_VOTES.setdefault(finding, {})
        # `at` stays human-readable ISO (it is displayed and parsed by
        # callers); `hlc` is the ordering stamp the merge uses. Additive on
        # purpose: no time-based consumer of `at` changes behaviour.
        box[env] = {"verdict": verdict, "at": _now_iso(), "hlc": _hlc_now()}
        if len(_QUARANTINE_VOTES) > _QUARANTINE_FINDINGS_MAX:
            oldest = min(_QUARANTINE_VOTES.items(),
                         key=lambda kv: max((v.get("at", "") for v in kv[1].values()),
                                            default=""))
            _QUARANTINE_VOTES.pop(oldest[0], None)
        tally: Dict[str, int] = {}
        for rec in box.values():
            tally[rec["verdict"]] = tally.get(rec["verdict"], 0) + 1
        top, count = ("", 0)
        if tally:
            top, count = max(tally.items(), key=lambda kv: kv[1])
        return {"success": True, "finding": finding, "envs": sorted(box),
                "env_count": len(box), "tally": tally, "top": top,
                "count": count, "quorum": int(quorum),
                "decided": count >= int(quorum)}


def get_quarantine_votes(finding: Optional[str] = None) -> Dict[str, Any]:
    """Read the quarantine ledger (one finding, or the whole ledger). Copy."""
    _ensure_loaded()
    with _QUARANTINE_LOCK:
        if finding is not None:
            return {str(finding): dict(_QUARANTINE_VOTES.get(str(finding), {}))}
        return {k: {e: dict(v) for e, v in box.items()}
                for k, box in _QUARANTINE_VOTES.items()}


# ── QUARANTINE ITEMS (the ACTIVE quarantine, as distinct from the votes) ──
# Audit 2026-09-16: `QuarantineWorm` kept `self._quarantined` as a per-instance
# dict, and `RedQueenWorm.order_quarantine()` built a FRESH `QuarantineWorm()`
# on every call (`spine.py:582`) — so the record was written, the object was
# dropped, and the record went with it. `is_quarantined()` had ZERO callers in
# the tree, and no scheduling path consulted quarantine at all. The vote ledger
# above could therefore "decide" to quarantine a worm and change nothing.
#
# The fix is the same shape as the vote ledger: the *state* moves to a single
# process-global, persisted, gossiped store that the execution path can consult,
# and `QuarantineWorm` becomes a view over it. A release is recorded as a
# tombstone (not a delete) so it propagates across envs instead of being
# silently re-adopted from a peer's older payload.
_QUARANTINE_ITEMS: Dict[str, Dict[str, Any]] = {}
_QUARANTINE_ITEMS_MAX = 512


def quarantine_item(item: str, reason: str = "", env: Optional[str] = None) -> Dict[str, Any]:
    """Actively quarantine `item` (a worm name, or a staged filename).

    Process-global and persisted, so the very next scheduling pass can see it
    regardless of which `QuarantineWorm` instance asked. Idempotent: a repeat
    call refreshes the reason and the stamp without lengthening anything.
    Thread-safe; never raises.
    """
    item = str(item or "")[:160]
    if not item:
        return {"success": False, "error": "item is required"}
    record = {"reason": str(reason or "")[:200],
              "by": str(env or _local_env_name())[:40],
              "at": _now_iso(), "hlc": _hlc_now(),
              "released": False, "released_at": ""}
    with _QUARANTINE_LOCK:
        _QUARANTINE_ITEMS[item] = record
        if len(_QUARANTINE_ITEMS) > _QUARANTINE_ITEMS_MAX:
            oldest = min(_QUARANTINE_ITEMS.items(),
                         key=lambda kv: _rec_stamp(kv[1], "released_at") or
                         _rec_stamp(kv[1], "at"))
            _QUARANTINE_ITEMS.pop(oldest[0], None)
        return {"success": True, "item": item, "quarantined": len(
            [v for v in _QUARANTINE_ITEMS.values() if not v.get("released")]),
            "record": dict(record)}


def release_item(item: str, by: Optional[str] = None) -> Dict[str, Any]:
    """Lift a quarantine. Written as a TOMBSTONE so gossip cannot undo it.

    Deleting the key instead would let a peer's older payload re-adopt the
    quarantine on the next merge (the resurrection pattern the purged-key
    tombstones exist to stop). A released tombstone is still an *active list*
    exclusion and is evicted by the same bound as everything else.
    """
    item = str(item or "")[:160]
    if not item:
        return {"success": False, "error": "item is required"}
    with _QUARANTINE_LOCK:
        cur = _QUARANTINE_ITEMS.get(item)
        was = bool(cur) and not cur.get("released")
        _QUARANTINE_ITEMS[item] = {
            "reason": (cur or {}).get("reason", ""),
            "by": str(by or (cur or {}).get("by") or _local_env_name())[:40],
            "at": (cur or {}).get("at", ""),
            "hlc": (cur or {}).get("hlc", ""),
            "released": True, "released_at": _now_iso(),
            "release_hlc": _hlc_now()}
        return {"success": True, "item": item, "released": was,
                "quarantined": len([v for v in _QUARANTINE_ITEMS.values()
                                    if not v.get("released")])}


def is_quarantined(item: str) -> bool:
    """True when `item` is actively quarantined right now.

    The one call the execution path needs. Read-only, lock-guarded, and
    deliberately cheap: it is consulted per worm per cluster run.
    """
    item = str(item or "")
    if not item:
        return False
    with _QUARANTINE_LOCK:
        rec = _QUARANTINE_ITEMS.get(item)
    return bool(rec) and not rec.get("released")


def get_quarantine_items(active_only: bool = True) -> Dict[str, Dict[str, Any]]:
    """Read the quarantine store (copy). `active_only` hides released ones."""
    with _QUARANTINE_LOCK:
        return {k: dict(v) for k, v in _QUARANTINE_ITEMS.items()
                if not (active_only and v.get("released"))}


def quarantine_count() -> int:
    """Number of actively quarantined items."""
    with _QUARANTINE_LOCK:
        return len([v for v in _QUARANTINE_ITEMS.values() if not v.get("released")])


# ── FABRIC STATE STORE (the mesh's operational memory) ──────────────
# Audit 2026-09-15 (finding #7): save_spine_state() persisted the spine's
# *learning* (map, ledger, Q, scars, policy, trust, anomaly model) but
# NOTHING persisted the fabric natures' *operational* state — routing
# tables, elected leader + term, quorum tallies, federated clients, model
# shard catalogue, endpoint loads. All of it lived in per-instance dicts,
# cached in ClusterWORMZ.instances, so it was lost on every new
# ClusterManager() and on restart. The mesh remembered how to feel
# and forgot what it knew.
#
# The fabric worms live in CORE.WORMS.expansion.*, which this module must
# not import (cycle). So they publish *through* this store: hydrate in
# __init__, publish on mutation. Keyed by provider (class name), bounded,
# persisted, and gossiped with the rest of the spine payload.
# Each record is `{provider, updated_at, state, field_stamps}`. `state` is the
# flattened attribute map callers already expect; `field_stamps` carries one
# HLC stamp per attribute. Per-FIELD stamps are what make two envs that each
# publish a different subset of a provider's attributes converge to the union
# instead of clobbering each other (deep dive 2026-09-15).
_FABRIC_STATE: Dict[str, Dict[str, Any]] = {}
_FABRIC_LOCK = threading.RLock()
_FABRIC_STATE_MAX = 256


def _hlc_now() -> str:
    """A fresh stamp from the process-wide hybrid logical clock."""
    try:
        from CORE.hlc import hlc
        return hlc().stamp()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L718", exc)
        return _now_iso()


def _observe_stamp(stamp: Any) -> None:
    """Fold a remote stamp into the local clock (causal ordering).

    Without this the local clock could hand out a stamp lower than one it has
    already seen, which is the inversion the HLC exists to prevent.
    """
    try:
        from CORE.hlc import hlc
        hlc().observe(str(stamp or ""))
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L731", exc)
        pass


def get_clock_skew_ms() -> int:
    """> 0 when the newest observed peer clock is ahead of ours.

    Diagnostic only — ordering never consults it. The mesh could not measure
    clock skew before, so skew-induced divergence was indistinguishable from
    a real disagreement.
    """
    try:
        from CORE.hlc import hlc
        return int(hlc().skew_ms())
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L745", exc)
        return 0


def _fabric_field_stamp(rec: Dict[str, Any], attr: str) -> str:
    """Ordering stamp for one attribute, falling back to the record-level one.

    The fallback is the migration path: a record written before per-field
    stamps existed has only `updated_at`, and treating it as the stamp for
    every field is the best available reading of it.
    """
    try:
        from CORE.hlc import normalize_stamp
        stamps = rec.get("field_stamps") or {}
        own = stamps.get(attr)
        if own:
            return normalize_stamp(str(own))
        return _rec_stamp(rec, "updated_at")
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L763", exc)
        return str(rec.get("updated_at") or "")


def _rec_stamp(rec: Any, iso_field: str) -> str:
    """Ordering stamp for a record: its HLC sidecar, else the legacy field.

    The additive half of the migration. Every record that participates in a
    cross-env merge carries BOTH a human-readable ISO field (`updated_at`,
    `last_at`, `at` — parsed and displayed by existing callers) and an `hlc`
    sidecar. Ordering consults the sidecar; nothing else changes. A record
    written before the sidecar existed normalises from its ISO field, so old
    and new records still compare correctly during the upgrade window.
    """
    if not isinstance(rec, dict):
        return ""
    stamp = rec.get("hlc")
    if not stamp:
        stamp = rec.get(iso_field)
    try:
        from CORE.hlc import normalize_stamp
        return normalize_stamp(str(stamp or ""))
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L785", exc)
        return str(stamp or "")


def _stamp_adoptable(stamp: str) -> bool:
    """False when a stamp is too far in the future to be trusted for ordering.

    The HLC refuses a remote physical time beyond its drift window so that a
    broken peer clock cannot pin the LOCAL clock (`HybridLogicalClock.observe`).
    Ordering is a separate path that uses the peer's stamp directly, so without
    this guard the same poisoned value still won every merge it took part in:

    * a real store was found carrying 98 stamps at year 2999
      (`032472162000000-04631-windows`, ~+30,682,668,693,692ms) that had been
      written before the drift bound existed;
    * those records would have won every future merge of that field until
      real time reached year 2999, i.e. permanently, with no way for a correct
      peer's later write to displace them.

    Refusing at the ordering chokepoint makes such a value inert: it cannot
    win, and it cannot propagate (a peer receives our poisoned stamp as a
    candidate and refuses it too, so the era stops spreading). An unparseable
    or legacy stamp is NOT refused here — it is handled by normalisation, and
    refusing it would re-break the migration path.
    """
    try:
        from CORE.hlc import MAX_DRIFT_MS, unpack_stamp
        import time as _t
        u = unpack_stamp(str(stamp or ""))
        if u is None:
            return True
        return u[0] <= int(_t.time() * 1000) + MAX_DRIFT_MS
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L817", exc)
        return True


def _stamp_newer(candidate: Any, current: Any) -> bool:
    """True when `candidate` is strictly newer than `current`, HLC-aware.

    Both sides are normalised before comparison, which is what lets a legacy
    naive-ISO timestamp persisted by an older build coexist with a new HLC
    stamp. A raw string compare would order *every* HLC stamp before *every*
    ISO one (HLC stamps begin with a zero-padded millisecond count, so they
    start with `0`), silently handing the win to the older write during the
    whole migration window.

    An empty candidate never wins, preserving the original guard's behaviour.

    Beyond-drift stamps are handled in BOTH directions, and the asymmetry is
    deliberate:

    * a candidate from beyond the drift window never wins, and the refusal is
      recorded on the shared clock so the diagnostic reports it even when the
      merge is what saw the stamp first; and
    * a *stored* value from beyond the window does not win either — otherwise a
      poisoned record would be frozen in place until real time caught up with
      it (year 2999), and a correct peer's later write could never displace it.
      Treating it as "not newer" means the first trustworthy write heals it.

    See `_stamp_adoptable`.
    """
    # Emptiness is judged on the RAW value, not the normalised one:
    # `normalize_stamp("")` is a perfectly valid zero stamp, so normalising
    # first makes an absent candidate look like a real one — and against a
    # poisoned incumbent that would let "no stamp at all" win.
    raw = str(candidate if candidate is not None else "").strip()
    if not raw:
        return False
    try:
        from CORE.hlc import normalize_stamp
        a = normalize_stamp(raw)
        b = normalize_stamp(str(current if current is not None else ""))
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L857", exc)
        a = raw
        b = str(current or "")
    if not _stamp_adoptable(a):
        _observe_stamp(candidate)
        return False
    if not _stamp_adoptable(b):
        # The incumbent is the untrustworthy one: an adoptable candidate wins
        # by default, which is how a poisoned record gets repaired.
        return True
    return a > b


def publish_fabric_state(provider: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one fabric worm's snapshot in, per field. Never raises.

    This used to *replace* the provider record wholesale. Because a provider
    key is shared by every env (each env's ``Lilith``, ``ModelShardServerWorm``
    ... publishes under the same name), a peer with a newer stamp erased the
    other env's fields entirely — which is why a catalogue meant to hold two
    envs' shards could only ever hold one. Fields are now merged individually
    and a publish only touches the attributes it actually carries.
    """
    provider = str(provider or "")[:80]
    if not provider or not isinstance(state, dict):
        return {"success": False, "error": "provider and dict state required"}
    stamp = _hlc_now()
    try:
        with _FABRIC_LOCK:
            rec = _FABRIC_STATE.get(provider)
            if rec is None:
                if len(_FABRIC_STATE) >= _FABRIC_STATE_MAX:
                    oldest = min(_FABRIC_STATE.items(),
                                 key=lambda kv: _rec_stamp(kv[1], "updated_at"))
                    _FABRIC_STATE.pop(oldest[0], None)
                rec = {"provider": provider, "updated_at": _now_iso(),
                       "hlc": stamp, "state": {}, "field_stamps": {}}
                _FABRIC_STATE[provider] = rec
            fields = rec.setdefault("state", {})
            stamps = rec.setdefault("field_stamps", {})
            for k, v in dict(state).items():
                key = str(k)[:120]
                fields[key] = v
                stamps[key] = stamp
            if _stamp_newer(stamp, _rec_stamp(rec, "updated_at")):
                rec["hlc"] = stamp
                rec["updated_at"] = _now_iso()
        return {"success": True, "provider": provider,
                "keys": sorted(state.keys())[:12]}
    except Exception as exc:
        note_failure("spine_registry:L906", exc)
        return {"success": False, "error": str(exc)[:120]}


def get_fabric_state(provider: str) -> Optional[Dict[str, Any]]:
    """Flattened last-known state for `provider` (copy), or None if unseen.

    Deliberately does NOT call `_ensure_loaded()`: this is called from every
    fabric worm's ``__init__``, so an auto-load here would mean a disk read
    per construction and would let a stale on-disk snapshot shadow in-process
    state. Disk restore happens on the explicit startup path
    (``startup_warm.restore_spine_state``) which is the one place it belongs.
    """
    with _FABRIC_LOCK:
        rec = _FABRIC_STATE.get(str(provider))
        return dict(rec.get("state") or {}) if rec else None


def get_fabric_record(provider: str) -> Optional[Dict[str, Any]]:
    """Full record including per-field stamps (diagnostics / merge debugging)."""
    with _FABRIC_LOCK:
        rec = _FABRIC_STATE.get(str(provider))
        return dict(rec) if rec else None


def get_fabric_view() -> Dict[str, Any]:
    """Introspection: which fabric providers have persisted state."""
    with _FABRIC_LOCK:
        return {k: {"updated_at": v.get("updated_at", ""),
                    "keys": sorted((v.get("state") or {}).keys()),
                    "stamped_fields": len(v.get("field_stamps") or {})}
                for k, v in _FABRIC_STATE.items()}


def _merge_fabric(remote: Any) -> int:
    """Fold a remote fabric collection in, per FIELD, HLC-ordered.

    Two changes over the previous per-provider recency merge:

    * **Per field.** A peer publishing `_lease_env` no longer erases our
      `_genome_version`; both survive, keyed by their own stamps.
    * **HLC ordering.** "Newer" is decided by a hybrid logical clock rather
      than by comparing naive local-time strings, so a timezone offset or a
      skewed clock can no longer invert the winner (or delay it by hours).

    Returns the number of individual field writes applied.
    """
    merged = 0
    if not isinstance(remote, dict):
        return 0
    with _FABRIC_LOCK:
        for provider, rec in remote.items():
            if not isinstance(rec, dict):
                continue
            r_state = rec.get("state")
            if not isinstance(r_state, dict):
                continue
            name = str(provider)[:80]
            cur = _FABRIC_STATE.get(name)
            if cur is None:
                if len(_FABRIC_STATE) >= _FABRIC_STATE_MAX:
                    oldest = min(_FABRIC_STATE.items(),
                                 key=lambda kv: _rec_stamp(kv[1], "updated_at"))
                    _FABRIC_STATE.pop(oldest[0], None)
                cur = {"provider": name, "updated_at": "", "hlc": "",
                       "state": {}, "field_stamps": {}}
                _FABRIC_STATE[name] = cur
            fields = cur.setdefault("state", {})
            stamps = cur.setdefault("field_stamps", {})
            for attr, value in r_state.items():
                key = str(attr)[:120]
                r_ts = _fabric_field_stamp(rec, attr)
                l_ts = _fabric_field_stamp(cur, key)
                # Via `_stamp_newer`, not a raw `>`: this comparison used to
                # bypass the drift guard entirely, which is how a poisoned
                # year-2999 field stamp could still win a fabric merge after
                # the clock itself had learned to refuse it.
                if key not in fields or _stamp_newer(r_ts, l_ts):
                    fields[key] = value
                    if r_ts:
                        stamps[key] = r_ts
                    merged += 1
            r_top = _rec_stamp(rec, "updated_at")
            if _stamp_newer(r_top, _rec_stamp(cur, "updated_at")):
                cur["hlc"] = r_top
                cur["updated_at"] = str(rec.get("updated_at") or "")
            # Fold the peer's clock into ours so nothing we stamp after this
            # can be ordered before a value we just adopted (causality). Only
            # stamps we would actually trust are folded in: taking a raw `max`
            # here would pick a poisoned value over a valid one and drop the
            # valid observation entirely.
            candidates = [r_top] + [str(v) for v in (stamps.values()
                                                     if isinstance(stamps, dict) else [])]
            adoptable = [c for c in candidates if c and _stamp_adoptable(c)]
            _observe_stamp(max(adoptable) if adoptable else (r_top or ""))
    return merged


def _merge_quarantine(remote: Any) -> int:
    """Fold remote quarantine votes in, per (finding, env) recency wins."""
    merged = 0
    if not isinstance(remote, dict):
        return 0
    with _QUARANTINE_LOCK:
        for finding, box in remote.items():
            if not isinstance(box, dict):
                continue
            local = _QUARANTINE_VOTES.setdefault(str(finding)[:160], {})
            for env, rec in box.items():
                if not isinstance(rec, dict):
                    continue
                cur = local.get(str(env))
                if cur is None or _stamp_newer(_rec_stamp(rec, "at"),
                                               _rec_stamp(cur, "at")):
                    local[str(env)[:40]] = {"verdict": rec.get("verdict", "quarantine"),
                                           "at": rec.get("at", ""),
                                           "hlc": rec.get("hlc", "")}
                    merged += 1
            if len(_QUARANTINE_VOTES) > _QUARANTINE_FINDINGS_MAX:
                overflow = len(_QUARANTINE_VOTES) - _QUARANTINE_FINDINGS_MAX
                for k in list(_QUARANTINE_VOTES)[:overflow]:
                    _QUARANTINE_VOTES.pop(k, None)
    return merged


def _quarantine_item_stamp(rec: Any) -> str:
    """Ordering stamp for a quarantine record: newest of its two events.

    A quarantine record has two moments that matter — when it was applied and
    when it was released — and the LATEST one wins, so a release always beats
    the older quarantine it is lifting (and vice versa). Without this, a
    release tombstone and the quarantine it replaced would compare on the same
    field and the older event could win on a peer.
    """
    if not isinstance(rec, dict):
        return ""
    applied = _rec_stamp(rec, "at")
    released = _rec_stamp({"hlc": rec.get("release_hlc") or "",
                           "released_at": rec.get("released_at", "")}, "released_at")
    if released and applied:
        return max(released, applied)
    return released or applied


def _merge_quarantine_items(remote: Any) -> int:
    """Fold remote quarantine records in; newest event per item wins."""
    merged = 0
    if not isinstance(remote, dict):
        return 0
    with _QUARANTINE_LOCK:
        for item, rec in remote.items():
            if not isinstance(rec, dict):
                continue
            key = str(item)[:160]
            cur = _QUARANTINE_ITEMS.get(key)
            if cur is None or _stamp_newer(_quarantine_item_stamp(rec),
                                           _quarantine_item_stamp(cur)):
                _QUARANTINE_ITEMS[key] = dict(rec)
                merged += 1
        if len(_QUARANTINE_ITEMS) > _QUARANTINE_ITEMS_MAX:
            overflow = len(_QUARANTINE_ITEMS) - _QUARANTINE_ITEMS_MAX
            for k in list(_QUARANTINE_ITEMS)[:overflow]:
                _QUARANTINE_ITEMS.pop(k, None)
    return merged


# ── PERSISTENCE (restart-safe, gossip-ready) ────────────────────────
_STATE_FILE = Path.home() / ".azazael" / "spine_state.json"

# Persisted-payload schema version, deliberately separate from __version__
# (the module version, which moves for reasons unrelated to the file format).
# This field did not exist, and the payload's "version" carried __version__ and
# stayed "1.0.0" across the change that ADDED `q_update_counts` to the saved
# shape. So there was no hook to hang a backfill on, and rows written before
# the field existed restored as "never measured" -- including the two routes
# with the most successes in the system (`route:windows>windows` at 27,
# `route:windows>g_archive` at 32, both reporting a pull count of zero).
# A schema change with no backfill does not lose data; it RELABELS it, which is
# worse, because the consumer cannot tell the label is a lie.
_STATE_SCHEMA_VERSION = 2


def _migrate_state(payload: Any) -> Dict[str, Any]:
    """Bring a payload up to `_STATE_SCHEMA_VERSION` in place; return a report.

    The v1 -> v2 repair is a **re-derivation, not a guess**: a key can carry a
    Q-weight with no pull count for exactly one reason -- it was written before
    the count table existed -- and its evidence was never actually lost. It sat
    in the homeostat the whole time as ``successes + failures``. So the
    migration reads the payload's own ledger and materialises the missing
    counts, which makes it idempotent and makes the next save converge the
    file to the new schema.

    A key with neither a count nor a ledger record is left alone: it has no
    evidence to recover and is reported as such by `audit_state()` rather than
    invented here.
    """
    report: Dict[str, Any] = {"to_version": _STATE_SCHEMA_VERSION,
                              "from_version": 0, "previous_schema": False,
                              "backfilled": []}
    if not isinstance(payload, dict):
        return report
    try:
        report["from_version"] = int(payload.get("schema_version") or 0)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L1110", exc)
        report["from_version"] = 0
    report["previous_schema"] = report["from_version"] < _STATE_SCHEMA_VERSION
    qw = payload.get("q_weights")
    if not isinstance(qw, dict):
        return report
    counts = payload.get("q_update_counts")
    if not isinstance(counts, dict):
        counts = {}
        payload["q_update_counts"] = counts
    homeo = payload.get("homeostat")
    if not isinstance(homeo, dict):
        homeo = {}
    for key in qw:
        if key in counts:
            continue
        rec = homeo.get(key)
        if not isinstance(rec, dict):
            continue
        try:
            n = int(rec.get("successes") or 0) + int(rec.get("failures") or 0)
        except (TypeError, ValueError) as exc:
            note_failure("spine_registry:L1131", exc)
            continue
        if n > 0:
            counts[key] = n
            report["backfilled"].append({"key": str(key), "evidence": n})
    return report

# Load-once guard: separate cron/script processes used to start empty and
# save() OVERWROTE each other's ledgers (found 2026-09-14: state file held
# 3 keys after 60+ samples fed across runs). First write in a process with
# empty memory now merges disk state instead of clobbering it.
_LOADED_ONCE = False
_LOAD_LOCK = threading.Lock()


def _merge_disk_state() -> None:
    """Fold the on-disk state file into memory (counters = MAX, missing adopted).

    Makes save-then-exit processes composable: N parallel hourly jobs each
    contribute their samples instead of clobbering each other. Never raises.
    """
    try:
        if not _STATE_FILE.exists():
            return
        payload = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("spine_registry:L1156", exc)
        return
    # Read-path migration. Both readers (`load_spine_state` is the other) run
    # it, so the file is interpreted identically whichever entry point touches
    # it first; the repair is the re-derivation of missing evidence from the
    # payload's own ledger, which makes it idempotent.
    _migrate_state(payload)
    try:
        with _HOMEOSTAT_LOCK:
            # Tombstones first, so a purge recorded in a previous session
            # blocks re-adoption in this one.
            #
            # The recorded reason is carried across, not replaced with
            # "fixture". A tombstone is the only place the ledger says WHY a
            # key is gone, and a restart used to overwrite that with a word
            # that means "this was synthetic test data": a key capped at
            # capacity came back labelled synthetic, and so did one purged for
            # a manufactured penalty. Nothing resurrected -- `_purged_blocked`
            # does not read the value -- so this was provenance loss alone,
            # and provenance is the whole claim this table makes.
            for k, why in (payload.get("purged_keys") or {}).items():
                _PURGED_KEYS.setdefault(str(k), str(why or "fixture"))
            for k, n in (payload.get("q_dream_counts") or {}).items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_DREAM_COUNTS[k] = max(_Q_DREAM_COUNTS.get(k, 0), int(n))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L1183", exc)
                    pass
            for k, v in (payload.get("homeostat") or {}).items():
                if _purged_blocked(k) or not isinstance(v, dict):
                    continue
                cur = _HOMEOSTAT.get(k)
                if cur is None:
                    _HOMEOSTAT[k] = v
                    continue
                for f in ("successes", "failures"):
                    try:
                        cur[f] = _adopt_counter(
                            k, f, int(v.get(f, 0)), int(cur.get(f, 0)))
                    except (TypeError, ValueError) as exc:
                        note_failure("spine_registry:L1196", exc)
                        pass
                if _stamp_newer(_rec_stamp(v, "last_at"),
                                _rec_stamp(cur, "last_at")):
                    for f in ("last_ms", "last_at", "last_error", "hist_ms",
                              "consec_fail", "hlc"):
                        if f in v:
                            cur[f] = v[f]
            for k, w in (payload.get("q_weights") or {}).items():
                if _purged_blocked(k):
                    continue
                if k not in _Q_WEIGHTS:
                    try:
                        _Q_WEIGHTS[k] = max(_Q_MIN, min(_Q_MAX, float(w)))
                    except (TypeError, ValueError) as exc:
                        note_failure("spine_registry:L1210", exc)
                        pass
            for k, n in (payload.get("q_evidence_floor") or {}).items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_EVIDENCE_FLOOR[str(k)] = int(n)
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L1217", exc)
                    pass
            for k, floors in (payload.get("counter_floor") or {}).items():
                if _purged_blocked(str(k)) or not isinstance(floors, dict):
                    continue
                try:
                    _COUNTER_FLOOR[str(k)] = {
                        f: int(n) for f, n in floors.items()
                        if f in ("successes", "failures")}
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L1226", exc)
                    pass
            for k, n in (payload.get("q_update_counts") or {}).items():
                if _purged_blocked(k):
                    continue
                _Q_UPDATE_COUNTS[k] = _adopt_evidence(k, n)
            for k, var in (payload.get("q_variance") or {}).items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_VARIANCE.setdefault(k, float(var))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L1237", exc)
                    pass
            for k, v in (payload.get("scars") or {}).items():
                if _purged_blocked(k):
                    continue
                if isinstance(v, dict):
                    _SCARS.setdefault(k, v)
        with _SPINE_LOCK:
            for k, v in (payload.get("spine_map") or {}).items():
                _SPINE_MAP.setdefault(k, v if isinstance(v, dict) else {})
        _merge_quarantine(payload.get("quarantine_votes"))
        _merge_quarantine_items(payload.get("quarantined_items"))
        _merge_fabric(payload.get("fabric_state"))
    except Exception as exc:
        note_failure("spine_registry:L1250", exc)
        pass


def _ensure_loaded() -> None:
    global _LOADED_ONCE
    if _LOADED_ONCE:
        return
    with _LOAD_LOCK:
        if _LOADED_ONCE:
            return
        _LOADED_ONCE = True
        try:
            # Load whenever there is something to load, NOT only when memory
            # happens to be empty. The old guard was `not _HOMEOSTAT and not
            # _Q_WEIGHTS`, so any process that wrote a single outcome (or
            # dreamed) before its first getter latched `_LOADED_ONCE` without
            # restoring anything -- and then ran its whole life on an empty
            # brainstem while `load_spine_state()` reported the file present.
            # Loading is safe to do at any point because it MERGES (setdefault
            # / MAX) rather than clobbers.
            #
            # This and the setdefault in `load_spine_state` are one change: the
            # assignment there was the only reason the emptiness heuristic was
            # safe, so either half alone is worse than neither.
            if _STATE_FILE.exists():
                load_spine_state()
        except Exception as exc:
            note_failure("spine_registry:L1277", exc)
            pass


def _now_iso() -> str:
    return datetime.now().isoformat()


# ── SPINE MAP API ────────────────────────────────────────────────────

def _ordering_stamp(info: Dict[str, Any]) -> str:
    """Pick the HLC sidecar for a new record.

    A caller that explicitly pinned `hlc` or `updated_at` in `info` is
    asserting when the content became true (tests, backfill, a record imported
    from a peer), and the ordering sidecar must AGREE with that pin —
    otherwise the pin is silently ignored and the merge orders by wall clock,
    which is the exact failure this sidecar exists to remove.
    """
    pinned = info.get("hlc") or info.get("updated_at")
    if pinned:
        try:
            from CORE.hlc import normalize_stamp
            return normalize_stamp(str(pinned))
        except Exception as exc:  # pragma: no cover - defensive
            note_failure("spine_registry:L1301", exc)
            return str(pinned)
    return _hlc_now()


def record_spine_node(env: str, worm: str, info: Dict[str, Any]) -> None:
    """Record/refresh one spine node (env:worm). Thread-safe."""
    key = f"{env}:{worm}"
    # `updated_at` stays ISO for the many readers that parse it; `hlc` is the
    # sidecar that decides cross-env recency in merge_spine_gossip, so a
    # timezone offset or a skewed clock cannot invert the winner.
    info = dict(info or {})
    rec: Dict[str, Any] = {"env": env, "worm": worm, "updated_at": _now_iso()}
    rec.update(info)
    rec.setdefault("hlc", _ordering_stamp(info))
    with _SPINE_LOCK:
        if key not in _SPINE_MAP and len(_SPINE_MAP) >= _SPINE_MAP_MAX:
            # Evict oldest-updated node (simple bounded-growth policy)
            oldest = min(_SPINE_MAP.items(),
                         key=lambda kv: kv[1].get("updated_at", ""))
            del _SPINE_MAP[oldest[0]]
        _SPINE_MAP[key] = rec


def get_spine_map() -> Dict[str, Dict[str, Any]]:
    """Copy of the full spine map. Thread-safe."""
    with _SPINE_LOCK:
        return {k: dict(v) for k, v in _SPINE_MAP.items()}


def get_spine_summary() -> Dict[str, Any]:
    """Aggregate summary: node count, per-env counts, route edges."""
    with _SPINE_LOCK:
        envs: Dict[str, int] = {}
        routes = 0
        for rec in _SPINE_MAP.values():
            env_name = rec.get("env", "?")
            envs[env_name] = envs.get(env_name, 0) + 1
            routes += len(rec.get("routes", []) or [])
        return {
            "nodes": len(_SPINE_MAP),
            "envs": envs,
            "route_edges": routes,
            "events": len(_SPINE_EVENTS),
        }


def get_spine_events(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Spine events (most recent first, capped)."""
    with _SPINE_LOCK:
        events = list(_SPINE_EVENTS)
    events.reverse()
    if since:
        events = [e for e in events if e.get("at", "") >= since]
    return events


def record_spine_event(kind: str, detail: Dict[str, Any]) -> None:
    """Append a spine event (bounded). Thread-safe."""
    with _SPINE_LOCK:
        _SPINE_EVENTS.append({"kind": kind, "detail": detail, "at": _now_iso()})
        if len(_SPINE_EVENTS) > _SPINE_EVENTS_MAX:
            del _SPINE_EVENTS[:len(_SPINE_EVENTS) - _SPINE_EVENTS_MAX]


# ── HOMEOSTAT API ────────────────────────────────────────────────────

def record_outcome(nature: str, worm: str, success: bool,
                   duration_ms: float = 0.0,
                   error: Optional[str] = None,
                   reward: Optional[float] = None,
                   delta: Optional[float] = None,
                   censored: bool = False) -> None:
    """Record one worm execution outcome. Called from ClusterWORMZ.execute().

    Thread-safe, never raises (feedback must not break execution).

    TWO LEARNING-SIGNAL CONVENTIONS, and the caller must pick the right one:

    * ``reward`` -- a signed TARGET. ``q += alpha * (reward - q)``, i.e. q
      converges toward it. This is what the boolean path means by +1/-1, and
      it is the default when neither argument is given.
    * ``delta`` -- a signed DELTA (a TD error). ``q += alpha * delta``, the
      textbook tabular update. This is what a TD learner actually computes.

    They are the same update only when ``reward == q + delta``. This docstring
    previously said "callers that computed a real learning signal (a TD error,
    a shaped reward) pass it here so magnitude survives the hand-off" -- i.e.
    it promised DELTA semantics while the implementation applied TARGET
    semantics. Both call sites read a different half of that sentence and
    neither was wrong about the half it read; the result was a table that was
    saturated at the clip for the sign callers (22 pulls at alpha 0.4) and
    flat for the TD caller (31 synapse edges collapsed to 3 distinct values).
    Naming the delta operation explicitly is what makes the two separable.

    The ledger counters still key off ``success`` (a boolean is the right unit
    for fail-rate statistics) regardless of which learning signal is used.

    A THIRD convention (2026-09-27, mesh forensic): ``censored=True`` marks an
    outcome that is NOT an observation of the thing being learned -- the
    canonical case is infrastructure noise (a refused connection because no
    listener was running, a timeout in an unrelated subsystem). A censored
    outcome is counted in its own bucket and changes NOTHING else: no
    success/failure counters (it would poison fail-rate stats too), no Q
    update, no consec_fail reflex. Callers that blur signal and noise create
    the poison loop where a missing dependency self-sustains a learned
    penalty against the policy that was never actually exercised.
    """
    _ensure_loaded()
    key = f"{nature}:{worm}"
    if _purged_blocked(key):
        # Homeostat choke point. `_q_observe_unlocked` is Q's. A purge must
        # hold against a live writer, not only against disk and peers.
        _note_blocked_write(key)
        return
    try:
        with _homeostat_stripe(key):
            rec = _HOMEOSTAT.setdefault(key, {
                "successes": 0, "failures": 0,
                "last_ms": 0.0, "last_at": "", "last_error": "",
                "hist_ms": [], "consec_fail": 0,
            })
            if censored:
                rec["censored"] = rec.get("censored", 0) + 1
                rec["last_at"] = _now_iso()
                rec["hlc"] = _hlc_now()
                if error:
                    rec["last_error"] = str(error)[:120]
                _append_outcome_event({
                    "nature": nature, "worm": worm,
                    "success": bool(success), "censored": True,
                    "ms": round(float(duration_ms), 2),
                    "at": rec["last_at"],
                })
                return
            if success:
                rec["successes"] += 1
                rec["consec_fail"] = 0
            else:
                rec["failures"] += 1
                rec["consec_fail"] = rec.get("consec_fail", 0) + 1
                rec["last_error"] = (error or "")[:120]
            rec["last_ms"] = round(float(duration_ms), 2)
            rec["last_at"] = _now_iso()
            rec["hlc"] = _hlc_now()
            hist: List[float] = rec["hist_ms"]
            hist.append(round(float(duration_ms), 2))
            if len(hist) > _HIST_MAX:
                del hist[:len(hist) - _HIST_MAX]
            _append_outcome_event({
                "nature": nature, "worm": worm,
                "success": bool(success), "ms": rec["last_ms"],
                "at": rec["last_at"],
            })
            # Auto-reinforce the brainstem. Default signal is the boolean
            # sign (success ~ +1, failure ~ -1); callers that computed a real
            # reward (e.g. a TD error) supply a signed magnitude instead, so
            # the Q-table is not quantised to one bit.
            if delta is not None:
                # DELTA convention: q accumulates the error, so a NaN/garbage
                # delta is a NO-OP rather than a sign fallback -- falling back
                # to `success` here would silently substitute a target for the
                # delta the caller asked for, which is the exact confusion
                # this parameter exists to remove.
                try:
                    signal = max(_Q_MIN, min(_Q_MAX, float(delta)))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L1467", exc)
                    signal = 0.0
                rec["last_reward"] = round(signal, 4)
                rec["last_signal_kind"] = "delta"
                # _reinforce_q_delta takes that key's Q stripe itself
                # (homeostat stripe is held here, canonical H→Q order).
                _reinforce_q_delta(key, signal)
            else:
                if reward is None:
                    signal = 1.0 if success else -1.0
                else:
                    try:
                        signal = max(_Q_MIN, min(_Q_MAX, float(reward)))
                    except (TypeError, ValueError) as exc:
                        note_failure("spine_registry:L1480", exc)
                        signal = 1.0 if success else -1.0
                rec["last_reward"] = round(signal, 4)
                rec["last_signal_kind"] = "target"
                # _reinforce_q takes that key's Q stripe itself
                # (homeostat stripe is held here, canonical H→Q order).
                _reinforce_q(key, signal)
            # Capture everything the reflex needs so the reflex can run
            # OUTSIDE this stripe. F9: the reflex used to do a
            # `from CORE.worm_registry import WORM_REGISTRY` — an import and
            # a foreign subsystem's dict access — while holding the ledger
            # lock, serialising every other worm behind it.
            reflex_fired = rec["consec_fail"] == _REFLEX_FAIL_THRESHOLD
            consec_fail = rec["consec_fail"]
            last_at = rec["last_at"]

        # ── TABLE BOUND (outside every stripe: `cap_q_tables` takes the whole
        # barrier, and acquiring homeostat stripes while holding a Q stripe
        # would invert the documented lock order). Cheap length check first,
        # so this costs a dict len on the hot path.
        if len(_Q_WEIGHTS) > _Q_KEYS_MAX:
            cap_q_tables()

        # ── REFLEX ARC: instant autonomic response ──────────────────
        # Fires at the failure threshold with no scheduler, no worm and no
        # delay — but now *after* releasing the stripe, so the emergency
        # path cannot stall the ledger. Lock order here is POLICY/SPINE/
        # SCAR with no ledger stripe held, which removes the old
        # HOMEOSTAT→SPINE nesting entirely.
        if reflex_fired:
            try:
                from CORE.worm_registry import WORM_REGISTRY as _WR
                info = _WR.get(worm)
                if info is not None:
                    old_cd = float(info.get("cooldown_seconds", 30.0))
                    old_pr = int(info.get("priority", 5))
                    info["cooldown_seconds"] = round(
                        min(old_cd * _REFLEX_COOLDOWN_MULT,
                            _REFLEX_COOLDOWN_CAP), 2)
                    info["priority"] = max(1, old_pr - 1)
                    # Journalled so revert_policy() can undo emergency
                    # measures once the incident is understood.
                    journal_policy("reflex", "emergency_backoff", worm, {
                        "cooldown_seconds": {"from": old_cd,
                                             "to": info["cooldown_seconds"]},
                        "priority": {"from": old_pr,
                                     "to": info["priority"]},
                    })
            except Exception as exc:
                note_failure("spine_registry:L1528", exc)
                pass
            with _SCAR_LOCK:
                _SCARS[key] = {"since": last_at,
                               "reason": (error or "unspecified")[:80]}
            try:
                record_spine_event("reflex_response", {
                    "key": key, "consec_fail": consec_fail,
                    "action": "cooldown+priority+scar",
                })
            except Exception as exc:
                note_failure("spine_registry:L1538", exc)
                pass
        elif success:
            # G7: Graduated scar healing. Instead of instant full heal,
            # the scar fades over N consecutive successes. heal_count
            # tracks progress; full heal at HEAL_THRESHOLD successes.
            _HEAL_THRESHOLD = 3  # consecutive successes to fully heal
            with _SCAR_LOCK:
                if key in _SCARS:
                    scar = _SCARS.get(key, {})
                    scar["heal_count"] = scar.get("heal_count", 0) + 1
                    if scar["heal_count"] >= _HEAL_THRESHOLD:
                        _SCARS.pop(key, None)
                        healed = scar["heal_count"]
                    else:
                        _SCARS[key] = scar  # write back updated count
                        healed = None
                else:
                    healed = None
                    scar = {}
            if healed is not None:
                try:
                    record_spine_event("scar_healed", {"key": key,
                        "heal_count": healed})
                except Exception as exc:
                    note_failure("spine_registry:L1562", exc)
                    pass
            elif scar:
                try:
                    record_spine_event("scar_fading", {"key": key,
                        "heal_count": scar.get("heal_count"),
                        "threshold": _HEAL_THRESHOLD})
                except Exception as exc:
                    note_failure("spine_registry:L1569", exc)
                    pass
    except Exception as exc:
        note_failure("spine_registry:L1571", exc)
        pass  # feedback is best-effort by design


def get_homeostat_view() -> Dict[str, Dict[str, Any]]:
    """Full homeostat ledger (copy). Thread-safe."""
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return {k: {kk: (list(vv) if isinstance(vv, list) else vv)
                    for kk, vv in v.items()}
                for k, v in _HOMEOSTAT.items()}


def get_outcome_events(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recent outcome events (most recent first)."""
    events = list(_merged_outcome_events())
    events.reverse()
    since = since or ""
    return [e for e in events if e.get("at", "") >= since]


# ── Q-WEIGHT API (brainstem) ───────────────────────────────────────

def _reinforce_q(key: str, reward: float) -> None:
    """EMA Q-update: q += alpha * (reward - q), clamped to [-1, 1].

    G2: Adaptive alpha. High-variance keys (volatile worms) get a higher
    alpha so they learn faster; low-variance keys (stable worms) get a
    lower alpha so they don't oscillate. Alpha is clamped to [0.05, 0.5].

    F9: takes ``_q_stripe(key)`` itself, so both call sites (the live hot
    path in ``record_outcome``, which already holds that key's homeostat
    stripe, and ``dream_reinforce``) are safe by construction and cannot
    address a key under the wrong lock. Homeostat→Q is the canonical order
    the lock invariant depends on.
    """
    with _q_stripe(key):
        _reinforce_q_unlocked(key, reward)


def _reinforce_q_delta(key: str, delta: float) -> None:
    """DELTA form of ``_reinforce_q``. Takes ``_q_stripe(key)`` itself.

    Same lock discipline by construction (F9): both call sites acquire that
    key's Q stripe, so neither can address a key under the wrong lock.
    Homeostat→Q remains the canonical acquisition order.
    """
    with _q_stripe(key):
        _reinforce_q_delta_unlocked(key, delta)


def _reinforce_q_simulated(key: str, reward: float) -> None:
    """SIMULATION form: moves Q without earning observation credit.

    Used by ``dream_reinforce``. Same target semantics as ``_reinforce_q``,
    but the update lands in ``_Q_DREAM_COUNTS``, so ``_evidence_unlocked`` --
    and therefore ``q_effective``, ``q_order`` and ``route_weight_learned`` --
    never treats a replay of a scenario as a measurement of the world.
    """
    with _q_stripe(key):
        _q_observe_unlocked(key, signal=reward - _Q_WEIGHTS.get(key, 0.0),
                            simulated=True)


def _reinforce_q_unlocked(key: str, reward: float) -> None:
    """TARGET form. Body of ``_reinforce_q``. Caller holds ``_q_stripe(key)``.

    ``q += alpha * (reward - q)`` -- ``reward`` is the value q converges
    TOWARD, which is what the boolean (+1/-1) callers mean by it.
    """
    _q_observe_unlocked(key, signal=reward - _Q_WEIGHTS.get(key, 0.0))


def _reinforce_q_delta_unlocked(key: str, delta: float) -> None:
    """DELTA form. Body of ``_reinforce_q_delta``. Caller holds the stripe.

    ``q += alpha * delta`` -- the textbook tabular update for a TD learner,
    where ``delta`` IS the TD error and the edge accumulates it.

    Kept as its own function rather than a flag on the target form so the two
    conventions cannot be confused at a call site: a reader sees which is
    meant. They are the same update only when ``target == q + delta``, so
    using one where the other belongs is not a small unit error -- it changes
    what the stored number means. That confusion was live: 31 synapse edges
    had collapsed to 3 distinct values (25 of them at exactly +0.0909)
    because a TD delta was being applied as a target.
    """
    _q_observe_unlocked(key, signal=delta)


def _q_observe_unlocked(key: str, signal: float,
                        simulated: bool = False) -> None:
    """Shared kernel: adaptive alpha, Welford variance, clipped step.

    ``signal`` is the amount to move q by, scaled by alpha. All three public
    forms reduce to this, so variance tracking and the clip cannot diverge
    between them. Variance is measured over the signal actually applied, which
    for the target form is exactly the EMA error the original implementation
    tracked -- the pre-existing adaptive-alpha behaviour is unchanged.

    ``simulated`` routes the update count into ``_Q_DREAM_COUNTS`` instead of
    ``_Q_UPDATE_COUNTS``. The Welford denominator still spans both, because
    variance is a property of the value's history; but only OBSERVED updates
    count as evidence, because only they are evidence.
    """
    if _purged_blocked(key):
        # Q's choke point: every public reinforce form (target, delta,
        # simulated) reduces to this kernel, so one guard here covers all
        # three rather than three guards that can drift apart.
        _note_blocked_write(key)
        return
    q = _Q_WEIGHTS.get(key, 0.0)
    observed = _Q_UPDATE_COUNTS.get(key, 0)
    dreamed = _Q_DREAM_COUNTS.get(key, 0)
    n = observed + dreamed
    # Adaptive alpha: scale by sqrt of running variance
    var = _Q_VARIANCE.get(key, 0.25)  # default variance ~ moderate
    adaptive_alpha = _Q_ALPHA_BASE * (1.0 + min(2.0, (var ** 0.5) * 2.0))
    adaptive_alpha = max(0.05, min(0.5, adaptive_alpha))
    # Update running variance via Welford's online algorithm
    n += 1
    if simulated:
        _Q_DREAM_COUNTS[key] = dreamed + 1
    else:
        _Q_UPDATE_COUNTS[key] = observed + 1
    old_var = _Q_VARIANCE.get(key, 0.0)
    _Q_VARIANCE[key] = old_var + (signal * signal - old_var) / n
    q = q + adaptive_alpha * signal
    _Q_WEIGHTS[key] = max(_Q_MIN, min(_Q_MAX, q))


def get_q_weights() -> Dict[str, float]:
    """Copy of the Q-weight table. Thread-safe."""
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return dict(_Q_WEIGHTS)


def dream_reinforce(nature: str, worm: str, reward: float) -> bool:
    """Offline-learning channel: move Q without touching the live ledger.

    Used by ConsolidationWorm (sleep phase) to replay dream-derived
    policy suggestions into the Q-table. Deliberately does NOT count
    successes/failures — dreams are simulations, not executions — so
    homeostat fail-rate statistics stay grounded in reality while the
    routing substrate still benefits from offline experience.

    The `_ensure_loaded()` call used to sit ABOVE this docstring, which made
    the string a dead expression statement and left `__doc__` as None: the
    function's entire contract was unreachable from help(), the audit probes
    and any doc-driven tooling.

    2026-09-16 correction: "does NOT count" was true of the success/failure
    ledger and FALSE of the pull count, so a dream incremented the very number
    that ``q_effective`` now ranks on. A key that had only ever been dreamed
    about reported ``source="measured"`` and earned a shrinkage weight from
    evidence no worm produced. Dreams are still evidence, but weaker evidence,
    and the split makes that explicit instead of implicit: the update moves q
    and is counted in ``_Q_DREAM_COUNTS``, while `_evidence_unlocked` reads
    only observed updates.
    """
    _ensure_loaded()
    try:
        key = f"{nature}:{worm}"
        _reinforce_q_simulated(key, max(_Q_MIN, min(_Q_MAX, float(reward))))
        return True
    except Exception as exc:
        note_failure("spine_registry:L1737", exc)
        return False


def is_idle(window_seconds: float = 300.0) -> bool:
    """True when no live outcome has been recorded recently.

    Gate for the sleep phase (ConsolidationWorm): dreaming on a busy
    system would both pollute Q with stale replay and compete for CPU.
    """
    try:
        cutoff = (datetime.now().timestamp() - window_seconds)
        # ISO timestamps compare lexicographically with isoformat strings
        from datetime import datetime as _dt
        since = _dt.fromtimestamp(cutoff).isoformat()
        with _HOMEOSTAT_LOCK:
            return not any(e.get("at", "") >= since for e in _merged_outcome_events())
    except Exception as exc:
        note_failure("spine_registry:L1754", exc)
        return False


def get_q_weight(nature: str, worm: str) -> float:
    """Single Q-weight; 0.0 (neutral) if the worm has no history."""
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return _Q_WEIGHTS.get(f"{nature}:{worm}", 0.0)


def get_q_updates(nature: str, worm: str) -> int:
    """How many times this key has ever been reinforced. 0 if never.

    Added 2026-09-14 for elastic AOA arm selection. ``get_q_weight()``
    returns 0.0 for an unknown key and for a known key that has settled on
    a neutral 0.0 -- so a *proven neutral* arm is indistinguishable from a
    *never-tried* one by Q alone. Any selector that ranks on Q therefore
    starves untried arms permanently: they score 0, are never chosen, and
    so are never scored (the cold-start collapse four live inference
    providers independently flagged).

    The count already existed as ``_Q_UPDATE_COUNTS`` (used to drive the
    Welford variance in ``_reinforce_q_unlocked``); this exposes it
    read-only so a caller can distinguish "unknown" from "measured" and
    apply a minimum-pull quota before trusting Q.
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return int(_Q_UPDATE_COUNTS.get(f"{nature}:{worm}", 0))


def get_q_update_counts() -> Dict[str, int]:
    """Copy of the WHOLE update-count table, keyed "nature:worm".

    Added 2026-09-15 for the RL monitor. ``get_q_updates()`` is per-key, so a
    caller that wants to render the entire Q-table had to either loop over
    every key (one lock acquisition each) or -- as the first cut of the RL
    monitor did -- call it with no arguments, raise ``TypeError``, and get
    swallowed by a defensive ``except``. That produced a card which silently
    reported "0 measured" for a populated brainstem while claiming the spine
    as its source, which is exactly the failure mode the monitor exists to
    prevent.

    Read-only copy, so a caller can pair it with ``get_q_weights()`` and tell
    a *proven neutral* key (weight 0.0, pulls > 0) from a *starved* one
    (weight 0.0, pulls == 0).
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return dict(_Q_UPDATE_COUNTS)


def get_q_dream_counts() -> Dict[str, int]:
    """Copy of the WHOLE dream-count table, keyed "nature:worm".

    The counterpart to ``get_q_update_counts()`` for the offline channel, and
    the reason it is public: ``dream_reinforce()`` moves q while counting in a
    SEPARATE table, so an observer that reads only the observed-update counts
    cannot tell a key that was never dreamed about from one that has been
    dreamed about many times. The consolidation stage needs exactly that
    distinction to prove a dream actually wrote something, rather than reading
    a weight delta that some other stage produced.
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return dict(_Q_DREAM_COUNTS)


def _evidence_unlocked(key: str) -> int:
    """Observations behind a key's Q value. Caller holds the ledger barrier.

    Presence in ``_Q_UPDATE_COUNTS`` is the marker, not a non-zero value: a
    key that has been explicitly invalidated sits at 0 evidence ON PURPOSE and
    must not fall through to the homeostat totals, or invalidation would
    resurrect exactly the evidence it discounted.

    The homeostat fallback exists for the migration case (playing-field TODO
    G4): ``_Q_UPDATE_COUNTS`` was added to the persisted schema with no
    backfill, so six live keys -- including the two routes with the most
    successes in the system (`route:windows>windows` at 27, `g_archive` at 32)
    -- report a pull count of zero. Their evidence was never lost, it was
    RELABELLED, and ``successes + failures`` is the re-derivation.
    """
    n = _Q_UPDATE_COUNTS.get(key)
    if n is not None:
        try:
            return max(0, int(n))
        except (TypeError, ValueError) as exc:
            note_failure("spine_registry:L1849", exc)
            return 0
    rec = _HOMEOSTAT.get(key)
    if isinstance(rec, dict):
        try:
            return max(0, int(rec.get("successes") or 0)
                       + int(rec.get("failures") or 0))
        except (TypeError, ValueError) as exc:
            note_failure("spine_registry:L1842", exc)
            return 0
    return 0


def q_effective_from(q: float, n: int, k: float = _Q_SHRINK_K) -> float:
    """Shrink a Q-value by its evidence: ``q * n / (n + k)``.

    Pure and public so a caller can score a hypothetical without touching the
    table (and so the shrinkage can be unit-tested without a live spine).
    """
    try:
        qf = float(q)
        nf = max(0, int(n))
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L1863", exc)
        return 0.0
    if nf <= 0:
        return 0.0
    kk = max(1e-9, float(k))
    return max(_Q_MIN, min(_Q_MAX, qf * (nf / (nf + kk))))


def q_stale_discount(last_at_iso: str,
                     half_life_s: float = _Q_STALE_HALF_LIFE_S) -> float:
    """Forgetting-curve factor in (0, 1]: ``0.5 ** (age / half_life)``.

    Pure and public for the same reasons as q_effective_from. Unknown or
    unparsable timestamps count as fresh (1.0) — see the G2 note above.
    """
    try:
        import datetime as _dt
        ts = _dt.datetime.fromisoformat(str(last_at_iso or ""))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        age = (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds()
    except Exception as exc:
        note_failure("spine_registry:L1884", exc)
        return 1.0
    if age <= 0:
        return 1.0
    try:
        hl = max(1.0, float(half_life_s))
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L1890", exc)
        hl = _Q_STALE_HALF_LIFE_S
    return 0.5 ** (age / hl)


def get_q_variance() -> Dict[str, float]:
    """Copy of the per-key variance of the applied learning signal.

    Exposed because it is the module's only uncertainty estimate and it was
    previously unreachable from outside -- computed on every update, persisted,
    gossiped, and readable by nothing but adaptive alpha. A caller that wants
    to know *how well measured* a value is needs this; `get_q_weights()` cannot
    express it.
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return dict(_Q_VARIANCE)


def selection_score_from(q: float, n: int, var: float,
                        k: float = _Q_SHRINK_K,
                        c: float = _Q_LCB_C) -> float:
    """Conservative value for SELECTION: a lower confidence bound.

    ``score = shrink(q, n) - c * sigma / sqrt(n + 1)``

    Two discounts doing different jobs:

    * ``shrink(q, n) = q * n/(n+K)`` discounts a value whose **evidence** is
      thin (a 4-pull -1.0 is not a verdict).
    * the confidence term discounts a value whose **measurement** is noisy,
      using the Welford variance the module already maintains.

    Only the second one can separate a 95% arm from a perfect arm, because
    they differ by 0.32% of the value scale while their uncertainties differ
    from 0.436 to 0.0. Pure and public so the ranking can be unit-tested
    without a live spine.
    """
    eff = q_effective_from(q, n, k)
    try:
        v = max(0.0, float(var))
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L1931", exc)
        v = 0.0
    try:
        obs = max(0, int(n))
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L1935", exc)
        obs = 0
    if v > 0.0:
        eff -= abs(float(c)) * (v ** 0.5) / ((obs + 1) ** 0.5)
    return max(_Q_MIN, min(_Q_MAX, eff))


def selection_band(q: float) -> int:
    """Which class a key belongs to for ranking: 0 negative, 1 neutral, 2 positive.

    A single conservative scalar CANNOT order across this boundary, and the
    failure that proved it is worth recording. Applying the lower confidence
    bound to an UNOBSERVED key makes it score exactly 0.0, because its
    variance is zero -- an unmeasured arm looks infinitely certain. So a cold
    key outranked a healthy one whose interval was merely wide, which is
    backwards and which the existing suite caught immediately
    (`test_q_order_healthy_first_cold_middle`).

    The general lesson: a bound is only meaningful AMONG measurements. Across
    the observed/unobserved line it rewards ignorance, because "no data" is
    not "no uncertainty" -- it is unbounded uncertainty that happens to be
    represented as zero. Bands keep the documented contract (healthy first,
    cold middle, failing last) and let the confidence term do its work inside
    the two observed classes, which is where it was needed.
    """
    try:
        qf = float(q)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L1962", exc)
        return 1
    if qf > 0.0:
        return 2
    if qf < 0.0:
        return 0
    return 1


def selection_rank(q: float, n: int, var: float,
                   scarred: bool = False) -> tuple:
    """The exact sort key ``q_order`` uses, exposed so it can be tested.

    ``(scar_band, value_band, score)`` -- three keys, compared in that order.
    A scar still demotes below every healthy peer regardless of value (the
    reflex-arc contract), then the value class, then the conservative score.
    """
    return (0 if scarred else 1, selection_band(q),
            selection_score_from(q, n, var))


def q_confidence(nature: str, worm: str) -> Dict[str, Any]:
    """Everything the brainstem knows about one key's measurement quality.

    Reports the estimate AND its uncertainty, because a selector that can only
    see the estimate cannot tell "reliable" from "lucky", which is precisely
    how a clipped table came to look like a ranking.
    """
    _ensure_loaded()
    key = f"{nature}:{worm}"
    with _HOMEOSTAT_LOCK:
        q = float(_Q_WEIGHTS.get(key, 0.0))
        n = _evidence_unlocked(key)
        var = float(_Q_VARIANCE.get(key, 0.0))
        dreamed = int(_Q_DREAM_COUNTS.get(key, 0))
    # typeshed declares float ** float as Any; annotating sigma keeps the
    # downstream round() calls typed (cleared the L2049 round-arg finding),
    # leaving only the stub-level Any on the RHS itself. Suppressed, not
    # "fixed", on purpose: replacing pow with math.sqrt would change the
    # exception type for negative variance values (ValueError vs TypeError
    # downstream round()).
    sigma: float = var ** 0.5  # pyright: ignore[reportAny]
    return {"key": key, "q": round(q, 6), "evidence": n,
            "dream_evidence": dreamed, "variance": round(var, 6),
            "sigma": round(sigma, 6),
            "q_effective": round(q_effective_from(q, n), 6),
            "selection_score": round(selection_score_from(q, n, var), 6),
            "half_width": round(sigma / ((n + 1) ** 0.5), 6)}


def get_q_effective() -> Dict[str, float]:
    """Evidence-shrunk Q-table, keyed "nature:worm". Copy. Thread-safe.

    This -- not ``get_q_weights()`` -- is the table to RANK on. Raw weights
    are a plateau at the clip for the mesh's busiest keys, so an ordering
    built on them is decided by the scar band and by input order rather than
    by reliability. The raw table is still exposed and documented as raw.
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        return {k: q_effective_from(v, _evidence_unlocked(k))
                for k, v in _Q_WEIGHTS.items()}


def q_effective(nature: str, worm: str) -> float:
    """Evidence-shrunk Q for one key. 0.0 when it has no evidence."""
    _ensure_loaded()
    key = f"{nature}:{worm}"
    with _HOMEOSTAT_LOCK:
        return q_effective_from(_Q_WEIGHTS.get(key, 0.0),
                                _evidence_unlocked(key))


def get_q_evidence(key: str) -> Dict[str, Any]:
    """How much evidence stands behind one key, and where it came from.

    ``source`` is ``measured`` (a real pull count), ``homeostat`` (a
    re-derivation -- the key predates the count table), ``simulated`` (dreams
    only: the value exists but nothing ever observed it), or ``none``.

    ``dream_evidence`` is reported BESIDE the observed count rather than
    folded into it. A simulated pull and an observed pull are different
    claims, and only the second can establish a preference from nothing.
    """
    _ensure_loaded()
    key = str(key or "")
    with _HOMEOSTAT_LOCK:
        raw = _Q_UPDATE_COUNTS.get(key)
        dreamed = int(_Q_DREAM_COUNTS.get(key, 0))
        n = _evidence_unlocked(key)
        if raw is not None:
            source = "measured"
        elif n:
            source = "homeostat"
        elif dreamed:
            source = "simulated"
        else:
            source = "none"
        return {"key": key, "evidence": n, "source": source,
                "dream_evidence": dreamed,
                "observed": bool(n),
                "q": float(_Q_WEIGHTS.get(key, 0.0)),
                "q_effective": q_effective_from(_Q_WEIGHTS.get(key, 0.0), n),
                "count_recorded": raw is not None}


def invalidate_q(key: str, reason: str = "", factor: float = 0.5) -> Dict[str, Any]:
    """Discount half the evidence behind a key, because its cause was fixed.

    Playing-field TODO G2. The brainstem has no forgetting curve, so a penalty
    outlives its cause and cannot be cleared by the key that carries it: a
    deprioritised route is never re-chosen, so it never produces the successes
    that would raise it. The live store still carries `route:windows>mesh` at
    the floor on 27/27 failures and `route:windows>wsl2-ubuntu` below neutral
    on 30 failures at exactly 8000ms -- a TIMEOUT signature belonging to the
    sync path-doubling bug fixed in commit 56c15679. Nothing told the ledger.

    ``get_q_updates()``'s docstring already names this failure mode for
    *never-tried* arms and solves it with a pull count. That is one half. The
    *tried-then-broken-then-fixed* arm is the other, and it is strictly worse:
    a never-tried arm scores neutral 0 and only needs a chance, while a fixed
    arm carries a negative score that is exactly as stale as the bug, and no
    quota helps it -- it needs its HISTORY invalidated, not its turn.

    Halving (not zeroing) is deliberate: the old evidence was true when it was
    recorded, so it is discounted rather than erased, and the key stays in the
    count table so `_evidence_unlocked`'s homeostat fallback cannot undo this.
    Journalled so `revert_policy()` can see it and the monitors can count it.
    """
    _ensure_loaded()
    key = str(key or "")
    if not key:
        return {"success": False, "error": "key required"}
    try:
        f = float(factor)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L2091", exc)
        return {"success": False, "error": "factor must be numeric"}
    f = max(0.0, min(1.0, f))
    with _HOMEOSTAT_LOCK:
        before_n = _evidence_unlocked(key)
        before_q = float(_Q_WEIGHTS.get(key, 0.0))
        before_eff = q_effective_from(before_q, before_n)
        new_n = int(before_n * f) if before_n else 0
        # Record the reduction as a FACT, the way a purge records a removal.
        # Without this the count is restored by the next read-path merge and
        # the invalidation is a no-op that merely looks like one.
        _Q_EVIDENCE_FLOOR[key] = new_n
        if len(_Q_EVIDENCE_FLOOR) > _Q_EVIDENCE_FLOOR_MAX:
            for k in list(_Q_EVIDENCE_FLOOR)[
                    :len(_Q_EVIDENCE_FLOOR) - _Q_EVIDENCE_FLOOR_MAX]:
                _Q_EVIDENCE_FLOOR.pop(k, None)
        _Q_UPDATE_COUNTS[key] = new_n
        after_eff = q_effective_from(before_q, new_n)
    try:
        journal_policy("invalidate", "q_invalidated", key, {
            "evidence": {"from": before_n, "to": new_n},
            "q_effective": {"from": round(before_eff, 6),
                            "to": round(after_eff, 6)},
            "reason": str(reason or "")[:120],
        })
    except Exception as exc:
        note_failure("spine_registry:L2116", exc)
        pass
    try:
        record_spine_event("q_invalidated", {
            "key": key, "evidence_from": before_n, "evidence_to": new_n,
            "reason": str(reason or "")[:120],
        })
    except Exception as exc:
        note_failure("spine_registry:L2123", exc)
        pass
    return {"success": True, "key": key, "evidence_from": before_n,
            "evidence_to": new_n, "q": round(before_q, 6),
            "q_effective_from": round(before_eff, 6),
            "q_effective_to": round(after_eff, 6),
            "reason": str(reason or "")[:120]}


def reset_counters(key: str, reason: str = "") -> Dict[str, Any]:
    """Zero a key's success/failure counters and make the zero stick.

    For a record whose counters cannot be trusted -- the 09-29 case was one
    `hist_ms` series holding an interleaved mix of an eBPF alert count and
    real route probes, written by the same caller into the same fields, so the
    two cannot be separated and only discarded.

    Assigning zero is not enough and never was. `_merge_disk_state` folds the
    file in with `max()` on both fields before every write, so a zero loses to
    the value already on disk and the next save puts the disowned count back.
    The reset has to leave a record, exactly as a purge leaves a tombstone and
    `invalidate_q` leaves an evidence floor. This is that record, and it is
    journalled -- a reset that cannot be shown to have happened is the same
    defect as one that did not happen.
    """
    _ensure_loaded()
    key = str(key or "")
    if not key:
        return {"success": False, "error": "key required"}
    with _HOMEOSTAT_LOCK:
        rec = _HOMEOSTAT.get(key)
        before = {f: int((rec or {}).get(f) or 0)
                  for f in ("successes", "failures")}
        if rec is not None:
            for f in ("successes", "failures"):
                rec[f] = 0
            rec["consec_fail"] = 0
        _COUNTER_FLOOR[key] = {"successes": 0, "failures": 0}
        if len(_COUNTER_FLOOR) > _COUNTER_FLOOR_MAX:
            for k in list(_COUNTER_FLOOR)[
                    :len(_COUNTER_FLOOR) - _COUNTER_FLOOR_MAX]:
                _COUNTER_FLOOR.pop(k, None)
    try:
        journal_policy("purge", "counters_reset", key, {
            "before": before, "after": {"successes": 0, "failures": 0},
            "reason": str(reason or "")[:200]})
    except Exception as exc:
        note_failure("spine_registry:L2169", exc)
        pass
    try:
        record_spine_event("counters_reset", {
            "key": key, "before": before,
            "reason": str(reason or "")[:200]})
    except Exception as exc:
        note_failure("spine_registry:L2175", exc)
        pass
    return {"success": True, "key": key, "before": before,
            "after": {"successes": 0, "failures": 0},
            "reason": str(reason or "")[:200]}


def counter_floor_report() -> Dict[str, Any]:
    """Which keys carry a recorded counter reset, and where each one stands.

    Reports `resets_in_force`, not a suppressed total. The number of
    observations a floor kept out of the ledger is not recoverable afterwards
    -- the clamp discards the incoming value, so by the time anything reads
    this there is no "before" left to subtract, and a total computed from the
    floor alone would be 0 for every key whether the reset worked or the fold-in
    quietly won. That distinction is the one a reader needs, so the pre-reset
    value goes to the policy journal in `reset_counters`, where it is a fact
    about a moment rather than a number that drifts.
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        rows = {}
        for key, floors in sorted(_COUNTER_FLOOR.items()):
            rec = _HOMEOSTAT.get(key) or {}
            current = {f: int(rec.get(f) or 0)
                       for f in ("successes", "failures")}
            rows[key] = {"floor": dict(floors), "current": current,
                         "at_floor": all(current.get(f) == int(floors.get(f, 0))
                                         for f in ("successes", "failures"))}
    return {"resets_in_force": len(rows), "keys": rows}


def _is_fixture_key(key: str) -> bool:
    """True when a key addresses a declared synthetic worm."""
    nature, _, worm = str(key or "").partition(":")
    # A composite name (an edge or a site) is never a bare fixture worm, even
    # when its shape looks like one -- synapse:"W0>W1" is a real edge over
    # synthetic endpoints, and treating it as a fixture would delete data.
    if any(ch in worm for ch in ">/"):
        return False
    return worm in _FIXTURE_WORMS


def audit_state() -> Dict[str, Any]:
    """Classify every Q key by provenance. The trustworthiness is a number.

    Playing-field TODO G3. `tests/conftest.py` reasons correctly about
    CONTAINMENT -- "a test must not be able to damage the machine it is
    testing" -- and then stops at the boundary. Containment prevents NEW
    damage; it says nothing about EXISTING damage, and it leaves the evidence
    of past damage indistinguishable from production data. The missing third
    leg is provenance.

    Classes: ``measured`` (has an observed pull count), ``bootstrapped`` (no
    count but real homeostat evidence -- the G4 migration), ``simulated``
    (dreams only: a value exists and nothing ever observed it, so it earns no
    ranking weight), ``unwitnessed`` (no count, no ledger record, no dreams),
    and ``fabricated`` (a declared fixture name; counted separately as well,
    because a fixture that somehow accumulated a count is still a fixture).
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        qw = dict(_Q_WEIGHTS)
        counts = dict(_Q_UPDATE_COUNTS)
        homeo = {k: dict(v) for k, v in _HOMEOSTAT.items()}
        dreams = dict(_Q_DREAM_COUNTS)
    buckets: Dict[str, List[str]] = {
        "measured": [], "bootstrapped": [], "unwitnessed": [],
        "fabricated": [], "simulated": [],
    }
    for key in qw:
        if _purged_blocked(key):
            # Cannot happen through the public API (a purged key is removed),
            # but a merge racing the purge could re-introduce one, and a
            # tombstoned key must never be counted as measured.
            buckets["fabricated"].append(key)
        elif _is_fixture_key(key):
            buckets["fabricated"].append(key)
        elif key in counts:
            buckets["measured"].append(key)
        elif key in homeo:
            buckets["bootstrapped"].append(key)
        elif dreams.get(key):
            # A value with no observation behind it: dreams are the only thing
            # holding it up, and it earns no ranking weight anywhere.
            buckets["simulated"].append(key)
        else:
            buckets["unwitnessed"].append(key)
    total = len(qw) or 1
    return {
        "keys": len(qw),
        "buckets": {k: sorted(v) for k, v in buckets.items()},
        "counts": {k: len(v) for k, v in buckets.items()},
        "fabricated_share": round(len(buckets["fabricated"]) / total, 4),
        "trustworthy": len(buckets["fabricated"]) == 0,
        "clip_keys": sorted(k for k, v in qw.items() if abs(v) > 0.999999),
    }


def purge_fixtures(persist: bool = False) -> Dict[str, Any]:
    """Remove declared fixture keys from every learning table. Reports what went.

    Removal is reported, never silent -- the whole point of G3 is that the
    damage was invisible. Touches Q weights, counts, variance, homeostat,
    scars and the outcome-derived views; leaves every non-fixture key exactly
    as it was.

    ``persist`` also saves, and it records a TOMBSTONE for every removed key
    first. Without the tombstone the purge is not durable and not even
    observable: the save path folds the disk in before writing, so the next
    save re-adopts every fixture that was just deleted (verified: 56 entries
    removed, all 14 keys back on the following save). Use ``persist=True`` when
    the removal is meant to stick.
    """
    _ensure_loaded()
    removed: Dict[str, List[str]] = {}
    with _HOMEOSTAT_LOCK:
        for table, name in ((_Q_WEIGHTS, "q_weights"),
                            (_Q_UPDATE_COUNTS, "q_update_counts"),
                            (_Q_DREAM_COUNTS, "q_dream_counts"),
                            (_Q_VARIANCE, "q_variance"),
                            (_HOMEOSTAT, "homeostat")):
            hit = sorted(k for k in list(table) if _is_fixture_key(k))
            for k in hit:
                table.pop(k, None)
            if hit:
                removed[name] = hit
        with _SCAR_LOCK:
            hit = sorted(k for k in list(_SCARS) if _is_fixture_key(k))
            for k in hit:
                _SCARS.pop(k, None)
            if hit:
                removed["scars"] = hit
    # Tombstone BEFORE anything can re-merge, and for every key seen in any
    # table -- not just the ones in `removed`, so a key that only ever existed
    # on disk cannot slip back in either.
    purged = sorted({k for keys in removed.values() for k in keys})
    for k in purged:
        _PURGED_KEYS[k] = "fixture"
    if len(_PURGED_KEYS) > _PURGED_MAX:
        for k in list(_PURGED_KEYS)[:len(_PURGED_KEYS) - _PURGED_MAX]:
            _PURGED_KEYS.pop(k, None)
    saved: Dict[str, Any] = {"saved": False, "reason": "persist=False"}
    if persist:
        saved = save_spine_state()
    try:
        record_spine_event("fixtures_purged", {
            "keys": sum(len(v) for v in removed.values()),
            "tables": {k: len(v) for k, v in removed.items()},
            "tombstoned": len(purged), "persisted": bool(saved.get("saved")),
        })
    except Exception as exc:
        note_failure("spine_registry:L2326", exc)
        pass
    return {"success": True, "removed": removed,
            "removed_count": sum(len(v) for v in removed.values()),
            "tombstoned": purged, "persisted": bool(saved.get("saved"))}


def purge_edges(keys: Iterable[str], reason: str = "unevidenced",
                persist: bool = False,
                require_no_evidence: bool = True) -> Dict[str, Any]:
    """Remove named Q edges from every learning table. Reports what went.

    The public prune `neural_loop`'s forgetting stage needed. ``purge_fixtures``
    removes *declared* keys and ``cap_q_tables`` drops by capacity; neither is
    a prune, and neither lets a caller say which specific edges should go.

    Two safety properties, both load-bearing:

    * **Evidence gate (default on).** A key with any observed update OR any
      dream count is REFUSED, not removed, and the refusal is reported. The
      edges a learning pass just created start near the neutral value by
      construction, so a magnitude-only prune deletes the very learning it was
      called after. "Carries no signal" and "is small" are different questions,
      and this is the API that refuses to conflate them: it can only ever
      remove an edge that nothing has ever observed or dreamed about.
    * **Tombstone before persist.** Without it the removal is neither durable
      nor observable: the save path folds the disk in before writing, so the
      next save re-adopts every key just deleted (verified on the fixture path:
      56 entries removed, all back on the following save). `cap_q_tables`
      demonstrated the same trap -- its drops were re-adopted on the next save,
      so the cap it enforced did not hold.

    ``persist=True`` saves after tombstoning. ``require_no_evidence=False``
    removes regardless -- available for an operator-facing repair, and the only
    way to delete learning on purpose.
    """
    _ensure_loaded()
    wanted = []
    for k in keys or ():
        try:
            ks = str(k)
        except Exception as exc:
            note_failure("spine_registry:L2367", exc)
            continue
        if ks and ks not in wanted:
            wanted.append(ks)
    removed: List[str] = []
    refused: List[str] = []
    with _HOMEOSTAT_LOCK:
        for k in wanted:
            if require_no_evidence:
                n_obs = _evidence_unlocked(k)
                try:
                    n_dream = int(_Q_DREAM_COUNTS.get(k, 0) or 0)
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2379", exc)
                    n_dream = 0
                if n_obs > 0 or n_dream > 0:
                    refused.append(k)
                    continue
            present = any(k in t for t in (_Q_WEIGHTS, _Q_UPDATE_COUNTS,
                                           _Q_DREAM_COUNTS, _Q_VARIANCE))
            for table in (_Q_WEIGHTS, _Q_UPDATE_COUNTS, _Q_DREAM_COUNTS,
                          _Q_VARIANCE):
                table.pop(k, None)
            if present:
                removed.append(k)
        with _SCAR_LOCK:
            for k in removed:
                _SCARS.pop(k, None)
    # Tombstone BEFORE anything can re-merge -- this is what makes the removal
    # hold against the next save and against a live writer for the same key.
    for k in removed:
        _PURGED_KEYS[k] = reason or "purged"
    if len(_PURGED_KEYS) > _PURGED_MAX:
        for k in list(_PURGED_KEYS)[:len(_PURGED_KEYS) - _PURGED_MAX]:
            _PURGED_KEYS.pop(k, None)
    saved: Dict[str, Any] = {"saved": False, "reason": "persist=False"}
    if persist and removed:
        saved = save_spine_state()
    if removed or refused:
        try:
            record_spine_event("edges_purged", {
                "reason": reason, "removed": len(removed),
                "refused": len(refused), "persisted": bool(saved.get("saved")),
            })
        except Exception as exc:
            note_failure("spine_registry:L2410", exc)
            pass
    return {"success": True, "removed": removed, "removed_count": len(removed),
            "refused": refused, "refused_count": len(refused),
            "tombstoned": len(removed), "persisted": bool(saved.get("saved")),
            "reason": reason}


def route_weight(q_effective: float, base_w: float = 1.0) -> float:
    """The ONE place a route's effective weight is computed.

    ``route()`` and ``get_route_usage()`` each carried their own copy of
    ``clamp(base_w * (1 + q), 0.1, 5.0)``, which is how two files came to
    advertise the same unreachable bound. Consolidated so the telemetry and
    the router cannot disagree, and so the claim has one place to be wrong.

    Expects the **evidence-shrunk** q (``q_effective_from``), not the raw EMA.
    Route weight is a decision, and ranking a 4-pull observation against a
    40-pull one as if they were equal claims is the same mistake that made
    ``q_order`` sort a plateau. ``route_weight_learned`` is the callers' entry
    point; this is the pure arithmetic behind it.
    """
    try:
        bw = float(base_w)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L2434", exc)
        bw = 1.0
    return round(max(_ROUTE_WEIGHT_MIN,
                     min(_ROUTE_WEIGHT_MAX, bw * (1.0 + float(q_effective)))), 4)


def cap_q_tables() -> int:
    """Prune the lowest-evidence Q keys when the brainstem exceeds its bound.

    Takes the ledger barrier itself, so it must be called with NO stripe held.
    Pruning by EVIDENCE rather than by age or insertion order is deliberate:
    the keys worth keeping are the ones with something behind them, and a
    low-evidence key is by construction the cheapest to lose, because its
    contribution to any ranking is already shrunk toward neutral.

    Returns the number of keys dropped (and records a spine event when it is
    non-zero -- a silently shrinking brainstem is the defect, not the fix).

    2026-09-21 correction: this dropped keys WITHOUT tombstoning them, so the
    bound it enforced did not hold. MEASURED, and the exact shape of the bug
    `purge_fixtures` already documented on the fixture path: with disk holding
    6 keys and a bound of 3, the cap dropped 3 in memory and the very next
    ``save_spine_state()`` wrote all 6 back out, because the save folds the disk
    in before writing and nothing marked those 3 as deliberately removed. A cap
    that reports ``dropped=3`` while the table stays at 6 is worse than no cap:
    it makes the bound look enforced. Each dropped key is now tombstoned before
    the save can re-adopt it.

    2026-09-29 correction: ranking purely by evidence made this cap the exact
    undoing of `invalidate_q`. Discounting a key's evidence to zero IS what
    makes it rank first for eviction, so "the evidence behind this is stale"
    and "this key is the cheapest thing in the table to lose" are the same
    statement -- and the second one is false for a floored key, whose whole
    point is to be KEPT with its history marked stale. MEASURED on the live
    ledger: `invalidate_q("route:windows>wsl2-ubuntu", factor=0.0)` returned
    `evidence 259 -> 0, q_effective -0.3196 -> 0.0` and the key was still
    present, so the discount read as a success; the next `cap_q_tables()`
    evicted it whole and tombstoned it `"capped"`. The same call on
    `route:windows>wsl2-kali` destroyed 10 genuine ~95ms latency samples
    alongside the fabricated 8000.0 ones the discount was meant to discount.
    The invalidation had to remove a false penalty, not the record of it.

    A floored key now sorts AFTER every unfloored one, so it is evicted only
    when the table is over the bound and there is nothing else to take, and the
    event reports how many of the dropped were floored rather than letting a
    bound-enforcing save quietly eat the operator's own decisions.
    """
    dropped = 0
    gone: List[str] = []
    try:
        with _HOMEOSTAT_LOCK:
            if len(_Q_WEIGHTS) <= _Q_KEYS_MAX:
                return 0
            # Floored keys sort LAST (True > False), not first. See the
            # 2026-09-29 note above: a floored key is discounted ON PURPOSE,
            # and ranking by evidence alone made the discount an eviction
            # order. `in` is read outside the key function's sort so the
            # floor table is not re-probed per comparison.
            floored = frozenset(_Q_EVIDENCE_FLOOR)
            ranked = sorted(_Q_WEIGHTS,
                            key=lambda k: (k in floored,
                                           _evidence_unlocked(k),
                                           abs(float(_Q_WEIGHTS.get(k, 0.0)))))
            dropped = len(_Q_WEIGHTS) - _Q_KEYS_MAX
            gone = [str(k) for k in ranked[:dropped]]
            for k in gone:
                for table in (_Q_WEIGHTS, _Q_UPDATE_COUNTS, _Q_DREAM_COUNTS,
                              _Q_VARIANCE):
                    table.pop(k, None)
    except Exception as exc:
        note_failure("spine_registry:L2503", exc)
        return 0
    # Tombstone, or the fold-in on the next save re-adopts every key just
    # dropped and the bound silently reverts.
    for k in gone:
        _PURGED_KEYS[k] = "capped"
    if len(_PURGED_KEYS) > _PURGED_MAX:
        for k in list(_PURGED_KEYS)[:len(_PURGED_KEYS) - _PURGED_MAX]:
            _PURGED_KEYS.pop(k, None)
    if dropped:
        # Reported, not swallowed: a cap that had to eat a floored key is
        # still enforcing the bound, but the operator needs to know the
        # eviction cost a recorded decision rather than a stale value.
        dropped_floored = sum(1 for k in gone if k in _Q_EVIDENCE_FLOOR)
        try:
            record_spine_event("q_tables_capped", {"dropped": dropped,
                                                  "tombstoned": len(gone),
                                                  "dropped_floored":
                                                      dropped_floored,
                                                  "keys": len(_Q_WEIGHTS),
                                                  "bound": _Q_KEYS_MAX})
        except Exception as exc:
            note_failure("spine_registry:L2524", exc)
            pass
    return dropped


def route_last_at(src_env: str, dst_env: str) -> str:
    """ISO last-outcome timestamp for a route key, "" if never observed."""
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        rec = _HOMEOSTAT.get(f"route:{src_env}>{dst_env}") or {}
        return str(rec.get("last_at", ""))


def route_weight_learned(src_env: str, dst_env: str,
                        base_w: float = 1.0) -> Dict[str, Any]:
    """Learned route weight + the evidence behind it, in one lock.

    The single entry point for a router that wants to act on route learning.
    It returns the evidence and the shrunk value alongside the weight, so the
    decision is auditable rather than a bare float: "weight 0.67" and "weight
    0.67 from 4 observations" are different claims, and a caller that can only
    see the first cannot tell them apart.
    """
    _ensure_loaded()
    key = f"route:{src_env}>{dst_env}"
    with _HOMEOSTAT_LOCK:
        q = float(_Q_WEIGHTS.get(key, 0.0))
        n = _evidence_unlocked(key)
        q_eff = q_effective_from(q, n)
        rec = _HOMEOSTAT.get(key) or {}
        stale = q_stale_discount(rec.get("last_at", ""))
        q_eff = max(_Q_MIN, min(_Q_MAX, q_eff * stale))
    return {"key": key, "q": round(q, 4), "evidence": n,
            "q_effective": round(q_eff, 4),
            "stale_discount": round(stale, 4),
            "effective_weight": route_weight(q_eff, base_w)}


def route_weight_range(base_w: float = 1.0) -> Dict[str, float]:
    """The weight range that is actually REACHABLE for ``base_w``.

    With q in [_Q_MIN, _Q_MAX] the factor is [0, 2], so for the only
    registered base_w (1.0) the real ceiling is 2.0 and the declared 5.0 is
    unreachable. Derived rather than declared so the two cannot drift: a
    changed clip or a registered heavy route moves this automatically.
    """
    try:
        bw = float(base_w)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L2572", exc)
        bw = 1.0
    return {"base_weight": bw, "q_min": _Q_MIN, "q_max": _Q_MAX,
            "min_reachable": route_weight(_Q_MIN, bw),
            "max_reachable": route_weight(_Q_MAX, bw),
            "declared_min": _ROUTE_WEIGHT_MIN,
            "declared_max": _ROUTE_WEIGHT_MAX}


def q_order(nature: str, worm_names: List[str]) -> List[str]:
    """Order worm names by descending Q-weight (healthy first).

    Scarred worms are demoted below every healthy peer regardless of
    their Q-weight (reflex arc consequence), then healed worms return
    to normal ordering. Unknown worms keep relative input order among
    themselves (stable sort on a 0.0 default).
    """
    # G18: `worm_names.index(n)` inside the comprehension made this O(n^2)
    # (a linear scan per element) on a path that runs once per nature per
    # cycle. Precomputing first-seen positions keeps the exact tie-break
    # semantics — equal (scar, q) pairs still fall back to input order —
    # in O(n log n) total.
    positions: Dict[str, int] = {}
    for i, n in enumerate(worm_names):
        positions.setdefault(n, i)
    with _HOMEOSTAT_LOCK:
        # Rank on the CONSERVATIVE key (`selection_rank`), not the raw EMA.
        # Three keys, and each fixes a defect the previous one exposed:
        #
        #   * the raw EMA is a plateau at the clip for the busiest keys (29 of
        #     76 live, up from 3 at the previous save), so ordering on it
        #     degrades to scar-band then input order exactly where reliability
        #     matters most;
        #   * shrinkage fixes that, but still separates a 95% arm from a
        #     perfect one by 0.32% of the scale;
        #   * the confidence term uses the Welford variance -- measured on
        #     every update and previously read by nothing -- to put the noisy
        #     arm below the certain one, which was the decision that was
        #     actually impossible before.
        #
        # The VALUE BAND sits between the scar band and the score because a
        # bound cannot order across the observed/unobserved line: an
        # unobserved key has zero variance and would score 0.0, outranking a
        # healthy key with a merely wide interval.
        with _SCAR_LOCK:  # reflex writes scars outside the ledger barrier
            ranked = [(positions[n],
                       selection_rank(
                           _Q_WEIGHTS.get(f"{nature}:{n}", 0.0),
                           _evidence_unlocked(f"{nature}:{n}"),
                           _Q_VARIANCE.get(f"{nature}:{n}", 0.0),
                           scarred=f"{nature}:{n}" in _SCARS),
                       n) for n in worm_names]
    ranked.sort(key=lambda t: (-t[1][0], -t[1][1], -t[1][2], t[0]))
    return [t[2] for t in ranked]


def get_route_usage() -> Dict[str, Dict[str, Any]]:
    """Per-route learned state, keyed by "src>dst".

    Audit section 7 (2026-09-14): the mapper published *nature* edges only,
    so which route actually fired — and whether it worked — was unfilled
    telemetry. `record_route_result()` writes `route:<src>><dst>` keys into
    the homeostat ledger (auto-reinforced by the brainstem), so this reads
    that ledger back as route telemetry rather than inventing a new store.

    Returns {route: {successes, failures, total, q, last_at, last_ms,
    effective_weight}}. Empty when no route has ever been observed.
    """
    _ensure_loaded()
    with _HOMEOSTAT_LOCK:
        homeo = {k: dict(v) for k, v in _HOMEOSTAT.items()
                 if str(k).startswith("route:")}
        q_weights = dict(_Q_WEIGHTS)
        q_counts = dict(_Q_UPDATE_COUNTS)
    out: Dict[str, Dict[str, Any]] = {}
    for key, rec in homeo.items():
        route = str(key)[len("route:"):]
        s = int(rec.get("successes") or 0)
        f = int(rec.get("failures") or 0)
        q = float(q_weights.get(key, 0.0))
        # Same evidence rule as the brainstem: a recorded count wins, else the
        # homeostat totals stand in (the migration case -- the two routes with
        # the most successes in the system had NO recorded count).
        n = int(q_counts[key]) if key in q_counts else (s + f)
        out[route] = {
            "successes": s, "failures": f, "total": s + f,
            "q": round(q, 4),
            "last_at": rec.get("last_at", ""),
            "last_ms": rec.get("last_ms", 0.0),
            # One shared formula (`route_weight`), so the telemetry cannot
            # drift from what MeshRouterWorm.route() actually applies. This
            # docstring used to claim that while carrying its own copy that
            # hardcoded an unreachable 5.0 ceiling.
            "effective_weight": route_weight(q_effective_from(q, n)),
            # The same weight from 4 pulls and from 40 are not the same claim.
            "evidence": n,
            "q_effective": round(q_effective_from(q, n), 4),
        }
    return out


def _scars_snapshot() -> Dict[str, Dict[str, Any]]:
    """Copy of the scar table. Caller may hold (or not hold) the ledger
    barrier; the scar lock is taken here because reflex writes now land
    outside that barrier."""
    with _SCAR_LOCK:
        return {k: dict(v) for k, v in _SCARS.items()}


def get_scars() -> Dict[str, Dict[str, Any]]:
    """Copy of the reflex-arc scar table. Thread-safe."""
    return _scars_snapshot()


# ── PERSISTENCE (restart-safe, gossip-ready) ───────────────────────

def save_spine_state() -> Dict[str, Any]:
    """Persist spine + homeostat + Q-weights to ~/.azazael/spine_state.json.

    Written atomically (tmp + os.replace) so concurrent readers never see
    a torn file. Returns a summary; never raises (persistence is
    best-effort — a read-only home dir must not break the mesh).
    """
    summary: Dict[str, Any] = {"saved": False}
    try:
        # Merge-on-save (2026-09-14): hourly jobs + manual runs interleave as
        # separate processes; last-writer-wins was silently dropping samples
        # (ledger swung 257 -> 18 between hours). Fold disk state in first:
        # counters take MAX (monotonic), missing keys are adopted.
        _merge_disk_state()
        with _SPINE_LOCK:
            spine_map = {k: dict(v) for k, v in _SPINE_MAP.items()}
            spine_events = list(_SPINE_EVENTS)
        with _HOMEOSTAT_LOCK:
            homeostat = {k: {kk: (list(vv) if isinstance(vv, list) else vv)
                             for kk, vv in v.items()}
                         for k, v in _HOMEOSTAT.items()}
            q_weights = dict(_Q_WEIGHTS)
            # The pull-count and the Welford variance are as much a part of the
            # learned brainstem as the weights are. Persisting the weights
            # alone (pre-2026-09-15) reset adaptive alpha to its default and
            # made every key look never-pulled on each restart -- which defeats
            # get_q_updates(), whose entire purpose is telling a proven-neutral
            # key (weight 0.0, pulls > 0) from a starved one (both 0).
            q_update_counts = {k: int(v) for k, v in _Q_UPDATE_COUNTS.items()}
            q_evidence_floor = {k: int(v) for k, v in _Q_EVIDENCE_FLOOR.items()}
            counter_floor = {k: {f: int(n) for f, n in v.items()}
                             for k, v in _COUNTER_FLOOR.items()}
            q_dream_counts = {k: int(v) for k, v in _Q_DREAM_COUNTS.items()}
            q_variance = {k: float(v) for k, v in _Q_VARIANCE.items()}
        with _POLICY_LOCK:
            policy_journal = [dict(e) for e in _POLICY_JOURNAL]
            policy_latest = {k: dict(v) for k, v in _POLICY_LATEST.items()}
        with _GOSSIP_TRUST_LOCK:
            gossip = {"seq": _GOSSIP_SEQ_LOCAL,
                      "seq_seen": dict(_GOSSIP_SEQ_SEEN),
                      "keys": dict(_GOSSIP_KEYS)}
        quarantine_votes = get_quarantine_votes()
        quarantined_items = get_quarantine_items(active_only=False)
        with _FABRIC_LOCK:
            fabric_state = {k: dict(v) for k, v in _FABRIC_STATE.items()}
        payload = {
            "version": __version__,
            # The FILE FORMAT version, so a future schema change has somewhere
            # to declare itself and somewhere for a backfill to hang.
            "schema_version": _STATE_SCHEMA_VERSION,
            "saved_at": _now_iso(),
            "spine_map": spine_map,
            "spine_events": spine_events,
            "homeostat": homeostat,
            "q_weights": q_weights,
            "q_update_counts": q_update_counts,
            "q_evidence_floor": q_evidence_floor,
            "counter_floor": counter_floor,
            "q_dream_counts": q_dream_counts,
            "q_variance": q_variance,
            # Deletion is a fact that has to be persisted, or the next merge
            # re-adopts what a purge removed (verified: 56 entries removed,
            # all 14 keys restored by the following save).
            "purged_keys": dict(_PURGED_KEYS),
            "scars": _scars_snapshot(),
            "policy_journal": policy_journal,
            "policy_latest": policy_latest,
            "gossip": gossip,
            "quarantine_votes": quarantine_votes,
            # The ACTIVE quarantine, without which a red-team "decided" vote
            # survives a restart but the isolation it ordered does not.
            "quarantined_items": quarantined_items,
            "fabric_state": fabric_state,
        }
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(str(tmp), str(_STATE_FILE))
        summary = {"saved": True, "path": str(_STATE_FILE),
                   "spine_nodes": len(spine_map),
                   "homeostat_worms": len(homeostat),
                   "q_weights": len(q_weights),
                   "q_update_counts": len(q_update_counts),
                   "quarantine_findings": len(quarantine_votes),
                   "quarantined_items": quarantine_count(),
                   "fabric_providers": len(fabric_state)}
    except Exception as exc:
        note_failure("spine_registry:L2774", exc)
        summary = {"saved": False, "error": str(exc)[:120]}
    return summary


def load_spine_state() -> Dict[str, Any]:
    """Restore spine state saved by save_spine_state(). Merges into memory.

    Safe against missing/corrupt files (returns counts restored, never
    raises). Called at session start so learned Q-weights and the last
    known topology survive restarts — the mesh remembers itself.
    """
    restored: Dict[str, Any] = {"restored": False}
    try:
        if not _STATE_FILE.exists():
            return {"restored": False, "reason": "no_state_file"}
        payload = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        migration = _migrate_state(payload)
        if migration.get("backfilled"):
            # Visible, not silent: a migration that repairs evidence should say
            # so, or the next reader has no way to know the counts it sees were
            # derived rather than observed.
            try:
                record_spine_event("state_migrated", {
                    "from_version": migration.get("from_version"),
                    "to_version": migration.get("to_version"),
                    "backfilled": len(migration.get("backfilled") or []),
                })
            except Exception as exc:
                note_failure("spine_registry:L2802", exc)
                pass
        spine_map = payload.get("spine_map") or {}
        spine_events = payload.get("spine_events") or []
        homeostat = payload.get("homeostat") or {}
        q_weights = payload.get("q_weights") or {}
        if not isinstance(spine_map, dict):
            spine_map = {}
        if not isinstance(homeostat, dict):
            homeostat = {}
        if not isinstance(q_weights, dict):
            q_weights = {}
        with _SPINE_LOCK:
            for k, v in spine_map.items():
                _SPINE_MAP.setdefault(k, v if isinstance(v, dict) else {})
            _SPINE_EVENTS.extend(e for e in spine_events if isinstance(e, dict))
            if len(_SPINE_EVENTS) > _SPINE_EVENTS_MAX:
                del _SPINE_EVENTS[:len(_SPINE_EVENTS) - _SPINE_EVENTS_MAX]
        with _HOMEOSTAT_LOCK:
            # Reason preserved on reload -- see `_merge_disk_state`.
            for k, why in (payload.get("purged_keys") or {}).items():
                _PURGED_KEYS.setdefault(str(k), str(why or "fixture"))
            for k, v in homeostat.items():
                if _purged_blocked(k):
                    continue
                if isinstance(v, dict):
                    _HOMEOSTAT.setdefault(k, v)
            # setdefault, not assignment -- the same rule the policy map and
            # the variance below already follow. `load_spine_state()` is
            # callable mid-process (it is public, and `_ensure_loaded` calls
            # it), and an in-memory adaptation is always newer than the disk
            # copy. Blind assignment silently rolled a live Q-weight back to
            # the last saved value: save at -0.33, learn to +0.67, load, and
            # the learner is at -0.33 again with both sides reporting success.
            # That is the exact silent reversion the policy_latest comment
            # below calls out -- it just was not applied to the weights.
            for k, w in q_weights.items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_WEIGHTS.setdefault(k, max(_Q_MIN, min(_Q_MAX, float(w))))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2843", exc)
                    pass
            # Counts are monotone observations: take MAX so N parallel
            # save-then-exit processes compose instead of overwriting (same
            # rule as successes/failures in _merge_disk_state).
            for k, n in (payload.get("q_evidence_floor") or {}).items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_EVIDENCE_FLOOR[str(k)] = int(n)
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2853", exc)
                    pass
            for k, floors in (payload.get("counter_floor") or {}).items():
                if _purged_blocked(str(k)) or not isinstance(floors, dict):
                    continue
                try:
                    _COUNTER_FLOOR[str(k)] = {
                        f: int(n) for f, n in floors.items()
                        if f in ("successes", "failures")}
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2862", exc)
                    pass
            for k, n in (payload.get("q_update_counts") or {}).items():
                if _purged_blocked(k):
                    continue
                _Q_UPDATE_COUNTS[k] = _adopt_evidence(k, n)
            for k, n in (payload.get("q_dream_counts") or {}).items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_DREAM_COUNTS[k] = max(_Q_DREAM_COUNTS.get(k, 0), int(n))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2873", exc)
                    pass
            # Variance: adopt only when we have none; a restored value must not
            # overwrite variance this process has already measured.
            for k, var in (payload.get("q_variance") or {}).items():
                if _purged_blocked(k):
                    continue
                try:
                    _Q_VARIANCE.setdefault(k, float(var))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2882", exc)
                    pass
            for k, v in (payload.get("scars") or {}).items():
                if _purged_blocked(k):
                    continue
                if isinstance(v, dict):
                    with _SCAR_LOCK:
                        _SCARS.setdefault(k, v)
        with _POLICY_LOCK:
            for e in payload.get("policy_journal") or []:
                if isinstance(e, dict) and e.get("worm"):
                    _POLICY_JOURNAL.append(e)
            if len(_POLICY_JOURNAL) > _POLICY_JOURNAL_MAX:
                del _POLICY_JOURNAL[:len(_POLICY_JOURNAL) - _POLICY_JOURNAL_MAX]
            # G10: rebuild the compacting map from whatever the payload has.
            # Newer payloads carry it explicitly; older ones only have the
            # (possibly wrapped) ring, so fold that too — best effort, and
            # never both overwriting a newer explicit entry.
            # setdefault, not assignment: `load_spine_state()` is callable
            # mid-process (via _ensure_loaded / _replay_policy_journal), and an
            # in-memory adaptation is always newer than the disk one. Blind
            # assignment would roll a live adaptation back to the last saved
            # value — the same class of silent reversion G10 is about.
            for name, e in (payload.get("policy_latest") or {}).items():
                if isinstance(e, dict) and e.get("worm"):
                    _POLICY_LATEST.setdefault(str(name), dict(e))
            for e in _POLICY_JOURNAL:
                worm = e.get("worm")
                if worm and str(worm) not in _POLICY_LATEST:
                    _POLICY_LATEST[str(worm)] = dict(e)
        # Anti-replay state must survive restarts, or a saved old payload
        # could be replayed against a freshly-started process.
        global _GOSSIP_SEQ_LOCAL
        gossip = payload.get("gossip") or {}
        if isinstance(gossip, dict):
            with _GOSSIP_TRUST_LOCK:
                try:
                    _GOSSIP_SEQ_LOCAL = max(_GOSSIP_SEQ_LOCAL,
                                            int(gossip.get("seq") or 0))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L2921", exc)
                    pass
                for env, seq in (gossip.get("seq_seen") or {}).items():
                    try:
                        _GOSSIP_SEQ_SEEN[str(env)] = max(
                            int(_GOSSIP_SEQ_SEEN.get(str(env), -1)), int(seq))
                    except (TypeError, ValueError) as exc:
                        note_failure("spine_registry:L2927", exc)
                        continue
                for env, key in (gossip.get("keys") or {}).items():
                    if isinstance(key, str) and key:
                        _GOSSIP_KEYS.setdefault(str(env), key)
        # Fabric operational state + quarantine votes: the mesh's memory of
        # *what it knew*, not just how it felt (audit 2026-09-15, finding #7).
        _merge_quarantine(payload.get("quarantine_votes"))
        _merge_quarantine_items(payload.get("quarantined_items"))
        _merge_fabric(payload.get("fabric_state"))
        restored = {"restored": True,
                    "spine_nodes": len(spine_map),
                    "homeostat_worms": len(homeostat),
                    "q_weights": len(q_weights),
                    "quarantine_findings": len(payload.get("quarantine_votes") or {}),
                    "quarantined_items": quarantine_count(),
                    "fabric_providers": len(payload.get("fabric_state") or {})}
    except Exception as exc:
        note_failure("spine_registry:L2944", exc)
        restored = {"restored": False, "error": str(exc)[:120]}
    return restored


def homeostat_reset() -> None:
    """Clear feedback ledger (for tests / explicit operator action)."""
    with _HOMEOSTAT_LOCK:
        _HOMEOSTAT.clear()
        _clear_outcome_events()


# ── POLICY JOURNAL API (endocrine memory) ─────────────────────────

def journal_policy(actor: str, action: str, worm: str,
                   changes: Dict[str, Any]) -> None:
    """Append one programmatic policy change to the journal (bounded)."""
    entry = {
        "actor": actor, "action": action, "worm": worm,
        "changes": dict(changes), "at": _now_iso(),
    }
    with _POLICY_LOCK:
        _POLICY_JOURNAL.append(entry)
        if len(_POLICY_JOURNAL) > _POLICY_JOURNAL_MAX:
            del _POLICY_JOURNAL[:len(_POLICY_JOURNAL) - _POLICY_JOURNAL_MAX]
    # G10: the compacting map is the state; the ring is the audit log.
    _remember_policy(entry)


def get_policy_journal(limit: int = 50) -> List[Dict[str, Any]]:
    """Most recent policy changes first (copy, bounded by `limit`)."""
    with _POLICY_LOCK:
        entries = [dict(e) for e in _POLICY_JOURNAL[-limit:]]
    entries.reverse()
    return entries


def revert_policy(actor: str = "operator", count: int = 1) -> List[Dict[str, Any]]:
    """Undo the last `count` journalled cooldown/priority changes.

    Scans the journal newest-first, applies each entry's inverse to the
    registry, and stops after `count` reverted ENTRIES (journal entries
    whose worms are no longer registered are skipped, still counted).
    Returns the reverted entries for audit. Never raises.
    """
    reverted: List[Dict[str, Any]] = []
    try:
        from CORE.worm_registry import WORM_REGISTRY as _WR
    except ImportError as exc:
        note_failure("spine_registry:L2992", exc)
        return reverted
    with _POLICY_LOCK:
        for entry in reversed(_POLICY_JOURNAL):
            if len(reverted) >= count:
                break
            changes = entry.get("changes") or {}
            info = _WR.get(entry.get("worm", ""))
            if info is None:
                continue
            inv: Dict[str, Any] = {}
            if "cooldown_seconds" in changes:
                inv["cooldown_seconds"] = changes["cooldown_seconds"]["from"]
            if "priority" in changes:
                inv["priority"] = changes["priority"]["from"]
            if not inv:
                continue
            info.update(inv)
            reverted.append({"at": entry.get("at"), "worm": entry.get("worm"),
                             "actor": entry.get("actor"), "undone": inv})
            _POLICY_JOURNAL.remove(entry)
    if reverted:
        try:
            record_spine_event("policy_reverted", {"by": actor,
                                                   "entries": len(reverted)})
        except Exception as exc:
            note_failure("spine_registry:L3017", exc)
            pass
    return reverted


def spine_reset() -> None:
    """Clear spine state (for tests / explicit operator action)."""
    with _SPINE_LOCK:
        _SPINE_MAP.clear()
        _SPINE_EVENTS.clear()
    with _HOMEOSTAT_LOCK:
        _HOMEOSTAT.clear()
        _clear_outcome_events()
        _Q_WEIGHTS.clear()
        _Q_DREAM_COUNTS.clear()
        _PURGED_KEYS.clear()
        _PURGED_BLOCKED_WRITES.clear()
        # The brainstem is three tables, not one. Clearing only the weights
        # left the pull-count and the Welford variance behind, so a "reset"
        # produced a Q-table that was empty of values but full of evidence:
        # every arm immediately read as proven, and the variance kept driving
        # adaptive alpha from data the weights no longer had (2026-09-15).
        _Q_UPDATE_COUNTS.clear()
        _Q_VARIANCE.clear()
        # The recorded decisions about the counters have to go with them. Left
        # behind, a floor outlives every key it clamps: the table reads empty
        # and `spine_reset` still claims a discount that nothing can be
        # measured against, which is the same empty-of-values/full-of-
        # evidence state the comment above was written about -- one level up.
        _Q_EVIDENCE_FLOOR.clear()
        _COUNTER_FLOOR.clear()
    with _SCAR_LOCK:
        _SCARS.clear()
    with _POLICY_LOCK:
        _POLICY_JOURNAL.clear()
        _POLICY_LATEST.clear()
    with _GOSSIP_TRUST_LOCK:
        _GOSSIP_SEQ_SEEN.clear()
        _GOSSIP_KEYS.clear()
    with _ANOMALY_MODEL_LOCK:
        _ANOMALY_MODEL.clear()
        global _ANOMALY_MODEL_SOURCE
        _ANOMALY_MODEL_SOURCE = "local"
    with _QUARANTINE_LOCK:
        _QUARANTINE_VOTES.clear()
        _QUARANTINE_ITEMS.clear()
    with _FABRIC_LOCK:
        _FABRIC_STATE.clear()


def purge_implausible_stamps(dry_run: bool = True) -> Dict[str, Any]:
    """Repair ordering stamps that sit beyond the clock's drift window.

    Opt-in, never automatic — except the env-gated auto-remedy in
    notifier.check_clock_and_alert (AZAZAEL_CLOCK_AUTO_PURGE_REFUSED,
    default 25 refused stamps, 0 disables), which exists because refused
    stamps already lose ordering and only ride gossip forever. Refusal (see
    `_stamp_adoptable`) makes a poisoned stamp harmless — it cannot win a
    merge and cannot propagate — but it stays in the store, and therefore
    in every gossip payload, forever. A real store was found carrying 98
    year-2999 stamps
    (``032472162000000-04631-windows``) written before the drift bound existed.

    Each implausible ``hlc`` sidecar is REPLACED with a stamp derived from the
    record's own human-readable ISO field, not deleted: deleting it would make
    the record look older than every real write and lose its next merge, which
    is a different bug from the one being fixed. ``field_stamps`` on fabric
    records are repaired the same way.

    ``dry_run=True`` (the default) reports what would change and touches
    nothing. Returns per-area counts plus `repaired` / `scanned`. Never raises.

    Operator usage:
        python -c "from CORE import spine_registry as s; print(s.purge_implausible_stamps(False))"
    """
    report: Dict[str, Any] = {"dry_run": bool(dry_run), "repaired": 0,
                              "scanned": 0, "areas": {}, "examples": []}

    def _fix(rec: Any, iso_field: str, area: str) -> None:
        if not isinstance(rec, dict):
            return
        report["scanned"] += 1
        stamp = rec.get("hlc")
        if not stamp or _stamp_adoptable(str(stamp)):
            return
        report["repaired"] += 1
        report["areas"][area] = report["areas"].get(area, 0) + 1
        if len(report["examples"]) < 5:
            report["examples"].append({"area": area, "stamp": str(stamp)[:40],
                                      "from": str(rec.get(iso_field) or "")[:32]})
        if dry_run:
            return
        try:
            from CORE.hlc import normalize_stamp
            rec["hlc"] = normalize_stamp(str(rec.get(iso_field) or ""))
        except Exception as exc:  # pragma: no cover - defensive
            note_failure("spine_registry:L3108", exc)
            rec.pop("hlc", None)

    try:
        with _HOMEOSTAT_LOCK:
            for rec in _HOMEOSTAT.values():
                _fix(rec, "last_at", "homeostat")
        with _SPINE_LOCK:
            for rec in _SPINE_MAP.values():
                _fix(rec, "last_at", "spine_map")
        with _QUARANTINE_LOCK:
            for box in _QUARANTINE_VOTES.values():
                for rec in (box or {}).values():
                    _fix(rec, "at", "quarantine_votes")
        with _FABRIC_LOCK:
            for rec in _FABRIC_STATE.values():
                _fix(rec, "updated_at", "fabric_state")
                stamps = rec.get("field_stamps") if isinstance(rec, dict) else None
                if not isinstance(stamps, dict):
                    continue
                for key, stamp in list(stamps.items()):
                    if stamp and not _stamp_adoptable(str(stamp)):
                        report["repaired"] += 1
                        report["areas"]["fabric_field_stamps"] = \
                            report["areas"].get("fabric_field_stamps", 0) + 1
                        if not dry_run:
                            try:
                                from CORE.hlc import normalize_stamp
                                stamps[key] = normalize_stamp(
                                    str(rec.get("updated_at") or ""))
                            except Exception as exc:  # pragma: no cover - defensive
                                note_failure("spine_registry:L3138", exc)
                                stamps.pop(key, None)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L3140", exc)
        report["error"] = str(exc)[:160]
    return report


def get_module_health() -> Dict[str, Any]:
    """Introspection endpoint for doctor/health tooling."""
    with _SPINE_LOCK:
        spine_nodes = len(_SPINE_MAP)
        spine_events = len(_SPINE_EVENTS)
    with _HOMEOSTAT_LOCK:
        homeostat_worms = len(_HOMEOSTAT)
        outcome_events = _outcome_event_count()
    with _QUARANTINE_LOCK:
        quarantine_findings = len(_QUARANTINE_VOTES)
    with _FABRIC_LOCK:
        fabric_providers = len(_FABRIC_STATE)
    return {
        "version": __version__,
        "spine_nodes": spine_nodes,
        "spine_events": spine_events,
        "homeostat_worms": homeostat_worms,
        "outcome_events": outcome_events,
        "quarantine_findings": quarantine_findings,
        "fabric_providers": fabric_providers,
    }


# ── GOSSIP TRUST (signature + monotonic sequence + key pinning) ──────
# Audit G9 (2026-09-14): merge_spine_gossip() used to adopt a peer record on
# timestamp comparison ALONE, so anything able to write a file into the
# gossip drop dir could inject Q-weights and thereby re-order worm execution
# across an environment. Payloads now carry a monotonic per-env `seq` and,
# when cryptography is importable, an Ed25519 signature over the canonical
# payload plus the signer's public key.
#
# Policy (env AZAZAEL_GOSSIP_POLICY):
#   strict (default) — signature required; unsigned peers refused
#   warn             — verify when present, admit unsigned (legacy peers)
#   off              — no signature checks (seq rollback checks always apply)
#
# Known-signer allowlist (2026-09-28): a VALID signature only proves the
# key, not the identity — with the listener LAN-bound anyone can mint an
# Ed25519 pair and sign whatever they like. `verified` is therefore reserved
# for KNOWN signers: our own key, or a key on the operator-pinned allowlist
# (env AZAZAEL_GOSSIP_TRUSTED_KEYS = JSON path or inline JSON, plus
# ~/.azazael/mesh_trusted_keys.json). Unknown signers fail closed under
# `strict` (reason `unknown_signer`); under `warn` they are admitted
# legacy-style but earn `unpinned`, never `verified` — so the transport's
# fail-closed gate (which demands exactly `verified`) still refuses them.
#
# When cryptography is unavailable or broken, `strict` degrades to
# seq-only enforcement and the merge result says so explicitly
# ("signature": "unavailable") instead of implying the payload was
# authenticated. Degradation is recorded as a spine event.
_GOSSIP_POLICY_ENV = "AZAZAEL_GOSSIP_POLICY"
_GOSSIP_POLICY_DEFAULT = "strict"
_GOSSIP_SEQ_LOCAL = 0                 # our own outgoing counter
_GOSSIP_SEQ_SEEN: Dict[str, int] = {}  # env -> highest seq accepted
_GOSSIP_KEYS: Dict[str, str] = {}      # env -> pinned base64 public key (TOFU)
_GOSSIP_TRUST_LOCK = threading.RLock()
_GOSSIP_TRUSTED_KEYS_ENV = "AZAZAEL_GOSSIP_TRUSTED_KEYS"
_TRUSTED_KEYS_FILE = Path.home() / ".azazael" / "mesh_trusted_keys.json"


def _gossip_policy() -> str:
    """Effective signature policy for this process (strict | warn | off)."""
    raw = (os.environ.get(_GOSSIP_POLICY_ENV) or _GOSSIP_POLICY_DEFAULT)
    pol = raw.strip().lower()
    return pol if pol in ("strict", "warn", "off") else _GOSSIP_POLICY_DEFAULT


def _gossip_crypto():
    """Lazy Ed25519 accessor: (sign_fn, verify_fn, local_pub_b64) or None.

    `sign_fn(canonical) -> signature_b64`; the local public key is returned
    separately so the caller can place it in the payload BEFORE signing — the
    signature covers the key, so signing first and attaching the key after
    silently invalidates every payload (caught by the stubbed-crypto test).

    Imported lazily because spine_registry is documented stdlib-only at
    module level (no heavy imports, no PAM/MemoryBus cycles). audit_trail
    degrades gracefully when its crypto backend is missing or broken, so
    this must too — a broken signing backend must never break execution.
    """
    try:
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from CORE.audit_trail import PUBLIC_KEY_PATH, _get_signing_key
    except Exception as exc:
        note_failure("spine_registry:L3229", exc)
        return None
    try:
        key = _get_signing_key()
        local_pub = _b64.b64encode(PUBLIC_KEY_PATH.read_bytes()).decode("ascii")
    except Exception as exc:
        note_failure("spine_registry:L3234", exc)
        return None
    if not key or not local_pub:
        return None

    def _sign(canonical: str) -> str:
        try:
            priv = ed25519.Ed25519PrivateKey.from_private_bytes(key)
            return _b64.b64encode(
                priv.sign(canonical.encode("utf-8"))).decode("ascii")
        except Exception as exc:
            note_failure("spine_registry:L3244", exc)
            return ""

    def _verify(canonical: str, sig_b64: str, pub_b64: str) -> bool:
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(_b64.b64decode(pub_b64))
            pub.verify(_b64.b64decode(sig_b64), canonical.encode("utf-8"))
            return True
        except Exception as exc:
            note_failure("spine_registry:L3252", exc)
            # Retry against the local public key: same-machine envs sharing
            # a home dir sign with one key, so a peer's declared key may be
            # absent from an older payload while ours still verifies it.
            try:
                pub = ed25519.Ed25519PublicKey.from_public_bytes(
                    PUBLIC_KEY_PATH.read_bytes())
                pub.verify(_b64.b64decode(sig_b64), canonical.encode("utf-8"))
                return True
            except Exception as exc:
                note_failure("spine_registry:L3261", exc)
                return False

    return _sign, _verify, local_pub


def _gossip_canonical(payload: Dict[str, Any]) -> str:
    """Deterministic JSON of the payload minus the signature field itself.

    Every other envelope field (seq, signer_pub, gossip_from, exported_at)
    IS covered by the signature, so a tampered sequence number or a swapped
    public key invalidates it.
    """
    body = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _peer_env(payload: Dict[str, Any]) -> str:
    """Env id claimed by a peer payload (bounded, sanitised for keying)."""
    env = payload.get("gossip_from") or "peer"
    env = str(env).strip()[:40]
    return env or "peer"


def _load_trusted_keys() -> tuple:
    """Operator-pinned known-signer allowlist -> ({env: key}, {keys}).

    Sources are merged: ``AZAZAEL_GOSSIP_TRUSTED_KEYS`` (a path to a JSON
    file, or inline JSON) and ``~/.azazael/mesh_trusted_keys.json``.
    Accepted shapes:

      {"keys": ["<pub>", ...], "envs": {"<env>": "<pub>"}}
      {"<env>": "<pub>", ...}          (bare env -> key map)

    ``keys`` entries are env-agnostic identities; ``envs`` entries bind a
    key to one claimed env. Any parse failure yields an EMPTY allowlist:
    an unreadable allowlist must fail closed (unknown signer), never open.
    """
    env_map: Dict[str, str] = {}
    key_set: set = set()
    sources = []
    raw = (os.environ.get(_GOSSIP_TRUSTED_KEYS_ENV) or "").strip()
    if raw:
        try:
            p = Path(raw)
            sources.append(p.read_text(encoding="utf-8") if p.is_file() else raw)
        except Exception as exc:
            note_failure("spine_registry:L3308", exc)
            sources.append(raw)
    try:
        if _TRUSTED_KEYS_FILE.exists():
            sources.append(_TRUSTED_KEYS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("spine_registry:L3313", exc)
        pass
    for src in sources:
        try:
            data = json.loads(src)
        except Exception as exc:
            note_failure("spine_registry:L3318", exc)
            continue  # unreadable source: fail closed (empty contribution)
        if not isinstance(data, dict):
            continue
        for k in (data.get("keys") or []):
            if isinstance(k, str) and k:
                key_set.add(k)
        envs = data.get("envs") if isinstance(data.get("envs"), dict) else data
        for env, val in (envs or {}).items():
            if str(env) in ("version", "keys", "envs", "updated_at"):
                continue
            for v in (val if isinstance(val, list) else [val]):
                if isinstance(v, str) and v:
                    env_map[str(env)[:40]] = v
    return env_map, key_set


def _signer_is_known(env: str, pub: str) -> bool:
    """True when ``pub`` is a pinned known signer for this mesh.

    Membership is (env-bound key match) or (env-agnostic key list); the
    caller separately treats OUR OWN key as known.
    """
    if not pub:
        return False
    env_map, key_set = _load_trusted_keys()
    return env_map.get(env) == pub or pub in key_set


def check_gossip_trust(payload: Any,
                       policy: Optional[str] = None) -> Dict[str, Any]:
    """Validate a peer payload's provenance before it may be merged.

    Enforces, in order:
      1. **Monotonic seq per env.** A payload whose seq is strictly below the
         highest seq already accepted from that env is a replay and is
         refused. Equal seq is admitted, because merge is idempotent by
         contract and an identical replay changes nothing (that property is
         covered by the existing idempotency tests).
      2. **Ed25519 signature** over the canonical payload, verified against
         the env's TOFU-pinned public key. First key seen for an env wins;
         a later payload from the same env presenting a different key is
         refused as impersonation.
      3. **Known-signer allowlist.** A cryptographically valid signature
         earns `verified` only when the signer is our own key or an
         operator-pinned key; otherwise the verdict is `unpinned` and,
         under strict, the payload is refused (`unknown_signer`).

    Returns {ok, reason, env, seq, signature, policy, key_pinned}.
    Never raises — a malformed payload is simply untrusted.
    """
    pol = policy or _gossip_policy()
    res: Dict[str, Any] = {
        "ok": False, "reason": "", "env": "peer", "seq": 0,
        "signature": "skipped", "policy": pol, "key_pinned": False,
    }
    try:
        if not isinstance(payload, dict):
            res["reason"] = "not_a_payload"
            return res
        env = _peer_env(payload)
        res["env"] = env
        try:
            seq = int(payload.get("seq") or 0)
        except (TypeError, ValueError) as exc:
            note_failure("spine_registry:L3382", exc)
            seq = 0
        res["seq"] = seq

        # 1. replay / rollback guard (crypto-independent)
        with _GOSSIP_TRUST_LOCK:
            seen = int(_GOSSIP_SEQ_SEEN.get(env, -1))
            pinned = _GOSSIP_KEYS.get(env, "")
        if seq < seen:
            res["reason"] = "replayed_seq"
            return res

        sig = str(payload.get("signature") or "")
        pub = str(payload.get("signer_pub") or "")
        res["signer_pub"] = pub

        # 2. key pinning: an env may not change its identity key
        if pub and pinned and pub != pinned:
            res["reason"] = "key_changed"
            res["signature"] = "invalid"
            return res
        res["key_pinned"] = bool(pinned)

        crypto = _gossip_crypto()
        if pol == "off":
            res["signature"] = "skipped"
            res["ok"] = True
            return res

        if crypto is None:
            # Degraded: cannot authenticate. Under strict we still enforce the
            # sequence guard but must NOT claim authentication happened.
            res["signature"] = "unavailable"
            res["ok"] = True
            res["reason"] = "crypto_unavailable_seq_only"
            return res

        _sign, _verify, _local_pub = crypto
        if not sig:
            if pol == "strict":
                res["reason"] = "unsigned_payload"
                res["signature"] = "missing"
                return res
            res["signature"] = "missing"
            res["ok"] = True
            return res

        if not pub:
            res["reason"] = "missing_signer_key"
            res["signature"] = "missing"
            return res

        if _verify(_gossip_canonical(payload), sig, pub):
            # A valid signature proves the KEY, not the identity: on a
            # LAN-bound port anyone can mint a key. `verified` is reserved
            # for KNOWN signers (own key / operator-pinned allowlist).
            if (_local_pub and pub == _local_pub) or _signer_is_known(env, pub):
                res["signature"] = "verified"
                res["ok"] = True
                return res
            res["signature"] = "unpinned"
            if pol == "strict":
                res["reason"] = "unknown_signer"
                return res
            res["reason"] = "unknown_signer_admitted"
            res["ok"] = True
            return res

        res["reason"] = "bad_signature"
        res["signature"] = "invalid"
        return res
    except Exception as exc:  # never raise out of a trust check
        note_failure("spine_registry:L3453", exc)
        res["reason"] = f"trust_error:{str(exc)[:60]}"
        return res


def _remember_gossip(trust: Dict[str, Any]) -> None:
    """Commit an accepted payload's env/seq/key to the anti-replay state."""
    try:
        env = trust.get("env") or "peer"
        seq = int(trust.get("seq") or 0)
        pub = str(trust.get("signer_pub") or "")
        with _GOSSIP_TRUST_LOCK:
            if seq > int(_GOSSIP_SEQ_SEEN.get(env, -1)):
                _GOSSIP_SEQ_SEEN[env] = seq
            # First key seen for an env is pinned (trust-on-first-use); later
            # payloads from that env must present the same key.
            if pub and not _GOSSIP_KEYS.get(env):
                _GOSSIP_KEYS[env] = pub
    except Exception as exc:
        note_failure("spine_registry:L3471", exc)
        pass


def get_gossip_trust_state() -> Dict[str, Any]:
    """Introspection: local sequence, per-env high-water marks, pinned keys."""
    with _GOSSIP_TRUST_LOCK:
        env_map, key_set = _load_trusted_keys()
        return {
            "policy": _gossip_policy(),
            "local_seq": _GOSSIP_SEQ_LOCAL,
            "seq_seen": dict(_GOSSIP_SEQ_SEEN),
            "pinned_keys": {k: v[:16] + "..." for k, v in _GOSSIP_KEYS.items()},
            "crypto_available": _gossip_crypto() is not None,
            "trusted_envs": {k: v[:16] + "..." for k, v in env_map.items()},
            "trusted_key_count": len(key_set),
        }


# ── SHARED ANOMALY MODEL (G13/G5 over the gossip transport) ──────────
# The fitted autoencoder is the mesh's only trained neural network. It fits
# per-process, so without transport every env relearns from zero and the mesh
# never accumulates a single detector — the exact "per-instance, not persisted,
# not shared" defect G13 named.
#
# The transport deliberately reuses the signed gossip envelope: the model is
# simply another field in the payload, so it is covered by the Ed25519
# signature and by the replay/key-pinning gate with **no separate trust
# path**. It can never travel unsigned, and a tampered model is a
# `bad_signature` rejection like any other edited field.
#
# `_ANOMALY_MODEL` is a *transport snapshot*, not the live detector. The
# authority for a running process is the worm instance (which persists to
# ~/.azazael/anomaly_model.json); this copy exists so a trained detector can be
# handed to a peer that has seen fewer observations.
_ANOMALY_MODEL: Dict[str, Any] = {}
_ANOMALY_MODEL_SOURCE = "local"
_ANOMALY_MODEL_LOCK = threading.RLock()
_ANOMALY_MAX_DIM = 512
_ANOMALY_MAX_OBSERVATIONS = 10_000_000


def _valid_anomaly_model(model: Any) -> bool:
    """Structural validation for a model arriving from disk or a peer.

    Shape-checks both weight matrices and bounds every number, so a malformed
    or hostile payload cannot cause a large allocation, load a NaN weight, or
    reach a peer's net at all.
    """
    if not isinstance(model, dict):
        return False
    try:
        # .get() on an isinstance-narrowed dict[Unknown, Unknown] returns
        # "Unknown | None"; basedpyright flags the unknown RHS even under an
        # explicit annotation. The union below intentionally admits str/None so
        # the int() call can hit the guarded except below.
        raw_dim: float | str | None = model.get("input_dim")  # pyright: ignore[reportUnknownVariableType]
        raw_hidden: float | str | None = model.get("hidden_dim")  # pyright: ignore[reportUnknownVariableType]
        n = int(raw_dim or 0)
        h = int(raw_hidden or 0)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L3525", exc)
        return False
    if not (1 <= n <= _ANOMALY_MAX_DIM and 1 <= h <= _ANOMALY_MAX_DIM):
        return False
    w_enc = model.get("W_enc")
    w_dec = model.get("W_dec")
    if not isinstance(w_enc, list) or not isinstance(w_dec, list):
        return False
    if len(w_enc) != h or len(w_dec) != n:
        return False
    try:
        for row in w_enc:
            if not isinstance(row, list) or len(row) != n:
                return False
            for v in row:
                f = float(v)
                if f != f or abs(f) > 1e6:
                    return False
        for row in w_dec:
            if not isinstance(row, list) or len(row) != h:
                return False
            for v in row:
                f = float(v)
                if f != f or abs(f) > 1e6:
                    return False
        obs = int(model.get("observations") or 0)
    except (TypeError, ValueError) as exc:
        note_failure("spine_registry:L3551", exc)
        return False
    return 0 <= obs <= _ANOMALY_MAX_OBSERVATIONS


def publish_anomaly_model(model: Any) -> Dict[str, Any]:
    """Offer a fitted model for transport. Monotone: strictly-better-trained only.

    The single rule (more observations wins) prevents the ping-pong where two
    envs holding snapshots overwrite each other forever, and it keeps the best
    detector regardless of which environment produced it.
    """
    if not _valid_anomaly_model(model):
        return {"published": False, "reason": "invalid_model"}
    obs = int(model.get("observations") or 0)
    with _ANOMALY_MODEL_LOCK:
        current = int((_ANOMALY_MODEL or {}).get("observations") or 0)
        if _ANOMALY_MODEL and obs <= current:
            return {"published": False, "reason": "not_better_trained",
                    "observations": obs, "current": current}
        _ANOMALY_MODEL.clear()
        _ANOMALY_MODEL.update(dict(model))
        global _ANOMALY_MODEL_SOURCE
        _ANOMALY_MODEL_SOURCE = "local"
    return {"published": True, "observations": obs, "current": current}


def get_anomaly_model() -> Optional[Dict[str, Any]]:
    """Transport snapshot of the shared model (deep copy), or None if unset."""
    with _ANOMALY_MODEL_LOCK:
        if not _ANOMALY_MODEL:
            return None
        return {k: ([list(r) for r in v] if isinstance(v, list) else v)
                for k, v in _ANOMALY_MODEL.items()}


def get_anomaly_model_meta() -> Dict[str, Any]:
    """Provenance of the snapshot: who fitted it and how well trained it is."""
    with _ANOMALY_MODEL_LOCK:
        return {"present": bool(_ANOMALY_MODEL),
                "source": _ANOMALY_MODEL_SOURCE,
                "observations": int((_ANOMALY_MODEL or {}).get("observations") or 0)}


# ── GOSSIP (cross-env merge) ────────────────────────────────────────
# The spine_state.json payload (see save_spine_state) is the gossip unit.
# export_spine_gossip() produces it; merge_spine_gossip() absorbs a peer's
# payload with a recency-wins conflict policy. Keyed by "env:worm", so
# same-named worms in different envs never collide, and merging is
# commutative/idempotent for identical payloads.

def _iso_or(ts: Any) -> str:
    """Best-effort ISO timestamp extraction from a record ("" if absent)."""
    if isinstance(ts, dict):
        ts = ts.get("updated_at") or ts.get("last_at") or ""
    return ts if isinstance(ts, str) else ""


def export_spine_gossip() -> Dict[str, Any]:
    """Export the gossip payload to send to peer environments.

    Reuses the persistence payload (same shape save_spine_state writes)
    but does NOT touch the filesystem. Envelope fields ("gossip_from",
    "exported_at") let the receiver trace provenance.

    Every payload carries a strictly increasing `seq` for anti-replay, and
    is Ed25519-signed over its canonical form when cryptography is
    available (`signature` + `signer_pub`). The signature covers seq, the
    public key and every state collection, so none of them can be edited in
    transit. When crypto is unavailable the payload is exported unsigned and
    receivers handle the degradation explicitly.
    """
    global _GOSSIP_SEQ_LOCAL
    with _SPINE_LOCK:
        spine_map = {k: dict(v) for k, v in _SPINE_MAP.items()}
        spine_events = list(_SPINE_EVENTS)
    with _HOMEOSTAT_LOCK:
        homeostat = {k: {kk: (list(vv) if isinstance(vv, list) else vv)
                         for kk, vv in v.items()}
                     for k, v in _HOMEOSTAT.items()}
        q_weights = dict(_Q_WEIGHTS)
        # Travel with the weights so a peer adopting a Q value does not read as
        # a starved key on the receiving monitor (the counts are the evidence
        # behind the weight).
        q_update_counts = dict(_Q_UPDATE_COUNTS)
        # And deletions travel with the state, for the same reason and one
        # level up. The tombstone made a purge durable against DISK; it did
        # nothing against the mesh, so a peer that still held the key
        # re-adopted it on the next exchange and the next save wrote it back
        # -- the resurrection this mechanism exists to stop, moved from the
        # disk path to the wire. Signed with everything else, so a tombstone
        # cannot be forged or edited in transit, and only ever created for
        # DECLARED fixture keys, which is what bounds what one env can delete
        # on another.
        purged_keys = dict(_PURGED_KEYS)
    with _GOSSIP_TRUST_LOCK:
        _GOSSIP_SEQ_LOCAL += 1
        seq = _GOSSIP_SEQ_LOCAL
    quarantine_votes = get_quarantine_votes()
    quarantined_items = get_quarantine_items(active_only=False)
    with _FABRIC_LOCK:
        fabric_state = {k: dict(v) for k, v in _FABRIC_STATE.items()}
    payload: Dict[str, Any] = {
        "version": __version__,
        "exported_at": _now_iso(),
        # Canonical env id, not a stale literal. `os.environ.get("AZAZAEL_ENV",
        # "local")` claimed "local" for EVERY host that does not set the env
        # var, so the provenance this payload SIGNED and a peer recorded was
        # wrong by construction (a Windows host said "local", never
        # "windows"). `_local_env_name()` is the audit-2026-09-15 resolver:
        # AZAZAEL_ENV still wins (two logical envs on one machine for gossip
        # testing), else CORE.env_id's canonical name.
        "gossip_from": _local_env_name(),
        "seq": seq,
        "signer_pub": "",
        "signature": "",   # filled below when crypto is available
        "spine_map": spine_map,
        "spine_events": spine_events,
        "homeostat": homeostat,
        "q_weights": q_weights,
        "q_update_counts": q_update_counts,
        # Inside the signed envelope: `_gossip_canonical` covers every field
        # except the signature itself, so the model needs no extra trust
        # machinery and cannot be edited in transit.
        "anomaly_model": get_anomaly_model(),
        # Cross-env quorum votes and fabric operational state travel with the
        # same signed payload: a peer's quarantine vote is what lets the
        # quorum gate on THIS host actually reach `decided`, and fabric state
        # is what stops the mesh forgetting its routing/consensus on restart.
        "quarantine_votes": quarantine_votes,
        # Inside the signed envelope for the same reason the votes are: a
        # quarantine ORDERED by quorum on a peer must actually isolate the worm
        # on this host, and a release must travel as fast as the order did.
        "quarantined_items": quarantined_items,
        "fabric_state": fabric_state,
        "purged_keys": purged_keys,
    }
    crypto = _gossip_crypto()
    if crypto is not None:
        try:
            sign, _verify, local_pub = crypto
            # The key must be IN the payload before signing — it is covered
            # by the signature (see _gossip_crypto's contract).
            payload["signer_pub"] = local_pub
            payload["signature"] = sign(_gossip_canonical(payload))
        except Exception as exc:
            note_failure("spine_registry:L3696", exc)
            pass  # unsigned export; receiver reports the degradation
    return payload


def merge_spine_gossip(payload: Any) -> Dict[str, Any]:
    """Merge a peer's gossip payload into local state (recency wins).

    Conflict policy per collection:
    - spine_map / homeostat: per-key, the record with the newer
      timestamp (updated_at / last_at) replaces the older one; missing
      timestamps never overwrite existing records.
    - q_weights: the value whose homeostat record is newer wins; with
      no ledger evidence, local value is kept (conservative).
    - spine_events: unioned, re-sorted by timestamp, re-capped.

    Defensive by contract: wrong types, missing keys, and malformed
    records are skipped, never raised. Returns a merge summary.
    Idempotent: re-merging the same payload changes nothing.
    """
    stats: Dict[str, Any] = {"merged": False, "spine_nodes": 0, "homeostat_worms": 0,
                             "q_weights": 0, "events": 0, "tombstoned": 0,
                             "purged_blocked": 0, "purged_rejected": 0}
    try:
        if not isinstance(payload, dict):
            return stats
        # Provenance gate: replay + signature + key pinning (audit G9).
        trust = check_gossip_trust(payload)
        stats.update({"env": trust.get("env", ""), "seq": trust.get("seq", 0),
                      "signature": trust.get("signature", "?"),
                      "policy": trust.get("policy", "?")})
        if not trust.get("ok"):
            stats["rejected"] = trust.get("reason", "untrusted")
            try:
                record_spine_event("gossip_rejected", {
                    "env": trust.get("env", "?"),
                    "reason": stats["rejected"],
                    "seq": trust.get("seq", 0),
                    "signature": trust.get("signature", "?"),
                })
            except Exception as exc:
                note_failure("spine_registry:L3736", exc)
                pass
            return stats
        remote_map = payload.get("spine_map")
        remote_home = payload.get("homeostat")
        remote_q = payload.get("q_weights")
        remote_counts = payload.get("q_update_counts")
        remote_events = payload.get("spine_events")
        if not all(isinstance(x, dict) for x in
                   (remote_map or {}, remote_home or {}, remote_q or {})):
            # tolerate None collections; a non-dict one is ignored
            if remote_map is not None and not isinstance(remote_map, dict):
                remote_map = {}
            if remote_home is not None and not isinstance(remote_home, dict):
                remote_home = {}
            if remote_q is not None and not isinstance(remote_q, dict):
                remote_q = {}
        remote_map = remote_map or {}
        remote_home = remote_home or {}
        remote_q = remote_q or {}

        with _SPINE_LOCK:
            for k, v in (remote_map or {}).items():
                if not isinstance(v, dict):
                    continue
                if _purged_blocked(k):
                    stats["purged_blocked"] += 1
                    continue
                cur = _SPINE_MAP.get(k)
                if cur is None:
                    _SPINE_MAP[k] = v
                    stats["spine_nodes"] += 1
                    continue
                # recency wins; strictly-newer only so re-merging the
                # same payload is a true no-op (idempotency contract).
                if _stamp_newer(_rec_stamp(v, "updated_at"),
                                _rec_stamp(cur, "updated_at")):
                    _SPINE_MAP[k] = v
                    stats["spine_nodes"] += 1
            if isinstance(remote_events, list):
                known = {json.dumps(e, sort_keys=True) for e in _SPINE_EVENTS}
                for e in remote_events:
                    if isinstance(e, dict):
                        sig = json.dumps(e, sort_keys=True)
                        if sig not in known:
                            _SPINE_EVENTS.append(e)
                            known.add(sig)
                            stats["events"] += 1
                _SPINE_EVENTS.sort(key=lambda e: e.get("at", ""))
                if len(_SPINE_EVENTS) > _SPINE_EVENTS_MAX:
                    del _SPINE_EVENTS[:len(_SPINE_EVENTS) - _SPINE_EVENTS_MAX]

        with _HOMEOSTAT_LOCK:
            # Adopt the peer's tombstones BEFORE the record loops, and drop
            # any local copy of a key they name. A deletion that does not
            # delete is the defect; without this block a peer's payload
            # re-adopts a key this env already purged, and the purge is not
            # durable against the network.
            #
            # Validated, not trusted: only a DECLARED fixture key may be
            # tombstoned by a peer. The set is bounded by a static declaration
            # both sides can check independently, so gossip cannot be used to
            # delete real learned weights, and the union is safe because "this
            # key is synthetic" is a fact rather than an opinion.
            remote_purged = payload.get("purged_keys") or {}
            if isinstance(remote_purged, dict):
                for raw in remote_purged:
                    key = str(raw)
                    if not _is_fixture_key(key):
                        stats["purged_rejected"] += 1
                        continue
                    if key in _PURGED_KEYS:
                        continue
                    _PURGED_KEYS[key] = "fixture"
                    stats["tombstoned"] += 1
                    for table in (_Q_WEIGHTS, _Q_UPDATE_COUNTS,
                                  _Q_DREAM_COUNTS, _Q_VARIANCE, _HOMEOSTAT):
                        table.pop(key, None)
                if len(_PURGED_KEYS) > _PURGED_MAX:
                    for k in list(_PURGED_KEYS)[:len(_PURGED_KEYS) - _PURGED_MAX]:
                        _PURGED_KEYS.pop(k, None)
            # Local ledger stamps, captured BEFORE the loop below replaces
            # those records wholesale. The q merge needs to know whether the
            # peer's evidence was newer than ours *before* the merge; after it,
            # local and remote are the same record and the comparison is
            # vacuous -- which is how the q merge came to ignore evidence
            # entirely and keep local on every known key forever.
            local_ledger_stamps = {
                k: _rec_stamp(v, "last_at") for k, v in _HOMEOSTAT.items()
                if isinstance(v, dict)
            }
            for k, v in (remote_home or {}).items():
                if not isinstance(v, dict):
                    continue
                if _purged_blocked(k):
                    stats["purged_blocked"] += 1
                    continue
                cur = _HOMEOSTAT.get(k)
                if cur is None:
                    _HOMEOSTAT[k] = v
                    stats["homeostat_worms"] += 1
                    continue
                if _stamp_newer(_rec_stamp(v, "last_at"),
                                _rec_stamp(cur, "last_at")):
                    _HOMEOSTAT[k] = v
                    stats["homeostat_worms"] += 1
            for k, w in (remote_q or {}).items():
                if _purged_blocked(k):
                    # A peer that has not heard the tombstone yet still ships
                    # the weight. Refusing it here is what makes the deletion
                    # hold without requiring both envs to be purged by hand.
                    stats["purged_blocked"] += 1
                    continue
                try:
                    wv = max(_Q_MIN, min(_Q_MAX, float(w)))
                except (TypeError, ValueError) as exc:
                    note_failure("spine_registry:L3851", exc)
                    continue
                # Recency decides, which is what this function's docstring has
                # always claimed and what the old "conservative" comment only
                # appeared to do: the peer's value is adopted when the peer's
                # LEDGER record for that key is newer than the local record we
                # held before this merge, and a key we have no record for is
                # adopted outright. An empty remote stamp never wins
                # (`_stamp_newer` refuses empties), so a peer that ships
                # weights without evidence still cannot displace our value.
                #
                # Strictly-newer plus counting only CHANGES keeps a re-merge a
                # true no-op even for keys adopted with no ledger record on
                # either side, which the idempotency contract requires.
                remote_rec = (remote_home or {}).get(k)
                remote_stamp = (_rec_stamp(remote_rec, "last_at")
                                if isinstance(remote_rec, dict) else "")
                local_stamp = local_ledger_stamps.get(k)
                adopt = (local_stamp is None
                         or _stamp_newer(remote_stamp, local_stamp))
                if adopt and _Q_WEIGHTS.get(k) != wv:
                    _Q_WEIGHTS[k] = wv
                    stats["q_weights"] += 1
            # Counts are evidence, not a value: take the MAX so a key adopted
            # from a peer still shows why it is trusted rather than reading as
            # never-pulled. Never lowers a local count.
            if isinstance(remote_counts, dict):
                for k, n in remote_counts.items():
                    if _purged_blocked(k):
                        continue
                    # Clamped by `_adopt_evidence`: a peer must not be able to
                    # resurrect a count this env has explicitly invalidated.
                    _Q_UPDATE_COUNTS[k] = _adopt_evidence(k, n)

        # Shared anomaly model. Only reached after the trust gate, so this is
        # an authenticated, non-replayed, key-pinned model — and it is
        # explicitly NOT adopted when this env already has a better-trained
        # one (that is what stops two envs overwriting each other forever).
        remote_model = payload.get("anomaly_model")
        if remote_model is not None:
            if not _valid_anomaly_model(remote_model):
                stats["anomaly_model"] = "rejected"
            else:
                remote_obs = int(remote_model.get("observations") or 0)
                with _ANOMALY_MODEL_LOCK:
                    local_obs = int((_ANOMALY_MODEL or {}).get("observations") or 0)
                    if remote_obs > local_obs:
                        _ANOMALY_MODEL.clear()
                        _ANOMALY_MODEL.update(dict(remote_model))
                        global _ANOMALY_MODEL_SOURCE
                        _ANOMALY_MODEL_SOURCE = f"peer:{stats.get('env') or 'peer'}"
                        stats["anomaly_model"] = "adopted"
                    else:
                        stats["anomaly_model"] = "kept_local"
        # Cross-env quorum votes + fabric operational state (authenticated: we
        # are past the trust gate). Both merge per-key with recency wins, so
        # re-merging the same payload stays a no-op.
        q_merged = _merge_quarantine(payload.get("quarantine_votes"))
        qi_merged = _merge_quarantine_items(payload.get("quarantined_items"))
        f_merged = _merge_fabric(payload.get("fabric_state"))
        if q_merged:
            stats["quarantine_votes"] = q_merged
        if qi_merged:
            stats["quarantined_items"] = qi_merged
        if f_merged:
            stats["fabric_state"] = f_merged
        # Acceptance commits the seq high-water mark and pins the env's key.
        _remember_gossip(trust)
        stats["merged"] = True
        record_spine_event("gossip_merged", {
            "from": payload.get("gossip_from", "peer"),
            **stats,
        })
        return stats
    except Exception as exc:
        note_failure("spine_registry:L3925", exc)
        return {"merged": False, "error": str(exc)[:120]}


# ── GOSSIP TRANSPORT (shared-filesystem drop) ───────────────────────
# Each env writes its payload to ~/.azazael/gossip/<env>.json and merges
# every other env's file it finds. Works across windows/wsl2/bare envs
# sharing a home volume; absence of peers is a clean no-op. Atomic writes
# only; readers tolerate missing/corrupt files.

_GOSSIP_DIR = Path.home() / ".azazael" / "gossip"
_GOSSIP_MAX_AGE_S = 24 * 3600.0  # ignore peer files older than 24h


def _local_env_name() -> str:
    """Local env id for gossip file naming — canonical (CORE.env_id).

    Audit 2026-09-15 (finding #4): this was a third stale 3-way copy whose
    docstring claimed it matched spine.py's _local_env(). Both returned
    "wsl2" while envmesh had split the bucket in two, so the gossip file this
    host wrote could never be matched by a peer's Kademlia lookup, which
    keyed on the 5-way id. Delegation, not a copy — and CORE.env_id is
    stdlib-only, so it stays safe for this module's "no heavy imports" rule.
    AZAZAEL_ENV still wins (that is the supported way to run two logical envs
    on one machine for gossip testing).
    """
    try:
        from CORE.env_id import env_id
        return env_id()[:40]
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("spine_registry:L3954", exc)
        env = os.environ.get("AZAZAEL_ENV")
        if env:
            return env[:40]
        return "windows" if os.name == "nt" else "bare"


def gossip_exchange(publish: bool = True) -> Dict[str, Any]:
    """Publish local spine state and merge peers' state from the drop dir.

    1. Export payload (export_spine_gossip).
    2. Write ~/.azazael/gossip/<env>.json (atomic tmp+replace).
    3. Merge every other env's fresh-enough file via merge_spine_gossip.

    `publish=False` skips only the write (used by the cycle's quota gate):
    peers are still merged, because throttling our own visibility must not
    blind us to theirs. The summary always states which happened via
    `published` / `publish_skipped`, so a suppressed publish is observable
    rather than silent.

    Never raises; returns a summary {exchanged, env, peers, merges}.
    """
    summary: Dict[str, Any] = {"exchanged": False, "env": _local_env_name(),
                               "peers": [], "merges": {}}
    try:
        payload = export_spine_gossip()
        mine = _GOSSIP_DIR / f"{summary['env']}.json"
        if not publish:
            summary["published"] = False
            summary["publish_skipped"] = "quota"
        else:
            _GOSSIP_DIR.mkdir(parents=True, exist_ok=True)
            tmp = mine.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(str(tmp), str(mine))
            summary["published"] = True

        now = datetime.now().timestamp()
        for f in sorted(_GOSSIP_DIR.glob("*.json")):
            if f.name == mine.name:
                continue
            env_name = f.stem
            try:
                if now - f.stat().st_mtime > _GOSSIP_MAX_AGE_S:
                    continue  # stale peer drop
                peer_payload = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                note_failure("spine_registry:L4000", exc)
                continue  # corrupt/in-flight file — skip, never raise
            summary["merges"][env_name] = merge_spine_gossip(peer_payload)
            summary["peers"].append(env_name)
        summary["exchanged"] = True
        return summary
    except Exception as exc:
        note_failure("spine_registry:L4006", exc)
        summary["error"] = str(exc)[:120]
        return summary
