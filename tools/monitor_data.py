#!/usr/bin/env python3
# coding: utf-8
"""
monitor_data.py — the one place a live monitor reads the world from.

WHY THIS EXISTS (`LIVE_MONITOR_RESTRUCTURE_TODO.md` §3, P2 item 8)

The `collect_*` functions and the small defensive readers around them were
being written once per renderer, so each monitor disagreed about what "missing"
looked like: one printed `0`, another `n/a`, a third crashed into a blank card.
Duplication is how a monitor starts lying, and the mesh work made the lie worse
because there is now far more live state than before.

THE RULE THIS MODULE ENFORCES (TODO §"Rule for this file")

**No monitor may display a hard-coded constant that the mesh computes.** Every
number a monitor shows comes from here, and every reader here comes from a live
source — `spine_registry`, `NATURE_DEFINITIONS`, a `state/*.json` artifact, or
the mesh module itself. When a source cannot be read the value is `None` and
renders `unknown`; it is never replaced with a plausible default. A monitor that
prints a fake `0.00` is worse than one that prints nothing, because the fake
value is indistinguishable from a real reading of zero.

Reads are defensive by design: this code runs in the same process as the mesh
and must never be the thing that takes it down. Every collector returns a dict
with `_source` / `_error` keys describing its own reliability, so a card can say
*why* it is empty instead of implying the system is idle.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

STATE_DIR = PROJECT_ROOT / "state"

# ── Staleness policy ─────────────────────────────────────────────
# A monitor showing three-day-old numbers as "live" is the failure mode the
# restructure exists to remove (TODO P3 item 12).
FRESH_SECONDS = 90.0          # under this: shown as seconds
MINUTES_SECONDS = 5400.0      # under this: shown in minutes, still fresh
STALE_SECONDS = 86400.0       # beyond this: marked STALE unconditionally


# ── Primitive readers ────────────────────────────────────────────

def load_state(name: str) -> Dict[str, Any]:
    """Read `state/<name>`. Returns {} (never raises) if absent or malformed."""
    try:
        data = json.loads((STATE_DIR / name).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_jsonl_tail(name: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Last `limit` well-formed objects from `state/<name>`. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        lines = (STATE_DIR / name).read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def num(value: Any) -> Optional[float]:
    """Float or None. `None` means *unreadable*, not zero."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Any, places: int = 4, dash: str = "unknown") -> str:
    """Format a number, or say `unknown` rather than inventing one."""
    n = num(value)
    return dash if n is None else f"{n:.{places}f}"


def pct(num_: Optional[float], lo: float, hi: float) -> float:
    """Position of `num_` within [lo, hi] as 0..100, for a bar."""
    if num_ is None or hi <= lo:
        return 0.0
    return max(0.0, min(100.0, (num_ - lo) / (hi - lo) * 100.0))


def age_of(at: Any) -> Tuple[str, bool]:
    """(human age, is_stale) for an ISO timestamp. Unusable timestamps are stale."""
    if not isinstance(at, str) or not at:
        return "no timestamp", True
    try:
        ts = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        secs = max(0.0, (datetime.now() - ts).total_seconds())
    except Exception:
        return "unparseable", True
    return _human_age(secs)


def state_age(name: str) -> Tuple[str, bool]:
    """(human age, is_stale) from the artifact's mtime — independent of its
    contents, so a file that exists but carries no timestamp is still dated."""
    try:
        secs = max(0.0, (datetime.now()
                         - datetime.fromtimestamp((STATE_DIR / name).stat().st_mtime))
                    .total_seconds())
    except Exception:
        return "absent", True
    return _human_age(secs)


def _human_age(secs: float) -> Tuple[str, bool]:
    if secs < FRESH_SECONDS:
        return f"{secs:.0f}s ago", False
    if secs < MINUTES_SECONDS:
        return f"{secs / 60:.0f}m ago", False
    if secs < STALE_SECONDS * 2:
        return f"{secs / 3600:.0f}h ago", secs > STALE_SECONDS
    return f"{secs / 86400:.0f}d ago", True


