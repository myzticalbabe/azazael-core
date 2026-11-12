#!/usr/bin/env python3
"""Repopulate the AOA data artifacts from the live system.

Why this exists
---------------
`AOA_MILESTONE.json` was hand-authored on **2026-06-30** and never had a
producer — only two readers (`aoa_monitor.py`, `scripts/live_monitor.py`).
By 2026-09-14 every descriptive claim in it had drifted:

    adapter_arms          8  ->  9   (KaliArm was added and never recorded)
    arm_registry.py     220  ->  246
    event_bus.py        120  ->  98
    session_manager.py  230  ->  212
    router.py           210  ->  374
    aoa_cli.py          180  ->  moved from core/ to cli/

The same shape as the monitor defects: a recorded value presented as current.
`benchmark_data.json` (the measurement baseline, dated 2026-08-27) likewise had
no producer and was read as if it described today's machine.

What this tool does
-------------------
Two categories of field, and they are treated differently on purpose:

* **Descriptive** (arm roster, module paths + line counts, live machine
  metrics) — refreshed from the live system every run.
* **Baseline / threshold** (`eve` t/s floors, the worm count that drift is
  measured *against*) — deliberately **not** rewritten. Overwriting a baseline
  with the value it is supposed to measure is how you manufacture false
  agreement, so instead every baseline gets stamped with the date it was taken
  and the current live value is recorded alongside it.

Recorded history (`session.milestones`) is never touched — those are real work
logs from a real date, and losing them to a refresh would be its own defect.

Usage::

    python refresh_oa_data.py            # rewrite the artifacts in place
    python refresh_oa_data.py --check    # report drift only, change nothing
"""
import argparse
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE = Path(__file__).resolve().parent
MILESTONE = BASE / "AOA_MILESTONE.json"
BENCHMARK = BASE / "benchmark_data.json"

sys.path.insert(0, str(BASE))
# `src/` is one level above BASE (this file's own directory, AOA/ -> src/).
# `_SRC_DIR` reuses the single `resolve()` already done above, so this module
# keeps ONE resolution of `__file__`; it is the same path as `parents[1]`.
# REPLACED `Path.home() / "azazael" / "src"`, which names a DIFFERENT, stale
# checkout from the one this file ships in -- with the env unset it put the
# wrong tree on sys.path, and with it whatever stale CORE lives there.
_SRC_DIR = BASE.parent
sys.path.insert(0, str(_SRC_DIR))

ARM_MODULES = (
    "arms.azazael_arm", "arms.codex_arm", "arms.eve_arm",
    "arms.libra_arm", "arms.support_arms", "arms.kali_arm",
)

# benchmark_data.json keys that existing consumers compare live values
# against. These are references, not descriptions: refresh must not touch them.
BASELINE_EVE_KEYS = (
    "min_gguf_tps", "min_ollama_tps", "best_gguf_model", "best_gguf_tps",
    "best_ollama_model", "best_ollama_tps", "discovery_gguf",
    "discovery_ollama", "discovery_total_models", "benchmark_date",
)
# Volatile machine readings: a snapshot of this moment, safe to restate.
VOLATILE_PERFMON_KEYS = (
    "cpu_percent", "memory_percent", "memory_available_gb",
    "disk_percent", "disk_free_gb",
)


def _now():
    return datetime.now().isoformat(timespec="seconds")


_REGISTRY = []


def _registry():
    """One initialised ArmRegistry for the whole run.

    `initialize_all()` is not optional: an arm that was never initialised
    answers `execute()` with zeros rather than raising, so probing a fresh
    registry silently returns `cpu_percent: 0`. Writing that over a real
    reading would corrupt the artifact with a *plausible* number — the exact
    failure this tool exists to remove.
    """
    if _REGISTRY:
        return _REGISTRY[0]
    try:
        for mod in ARM_MODULES:
            __import__(mod)
        from core.arm_registry import ArmRegistry
        reg = ArmRegistry()
        reg.initialize_all()
        _REGISTRY.append(reg)
        return reg
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] live registry unavailable ({e})")
        _REGISTRY.append(None)
        return None


def live_arms():
    """Arm roster from the live registry (never from the json)."""
    reg = _registry()
    if reg is None:
        return []
    return [dict(r) for r in reg.list_arms()]


def live_worms():
    """Worm count straight from the azazael arm, not the json snapshot."""
    reg = _registry()
    if reg is None:
        return None
    try:
        arm = reg.get_arm("azazael")
        if arm is None:
            raise LookupError("azazael arm not registered")
        d = arm.execute("discover_worms")
        return int(d.get("data", {}).get("worm_count"))
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] live worm discovery unavailable ({e})")
        return None


def live_perfmon():
    """Machine readings via the perfmon arm (same source the panel uses)."""
    reg = _registry()
    if reg is None:
        return {}
    try:
        arm = reg.get_arm("perfmon")
        if arm is None:
            raise LookupError("perfmon arm not registered")
        d = arm.execute("get_metrics")
        return dict(d.get("data", {}))
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] perfmon unavailable ({e})")
        return {}


