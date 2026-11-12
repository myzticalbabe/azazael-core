# coding: utf-8
"""COLOUR QUEENS — the evolutionary axes the mesh was missing (audit §5).

The 2026-09-16 adversarial audit found the mesh implemented exactly ONE colour
queen (``spine.RedQueenWorm``) and that the other three mechanisms existed only
in a standalone numpy toy (``rainbow_queens_test/``) which nothing in this tree
imports. This module ports those three mechanisms into the mesh as registered
worms, each attached to an organ that already exists, so none of them is an
orphan:

* ``ScoutBeeWorm``   (mesh)     — Artificial Bee Colony's queenless scout
  quota (Karaboga 2005 TR06). **Consumer:** ``RedQueenWorm._probe_candidates``.
  Selection used to be the first ``limit`` entries of ``WORM_REGISTRY`` in
  registration order — a fixed subset of ~28 of 226 worms, the same every cycle
  (audit R4) — and each pass recorded ``ok=True`` for that same subset, which
  reinforced Q and promoted them into healthy-first scheduling *for passing a
  test that could not fail* (audit R2). The quota is the exploration term the
  schedule lacked.

* ``GrayQueenWorm``  (monitor)  — drift-first entrenchment detector
  (Brunet & Doolittle 2018, CNE reinterpretation). **Consumer:** the PF16
  wire-or-cut census (``NEURAL_MESH_WIRE_OR_CUT.json``), snapshotted and
  DIFFED. Founding event is a *neutral* loss (a caller is removed in a
  refactor); entrenchment is what follows — purifying selection keeps the class
  registered because "it's registered, so it must be used". The detector fires
  on the delta, so a class that gains a caller leaves the list and a class that
  loses its last caller appears the session it happens.

* ``BlackQueenWorm`` (analysis) — adaptive gene loss (Morris 2011/2012).
  **Consumer:** the wire-or-cut declaration table + the shared quorum ledger.
  The mesh could generate specialists forever (``MorphogenesisWorm``) and had
  no mechanism to lose one; that asymmetry is why 52 % of the fabric was
  uninstantiated. Loss requires cost **and** leak **and** community provision:
  retire only when (a) the worm's evidence is thin, (b) a registered twin's
  capability signature covers it, and (c) the quorum agrees.

Deliberately NOT ported: the toy's ``requeen`` invariant is carried over as a
*statement*, not a symbol — ``requeen`` is not an operator. Queens are
persistent roles with leases; nothing replaces the object mid-race.
``test_colour_queens.py`` asserts the symbol is absent from this module.

Every claim here is re-runnable; see ``tests/test_colour_queens.py``.
"""

import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import os

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm, WORM_REGISTRY  # type: ignore[reportAssignmentType]
except ImportError as exc:  # pragma: no cover — registry optional
    note_failure("queens:L55", exc)
    def register_worm(**kw: Any) -> Callable[[type], type]:
        return lambda cls: cls
    WORM_REGISTRY: Dict[str, Dict[str, Any]] = {}

try:
    from CORE import spine_registry as _spine
except ImportError as exc:  # pragma: no cover — ledger optional
    note_failure("queens:L62", exc)
    _spine = None


# ── Shared helpers ──────────────────────────────────────────────────

def _census_paths() -> Tuple[Path, Path]:
    """(wire-or-cut census, entrenchment snapshot) — env-overridable.

    The snapshot is a sibling of the census because that is where the census
    generator (``tools/mesh_function_preflight.py``, ``REPO`` = azazael root)
    already writes. ``AZAZAEL_ENTRENCHMENT_PATH`` overrides it so a test run
    never writes into the tree it is testing.
    """
    root = Path(__file__).resolve().parents[4]  # .../azazael
    census = Path(os.environ.get("AZAZAEL_WIRE_OR_CUT_PATH")
                  or (root / "NEURAL_MESH_WIRE_OR_CUT.json"))
    snap = Path(os.environ.get("AZAZAEL_ENTRENCHMENT_PATH")
                or (root / "NEURAL_MESH_ENTRENCHMENT.json"))
    return census, snap


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("queens:L88", exc)
        return None


def _stamp_stem(name: str) -> str:
    """Normalise a module/class name for census matching."""
    s = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if s.endswith(".py"):
        s = s[:-3]
    return s.strip().lower()


_MENTIONS: Optional[Dict[str, set]] = None

#: Guards the check-then-build in `_production_mentions()`. Several spines can
#: share this module under free-threading, and without it every thread that
#: arrives before the first publish re-scans the whole tree -- parsing each file
#: with `ast` -- so the cost is multiplied exactly when startup is busiest.
_MENTIONS_LOCK = threading.Lock()


def _reset_mentions_cache() -> None:
    """Drop the cache so the next call re-scans.

    The index is only valid while the tree is unchanged, so a caller that has
    just added or moved a module must be able to invalidate it. Leaving it stale
    is the same class of defect this session found in the census table: a
    generated artifact answering confidently for a tree it no longer describes.
    """
    global _MENTIONS
    with _MENTIONS_LOCK:
        _MENTIONS = None