def source_line(*names: str, extra: str = "") -> str:
    """A card footer naming the artifact behind the card, its age, and staleness.

    `names` are `state/` filenames; if none exist the line says so instead of
    letting a card pass an empty panel off as a zero reading.
    """
    present = [n for n in names if (STATE_DIR / n).exists()]
    if not present:
        hint = f" &middot; {extra}" if extra else ""
        return (f'<div class="sub" style="margin-top:8px;color:#8b949e">'
                f'source: {", ".join(names) or "n/a"} &mdash; not present{hint}</div>')
    ages = [state_age(n) for n in present]
    worst = max(ages, key=lambda a: _age_seconds(a))
    label = f'source: {", ".join(present)} &middot; {worst[0]}'
    if extra:
        label += f' &middot; {extra}'
    if worst[1]:
        return (f'<div class="sub" style="margin-top:8px">'
                f'<span class="badge badge-down">STALE</span> {label}</div>')
    return f'<div class="sub" style="margin-top:8px">{label}</div>'


def _age_seconds(age: Tuple[str, bool]) -> float:
    t = age[0]
    try:
        v = float(t.split()[0])
    except Exception:
        return float("inf")
    if t.endswith("s ago"):
        return v
    if t.endswith("m ago"):
        return v * 60
    if t.endswith("h ago"):
        return v * 3600
    if t.endswith("d ago"):
        return v * 86400
    return float("inf")


def row(label: str, value: Any, state: str = "unknown", unit: str = "") -> str:
    """One label/value row. `state` is a semantic name from `monitor_theme`."""
    from monitor_theme import semantic

    colour, _ = semantic(state)
    shown = "unknown" if value is None else f"{value}{unit}"
    return (f'<div class="row"><span class="label">{label}</span>'
            f'<span class="value" style="color:{colour}">{shown}</span></div>')


def error_row(exc: Any, what: str) -> str:
    """A visible failure. Silence on a failed read is how monitors lie."""
    return (f'<div class="row"><span class="label" style="color:#f85149">'
            f'{what} read failed</span>'
            f'<span class="value" style="color:#f85149">{str(exc)[:60]}</span></div>')


# ── Spine / mesh collectors ──────────────────────────────────────

def collect_spine_data() -> Dict[str, Any]:
    """Everything the spine holds in-process. Learning + fabric + clock."""
    d: Dict[str, Any] = {
        "_source": "CORE.spine_registry",
        "_error": None,
        "q_weights": {}, "q_effective": {}, "q_updates": {},
        "scars": {}, "homeostat": {},
        "spine_map": {}, "events": [], "policy_journal": [],
        "quarantine_votes": {}, "fabric": {}, "fabric_record": {},
        "clock": {}, "route_usage": {}, "anomaly_meta": {},
    }
    try:
        from CORE import spine_registry as sp

        d["q_weights"] = _safe(sp.get_q_weights, {})
        # The table the mesh actually RANKS on. The raw EMA is a plateau at the
        # clip for the busiest keys, so a card that shows only the raw values
        # renders a tie where the system has a decision.
        d["q_effective"] = _safe(sp.get_q_effective, {})
        d["q_updates"] = _safe(sp.get_q_update_counts, {})
        d["scars"] = _safe(sp.get_scars, {})
        d["homeostat"] = _safe(sp.get_homeostat_view, {})
        d["spine_map"] = _safe(sp.get_spine_map, {})
        d["events"] = _safe(lambda: sp.get_spine_events()[:20], [])
        d["policy_journal"] = _safe(lambda: sp.get_policy_journal()[:10], [])
        d["quarantine_votes"] = _safe(sp.get_quarantine_votes, {})
        d["fabric"] = _safe(sp.get_fabric_view, {})
        d["route_usage"] = _safe(sp.get_route_usage, {})
        d["anomaly_meta"] = _safe(sp.get_anomaly_model_meta, {})
    except ImportError as exc:
        d["_error"] = f"spine unavailable: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        d["_error"] = str(exc)[:120]
    return d


def collect_clock_data() -> Dict[str, Any]:
    """HLC health: local node, current stamp, observed skew, rejected remotes.

    Before this session the mesh could not measure clock skew at all, and no
    monitor has ever shown it, so skew-induced divergence was indistinguishable
    from a genuine disagreement (audit §5 item 3).
    """
    d: Dict[str, Any] = {"_source": "CORE.hlc", "_error": None,
                         "node": None, "stamp": None, "skew_ms": None,
                         "rejected_skew_ms": None,
                         "observations": None, "rejected": None,
                         "max_drift_ms": None}
    try:
        from CORE import hlc as _hlc_mod

        h = _hlc_mod.hlc()
        # `node` is a property and the rest are methods; `_read` handles both,
        # so the card keeps working if any of them is converted to a property
        # (or back) later. A monitor that dies on an API refactor is how the
        # restructure started.
        d["node"] = _read(h, "node")
        d["stamp"] = _read(h, "peek")
        d["skew_ms"] = _read(h, "skew_ms")
        d["rejected_skew_ms"] = _read(h, "rejected_skew_ms")
        d["observations"] = _read(h, "observations")
        d["rejected"] = _read(h, "rejected")
        d["max_drift_ms"] = getattr(_hlc_mod, "MAX_DRIFT_MS", None)
        if d["stamp"] is None:
            d["_error"] = "clock introspection unavailable"
    except Exception as exc:
        d["_error"] = str(exc)[:120]
    return d