def resolve_module(name, recorded_path):
    """Locate a module that may have moved since the file was authored.

    `aoa_cli.py` lived in `core/` when the file was written and lives in
    `cli/` now. Reporting the new path is the point; silently dropping the
    row because the old path 404s would hide the move.
    """
    cands = [BASE / recorded_path] if recorded_path else []
    cands.append(BASE / "core" / name)
    cands.append(BASE / "cli" / name)
    for c in cands:
        if c.exists():
            return c
    hits = sorted(p for p in BASE.rglob(name)
                  if "__pycache__" not in p.parts and p.is_file())
    return hits[0] if hits else None


def refresh_milestone(check_only=False):
    """Repopulate the descriptive sections of AOA_MILESTONE.json in place."""
    if not MILESTONE.exists():
        print(f"  [FAIL] {MILESTONE} missing")
        return []
    doc = json.loads(MILESTONE.read_text(encoding="utf-8"))
    arch = doc.setdefault("architecture", {})
    changes = []

    # --- core modules: live paths + line counts ---
    mods = arch.get("modules") or []
    resolved = []
    for m in mods:
        name = m.get("name", "")
        path = resolve_module(name, m.get("path"))
        live_lines = (len(path.read_text(encoding="utf-8").splitlines())
                      if path else None)
        new = dict(m)
        if path is not None:
            try:
                rel = path.relative_to(BASE).as_posix()
            except ValueError as exc:
                note_failure("refresh_oa_data:L180", exc)
                rel = str(path)
            new["path"] = rel
        if live_lines is not None and live_lines != m.get("lines"):
            changes.append(f"{name}: lines {m.get('lines')} -> {live_lines}")
            new["lines"] = live_lines
        if path is None and m.get("lines") is not None:
            changes.append(f"{name}: source file NOT FOUND (was {m.get('lines')} lines)")
            new["lines"] = None
            new["missing"] = True
        else:
            new.pop("missing", None)
        resolved.append(new)
    if resolved:
        arch["modules"] = resolved
        arch["core_modules"] = len(resolved)

    # --- arms: the live registry is the only truthful roster ---
    arms = live_arms()
    if arms:
        recorded = [a.get("id") for a in (arch.get("arms") or [])]
        live_ids = [a["arm_id"] for a in arms]
        added = [a for a in live_ids if a not in recorded]
        gone = [a for a in recorded if a not in live_ids]
        if added:
            changes.append(f"arms: +{', '.join(added)}")
        if gone:
            changes.append(f"arms: -{', '.join(gone)}")
        if len(recorded) != len(live_ids):
            changes.append(f"adapter_arms: {arch.get('adapter_arms')} -> {len(live_ids)}")
        if arch.get("arms"):
            # Keep the recorded shape, repopulate the fields from the registry.
            by_id = {a["arm_id"]: a for a in arms}
            rebuilt = []
            for old in arch["arms"]:
                live = by_id.get(old.get("id"))
                row = dict(old)
                if live:
                    row["name"] = live.get("name", row.get("name"))
                    row["category"] = live.get("category", row.get("category"))
                    row["priority"] = live.get("priority", row.get("priority"))
                    row["capabilities"] = live.get("capabilities", row.get("capabilities"))
                    row.pop("missing", None)
                else:
                    row["missing"] = True
                rebuilt.append(row)
            for a in arms:                       # newly-registered arms
                if a["arm_id"] not in recorded:
                    rebuilt.append({
                        "id": a["arm_id"], "name": a.get("name", ""),
                        "category": a.get("category", ""),
                        "priority": a.get("priority"),
                        "capabilities": a.get("capabilities"),
                    })
            arch["arms"] = rebuilt
        arch["adapter_arms"] = len(live_ids)

    worm_count = live_worms()
    if worm_count is not None:
        sess = doc.setdefault("session", {})
        if sess.get("worm_count") != worm_count:
            changes.append(f"session.worm_count: {sess.get('worm_count')} -> {worm_count}")
            sess["worm_count"] = worm_count

    doc["last_updated"] = _now()
    doc["provenance"] = {
        "generated_by": "refresh_oa_data.py",
        "generated_at": _now(),
        "descriptive_sections": ["architecture.modules", "architecture.arms",
                                 "architecture.adapter_arms", "session.worm_count"],
        "recorded_history_untouched": ["session.milestones", "cross_arm_routes"],
        "note": ("Descriptive fields come from the live registry at run time. "
                 "Recorded history is preserved verbatim."),
    }

    if check_only:
        return changes
    MILESTONE.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    return changes