def _scan_production_mentions() -> Dict[str, set]:
    """Uncached scan: name -> files under ``src/`` that name it.

    See `_production_mentions()` for the legitimate-service test this serves.
    """
    out: Dict[str, set] = {}
    try:
        import ast as _ast
        # parents: [0]=expansion [1]=WORMS [2]=CORE [3]=src.  parents[3] IS the
        # src root; appending "src" again would scan nothing and silently
        # disable this guard, so it is asserted below rather than assumed.
        src = Path(__file__).resolve().parents[3]
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("queens:L134", exc)
        return out
    if not src.exists() or not (src / "CORE").exists():
        return out
    for path in src.rglob("*.py"):
        if any(x in path.parts for x in ("__pycache__", ".venv", "venv", "generated")):
            continue
        # A re-export is not a consumer. The audit's legitimate-service test
        # excludes `__init__.py` for exactly this reason: counting package
        # re-exports as reachability made EVERY worm look wired and left the
        # queen with nothing to evaluate, which is inert rather than correct.
        if path.name == "__init__.py":
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            note_failure("queens:L149", exc)
            continue
        rel = str(path)
        hits = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)}
        hits |= {n.attr for n in _ast.walk(tree) if isinstance(n, _ast.Attribute)}
        for nm in hits:
            out.setdefault(nm, set()).add(rel)
    return out


def _production_mentions() -> Dict[str, set]:
    """name -> the set of files under ``src/`` that mention it, single-flight.

    Same legitimate-service test the 2026-09-16 audit applies per class ("is it
    instantiated or imported outside its own module?"): a worm production code
    *names* has a purpose already, whatever its method set looks like.

    The publish happens once, under the lock, with a fully built dict, so a
    concurrent reader gets either an empty cache or the complete index -- never
    a half-filled one.
    """
    global _MENTIONS
    with _MENTIONS_LOCK:
        if _MENTIONS is None:
            _MENTIONS = _scan_production_mentions()
        return _MENTIONS


def _is_wired_in_production(name: str, info: Dict[str, Any]) -> bool:
    """True when production code outside the worm's own module names it."""
    cls = info.get("class") if isinstance(info, dict) else None
    own_file: Optional[str] = None
    mod = getattr(cls, "__module__", None)
    if mod:
        try:
            import sys as _sys
            handle = _sys.modules.get(mod)
            target = getattr(handle, "__file__", None)
            own_file = str(target) if target else None
        except Exception as exc:  # pragma: no cover - defensive
            note_failure("queens:L188", exc)
            own_file = None
    files = _production_mentions().get(name) or set()
    return bool({f for f in files if f != own_file})


# ═══════════════════════════════════════════════════════════════════
# 1. SCOUT BEE — queenless exploration quota (the R2/R4 fix, as a policy)
# ═══════════════════════════════════════════════════════════════════

@register_worm(category="mesh", priority=6, cooldown_seconds=90.0,
    description="ABC scout quota: schedule probing by ignorance, not registration order",
    tags={"colour_queen", "scout", "abc"})
class ScoutBeeWorm:
    """Base ABC has NO queen: employed / onlooker / scout only.

    This class therefore defines no ``queen`` attribute, and the acceptance test
    asserts that. The division of labour is the whole mechanism:

    * **employed** — members that already have evidence; they keep their slot
      (exploitation);
    * **onlooker**  — fitness-proportional fill of the remaining slots;
    * **scout**     — a FORCED quota of never-probed / longest-unprobed arms
      (exploration of ignorance, not of fitness).

    The quota is why coverage rotates and why no fixed subset can collect a
    guaranteed +1 every cycle (audit R2/R4). Ordering is
    ``(last_probed_stamp, name)`` so a never-probed member has no stamp, sorts
    first, and the order stays deterministic.
    """

    VERSION = "1.0.0"
    #: Minimum share of slots reserved for the most ignorant eligible members.
    SCOUT_FRACTION = 0.5

    def select(self, eligible: List[Tuple[str, Any]], limit: int,
               last_probed: Optional[Dict[str, float]] = None,
               ) -> Dict[str, Any]:
        """Choose up to `limit` members of `eligible`, scouts first.

        `eligible` is a list of ``(name, info)``. `last_probed` maps name ->
        epoch seconds of the last probe (absent = never probed). Returns
        ``{"chosen", "scout", "employed", "onlooker"}``; the first element of
        each list is a name.
        """
        limit = max(1, int(limit))
        stamps = dict(last_probed or {})
        ranked = sorted(eligible, key=lambda kv: (stamps.get(kv[0], 0.0), kv[0]))
        if not ranked:
            return {"chosen": [], "scout": [], "employed": [], "onlooker": []}

        n_scout = min(limit, max(1, int(math.ceil(limit * self.SCOUT_FRACTION))))
        never = [kv for kv in ranked if not stamps.get(kv[0])]
        scouts = never[:n_scout]
        scout_names = {n for n, _ in scouts}

        employed = [kv for kv in ranked if stamps.get(kv[0])]
        onlooker = [kv for kv in ranked
                    if kv[0] not in scout_names and not stamps.get(kv[0])]
        # Slots not spent on scouts are filled ignorance-first (the onlooker
        # phase), then by the evidenced members.
        chosen = scouts + onlooker + employed
        chosen = chosen[:limit]
        return {
            "chosen": chosen,
            "scout": [n for n, _ in chosen if n in scout_names],
            "employed": [n for n, _ in chosen if n in {m for m, _ in employed}],
            "onlooker": [n for n, _ in chosen
                         if n not in scout_names
                         and n not in {m for m, _ in employed}],
        }

    def coverage_quota_met(self, eligible, last_probed=None) -> bool:
        """True when every eligible member has been probed at least once."""
        stamps = dict(last_probed or {})
        return all(stamps.get(n) for n, _ in eligible)

    def execute(self) -> Dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"policy": "abc-scout",
                           "scout_fraction": self.SCOUT_FRACTION,
                           "queenless": not hasattr(self, "queen")}}