# The fabric worms that persist operational state (audit #7), with the label a
# human should see and the keys worth putting on screen. A worm that has never
# published is reported as "no state" rather than as an empty-but-healthy card.
FABRIC_PROVIDERS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("Lilith", "Queen lease", ("_lease_env", "_lease_until", "_term",
                               "_genome_version")),
    ("LeaderElectorWorm", "Leader election", ("_leader", "_term", "_nodes")),
    ("MeshRouterWorm", "Routing table", ("_routes",)),
    ("InferenceLoadBalancerWorm", "Endpoint load", ("_endpoints",)),
    ("ProviderFailoverMeshWorm", "Provider failover", ("_uses",)),
    ("ModelShardServerWorm", "Model shards", ("_shards",)),
    ("FederatedAggregatorWorm", "FedAvg rounds", ("_clients", "_rounds")),
    ("SplitBrainResolverWorm", "Split brain", ("_forks",)),
    ("PartitionHealerWorm", "Partitions", ("_partitions",)),
    ("NASMeshSearcherWorm", "NAS search", ("_evaluated", "_best")),
    ("RewardShaperMeshWorm", "Reward shaping", ("_scores", "_dlq")),
    ("GossipSyncWorm", "Gossip state", ("_rounds",)),
    ("EnvBeaconWorm", "Env beacons", ("_beacons",)),
)


def collect_fabric_data() -> Dict[str, Any]:
    """The fabric natures' operational memory, per provider.

    This is the state the mesh forgot on every restart before this session
    (audit finding #7). Reading it is the only way a monitor can show that the
    routing table, the elected leader term and the shard catalogue are real and
    persisted rather than rebuilt per process.
    """
    d: Dict[str, Any] = {"_source": "CORE.spine_registry.get_fabric_record",
                         "_error": None, "providers": [], "missing": [],
                         "published": None}
    try:
        from CORE import spine_registry as sp

        view = _safe(sp.get_fabric_view, {})
        d["published"] = len(view)
        for provider, label, keys in FABRIC_PROVIDERS:
            rec = _safe(lambda p=provider: sp.get_fabric_record(p), None) or {}
            meta = view.get(provider) or {}
            state = rec.get("state") or {}
            if not rec:
                d["missing"].append(provider)
            d["providers"].append({
                "provider": provider, "label": label, "keys": keys,
                "state": state,
                "updated_at": rec.get("updated_at") or meta.get("updated_at") or "",
                "stamped_fields": rec.get("stamped_fields")
                                 or len(rec.get("field_stamps") or {})
                                 or meta.get("stamped_fields") or 0,
                "present": bool(rec),
            })
    except Exception as exc:
        d["_error"] = str(exc)[:120]
    return d


# ── Failure visibility: silence / fencing / liveness ─────────────
#
# The three primitives added by the 2026-09-16 per-function audit are all
# IN-PROCESS by design (see their module docstrings), which creates a trap for
# a monitor: the renderer is a different process, so importing the ledgers here
# would read three empty dictionaries and draw a green panel. That is the same
# "success-shaped reporting" defect the audit spent the session removing, one
# level up.
#
# So the contract is explicit. A process that owns the ledgers calls
# `publish_failure_visibility()`, and the monitor READS the published snapshot.
# No published snapshot means `observed: False` -- reported as unknown, never as
# healthy, because an empty ledger and an unwatched one look identical and only
# one of them is good news.
FAILURE_VISIBILITY_STATE = "failure_visibility.json"

#: How long a latched miss survives a later publish that does not know about it.
#: Short-lived processes (a chain tick, a CLI run) each own their own ledger, so
#: a fault found by one tick would be erased by the next tick's empty snapshot
#: before any human read it. Carrying the entry forward fixes that; the bound is
#: what stops a since-fixed fault from being reported forever.
CARRY_FORWARD_SECONDS = 7200.0


