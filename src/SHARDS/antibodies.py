#!/usr/bin/env python3
"""Antibody shards — errors cloned into self-seeking detectors (routable).

Each antibody is derived from one observed error instance and sweeps for
every recurrence of it. Shape follows the shard contract: runnable probe,
JSON-serializable result, self-test. See
docs/ANTIBODY_SHARD_PARADIGM_2026-10-29.md for the paradigm.

Lifecycle (closed loop, evidence-gated):
    seeking -> neutralized -> immune, with reinfection reopening to seeking.
    Status advances only on recorded seek evidence: neutralized requires a
    passing seek; immune requires three consecutive passing seeks. The store
    (antibodies.json) is the deed book: atomic writes (D1) under an
    exclusive lock (D2), same pattern as morphed_registry.

Usage:
    from SHARDS.antibodies import run_antibody, seek_all
    seek_all()   # run every probe, record generations, print JSON
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple, TypedDict, NotRequired

try:
    from CORE.atomic_store import atomic_write_json, file_lock
    from CORE.failure_ledger import note_failure
except ImportError:
    # Script path (`python src/SHARDS/antibodies.py`): the checkout root
    # is cwd but src/ is not on sys.path. Bootstrap it once, guarded so
    # an installed/importable CORE is never shadowed by the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from CORE.atomic_store import atomic_write_json, file_lock
    from CORE.failure_ledger import note_failure
from SHARDS.polyglot import EXPECTED_SUM, run_shard, supported_shard_langs
from SHARDS import registry as _registry


# ── Type definitions ──────────────────────────────────────────────

class PythonInfo(TypedDict):
    path: str
    type: str
    discovered_via: str
    version: str | None
    source: NotRequired[str]
    embedded: NotRequired[bool]
    file: NotRequired[str]
    size_mb: NotRequired[float]
    age_days: NotRequired[int]
    size_bytes: NotRequired[int]
    modified: NotRequired[str | None]


class InstallInfo(TypedDict):
    path: str
    version: str | None
    type: str


class EmbeddedInfo(TypedDict):
    path: str
    source: str
    version: str | None
    embedded: bool


class RelationshipGraph(TypedDict):
    dependency_graph: Dict[str, Dict[str, Any]]
    version_distribution: Dict[str, List[str]]
    category_distribution: Dict[str, int]


class CleanupRecommendation(TypedDict):
    action: str
    item: str
    path: str
    reason: str
    impact: str
    size_mb: NotRequired[float]
    size_gb: NotRequired[float]
    can_remove: NotRequired[bool]


class SystemData(TypedDict):
    metadata: Dict[str, str]


_STORE_PATH = Path(__file__).with_name("antibodies.json")
_HISTORY_CAP = 50  # bounded growth: the store is a deed book, not a log
_IMMUNE_STREAK = 3  # consecutive passing seeks required for immunity


def _antibody_specs() -> Dict[str, Dict[str, Any]]:
    """The two first-generation antibodies (cloned from observed instances)."""
    return {
        "seq-range-mismatch": {
            "signature": {
                "namespace": "polyglot",
                "handler_role": "work-golden",
                "exc_type": "none",
                "message_shape": "work sum != EXPECTED_SUM",
                "ast_shape": "n/a (data bug, not structural)",
                "first_seen": "2026-10-29",
                "parent_instance": "bash work golden summed 1..20000 via seq",
            },
            "probe": "work-tier agreement across every toolchain",
        },
        "stale-table-routing": {
            "signature": {
                "namespace": "registry",
                "handler_role": "_measured_order",
                "exc_type": "none",
                "message_shape": "stale medians spent as measurements",
                "ast_shape": "n/a (policy bug, not structural)",
                "first_seen": "2026-10-29",
                "parent_instance": "stale sweep.json drove route_lang with warning only",
            },
            "probe": "forced-stale sweep must degrade to LANG_ORDER",
        },
    }


def _load_store() -> Dict[str, Any]:
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generation": 0, "updated_at": "", "antibodies": {}}


def _save_store(store: Dict[str, Any]) -> None:
    """Atomic write via CORE.atomic_store (D1) — single implementation."""
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(_STORE_PATH, store, "antibodies")


def _store_lock() -> Any:
    """Exclusive lock for read-modify-write (D2) — via CORE.atomic_store."""
    return file_lock(_STORE_PATH.with_suffix(".lock"), "antibodies")


def _probe_work_tier() -> Dict[str, Any]:
    """Seek every work golden that disagrees with EXPECTED_SUM."""
    instances: List[str] = []
    ran = 0
    for lang in supported_shard_langs():
        res = run_shard(lang, tier="work")
        if res["status"] == "skipped":
            continue
        ran += 1
        if not res["ok"]:
            instances.append(f"{lang}: {res.get('status')}"
                             f" sum_ok={res.get('sum')}")
    return {"ok": not instances, "ran": ran, "instances": instances,
            "expected_sum": EXPECTED_SUM}


def _probe_stale_fallback() -> Dict[str, Any]:
    """Stale sweep must degrade to canonical order (forced-stale probe)."""
    old = _registry.SWEEP_MAX_AGE_HOURS
    _registry.SWEEP_MAX_AGE_HOURS = -1  # every table stale by age
    try:
        order = _registry._measured_order()
        ok = order == list(_registry.LANG_ORDER)
    finally:
        _registry.SWEEP_MAX_AGE_HOURS = old
    return {"ok": ok, "ran": 1,
            "instances": [] if ok else ["_measured_order spent stale medians"]}


_PROBES: Dict[str, Any] = {
    "seq-range-mismatch": _probe_work_tier,
    "stale-table-routing": _probe_stale_fallback,
}


def run_antibody(name: str) -> Dict[str, Any]:
    """Execute one antibody probe; JSON-serializable result (shard shape)."""
    key = (name or "").lower()
    if key not in _PROBES:
        return {"name": key, "status": "unknown-antibody", "ok": False}
    try:
        found = _PROBES[key]()
    except Exception as e:
        note_failure("antibodies:probe", e)
        return {"name": key, "status": "probe-crashed", "ok": False,
                "error": str(e)}
    return {"name": key, "status": "pass" if found["ok"] else "fail",
            "ok": found["ok"], "ran": found["ran"],
            "instances": found["instances"]}


def record_seek(name: str, outcome: Dict[str, Any], root: str = "") -> Dict[str, Any]:
    """Append a seek observation as a new generation (Train step).

    Every run is evidence: generation counter advances, history stays
    bounded at _HISTORY_CAP. `root` tags which checkout the observation
    came from ("" = local); box seeks record one generation per root so
    the deed book holds the entire box, not just this checkout.
    Returns the recorded generation."""
    key = (name or "").lower()
    if key not in _PROBES:
        return {"ok": False, "error": f"unknown antibody: {key}"}
    with _store_lock():
        store = _load_store()
        store["generation"] = int(store.get("generation", 0)) + 1
        gen = store["generation"]
        entry = store.setdefault("antibodies", {}).setdefault(key, {
            "status": "seeking",
            "signature": _antibody_specs()[key]["signature"],
            "generations": [],
        })
        entry["generations"].append({
            "gen": gen,
            "at": datetime.now(timezone.utc).isoformat(),
            "ok": bool(outcome.get("ok", False)),
            "instances": list(outcome.get("instances", []))[:20],
            "origin": _origin(),
            "root": root,
            "uuid": uuid.uuid4().hex[:8],
        })
        entry["generations"] = entry["generations"][-_HISTORY_CAP:]
        try:
            _save_store(store)
        except OSError as e:
            note_failure("antibodies:store", e)
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": key, "gen": gen,
                "status": entry["status"]}


def _origin() -> str:
    try:
        return socket.gethostname()
    except OSError as e:
        note_failure("antibodies:origin", e)
        return "unknown-host"


def antibody_status(name: Optional[str] = None) -> Dict[str, Any]:
    """Current status (and bounded history) for one or all antibodies."""
    specs = _antibody_specs()
    stored = _load_store().get("antibodies", {})
    if name is not None:
        key = name.lower()
        if key not in specs:
            return {"ok": False, "error": f"unknown antibody: {key}"}
        return {"ok": True, "name": key,
                "status": stored.get(key, {}).get("status", "seeking"),
                "generations": stored.get(key, {}).get("generations", []),
                "signature": specs[key]["signature"]}
    return {"ok": True, "antibodies": {
        key: {"status": stored.get(key, {}).get("status", "seeking"),
              "seeks": len(stored.get(key, {}).get("generations", []))}
        for key in specs}}


def _streak(entry: Dict[str, Any]) -> int:
    """Consecutive passing seeks at the head of history."""
    streak = 0
    for gen in reversed(entry.get("generations", [])):
        if gen.get("ok"):
            streak += 1
        else:
            break
    return streak


def promote_antibody(name: str, to_status: str) -> Dict[str, Any]:
    """Advance (or reopen) an antibody on recorded evidence (Evolve step).

    seeking -> neutralized requires a passing seek on record.
    neutralized -> immune requires _IMMUNE_STREAK consecutive passing seeks.
    Any status -> seeking reopens on reinfection. Refusals are ledger-noted,
    never silent, never crashing."""
    key = (name or "").lower()
    want = (to_status or "").lower()
    if key not in _PROBES:
        return {"ok": False, "error": f"unknown antibody: {key}"}
    if want not in ("seeking", "neutralized", "immune"):
        return {"ok": False, "error": f"unknown status: {want}"}
    with _store_lock():
        store = _load_store()
        entry = store.setdefault("antibodies", {}).setdefault(key, {
            "status": "seeking",
            "signature": _antibody_specs()[key]["signature"],
            "generations": [],
        })
        have = entry.get("status", "seeking")
        if want == "seeking":
            entry["status"] = "seeking"  # reinfection reopens, always allowed
        elif want == "neutralized":
            if have == "immune":
                note_failure("antibodies:promote-skips-immune", None)
                return {"ok": False,
                        "error": "immune -> neutralized skips backward; "
                                 "reopen via seeking on reinfection"}
            last = (entry.get("generations") or [{}])[-1]
            if not last.get("ok", False):
                note_failure("antibodies:promote-no-evidence", None)
                return {"ok": False,
                        "error": "neutralized requires a passing seek on record"}
            entry["status"] = "neutralized"
        elif want == "immune":
            if have != "neutralized":
                note_failure("antibodies:promote-order", None)
                return {"ok": False,
                        "error": "immune requires neutralized first"}
            if _streak(entry) < _IMMUNE_STREAK:
                note_failure("antibodies:promote-streak", None)
                return {"ok": False,
                        "error": f"immune requires {_IMMUNE_STREAK} "
                                 f"consecutive passing seeks"}
            entry["status"] = "immune"
        try:
            _save_store(store)
        except OSError as e:
            note_failure("antibodies:store", e)
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": key, "from": have, "to": entry["status"]}


def seek_all(record: bool = True) -> Dict[str, Any]:
    """Run every probe; optionally record each as a generation (Train).

    Returns {ok, results}. ok is True only when every antibody passes
    on every toolchain present (missing toolchains skip cleanly)."""
    results: List[Dict[str, Any]] = []
    for key in _antibody_specs():
        res = run_antibody(key)
        if record:
            record_seek(key, res)
        results.append(res)
    report = {"seek": "PASS" if all(r["ok"] for r in results) else "FAIL",
              "ok": all(r["ok"] for r in results), "results": results}
    print(json.dumps(report, indent=1))
    return report


_BOX_MARKER = os.path.join("src", "SHARDS", "antibodies.py")
_BOX_SKIP_DIRS = {".git", "__pycache__", "node_modules", "target", "build",
                  "venv", ".venv", ".hg", ".svn", "site-packages"}
_BOX_MAX_DEPTH = 5
_BOX_TIMEOUT_S = 300


def _default_box_roots() -> List[str]:
    """Where to look for checkouts: repo parent + AZAZAEL_BOX_ROOTS env.

    The default stays conservative (siblings of this checkout, where the
    WSL mirror and distro copies live). Extend with the env var
    (os.pathsep-separated) to cover the rest of the box. Only existing
    directories survive; anything else is ignored, never an error."""
    roots: List[str] = []
    try:
        repo = Path(__file__).resolve().parents[2]
        roots.append(str(repo.parent))
    except Exception as e:
        note_failure("antibodies:box-roots", e)
    env = os.environ.get("AZAZAEL_BOX_ROOTS", "")
    if env:
        roots.extend(p for p in env.split(os.pathsep) if p.strip())
    seen: Set[str] = set()
    out: List[str] = []
    for root in roots:
        try:
            resolved = str(Path(root).resolve())
        except OSError as e:
            note_failure("antibodies:box-root-resolve", e)
            continue
        if resolved not in seen and Path(resolved).is_dir():
            seen.add(resolved)
            out.append(resolved)
    return out


def discover_checkouts(roots: Optional[List[str]] = None, max_depth: int = _BOX_MAX_DEPTH) -> List[str]:
    """Find Azazael checkouts under roots (marker-gated, bounded depth).

    A checkout is a directory containing src/SHARDS/antibodies.py — the
    antibody module itself is the marker, so pre-antibody checkouts are
    invisible by construction (documented limitation: sync first, then
    they join the box). Matched checkouts are not descended into;