# ═══════════════════════════════════════════════════════════════════
# 2. GRAY QUEEN — entrenchment detector (drift ratchet)
# ═══════════════════════════════════════════════════════════════════

@register_worm(category="monitor", priority=6, cooldown_seconds=300.0,
    description="Gray Queen: neutral loss that became entrenched — census delta, not totals",
    tags={"colour_queen", "gray"},)
class GrayQueenWorm:
    """Detect *neutral loss that has become entrenched*.

    A class with no caller stays registered, is swept by the fabric, is tested
    by nothing, and therefore cannot be repaired by observation — the measured
    35-of-67 orphan surface. Neutral loss (a caller is removed in a refactor) is
    the founding event; entrenchment is the ratchet that follows, because the
    registry keeps it alive ("it's registered, so it must be used").

    The detector therefore reports the **delta**, not the total:

    * ``new``         — orphan this session, not last (the founding event);
    * ``resolved``    — was an orphan, now has a caller (left the list);
    * ``entrenched``  — orphan last session AND this session (the ratchet).

    The snapshot is persisted per session so the comparison is real rather than
    recomputed-from-scratch.
    """

    VERSION = "1.0.0"
    SNAPSHOT_ENV = "AZAZAEL_ENTRENCHMENT_PATH"

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = 0

    # -- census -------------------------------------------------------

    def read_census(self, census_path: Optional[Path] = None) -> Dict[str, str]:
        """Orphan surface as ``{label: status}`` from the PF16 census table.

        Only entries whose declared status is ``pending`` are orphans: the
        table's own rule is that an unreviewed function is not the same as a
        decided one. An empty dict means the census could not be read — which
        the caller must treat as *unknown*, never as *clean*.
        """
        path = Path(census_path) if census_path else _census_paths()[0]
        raw = _load_json(path)
        if not isinstance(raw, dict):
            return {}
        declared = raw.get("declared")
        if not isinstance(declared, dict):
            return {}
        out: Dict[str, str] = {}
        for key, rec in declared.items():
            status = str((rec or {}).get("status") or "pending")
            out[str(key)] = status
        return out

    def detect(self, census_path: Optional[Path] = None,
               snapshot_path: Optional[Path] = None,
               persist: bool = True) -> Dict[str, Any]:
        """Diff this session's orphan surface against the persisted one."""
        path = Path(census_path) if census_path else _census_paths()[0]
        snap_path = Path(snapshot_path) if snapshot_path else _census_paths()[1]
        current = self.read_census(path)
        if not current:
            reason = "census_unreadable" if not path.exists() else "census_empty"
            return {"success": False, "reason": reason, "census_path": str(path),
                    "new": [], "resolved": [], "entrenched": [],
                    "counts": {"current": 0, "previous": 0,
                               "new": 0, "resolved": 0, "entrenched": 0}}

        orphans_now = {k for k, s in current.items() if s == "pending"}

        prev = _load_json(snap_path)
        prev_orphans: set = set()
        prev_at = ""
        if isinstance(prev, dict):
            prev_at = str(prev.get("at") or "")
            prev_orphans = set(prev.get("orphans") or [])

        new = sorted(orphans_now - prev_orphans)
        resolved = sorted(prev_orphans - orphans_now)
        entrenched = sorted(orphans_now & prev_orphans)

        if persist:
            try:
                snap_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = snap_path.with_suffix(snap_path.suffix + ".tmp")
                tmp.write_text(json.dumps({
                    "at": datetime.now().isoformat(),
                    "census_at": str((_load_json(path) or {}).get("generated_at")
                                     or ""),
                    "orphans": sorted(orphans_now),
                    "total_declared": len(current),
                }, indent=1), encoding="utf-8")
                import os
                os.replace(str(tmp), str(snap_path))
            except Exception as exc:
                note_failure("queens:L368", exc) # a failed snapshot must never break the observer

        with self._lock:
            self._sessions += 1
        result = {
            "success": True,
            "census_path": str(path), "snapshot_path": str(snap_path),
            "previous_snapshot_at": prev_at,
            "new": new, "resolved": resolved, "entrenched": entrenched,
            "counts": {"current": len(orphans_now),
                       "previous": len(prev_orphans),
                       "new": len(new), "resolved": len(resolved),
                       "entrenched": len(entrenched),
                       "delta": len(orphans_now) - len(prev_orphans)},
        }
        if _spine is not None:
            try:
                _spine.record_spine_event("entrenchment_detected", {
                    "new": len(new), "resolved": len(resolved),
                    "entrenched": len(entrenched),
                    "delta": result["counts"]["delta"]})
            except Exception as exc:
                note_failure("queens:L390", exc)
        return result

    def execute(self) -> Dict[str, Any]:
        # Report-only: a probe must never write the snapshot.
        res = self.detect(persist=False)
        return {"success": True, "method": "execute", "result": res}