def publish_failure_visibility(path: Optional[Path] = None) -> Dict[str, Any]:
    """Write this process's silence/fence/liveness ledgers where a monitor can read them.

    Called by a mesh process that actually owns the ledgers. Atomic (tmp +
    replace) so a monitor reading concurrently never sees a torn file. Returns
    the payload; never raises -- a monitor must not be the reason a mesh job
    fails.

    Latched `liveness` misses that this process does not know about are carried
    forward from the previous snapshot (see CARRY_FORWARD_SECONDS), because the
    ledgers are in-process and a short-lived publisher would otherwise erase
    another process's evidence simply by having a clean run.
    """
    target = Path(path) if path else (STATE_DIR / FAILURE_VISIBILITY_STATE)
    payload: Dict[str, Any] = {"written_at": None, "written_by": "",
                               "silence": {}, "fences": {}, "liveness": {}}
    try:
        from CORE.WORMS.expansion.silence import silence_report
        from CORE.WORMS.expansion.fencing import fence_report
        from CORE.WORMS.expansion.liveness import liveness_report
        import os as _os

        previous = {}
        try:
            if target.exists():
                previous = json.loads(target.read_text(encoding="utf-8")) or {}
                prev_age = (datetime.now() - datetime.fromtimestamp(
                    target.stat().st_mtime)).total_seconds()
                if prev_age > CARRY_FORWARD_SECONDS:
                    previous = {}
        except Exception:
            previous = {}

        payload["written_at"] = datetime.now().isoformat()
        try:
            from CORE.env_id import env_id
            payload["written_by"] = str(_safe(env_id, "unknown"))
        except ImportError:
            payload["written_by"] = "unknown"
        payload["pid"] = _os.getpid()
        payload["silence"] = silence_report()
        payload["fences"] = fence_report()
        live = liveness_report()

        mine = {str(m.get("name")) for m in (live.get("overdue") or [])}
        carried = [dict(m) for m in ((previous.get("liveness") or {})
                                     .get("overdue") or [])
                   if str(m.get("name")) not in mine]
        if carried:
            live = dict(live)
            live["overdue"] = list(live.get("overdue") or []) + carried
            live["healthy"] = False
            for miss in carried:
                miss["carried_from"] = previous.get("written_at") or "previous publish"
            payload["carried_misses"] = len(carried)
        payload["liveness"] = live

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        _os.replace(str(tmp), str(target))
        payload["published"] = True
    except Exception as exc:
        payload["published"] = False
        payload["error"] = str(exc)[:120]
    return payload


def collect_failure_data() -> Dict[str, Any]:
    """The published failure-visibility snapshot, with its age.

    `observed=False` is a distinct state from `healthy` and the renderer must
    treat it as such: it means no process has published, i.e. nothing in the
    mesh is watching for silent failures yet -- an unknown, not an all-clear.
    """
    d: Dict[str, Any] = {
        "_source": f"state/{FAILURE_VISIBILITY_STATE}", "_error": None,
        "observed": False, "age": "", "stale": True,
        "written_by": "", "written_at": "", "pid": None,
        "silence": {}, "fences": {}, "liveness": {},
        "faults": 0, "unobservable": [],
    }
    try:
        raw = load_state(FAILURE_VISIBILITY_STATE)
        # `state_age`'s second value is is_STALE, not is-fresh -- naming it
        # `fresh` here once caused the renderer to label a just-published
        # snapshot "publisher stopped".
        if not raw:
            d["age"], d["stale"] = state_age(FAILURE_VISIBILITY_STATE)
            return d
        d["observed"] = True
        d["written_by"] = raw.get("written_by") or "unknown"
        d["written_at"] = raw.get("written_at") or ""
        d["pid"] = raw.get("pid")
        d["age"], d["stale"] = state_age(FAILURE_VISIBILITY_STATE)
        d["silence"] = raw.get("silence") or {}
        d["fences"] = raw.get("fences") or {}
        d["liveness"] = raw.get("liveness") or {}

        sil = d["silence"]
        tripped = list(sil.get("tripped") or [])
        fences = d["fences"] or {}
        rejected = sum(int(f.get("rejections") or 0) for f in fences.values())
        live = d["liveness"] or {}
        over = list(live.get("overdue") or [])
        d["faults"] = len(tripped) + len(over) + (1 if rejected else 0)

        # The two ways a green panel can mean nothing at all.
        if not live.get("observable"):
            d["unobservable"].append(
                "no obligations declared: nothing is watching for a missing event")
        if not tripped and not sil.get("tags"):
            d["unobservable"].append(
                "no swallowed-failure tags recorded in this process")
    except Exception as exc:
        d["_error"] = str(exc)[:120]
    return d