def refresh_benchmark(check_only=False):
    """Restate the volatile machine snapshot; stamp baselines, never rewrite."""
    if not BENCHMARK.exists():
        print(f"  [FAIL] {BENCHMARK} missing")
        return []
    doc = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    changes = []

    perf = doc.setdefault("perfmon", {})
    live_perf = live_perfmon()
    for k in VOLATILE_PERFMON_KEYS:
        v = live_perf.get(k)
        # A zero reading is what an uninitialised or failed probe returns, so
        # it is not usable as evidence and must never overwrite a real value.
        if v is None or (isinstance(v, (int, float)) and v <= 0):
            if k in live_perf:
                print(f"  [WARN] perfmon.{k}={v!r} looks like a failed probe; kept")
            continue
        if v != perf.get(k):
            changes.append(f"perfmon.{k}: {perf.get(k)} -> {v}")
            perf[k] = v

    sysinfo = doc.setdefault("system", {})
    live_sys = {
        "python_version": platform.python_version(),
        "free_threaded": not getattr(sys, "_is_gil_enabled", lambda: True)(),
        "os": platform.system(),
        "node": platform.node(),
        "cpu_count": os.cpu_count(),
    }
    for k, v in live_sys.items():
        # `sys._is_gil_enabled` is 3.13+; if it is ever absent the answer is
        # unknown, and "unknown" must not be recorded as False (GIL enabled).
        if k == "free_threaded" and not hasattr(sys, "_is_gil_enabled"):
            print("  [WARN] free-threading state undetectable; kept")
            continue
        if v is not None and sysinfo.get(k) != v:
            changes.append(f"system.{k}: {sysinfo.get(k)} -> {v}")
            sysinfo[k] = v

    worms = live_worms()
    arms = live_arms()
    full = doc.get("azazael") or {}
    if worms is not None and full.get("live_worm_count") != worms:
        full["live_worm_count"] = worms
        full["live_worm_count_at"] = _now()
        changes.append(f"azazael.live_worm_count -> {worms}")
    if arms:
        full["live_arm_count"] = len(arms)
        full["live_arm_count_at"] = _now()
    doc["azazael"] = full

    # The baseline stays put, but it stops being anonymous: when it was taken,
    # and what the live value is right now, both on the record.
    doc["_meta"] = {
        "baseline_date": doc.get("_meta", {}).get("baseline_date")
                         or (doc.get("eve") or {}).get("benchmark_date"),
        "refreshed_at": _now(),
        "baseline_keys_not_rewritten": [
            "eve." + k for k in BASELINE_EVE_KEYS] + ["azazael.total_worms"],
        "note": ("`eve` thresholds and `azazael.total_worms` are the reference "
                 "this system measures drift against. They are NOT refreshed by "
                 "design; compare them against `live_*` / the panel's live line."),
    }
    if check_only:
        return changes
    BENCHMARK.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    return changes


def record_session(arms, worms, check_only=False):
    """Populate ~/.aoa/sessions/ using the project's own SessionManager.

    Nothing ever wrote here — the directory does not exist, so the panel
    reported "0 sessions on disk" for a subsystem that had, in fact, never
    recorded one. This records what actually happened on this run.
    """
    try:
        from core.session_manager import get_session_manager
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] SessionManager unavailable ({e})")
        return None
    mgr = get_session_manager()
    mgr.clear()
    sess = mgr.create_session(name="AOA data refresh",
                              tags=["data-refresh", "live-snapshot"])
    mgr.record_milestone(
        "aoa", "refresh_data_artifacts",
        description="Repopulate descriptive AOA artifacts from the live system",
        status=None,
        artifacts=["AOA_MILESTONE.json", "benchmark_data.json"])
    for a in arms:
        mgr.record_milestone(
            a["arm_id"], "snapshot_arm",
            description=f"{a.get('name','')} category={a.get('category','')} "
                        f"caps={a.get('capabilities','')}")
    if worms is not None:
        mgr.record_milestone("azazael", "discover_worms",
                            description=f"live worm count {worms}",
                            artifacts=[])
    mgr.complete_session()
    if check_only:
        return sess.session_id
    path = mgr.save_session(sess.session_id)
    print(f"  wrote session {sess.session_id} -> {path}")
    return sess.session_id


def main():
    ap = argparse.ArgumentParser(description="Repopulate AOA data from live state")
    ap.add_argument("--check", action="store_true",
                    help="report drift without writing anything")
    args = ap.parse_args()

    # keep the arm imports from spamming a 200-worm discovery trace
    try:
        import logging as _lg
        _lg.disable(_lg.WARNING)
    except Exception as exc:
        note_failure("refresh_oa_data:L381", exc)

    print(f"AOA data refresh  ({'CHECK ONLY' if args.check else 'WRITE'})  {_now()}")
    arms = live_arms()
    worms = live_worms()
    print(f"  live: {len(arms)} arms, {worms} worms")

    for label, fn in (("AOA_MILESTONE.json", refresh_milestone),
                      ("benchmark_data.json", refresh_benchmark)):
        ch = fn(check_only=args.check)
        print(f"\n{label}: {'clean — no drift' if not ch else str(len(ch)) + ' change(s)'}")
        for c in ch:
            print(f"    {c}")

    sid = record_session(arms, worms, check_only=args.check)
    if sid:
        print(f"\nsession recorded: {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