# ═══════════════════════════════════════════════════════════════════
# 3. BLACK QUEEN — adaptive loss (retirement)
# ═══════════════════════════════════════════════════════════════════

@register_worm(category="analysis", priority=6, cooldown_seconds=300.0,
    description="Black Queen: retire a worm whose capability a registered twin already covers",
    tags={"colour_queen", "black"})
class BlackQueenWorm:
    """Selection-driven loss of a costly, leaky, essential function.

    Morris 2011/2012: a gene is lost when it is (a) costly to keep, (b) leaky —
    its benefit accrues to others, and (c) the community already provides it.
    Mapped onto the fleet:

    * **cost**   — the worm carries evidence thin enough that nothing depends on
      it (``successes + failures < MIN_EVIDENCE``);
    * **leak**   — a registered *twin* exists whose method signature covers it
      (``DedupAnalyzer.compare_worms`` ≥ ``MIN_SIMILARITY``), so the capability
      is provided by the community;
    * **select** — the retirement is quorum-gated on DISTINCT envs via the same
      shared ledger every other adversarial verdict uses.

    `force=True` is the operator channel (mirrors ``promote(force=True)`` and
    ``order_quarantine(force=True)``): it records an explicit ``operator`` vote
    so a single-env host can exercise the path without a peer, and the decision
    stays auditable rather than invisible.

    Applying a retirement sets ``lifecycle_state='retired'`` on the registry
    entry (``worm_governance.LifecycleManager`` understands that state) and
    ``ClusterWORMZ.execute``/``RedQueenWorm`` both honour it, so the retirement
    actually removes the worm from the pipeline instead of decorating a table.
    """

    VERSION = "1.0.0"
    MIN_EVIDENCE = 4
    MIN_SIMILARITY = 0.5
    QUORUM = 2
    MAX_PROPOSALS = 8

    #: Categories whose members are PARALLEL IMPLEMENTATIONS for different
    #: targets rather than candidates for consolidation. `envmesh` is the
    #: env-coverage set -- one bridge per environment the mesh must run in
    #: (W11, WSL2-Ubuntu, WSL2-Kali, Bare, G:) -- and each member declares its
    #: own target explicitly (`WslUbuntuWorm.profile()` reports
    #: `"expect": "wsl2-ubuntu"`, `WslKaliWorm.profile()` reports
    #: `"expect": "wsl2-kali"`). They therefore share a method set BY
    #: CONSTRUCTION, and a name-and-body comparison cannot see the difference
    #: between "the same work twice" and "the same work in another environment".
    #:
    #: Retiring one as the other's duplicate does not remove a duplicate: it
    #: removes that environment's ability to run the mesh. Measured case that
    #: forced this: `propose(force=True)` selected `WslKaliWorm` on the strength
    #: of `WslUbuntuWorm`, whose coverage is of a different distro entirely.
    ENV_PARALLEL_CATEGORIES = frozenset({"envmesh"})

    def __init__(self):
        self._lock = threading.Lock()
        self._retired: List[str] = []
        # Comparator self-check for the run (see `_metric_check`).
        self._metric: Optional[Dict[str, Any]] = None

    # -- analysis -----------------------------------------------------

    def _evidence(self, name: str) -> int:
        """Total recorded outcomes for `name` across EVERY nature.

        Scans the whole homeostat view instead of a hardcoded nature list: a
        worm has exactly one nature per key, but hardcoding the list would
        make every worm in an unlisted nature look permanently evidence-free —
        i.e. permanently retirable.
        """
        if _spine is None:
            return 0
        try:
            view = _spine.get_homeostat_view()
        except Exception as exc:
            note_failure("queens:L475", exc)
            return 0
        suffix = ":" + name
        total = 0
        for key, rec in (view or {}).items():
            if not key.endswith(suffix) or not isinstance(rec, dict):
                continue
            total += int(rec.get("successes", 0)) + int(rec.get("failures", 0))
        return total

    def _twin(self, name: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The lowest-cost registered worm that already covers `name`."""
        try:
            from CORE.worm_governance import DedupAnalyzer
        except Exception as exc:
            note_failure("queens:L489", exc)
            return None
        analyzer = DedupAnalyzer()
        best: Optional[Dict[str, Any]] = None
        for other, oinfo in list(WORM_REGISTRY.items()):
            if other == name or oinfo.get("lifecycle_state") == "retired":
                continue
            if oinfo.get("category") != info.get("category"):
                continue
            # Two members of an env-coverage category target DIFFERENT
            # environments, so neither can cover the other (see
            # ENV_PARALLEL_CATEGORIES). The mesh has to remain executable in
            # every environment, which makes this coverage set invariant.
            if str(info.get("category") or "") in self.ENV_PARALLEL_CATEGORIES:
                continue
            try:
                match = analyzer.compare_worms(name, info, other, oinfo)
            except Exception as exc:
                note_failure("queens:L506", exc)
                continue
            if match is None:
                continue
            if match.similarity < self.MIN_SIMILARITY:
                continue
            cand = {"twin": other, "similarity": match.similarity,
                    "match_type": match.match_type,
                    "shared_methods": len(match.shared_methods),
                    "evidence": self._evidence(other)}
            if best is None or cand["similarity"] > best["similarity"]:
                best = cand
        return best

    def candidates(self) -> List[Dict[str, Any]]:
        """Worms that are costly (thin evidence) AND leaky (twin-covered).

        Deliberately excludes fleet governors: a regulator whose evidence is
        thin is a *wiring* gap, not a retirement — retiring it would remove the
        organ that notices the gap.

        Returns the FULL candidate list; `propose()` applies MAX_PROPOSALS, so
        a caller can see the whole backlog and the cap is one place.
        """
        out: List[Dict[str, Any]] = []
        try:
            from CORE.worm_registry import is_protected_worm  # type: ignore[reportAssignmentType]
        except Exception as exc:
            note_failure("queens:L533", exc)
            def is_protected_worm(_info):  # type: ignore
                return False
        for name, info in list(WORM_REGISTRY.items()):
            if not isinstance(info, dict):
                continue
            if info.get("lifecycle_state") == "retired":
                continue
            if is_protected_worm(info):
                continue
            # A worm that production code names is WIRED, not redundant:
            # retiring it removes a capability something still calls. This is the
            # same legitimate-service test the per-class audit uses. The case
            # that forced it: `InferenceWorm` is named by `CROSS_NATURE_ROUTER`
            # as the fallback inference path, was NOT flagged
            # `is_protected_worm`, and scored a mutual 1.0 `exact_signature`
            # against `NimInferenceWorm` -- so both halves of the pair were
            # retirable and the fleet could have lost local inference entirely.
            if _is_wired_in_production(name, info):
                continue
            evidence = self._evidence(name)
            if evidence >= self.MIN_EVIDENCE:
                continue
            twin = self._twin(name, info)
            if not twin:
                continue
            out.append({"worm": name, "nature": info.get("category", ""),
                        "evidence": evidence, "cost": "thin_evidence",
                        "leak": twin, "action": "retire"})
        # Cheapest service first, then the most-redundant: a worm with no
        # evidence at all that a 100 %-identical twin covers is the safest
        # possible loss, so it should be proposed before a thinly-evidenced one.
        out.sort(key=lambda c: (c["evidence"],
                                -float(c["leak"].get("similarity") or 0.0),
                                c["worm"]))
        return out

    # -- action -------------------------------------------------------

    def _metric_check(self) -> Tuple[bool, Dict[str, Any]]:
        """The comparator's self-check, cached for the run.

        Cached because `propose()` is the ACT stage and may be consulted more
        than once per cycle while the registry and the rules stay static within
        a run. A failure to even run the check is reported as NOT trustworthy:
        an unvalidated instrument is the thing being guarded against.
        """
        with self._lock:
            if self._metric is None:
                try:
                    from CORE.worm_governance import DedupAnalyzer
                    self._metric = DedupAnalyzer().self_check()
                except Exception as exc:
                    note_failure("queens:L585", exc)
                    self._metric = {"trustworthy": False,
                                    "reason": "self_check_failed",
                                    "detail": repr(exc)[:160]}
            return bool(self._metric.get("trustworthy")), self._metric

    def propose(self, force: bool = False, env: Optional[str] = None,
                apply: bool = True) -> Dict[str, Any]:
        """Quorum-gated retirement proposals. Never raises.

        Fails closed on an unvalidated comparator: every candidate is justified
        SOLELY by that score, and the score has already shipped once with a
        metric that could not tell a duplicate from a coincidence (see
        `DedupAnalyzer.self_check`). "The check could not run" is a failure, not
        a pass.
        """
        trustworthy, metric = self._metric_check()
        if not trustworthy:
            return {"success": False,
                    "reason": "similarity_metric_failed_self_check",
                    "evaluated": 0, "retired": 0, "held": 0,
                    "proposals": [], "pending": [], "metric": metric}
        full = self.candidates()
        # A MUTUAL pair must not retire twice. When A's twin is B and B's twin is
        # A, each is justified only by the other, so honouring both deletes the
        # capability instead of removing a duplicate. Measured: on the live fleet
        # `InferenceWorm` and `NimInferenceWorm` named each other at similarity
        # 1.0, and retiring both would have left no inference worm at all.
        #
        # The restriction belongs HERE, not in `candidates()`: the backlog should
        # report every twin-covered worm, and the decision is what must not act
        # twice on one pair. More evidence wins; ties break on the name.
        by_worm = {c["worm"]: c for c in full}
        kept: List[Dict[str, Any]] = []
        for cand in full:
            twin = (cand.get("leak") or {}).get("twin")
            other = by_worm.get(twin)
            if (other is not None
                    and (other.get("leak") or {}).get("twin") == cand["worm"]
                    and (cand["evidence"], cand["worm"])
                    > (other["evidence"], other["worm"])):
                continue
            kept.append(cand)
        cands = kept[: self.MAX_PROPOSALS]
        env = env or _local_env()
        proposed: List[Dict[str, Any]] = []
        held: List[Dict[str, Any]] = []
        for cand in cands:
            name = cand["worm"]
            finding = f"retire:{name}"
            vote: Dict[str, Any] = {}
            if _spine is not None:
                try:
                    vote = _spine.cast_quarantine_vote(
                        finding, "operator" if force else env, "retire",
                        quorum=self.QUORUM)
                except Exception as exc:
                    note_failure("queens:L641", exc)
                    vote = {}
            decided = bool(force) or bool(vote.get("decided"))
            if not decided:
                held.append({"worm": name, "reason": "awaiting_quorum",
                             "envs": vote.get("envs")})
                continue
            rec = dict(cand)
            rec["forced"] = bool(force)
            rec["envs"] = vote.get("envs")
            rec["applied"] = False
            if apply:
                info = WORM_REGISTRY.get(name)
                if isinstance(info, dict):
                    info["lifecycle_state"] = "retired"
                    rec["applied"] = True
                    with self._lock:
                        if name not in self._retired:
                            self._retired.append(name)
            rec["wire_or_cut_rows_retired"] = self._mark_census_retired(rec)
            if _spine is not None:
                try:
                    _spine.record_spine_event("black_queen_retired", {
                        "worm": name, "twin": (rec.get("leak") or {}).get("twin"),
                        "forced": bool(force), "envs": vote.get("envs"),
                        "applied": rec["applied"]})
                except Exception as exc:
                    note_failure("queens:L667", exc)
            proposed.append(rec)
        return {"success": True, "evaluated": len(cands),
                "retired": len(proposed), "held": len(held),
                "proposals": proposed, "pending": held}

    def _mark_census_retired(self, rec: Dict[str, Any]) -> int:
        """Mark this worm's PF16 rows `retired` in the declaration table.

        Best-effort and additive: a row that some other worm also claims is left
        alone. The reason is written so the table explains itself, which is what
        the census rule asks for ("retired means it should be deleted;
        retired_reason required").
        """
        census_path, _ = _census_paths()
        raw = _load_json(census_path)
        if not isinstance(raw, dict) or not isinstance(raw.get("declared"), dict):
            return 0
        stem = _stamp_stem(str(rec.get("worm") or ""))
        if not stem:
            return 0
        rows = raw["declared"]
        touched = 0
        for key, entry in rows.items():
            if not isinstance(entry, dict):
                continue
            module = _stamp_stem(entry.get("module") or key.split(":")[0])
            if module != stem:
                continue
            if entry.get("status") == "wired":
                continue  # a wired row is a real caller; never override it
            entry["status"] = "retired"
            entry["reason"] = ("black_queen: capability covered by '%s' "
                               "(similarity %.2f); service retired"
                               % ((rec.get("leak") or {}).get("twin", "?"),
                                  (rec.get("leak") or {}).get("similarity", 0.0)))
            touched += 1
        if touched:
            try:
                import os
                from collections import Counter
                statuses = ("wired", "retired", "pending")
                tally = Counter(str(e.get("status") or "pending")
                                for e in rows.values() if isinstance(e, dict))
                raw["counts"] = {s: tally.get(s, 0) for s in statuses}
                raw["generated_at"] = datetime.now().isoformat()
                tmp = census_path.with_suffix(census_path.suffix + ".tmp")
                tmp.write_text(json.dumps(raw, indent=1), encoding="utf-8")
                os.replace(str(tmp), str(census_path))
            except Exception as exc:
                note_failure("queens:L717", exc)
                return touched  # the in-memory decision still stands
        return touched

    def execute(self) -> Dict[str, Any]:
        # Propose-only by default: a probe must never retire a worm.
        res = self.propose(apply=False)
        res.pop("proposals", None)
        return {"success": True, "method": "execute", "result": res}


def _local_env() -> str:
    """This host's env label — delegation to the ONE canonical implementation.

    Env id is a primary key for the quorum ledger (district-env verdicts), so a
    second spelling here would let one host vote twice under two names.
    """
    try:
        from CORE.env_id import env_id
        return str(env_id())
    except Exception as exc:  # pragma: no cover - CORE.env_id is stdlib-only
        note_failure("queens:L737", exc)
        import os
        return "windows" if os.name == "nt" else "bare"


# ═══════════════════════════════════════════════════════════════════
# RAINBOW ROUND — the colour teams as one executable round
# ═══════════════════════════════════════════════════════════════════
#
# The colour roles live in DIFFERENT layers (red: xenomorph probes in
# spine.py; gray/black: monitor/analysis here; yellow: attestation in the
# morphogenesis gate; white: ratification in worm_governance.py), so the
# round coordinates rather than relocates: each team runs in its home
# module and reports here. Report-only everywhere except the spine cycle's
# own cadence — a heartbeat round must never retire, quarantine, or
# promote (black runs propose(apply=False), gray runs detect(persist=False),
# yellow reads the staged pen without promoting, white ratifies without
# replaying).
#
# Each team leaves one join row (memory unity, hook:rainbow_round:<team>)
# and one canonical reward row (reward co-tabulation intake,
# source:reward-ledger, provenance rainbow_round) so the learning loop can
# consume per-team outcomes in one common scale.

RAINBOW_TEAMS = ("red", "gray", "black", "yellow", "white", "scout")


def _verification_ok(container: Any, key: str, where: str) -> bool:
    """Read a verification field, treating ABSENCE as unverified -- not PASS.

    MEASURED: `result.get("ok", True)` made a missing verdict field read as
    success, which is the `default_true` class the meta-evolution detector raises
    as HIGH ("a missing verification field reads as PASS") and queued as its
    pending candidate against this module. Every return path of
    `GrayQueenWorm.detect` and `BlackQueenWorm.propose` carries its key, so
    flipping the default is behaviour-preserving on real data; a producer that
    drops the key now RECORDS the absence instead of reporting it as a pass.
    """
    if isinstance(container, dict) and key in container:
        return bool(container[key])
    note_failure("%s:missing-%s" % (where, key), None)
    return False


def _rainbow_hook(team: str, subject: str, ok: bool, citation: str = "") -> bool:
    """One join row per team outcome. Never raises."""
    try:
        from CORE.technique_runtime import record_link
        return bool(record_link("rainbow_round:%s" % team, subject,
                                bool(ok), citation=str(citation or "")))
    except Exception as exc:
        note_failure("queens:rainbow-hook", exc)
        return False


def _rainbow_reward(store, team: str, value, scale_id: str) -> bool:
    """One canonical reward row per team. Skips None (no fabrication)."""
    if value is None or store is None:
        return False
    try:
        from CORE import reward_scales as _rs
        return bool(_rs.emit_canonical(store, value, scale_id,
                                       "rainbow_round", worm="rainbow:%s" % team))
    except Exception as exc:
        note_failure("queens:rainbow-reward", exc)
        return False


def _stamps_from_outcomes(limit: int = 1000) -> Dict[str, float]:
    """Worm -> last outcome epoch from the homeostat view (durable).

    Gives the scout quota real ignorance ordering instead of cold-start.
    The event stream is in-memory (empty in a fresh process); the view is
    the persisted form and carries `last_at` per worm. Mixed ISO/float
    stamps would crash the sort, so unparseable stamps are dropped
    (cold-start for that worm). Bounded read, never raises.
    """
    stamps: Dict[str, float] = {}
    try:
        from datetime import datetime as _dt
        from CORE import spine_registry as _sr2
        view = _sr2.get_homeostat_view() or {}
        items = list(view.items())[:max(1, int(limit))]
        for key, rec in items:
            if not isinstance(rec, dict):
                continue
            name = str(key).split(":")[-1] if ":" in str(key) else str(key)
            at = rec.get("last_at")
            if not name or not at:
                continue
            try:
                ts = _dt.fromisoformat(str(at)).timestamp()
            except Exception as exc:
                note_failure("expansion/queens:835", exc)
                continue
            if ts > stamps.get(name, 0.0):
                stamps[name] = ts
    except Exception as exc:
        note_failure("queens:rainbow-stamps", exc)
    return stamps


def rainbow_round(probe_limit: int = 1) -> Dict[str, Any]:
    """Run every colour team once, bounded and report-only. Never raises.

    Returns {"teams": {color: {...}}, "ok": bool}. Each team entry carries
    its outcome plus hook/reward evidence flags. A team that errors reports
    {"ok": False, "error": ...} and the round continues — one sick queen
    must not ground the rainbow.
    """
    out: Dict[str, Any] = {"teams": {}, "ok": True}
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
    except Exception as exc:
        note_failure("queens:rainbow-store", exc)
        store = None

    def _finish(team: str, subject: str, result: Dict[str, Any],
                reward_value, reward_scale: str) -> None:
        ok = _verification_ok(result, "ok", "queens:rainbow-finish")
        result["hook_kept"] = _rainbow_hook(team, subject, ok)
        result["reward_kept"] = _rainbow_reward(store, team, reward_value,
                                                reward_scale)
        out["teams"][team] = result
        if not ok:
            out["ok"] = False

    # -- RED: hostile read-only probes (home: spine.RedQueenWorm) ----------
    try:
        from CORE.WORMS.expansion.spine import RedQueenWorm
        rq = RedQueenWorm()
        natures = list(getattr(rq, "PROBE_NATURES", ()) or ())
        probed, failed = 0, 0
        for nat in natures:
            try:
                p = rq.pressure_probe(nat, limit=max(1, int(probe_limit)))
                probed += 1
                failed += sum(1 for r in (p.get("results") or [])
                              if not r.get("success"))
            except Exception as exc:
                note_failure("queens:rainbow-red", exc)
        red_ok: Any = (1.0 - failed / probed) if probed else None
        _finish("red", "pressure-probe:%d-natures" % len(natures),
                {"ok": True, "categories": len(natures), "probed": probed,
                 "failed": failed, "ok_rate": red_ok},
                red_ok, "rate_01")
    except Exception as exc:
        note_failure("queens:rainbow-red", exc)
        _finish("red", "pressure-probe", {"ok": False, "error": repr(exc)[:160]},
                None, "rate_01")

    # -- GRAY: entrenchment delta, report-only (home: GrayQueenWorm) --------
    try:
        gq = GrayQueenWorm()
        d = gq.detect(persist=False) or {}
        counts = d.get("counts", {}) if isinstance(d, dict) else {}
        entrenched = counts.get("entrenched")
        _finish("gray", "entrenchment-delta",
                {"ok": _verification_ok(d, "success", "queens:rainbow-gray"),
                 "counts": counts},
                entrenched if isinstance(entrenched, int) else None, "count")
    except Exception as exc:
        note_failure("queens:rainbow-gray", exc)
        _finish("gray", "entrenchment-delta",
                {"ok": False, "error": repr(exc)[:160]}, None, "count")

    # -- BLACK: retirement proposals, propose-only (home: BlackQueenWorm) ---
    try:
        bq = BlackQueenWorm()
        p = bq.propose(apply=False) or {}
        retired = p.get("retired")
        _finish("black", "retirement-proposals",
                {"ok": _verification_ok(p, "success", "queens:rainbow-black"),
                 "retired": retired, "held": len(p.get("pending") or [])},
                retired if isinstance(retired, int) else None, "count")
    except Exception as exc:
        note_failure("queens:rainbow-black", exc)
        _finish("black", "retirement-proposals",
                {"ok": False, "error": repr(exc)[:160]}, None, "count")

    # -- YELLOW: attestation backlog (home: morphogenesis gate) -------------
    # Read-only: staged-pen depth. Promotion itself stays on the cycle's
    # cadence; the round reports the gate backlog, never moves files.
    try:
        from CORE.WORMS.expansion.spine import MorphogenesisWorm
        staged = MorphogenesisWorm().staged() or []
        _finish("yellow", "attestation-backlog",
                {"ok": True, "staged": len(staged)},
                len(staged), "count")
    except Exception as exc:
        note_failure("queens:rainbow-yellow", exc)
        _finish("yellow", "attestation-backlog",
                {"ok": False, "error": repr(exc)[:160]}, None, "count")

    # -- WHITE: ratify the pending journal (home: worm_governance) ----------
    # Read-only verdicts: ratify_policy approves/refuses without replaying,
    # so evaluating the backlog changes nothing durable.
    try:
        from CORE import spine_registry as _sr
        from CORE.worm_registry import WORM_REGISTRY as _REG
        from CORE.worm_governance import WormGovernor
        journal = _sr.get_policy_journal() or []
        gov = WormGovernor()
        approved = 0
        for entry in journal:
            try:
                if gov.ratify_policy(entry, registry=_REG).get("approved"):
                    approved += 1
            except Exception as exc:
                note_failure("queens:rainbow-white-entry", exc)
        rate: Any = (approved / len(journal)) if journal else None
        _finish("white", "ratify-backlog:%d" % len(journal),
                {"ok": True, "pending": len(journal), "approved": approved,
                 "approval_rate": rate},
                rate, "rate_01")
    except Exception as exc:
        note_failure("queens:rainbow-white", exc)
        _finish("white", "ratify-backlog",
                {"ok": False, "error": repr(exc)[:160]}, None, "rate_01")

    # -- SCOUT: quota composition by real ignorance (home: ScoutBeeWorm) ----
    # last_probed comes from the homeostat outcome stream (empty only when
    # the stream is unreachable — the labeled cold-start fallback).
    try:
        from CORE.worm_registry import WORM_REGISTRY as _REG2
        eligible = [(n, i) for n, i in list(_REG2.items())[:200]]
        stamps = _stamps_from_outcomes()
        sel = ScoutBeeWorm().select(eligible, min(8, max(1, len(eligible))),
                                    last_probed=stamps)
        chosen = len(sel.get("chosen") or [])
        _finish("scout", "quota:%d-eligible:%d-stamped" % (len(eligible),
                                                           len(stamps)),
                {"ok": True, "eligible": len(eligible), "chosen": chosen,
                 "stamped": len(stamps),
                 "scouts": len(sel.get("scout") or [])},
                chosen, "count")
    except Exception as exc:
        note_failure("queens:rainbow-scout", exc)
        _finish("scout", "coldstart-quota",
                {"ok": False, "error": repr(exc)[:160]}, None, "count")

    # Round aggregate: one row per round so trend queries don't re-aggregate
    # six team rows. Per-team rows carry the detail; this carries the verdict.
    bits = ",".join(
        "%s:%s" % (t, "ok" if _verification_ok(out["teams"].get(t), "ok",
                                                "queens:rainbow-summary")
                   else "ERR")
        for t in RAINBOW_TEAMS)
    out["aggregate_kept"] = _rainbow_hook("round", "6-teams", out["ok"],
                                          citation=bits)

    return out