def collect_registry_data() -> Dict[str, Any]:
    """Registered worms and categories."""
    d: Dict[str, Any] = {"_source": "CORE.worm_registry", "_error": None,
                         "worms": {}, "categories": set(), "total": 0}
    try:
        from CORE.worm_registry import WORM_REGISTRY, discover_worms, get_all_categories

        discover_worms()
        d["worms"] = WORM_REGISTRY
        d["categories"] = get_all_categories()
        d["total"] = len(WORM_REGISTRY)
    except ImportError as exc:
        d["_error"] = f"registry unavailable: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        d["_error"] = str(exc)[:120]
    return d


def collect_exmachina_data() -> Dict[str, Any]:
    """The 10 epistemic organs' on-disk state."""
    d: Dict[str, Any] = {"_source": "state/organ_*.json", "_error": None,
                         "organs": [], "mood": "unknown", "severity": 0.0}
    for organ in ("speculum", "fulcrum", "testudo", "vigil", "filum",
                  "perennis", "compages", "brevis", "emergon", "mutagen"):
        rec = load_state(f"organ_{organ}.json")
        if rec:
            rec.setdefault("organ", organ)
            d["organs"].append(rec)
    return d


def collect_mesh_cycle() -> Dict[str, Any]:
    """`state/mesh_cycle.json` — last cycle's per-nature worms/ok/fail/ms."""
    d = load_state("mesh_cycle.json")
    out: Dict[str, Any] = {"_source": "state/mesh_cycle.json",
                           "_error": None, "at": d.get("at") or d.get("ts"),
                           "cycle": d.get("cycle"), "natures": {},
                           "raw": d}
    natures = d.get("natures") or d.get("by_nature") or {}
    if isinstance(natures, dict):
        out["natures"] = natures
    return out


def collect_mesh_facts() -> Dict[str, Any]:
    """The constants the mesh *computes*, read live instead of hard-coded.

    The pre-restructure monitors printed `PROBE_NATURES: mesh, synapse, immune`,
    `Lease 300s` and `quorum: 2/3 vote` as literals. Every one of those had
    changed under them, which made the monitor an active source of wrong
    information (TODO §1.2). These are read from the classes that own them.
    """
    d: Dict[str, Any] = {"_source": "CORE.WORMS.expansion.{spine,xenomorph}",
                         "_error": None, "error": None,
                         "probe_natures": None, "probe_limit": None,
                         "min_failures": None, "fail_rate": None,
                         "quorum": None, "lease_seconds": None,
                         "full_envs": None, "fallback_order": None,
                         "preferred_env": None, "lease": None,
                         "local_env": None, "host_capable": None}
    try:
        from CORE.WORMS.expansion.spine import RedQueenWorm
        from CORE.WORMS.expansion.xenomorph import Lilith
        from CORE.env_id import env_id

        d["probe_natures"] = list(RedQueenWorm.PROBE_NATURES)
        d["probe_limit"] = RedQueenWorm.PROBE_LIMIT
        d["min_failures"] = RedQueenWorm.QUARANTINE_MIN_FAILURES
        d["fail_rate"] = RedQueenWorm.QUARANTINE_FAIL_RATE
        d["quorum"] = RedQueenWorm.QUORUM
        d["lease_seconds"] = Lilith.LEASE_SECONDS
        d["full_envs"] = list(Lilith.FULL_ENVS)
        d["fallback_order"] = list(Lilith.FALLBACK_ORDER)
        d["preferred_env"] = Lilith.PREFERRED_ENV
        d["local_env"] = _safe(env_id, None)
        d["lease"] = _safe(Lilith().lease_holder, None)
        try:
            from CORE.WORMS.expansion.xenomorph import _host_capable

            d["host_capable"] = _safe(_host_capable, None)
        except ImportError:
            pass
    except ImportError as exc:
        d["_error"] = f"mesh modules unavailable: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        d["_error"] = str(exc)[:120]
    return d