hidden, VCS, build, and environment dirs are never entered."""
    if roots is None:
        roots = _default_box_roots()
    found: List[str] = []
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        stack: List[Tuple[Path, int]] = [(base, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                if (cur / _BOX_MARKER).is_file():
                    found.append(str(cur))
                    continue
            except OSError as e:
                note_failure("antibodies:discover-marker", e)
                continue
            if depth >= max_depth:
                continue
            try:
                children = [p for p in cur.iterdir() if p.is_dir()]
            except OSError as e:
                note_failure("antibodies:discover-list", e)
                continue
            for child in children:
                name = child.name
                if (name.startswith((".", "__", "venv", "conda"))
                        or name in _BOX_SKIP_DIRS
                        or "site-packages" in name):
                    continue
                stack.append((child, depth + 1))
    seen: Set[str] = set()
    ordered: List[str] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _seek_checkout(root: str, timeout: int = _BOX_TIMEOUT_S) -> Dict[str, Any]:
    """Run one checkout's own seek in a subprocess; parse its JSON report.

    Subprocess isolation (not import): the other checkout's code runs in
    its own interpreter and tree, so version drift between checkouts
    cannot contaminate this process. Anything unparseable is an
    `unreachable` outcome — never mistaken for clean."""
    script = str(Path(root) / "src" / "SHARDS" / "antibodies.py")
    try:
        proc = subprocess.run(
            [sys.executable, script, "--seek", "--no-record"],
            cwd=root, capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        note_failure("antibodies:checkout-seek", e)
        return {"unreachable": str(e)[:200]}
    try:
        text = proc.stdout.decode("utf-8", errors="replace")
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, UnicodeDecodeError) as e:
        note_failure("antibodies:checkout-parse", e)
        err = proc.stderr.decode("utf-8", errors="replace")[:200]
        return {"unreachable": f"unparseable report: {err}"}


def seek_box(roots: Optional[List[str]] = None, timeout: int = _BOX_TIMEOUT_S,
             record: bool = True,
             max_depth: int = _BOX_MAX_DEPTH) -> Dict[str, Any]:
    """Seek every checkout on the box; optionally record per-root (Train).

    Box semantics are strict: ok requires EVERY reachable checkout clean
    AND every discovered checkout reachable. An unreachable checkout is
    recorded as an instance (cannot prove clean != clean), so a stale or
    broken copy breaks the streak honestly instead of silently dropping
    out of the evidence. Recorded as one generation per (antibody, root).
    """
    checkouts = discover_checkouts(roots, max_depth=max_depth)
    per_root: List[Dict[str, Any]] = []
    for root in checkouts:
        per_root.append({"root": root,
                         "report": _seek_checkout(root, timeout=timeout)})
    results: List[Dict[str, Any]] = []
    record_errors: List[str] = []
    for name in _antibody_specs():
        instances: List[str] = []
        ran = 0
        roots_seen = 0
        for item in per_root:
            root = item["root"]
            rep = item["report"]
            sub: List[Dict[str, Any]] = [r for r in rep.get("results", [])
                    if r.get("name") == name]
            if not sub:
                instances.append(f"{root}: checkout unreachable "
                                 f"({rep.get('unreachable', 'no report')[:150]})")
                continue
            roots_seen += 1
            res = sub[0]
            ran += int(res.get("ran", 0))
            for inst in res.get("instances", []):
                instances.append(f"{root}: {inst}")
            if record:
                rec = record_seek(name, {"ok": bool(res.get("ok", False)),
                                          "instances": res.get("instances", [])},
                                   root=root)
                if not rec.get("ok"):
                    record_errors.append(f"{root}: {rec.get('error', '')[:150]}")
        ok = not instances
        results.append({"name": name, "status": "pass" if ok else "fail",
                        "ok": ok, "ran": ran, "roots": roots_seen,
                        "discovered": len(checkouts), "instances": instances})
    report = {"seek": "BOX-PASS" if all(r["ok"] for r in results) else "BOX-FAIL",
              "ok": all(r["ok"] for r in results),
              "checkouts": checkouts, "results": results}
    if record_errors:
        report["record_errors"] = record_errors
    print(json.dumps(report, indent=1))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Azazael antibody shards")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seek", action="store_true")
    ap.add_argument("--seek-box", action="store_true",
                    help="seek every checkout on the box")
    ap.add_argument("--roots", nargs="*", default=None,
                    help="box-seek roots (default: repo parent + env)")
    ap.add_argument("--max-depth", type=int, default=_BOX_MAX_DEPTH)
    ap.add_argument("--timeout", type=int, default=_BOX_TIMEOUT_S)
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--promote", nargs=2, metavar=("NAME", "STATUS"),
                    help="advance an antibody (evidence-gated)")
    args = ap.parse_args(argv)

    if args.list:
        print(json.dumps(
            {"antibodies": list(_antibody_specs()),
             "shard": "antibodies",
             "specs": _antibody_specs()}, indent=1))
        return 0
    if args.seek:
        return 0 if seek_all(record=not args.no_record)["ok"] else 1
    if args.seek_box:
        rep = seek_box(roots=args.roots, timeout=args.timeout,
                       record=not args.no_record,
                       max_depth=args.max_depth)
        return 0 if rep["ok"] else 1
    if args.status:
        print(json.dumps(antibody_status(), indent=1))
        return 0
    if args.promote:
        res = promote_antibody(args.promote[0], args.promote[1])
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
