#!/usr/bin/env python3
"""technique_heartbeat.py — unconditional heartbeat caller for the technique runtime.

Closes the L3/L4 gap: run_all/status had no production callers, so nothing
executed the registered adapters outside manual runs. This script is the
heartbeat: invoke every adapter, collect fleet health, leave one join row.

ALLOW_WRITES policy (decided 2026-10-10, see Step 3): the module default in
technique_wiring/_l1_memory.py stays False so ad-hoc run_all() never deposits
rows; the heartbeat ALONE opts in per-run, bounding deposits to one row per
write-loop adapter (EP120 reflection_lesson, EP131 memory_tier_rollup, EP235
recall_feedback) per heartbeat. Revisit only with an explicit order.

Hook discipline: every run leaves source="technique-link" rows
(heartbeat summary + per-hook verify rows), so fleet_link_health and the
live_monitor links block read this heartbeat even when nothing fails.

Fail-open: exit code is always 0; failures go to stderr + failure ledger.
Run from the repo's scripts/ dir or anywhere (paths resolve from __file__).
Scheduled as Azazael-TechniqueHeartbeat (hourly) — see technique_heartbeat.bat.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# -- Lifecycle-owned collaborators (Lane C redo, 2026-10-22) ----------------
# Script-level declarations: resolved once at startup, not per heartbeat leg.
# Both optional with None fallbacks; legs degrade to legacy/report paths.
try:
    from CORE.rubric_evolution import gate_distillation as _gate_distillation
    _GATE_OK = True
except ImportError:
    _gate_distillation = None  # type: ignore
    _GATE_OK = False
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from meta_reflect import gather_trajectories as _gather_trajectories
    _TRAJ_OK = True
except ImportError:
    _gather_trajectories = None  # type: ignore
    _TRAJ_OK = False


def _fail(where, exc):
    try:
        from CORE.failure_ledger import note_failure
        note_failure(where, exc)
    except Exception as inner:
        print(f"heartbeat recorder down: {where}: {type(inner).__name__}",
              file=sys.stderr)
    print(f"heartbeat note: {where}: {type(exc).__name__}: {exc}", file=sys.stderr)


#: Action vocabularies the PSYCHE fit loop accepts (PSYCHE/distill.py
#: choice_features). Anything else in a chain row is a malformed row, not a
#: new action type: counted, never fitted.
_PSYCHE_ACTIONS = {"persist": {"continue", "quit", "defer"},
                   "fair": {"fair", "share", "exploit", "withhold"}}


def psyche_fit_step(store=None, chain_limit: int = 500) -> dict:
    """Hourly PSYCHE fit leg: behavior chains -> per-actor thetas (L4 closer).

    Reads `psyche-chain`/`behavior_action` rows, fits each actor's history
    with the REAL `choice_features`/`fit_choice_traits` (no reimplementation),
    writes the OWNER snapshot only when at least one trait fitted (an
    abstention must never clobber the last good snapshot), and records a fit
    report row whenever chains existed — so `no-input` (nothing to read) and
    `abstained` (read, too sparse to fit) stay distinguishable downstream.
    Never raises; returns the report dict (also stored under
    out["steps"]["psyche_fit"] by main).
    """
    report: dict = {"ok": False, "n_rows": 0, "actors": {},
                    "snapshot_written": False, "verdict": "no-input"}
    try:
        from PSYCHE.distill import choice_features, fit_choice_traits
        from PSYCHE.traits import default_theta_path
        if store is None:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
        rows = (store.query_memories(source="psyche-chain",
                                     event_type="behavior_action",
                                     limit=chain_limit) or [])
        report["n_rows"] = len(rows)
        if not rows:
            # Healthy idle, not a failure: the leg ran, nothing was fitted,
            # nothing was written. ok=True distinguishes this from leg-failed.
            report["ok"] = True
            return report
        by_actor: dict = {}
        malformed = 0
        for row in rows:
            try:
                data = (row or {}).get("data", {})
                if isinstance(data, str):
                    data = json.loads(data)
                if not isinstance(data, dict):
                    raise ValueError("non-dict data")
                actor = str(data.get("actor") or "?")[:80]
                kind = data.get("kind")
                action = data.get("action")
                if kind not in _PSYCHE_ACTIONS or action not in _PSYCHE_ACTIONS[kind]:
                    malformed += 1
                    continue
                by_actor.setdefault(actor, []).append(
                    {"kind": kind, "action": action,
                     "planned": bool(data.get("planned", False))})
            except Exception:
                malformed += 1
        report["malformed"] = malformed
        fitted_any_owner = False
        for actor in sorted(by_actor):
            actions = by_actor[actor]
            feats = choice_features(actions)
            fitted = fit_choice_traits(feats)
            n_persist = sum(1 for a in actions if a["kind"] == "persist")
            n_fair = sum(1 for a in actions if a["kind"] == "fair")
            entry = {"n_actions": len(actions), "n_persist": n_persist,
                     "n_fair": n_fair, "C": fitted.get("C"),
                     "H": fitted.get("H")}
            report["actors"][actor] = entry
            if actor == "owner":
                scores = {k: v for k, v in
                          (("C", fitted.get("C")), ("H", fitted.get("H")))
                          if isinstance(v, (int, float))}
                if scores:
                    # EP471 (RG-SED strict-improvement gate): the owner
                    # snapshot is the distilled policy — distill back ONLY on
                    # empirical gain over the last good snapshot. A regression
                    # or tie keeps the old snapshot and reports gated, so a
                    # bad fit can never clobber a good one. Any comparison
                    # error falls back to the legacy write: the gate must
                    # never clobber on an unreadable comparison.
                    snapshot = {"name": "heartbeat-fit",
                                "source": "psyche-heartbeat",
                                "domain_scores": scores,
                                "n_used": len(actions)}
                    dest = default_theta_path()
                    try:
                        if _gate_distillation is None:
                            raise RuntimeError("distill-gate-unavailable")
                        old_scores = {}
                        if dest.is_file():
                            old_scores = (json.loads(dest.read_text(
                                encoding="utf-8")) or {}).get(
                                    "domain_scores", {}) or {}
                        old_scalar = sum(
                            float(v) for v in old_scores.values()
                            if isinstance(v, (int, float)))
                        gate = _gate_distillation(
                            old_scalar, float(sum(scores.values())))
                    except Exception as exc:
                        _fail("technique-heartbeat:distill-gate", exc)
                        gate = {"distill": True, "gain": 0.0,
                                "gaps": ["gate-error-legacy-write"]}
                    if gate.get("distill"):
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(json.dumps(snapshot, indent=2,
                                                   sort_keys=True),
                                        encoding="utf-8")
                        report["snapshot_written"] = True
                        fitted_any_owner = True
                    else:
                        report["snapshot_written"] = False
                        report["snapshot_gated"] = {
                            "gain": gate.get("gain"),
                            "reason": ("no empirical gain over last "
                                       "snapshot")}
        if fitted_any_owner:
            report["verdict"] = "fitted"
        elif report["actors"]:
            report["verdict"] = "abstained-below-threshold"
        try:
            store.record_event(
                source="psyche-fit", event_type="theta_fit", data=report,
                severity="info", tags=["psyche", "theta-fit"])
            report["report_recorded"] = True
        except Exception as exc:
            _fail("technique-heartbeat:psyche-fit-report", exc)
            report["report_recorded"] = False
        report["ok"] = True
        return report
    except Exception as exc:
        _fail("technique-heartbeat:psyche-fit", exc)
        report["ok"] = False
        report["verdict"] = "leg-failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report


def main() -> int:
    t0 = time.perf_counter()
    out = {"ok": True, "steps": {}}
    try:
        from CORE.technique_runtime import (
            fleet_link_health,
            load_slices,
            record_link,
            run_all,
        )
        from CORE.technique_wiring import _l1_memory as l1

        out["steps"]["slices"] = load_slices()
        # E1 production caller for ErrorRecoveryOrchestrator: the heartbeat's
        # own run_all runs under retry/backoff/circuit. Circuits are
        # per-process (one-shot hourly run), so cross-run memory is absent by
        # construction — retries still engage transient faults (store locks).
        # A failed pass may deposit duplicate lesson rows (bounded: 2 attempts).
        from CORE.WORMS.expansion.error_recovery import (
            ErrorRecoveryOrchestrator as _Ero)
        _ero = _Ero(max_retries=2, base_backoff_ms=1000.0)

        def _checked_run_all():
            _r = run_all(record=True)
            if _r.get("errored", 0):
                raise RuntimeError("run_all errored=%s" % _r.get("errored"))
            return _r

        # Step 3 policy: heartbeat-only opt-in, restored immediately after.
        prev = l1.enable_writes(True)
        try:
            _rec = _ero.execute_with_recovery(
                "technique-heartbeat", None, execute_fn=_checked_run_all)
        finally:
            l1.enable_writes(prev)
        run = _rec.get("result") or {}
        out["steps"]["run_all"] = {
            "n": run.get("n"), "engaged": run.get("engaged"),
            "errored": run.get("errored"),
        }
        out["steps"]["recovery"] = {
            "outcome": _rec.get("outcome"),
            "strategy": (_rec.get("recovery") or {}).get("strategy"),
        }
        out["steps"]["health"] = fleet_link_health(limit=1000)
        try:
            out["steps"]["psyche_fit"] = psyche_fit_step()
        except Exception as exc:
            _fail("technique-heartbeat:psyche-fit-leg", exc)
            out["steps"]["psyche_fit"] = {"ok": False, "verdict": "leg-failed"}
        try:
            from CORE.WORMS.expansion.learning_grpo import snapshot_log_ratios
            from CORE.local_memory import get_memory_store as _get_store
            _drift = snapshot_log_ratios(store=_get_store())
            out["steps"]["policy_drift"] = _drift
            if _drift.get("verdict") == "pairs":
                try:
                    _get_store().record_event(
                        source="GRPOTrainer", event_type="policy_drift",
                        data={"pairs": _drift.get("pairs"),
                              "n_snapshots": _drift.get("n_snapshots")},
                        severity="info",
                        tags=["grpo", "policy", "drift"])
                    out["steps"]["policy_drift_kept"] = True
                except Exception as exc:
                    _fail("technique-heartbeat:policy-drift-report", exc)
                    out["steps"]["policy_drift_kept"] = False
        except Exception as exc:
            _fail("technique-heartbeat:policy-drift-leg", exc)
            out["steps"]["policy_drift"] = {"ok": False, "verdict": "leg-failed"}
        try:
            from CORE.WORMS.expansion.learning_grpo import snapshot_concentration
            from CORE.local_memory import get_memory_store as _get_store_c
            _conc = snapshot_concentration(store=_get_store_c())
            out["steps"]["policy_concentration"] = _conc
            if _conc.get("verdict") == "pairs":
                try:
                    _get_store_c().record_event(
                        source="GRPOTrainer", event_type="policy_concentration",
                        data={"pairs": _conc.get("pairs"),
                              "n_snapshots": _conc.get("n_snapshots")},
                        severity="info",
                        tags=["grpo", "policy", "concentration"])
                    out["steps"]["policy_concentration_kept"] = True
                except Exception as exc:
                    _fail("technique-heartbeat:policy-concentration-report", exc)
                    out["steps"]["policy_concentration_kept"] = False
        except Exception as exc:
            _fail("technique-heartbeat:policy-concentration-leg", exc)
            out["steps"]["policy_concentration"] = {"ok": False, "verdict": "leg-failed"}
        try:
            from CORE.WORMS.expansion.queens import rainbow_round
            out["steps"]["rainbow"] = rainbow_round(probe_limit=1)
        except Exception as exc:
            _fail("technique-heartbeat:rainbow", exc)
            out["steps"]["rainbow"] = {"ok": False}
        try:
            from CORE.reward_scales import calibration_snapshot
            from CORE.local_memory import get_memory_store
            out["steps"]["calibration"] = calibration_snapshot(
                get_memory_store())
        except Exception as exc:
            _fail("technique-heartbeat:calibration", exc)
            out["steps"]["calibration"] = {"scales": {}, "refused": []}
        # EP427: recent trial outcomes as a health signal. gather_trajectories
        # reads the meta lineage + evolution margins (read-only); the leg
        # reports the verdict mix so a failing trial regime is visible in the
        # heartbeat. Read-only: never writes, never gates.
        try:
            if _gather_trajectories is None:
                raise RuntimeError("trajectories-unavailable")
            _trajs, _note = _gather_trajectories(limit=25)
            _verdicts: dict = {}
            for _t in _trajs:
                _v = str((_t or {}).get("verdict", "?"))
                _verdicts[_v] = _verdicts.get(_v, 0) + 1
            out["steps"]["meta_trials"] = {
                "ok": True, "n": len(_trajs), "note": str(_note)[:160],
                "verdicts": _verdicts}
        except Exception as exc:
            _fail("technique-heartbeat:meta-trials", exc)
            out["steps"]["meta_trials"] = {"ok": False, "n": 0}
        # E2 production caller for NeuralPipeline.replay_failed: the shared
        # file-backed DLQ (populated by pipeline training failures) is
        # replayed through single-worm clusters. Bounded: max 1 thread, and
        # a backlog over 10 is reported for the operator instead of
        # retried blind (a flood is systemic, not transient).
        try:
            from CORE.NeuralPipeline import NeuralPipeline
            from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

            def _replay_executor(worm_name, config):
                try:
                    cluster = ClusterWORMZ.build_from_registry([worm_name])
                    cluster.execute_all(worm_names=[worm_name])
                    agg = cluster.aggregate_results()
                    wr = (agg.worm_results or {}).get(worm_name, {})
                    good = (bool(wr) and wr.get("error") is None
                            and wr.get("success", True) is not False)
                    return {"success": good, "result": wr}
                except Exception as exc:
                    return {"success": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)}

            _pipe = NeuralPipeline(max_workers=2, llm_augmentation=False)
            _dlq = _pipe.dlq
            _pending = (_dlq.get_pending()
                        if hasattr(_dlq, "get_pending") else [])
            if len(_pending) > 10:
                out["steps"]["replay"] = {
                    "replayed": 0, "pending": len(_pending),
                    "deferred": "backlog-overflow (operator scope)"}
            else:
                _rep = _pipe.replay_failed(_replay_executor, max_concurrent=1)
                out["steps"]["replay"] = {
                    "replayed": _rep.get("replayed"),
                    "pending": len(_pending),
                    "results": _rep.get("results", [])[:10]}
        except Exception as exc:
            _fail("technique-heartbeat:replay", exc)
            out["steps"]["replay"] = {"replayed": 0, "pending": None}
        ok = run.get("errored", 1) == 0
        out["ok"] = bool(ok)
        try:
            out["steps"]["link_kept"] = bool(record_link(
                "technique_heartbeat", "hourly-run-all",
                bool(ok), citation=f"engaged={run.get('engaged')}/n={run.get('n')}"))
        except Exception as exc:  # link row is evidence, never fatal
            _fail("technique-heartbeat:record-link", exc)
            out["steps"]["link_kept"] = False
    except Exception as exc:  # noqa: BLE001 — heartbeat must never raise
        _fail("technique-heartbeat:run", exc)
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["trace"] = traceback.format_exc(limit=5)
    out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