def quarantine_summary(spine: Dict[str, Any]) -> Dict[str, Any]:
    """Roll the cross-env quarantine ledger up into what a human needs to see.

    Quorum is counted over **distinct envs** in the persisted, gossiped ledger
    (audit finding #1), so this reports env counts — not votes, and not "2/3".
    """
    votes = spine.get("quarantine_votes") or {}
    per_finding = []
    total_votes = 0
    decided = 0
    envs: set = set()
    for finding, box in votes.items():
        if not isinstance(box, dict):
            continue
        tally: Dict[str, int] = {}
        for env, rec in box.items():
            envs.add(env)
            total_votes += 1
            verdict = (rec or {}).get("verdict", "?") if isinstance(rec, dict) else "?"
            tally[verdict] = tally.get(verdict, 0) + 1
        top, count = max(tally.items(), key=lambda kv: kv[1]) if tally else ("", 0)
        per_finding.append({"finding": finding, "envs": sorted(box),
                            "env_count": len(box), "top": top, "count": count,
                            "tally": tally})
        if count >= 2:
            decided += 1
    per_finding.sort(key=lambda f: -f["env_count"])
    return {"findings": per_finding, "finding_count": len(per_finding),
            "votes": total_votes, "envs": sorted(envs), "env_count": len(envs),
            "decided": decided}


def q_summary(spine: Dict[str, Any]) -> Dict[str, Any]:
    """Q-table split into measured / starved / settled-neutral.

    `get_q_weight()` alone cannot distinguish a *proven neutral* arm (0.0 with
    pulls) from a *never-tried* one (0.0 with none); the pre-restructure monitor
    showed only weights, so a starved arm rendered identically to a settled one.
    """
    weights = spine.get("q_weights") or {}
    counts = spine.get("q_updates") or {}
    pulled = {k for k, c in counts.items() if (num(c) or 0) > 0}
    # The spine's own definition of STARVED is "weight 0.0 AND never pulled"
    # (see `get_q_updates`'s docstring): that is the cold-start collapse the
    # pull counter exists to expose. A key with a non-zero weight and no pulls
    # is a different thing — seeded, not starved — and is reported separately so
    # the two monitors that both say "starved" cannot mean different things.
    starved = [k for k, v in weights.items()
               if k not in pulled and abs(num(v) or 0.0) < 1e-9]
    unmeasured = [k for k in weights if k not in pulled
                  and abs(num(weights.get(k)) or 0.0) >= 1e-9]
    settled = [k for k in pulled if abs(num(weights.get(k)) or 0.0) < 1e-9]
    vals = [num(v) or 0.0 for v in weights.values()]
    # `top` is ranked on the EVIDENCE-SHRUNK value, which is what q_order sorts
    # by. Ranking the card on the raw weight showed a plateau as a ranking: 29
    # of 76 live keys sit within 1e-5 of the clip, so the old "top" list was
    # decided by dict order. `top_raw` is kept for the contrast, and `clip_keys`
    # is the measured size of that plateau.
    eff = spine.get("q_effective") or {}
    ranked = [(k, num(eff.get(k, weights.get(k))) or 0.0) for k in weights]
    return {
        "keys": len(weights), "count_keys": len(counts),
        "measured": len(pulled), "starved": len(starved),
        "unmeasured": len(unmeasured),
        "settled_neutral": len(settled),
        "positive": sum(1 for v in vals if v > 0),
        "negative": sum(1 for v in vals if v < 0),
        "mean": (sum(vals) / len(vals)) if vals else None,
        "clip_keys": sorted(k for k, v in weights.items()
                            if abs(num(v) or 0.0) > 0.999999),
        "starved_keys": sorted(starved)[:10],
        "unmeasured_keys": sorted(unmeasured)[:10],
        "top": sorted(ranked, key=lambda kv: -kv[1])[:6],
        "top_raw": sorted(((k, num(v) or 0.0) for k, v in weights.items()),
                          key=lambda kv: -kv[1])[:6],
    }


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _read(obj: Any, name: str) -> Any:
    """Read `obj.name` whether it is a plain attribute, a property, or a method.

    Caught for real: `HybridLogicalClock.node` is a property while `peek`,
    `skew_ms`, `observations` and `rejected` are methods, so `h.node()` raised
    `'str' object is not callable` and the whole clock card silently degraded to
    `unknown` (the collector's own try/except caught it). Treating the two the
    same removes a failure mode that costs a card and never announces itself.
    """
    try:
        attr = getattr(obj, name)
    except Exception:
        return None
    if callable(attr):
        try:
            return attr()
        except Exception:
            return None
    return attr
