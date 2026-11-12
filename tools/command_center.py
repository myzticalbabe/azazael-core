#!/usr/bin/env python3
"""One-screen, screenshot-ready command center for the live Azazael ecosystem.

The existing monitors are deep forensic surfaces.  This module is deliberately a
*summary* surface: four bounded panels, four global decisions, and closed raw
evidence.  Every displayed number comes from a current collector or a state
artifact; missing evidence is shown as unknown rather than converted to zero.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STATE = ROOT / "state"
for path in (str(ROOT / "src"), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

PANEL_ORDER = ("azazael", "aoa", "exmachina", "machine", "algorithms")
STATUS_RANK = {"unknown": 0, "ok": 1, "warn": 2, "critical": 3}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value


def _age_seconds(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, time.time() - float(value))


def _age_label(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or not math.isfinite(float(seconds)):
        return "unknown"
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _mtime_age(path: Path) -> float | None:
    try:
        return _age_seconds(path.stat().st_mtime)
    except OSError:
        return None


def _fmt_number(value: Any, digits: int = 2) -> str:
    if value is None or isinstance(value, bool):
        return "unknown"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "unknown"
    if number.is_integer() and abs(number) < 10000:
        return str(int(number))
    return f"{number:.{digits}f}"


def _metric(label: str, value: Any, detail: str = "") -> dict[str, str]:
    return {"label": label, "value": str(value), "detail": detail}


def _panel(
    key: str,
    title: str,
    status: str,
    headline: str,
    metrics: list[dict[str, str]],
    evidence: list[str],
    action: str,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "headline": headline,
        "metrics": metrics,
        "evidence": evidence[:4],
        "action": action,
        "raw": dict(raw),
    }


def collect_azazael() -> dict[str, Any]:
    try:
        import live_monitor as lm
        connection = lm.db_connect()
        if connection is None:
            raise RuntimeError("DirectMemory database unavailable")
        try:
            data = lm.collect(connection)
        finally:
            connection.close()
    except Exception as exc:
        return _panel(
            "azazael", "AZAZAEL", "unknown", "collector unavailable",
            [_metric("Mesh", "unknown"), _metric("Loops", "unknown"),
             _metric("Store", "unknown"), _metric("Audit", "unknown")],
            [f"collector error: {type(exc).__name__}: {exc}"],
            "Repair the Azazael collector before trusting this panel.",
            {"error": str(exc)},
        )

    freshness = data.get("freshness") or []
    stale = [row for row in freshness if not row.get("ok")]
    mesh = data.get("mesh") or {}
    dbmon = data.get("dbmon") or {}
    audit = data.get("audit") or {}
    thoughts = data.get("thoughts") or {}
    evo = data.get("evo") or []
    latest = evo[0] if evo else {}
    chain = _load_json(STATE / "chain_health.json", {})
    chain_age = _age_seconds(chain.get("generated"))
    # Data plane (2026-09-27): the transport seam's own liveness. This state
    # was written by the listener and read by no surface -- the exact
    # "state that exists but no monitor reads" class this panel fights.
    listener = _load_json(STATE / "mesh_listener.json", {})
    lp_age = _age_seconds(listener.get("at"))
    lp_up = bool(listener.get("listening")) and (lp_age is not None and lp_age <= 45)
    lp_label = "up" if lp_up else ("down" if listener else "unknown")
    live_chain_state = "unknown"
    live_chain_result = None
    live_chain_detail = "task-state probe unavailable"
    try:
        import chain_controller as chain_runtime
        live_chain_state, live_chain_result, live_chain_detail = chain_runtime._own_task_state()
    except Exception as exc:
        live_chain_detail = f"{type(exc).__name__}: {exc}"[:160]
    status = "critical" if (
        live_chain_state == "disabled" or stale or dbmon.get("healthy") is False
    ) else "ok"
    if live_chain_state == "disabled":
        headline = (
            f"chain disabled; {len(stale)}/{len(freshness)} loops stale; "
            f"DB {'unhealthy' if dbmon.get('healthy') is False else 'healthy'}"
        )
    elif stale or dbmon.get("healthy") is False:
        headline = (
            f"{len(stale)}/{len(freshness)} loops stale; "
            f"DB {'unhealthy' if dbmon.get('healthy') is False else 'healthy'}"
        )
    else:
        headline = f"mesh {mesh.get('ok', 0)}/{mesh.get('ok', 0) + mesh.get('fail', 0)} healthy"
    evidence = [
        f"Loops: " + (", ".join(str(x.get("label")) for x in stale) if stale else "all current"),
        f"DB: {_fmt_number(dbmon.get('err7d'))} errors / 7d; {dbmon.get('healthy')}",
        f"Chain: last fire {_age_label(chain_age)}; task {live_chain_state} "
        f"(result {live_chain_result if live_chain_result is not None else '?'})",
        f"Audit: {audit.get('compile_bad', '?')} compile failures; "
        f"{audit.get('uncovered', '?')} uncovered",
        f"Data plane: listener {lp_label} on port {listener.get('port', '?')}; "
        f"{listener.get('received', 0)} rx / {listener.get('errors', 0)} err / "
        f"{listener.get('gossip_merged', 0)} merges; inbox {listener.get('inbox', 0)}",
    ]
    action = (
        "Restore the Azazael chain heartbeat, then triage DB-monitor error bursts."
        if status == "critical" else
        "Keep the current cycle and confirm the next scheduled heartbeat."
    )
    raw = {
        "cycles": data.get("cycles"), "freshness": freshness, "mesh": mesh,
        "dbmon": dbmon, "audit": audit, "thoughts": thoughts,
        "chain_health": chain, "mesh_listener": listener,
        "chain_live_state": {
            "state": live_chain_state, "last_result": live_chain_result,
            "detail": live_chain_detail,
        },
        "latest_evolution": latest,
    }
    return _panel(
        "azazael", "AZAZAEL", status, headline,
        [
            _metric("Mesh", f"{mesh.get('ok', '?')}/{mesh.get('ok', 0) + mesh.get('fail', 0)}",
                    f"{_age_label(mesh.get('age'))} old"),
            _metric("Evolution", _fmt_number(latest.get("best"), 3),
                    f"{_age_label((latest.get('ts') and _parse_iso_age(latest.get('ts'))))} old"),
            _metric("Store", f"{_fmt_number((data.get('store') or {}).get('mb'), 0)} MB",
                    f"{dbmon.get('err7d', '?')} errors / 7d"),
            _metric("Thought", _fmt_number(thoughts.get("n"), 0),
                    f"{thoughts.get('ok', '?')} successful"),
            _metric("DataPlane",
                    f"{lp_label}:{listener.get('port', '?')}" if listener else "unknown",
                    f"{_age_label(lp_age)} old; "
                    f"{listener.get('received', 0)} rx / {listener.get('errors', 0)} err"),
        ], evidence, action, raw,
    )


def _parse_iso_age(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.astimezone()
        return max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None


def collect_aoa() -> dict[str, Any]:
    command = [sys.executable, str(ROOT / "src" / "AOA" / "aoa_monitor.py"), "--json", "--once"]
    try:
        result = subprocess.run(
            command, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "AOA probe failed")[-240:])
        lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
        data = json.loads(lines[-1]) if lines else {}
    except Exception as exc:
        return _panel(
            "aoa", "AOA", "unknown", "collector unavailable",
            [_metric("Arms", "unknown"), _metric("Workflow", "unknown"),
             _metric("Capabilities", "unknown"), _metric("Durable", "unknown")],
            [f"collector error: {type(exc).__name__}: {exc}"],
            "Repair the AOA JSON probe before trusting routing status.",
            {"error": str(exc)},
        )

    init = data.get("init") or {}
    workflow = data.get("workflow") or {}
    routing = data.get("routing") or {}
    files = data.get("files") or {}
    arms_total = len([key for key in init if not key.startswith("_")])
    arms_ok = sum(value is True for value in init.values())
    contract = routing.get("contract") or {}
    violation_count = contract.get("violations") or 0
    try:
        violation_count = int(violation_count)
    except (TypeError, ValueError):
        violation_count = 0
    violation_kinds = list((contract.get("by_kind") or {}).keys())
    milestone_age = _age_seconds(files.get("ms_newest_ts"))
    status = "critical" if workflow.get("passed") is False else (
        "warn" if violation_count or (isinstance(milestone_age, float) and milestone_age > 86400 * 7)
        else "ok"
    )
    return _panel(
        "aoa", "AOA", status,
        f"workflow {workflow.get('completed', '?')}/{workflow.get('total', '?')}; "
        f"routing contract {'clean' if contract.get('clean') else 'violated'}",
        [
            _metric("Arms", f"{arms_ok}/{arms_total}", f"{_fmt_number(data.get('startup_elapsed'))}s init"),
            _metric("Workflow", f"{workflow.get('completed', '?')}/{workflow.get('total', '?')}",
                    f"{workflow.get('elapsed_ms', '?')}ms"),
            _metric("Capabilities", routing.get("caps", "?"), f"{routing.get('pairs', '?')} slots"),
            _metric("Durable", routing.get("learned", "?"), "learned route keys"),
        ],
        [
            f"Routing: {routing.get('route_status', 'unknown')} via {routing.get('route_arm', '?')}",
            f"Contract: {violation_count} violation(s); {', '.join(violation_kinds[:2]) or 'none'}",
            f"Provenance: {files.get('sessions_n', '?')} sessions; milestones newest {_age_label(milestone_age)}",
            f"Workflow rc={workflow.get('rc', '?')}, failures={workflow.get('failed', '?')}",
        ],
        "Repair the stale-after-missing contract signal and refresh milestone ownership.",
        data,
    )


def collect_exmachina() -> dict[str, Any]:
    try:
        import exmachina_live_monitor as monitor
        data = monitor.exmachina_snapshot()
    except Exception as exc:
        return _panel(
            "exmachina", "EX MACHINA", "unknown", "collector unavailable",
            [_metric("Severity", "unknown"), _metric("Fresh", "unknown"),
             _metric("Funnel", "unknown"), _metric("Oracle", "unknown")],
            [f"collector error: {type(exc).__name__}: {exc}"],
            "Repair the Ex Machina collector before trusting epistemic verdicts.",
            {"error": str(exc)},
        )
    severity = data.get("severity") or {}
    summary = data.get("summary") or {}
    vitals = data.get("vitals") or {}
    signals = data.get("signals") or {}
    oracle = data.get("oracle") or {}
    fresh = vitals.get("fresh", 0)
    total_vitals = vitals.get("n", 0)
    status = "critical" if severity.get("escalated") or fresh == 0 else "warn"
    ranked = sorted(signals.items(), key=lambda row: (-float(row[1] or 0), row[0]))[:4]
    return _panel(
        "exmachina", "EX MACHINA", status,
        f"severity {_fmt_number(severity.get('score'))}; {severity.get('active', '?')} active signals",
        [
            _metric("Severity", _fmt_number(severity.get("score")), "escalated" if severity.get("escalated") else "contained"),
            _metric("Fresh", f"{fresh}/{total_vitals}", "evidence artifacts"),
            _metric("Funnel", f"{summary.get('passes', '?')}/{summary.get('attempts', '?')}",
                    f"{summary.get('promoted', '?')} promoted"),
            _metric("Oracle", oracle.get("rows", "?"), f"{oracle.get('eras', '?')} eras"),
        ],
        [f"{name.replace('_', ' ')}: {_fmt_number(value)}" for name, value in ranked],
        "Restore evidence producers first; then resolve causal absence and era invariance.",
        data,
    )


def collect_machine() -> dict[str, Any]:
    genome = _load_json(STATE / "tensor_genome.json", {})
    qd = _load_json(STATE / "qd_archive.json", {})
    oracle = _load_json(STATE / "fitness_oracle.last.json", {})
    gate = _load_json(STATE / "tensor_gate_ab.json", {})
    mutation = _load_json(STATE / "mutation_summary.json", {})
    rl = _load_json(STATE / "evolved_rl_params.json", {})
    genes = genome.get("genes") or []
    cells = qd.get("cells") or {}
    holdout = rl.get("holdout") or {}
    gate_ok = gate.get("approved") is True
    oracle_ok = oracle.get("passed") is True
    ages = [
        _mtime_age(STATE / "tensor_genome.json"),
        _mtime_age(STATE / "fitness_oracle.last.json"),
        _mtime_age(STATE / "tensor_gate_ab.json"),
    ]
    stale = [age for age in ages if age is not None and age > 86400]
    status = "critical" if not gate_ok or not oracle_ok else ("warn" if stale else "ok")
    return _panel(
        "machine", "MACHINE", status,
        f"gate {gate.get('decision', 'unknown')}; oracle {'pass' if oracle_ok else 'fail'}",
        [
            _metric("Genome", len(genes), "tunable genes"),
            _metric("QD", len(cells), "occupied cells"),
            _metric("Mutation", _fmt_number(mutation.get("score")), f"{mutation.get('killed', '?')}/{mutation.get('mutants', '?')} killed"),
            _metric("Holdout", "pass" if holdout.get("passed") else "fail", f"margin {_fmt_number(holdout.get('margin'))}"),
        ],
        [
            f"Era-robust p: {_fmt_number(oracle.get('era_robust_p'))} / floor {_fmt_number(oracle.get('min_prob'))}",
            f"Gate approved: {gate.get('approved')}; criterion {gate.get('criterion', 'unknown')}",
            f"Evidence ages: {', '.join(_age_label(age) for age in ages)}",
            f"Target: {mutation.get('target', 'unknown')}",
        ],
        "Rebuild current oracle/gate evidence; do not promote the unapproved genome.",
        {
            "genome": genome, "qd_summary": {"cells": len(cells), "at": qd.get("saved_at")},
            "oracle": oracle, "gate": gate, "mutation": mutation,
            "rl": {"promoted_at": rl.get("promoted_at"), "holdout": holdout},
        },
    )


def collect_algorithms() -> dict[str, Any]:
    """Algorithm tier: laboratory qualification + meta-evolution control plane.

    Both run in production on the chain (`algolab` / `meta` jobs) and had no
    monitor at all (reconciliation 2026-09-24). Missing artifacts read as
    `unknown`, never zero; ages come from the stamps INSIDE the artifacts.
    """
    lab = _load_json(STATE / "algorithm_lab" / "current.json", {})
    mevo_dir = Path(os.environ.get(
        "AZAZAEL_META_EVOLUTION_DIR", str(STATE / "meta_evolution")))
    mevo = _load_json(mevo_dir / "current.json", {})
    lab_age = _parse_iso_age(lab.get("generated_at"))
    mevo_age = _parse_iso_age(mevo.get("at"))

    qual = {str(r.get("component")): str(r.get("status") or "unknown")
            for r in (lab.get("qualification") or [])
            if isinstance(r, dict) and r.get("component")}
    served = sum(v == "served" for v in qual.values())
    me = ((lab.get("diversity") or {}).get("map_elites") or {})
    cand = mevo.get("candidate")
    open_cand = 1 if (cand and not mevo.get("apply")) else 0
    life = mevo.get("lifecycle")
    life = life.get("state") if isinstance(life, dict) else life

    lab_stale = lab_age is not None and lab_age > 24 * 3600
    mevo_stale = mevo_age is not None and mevo_age > 150 * 60
    if not qual and not life:
        status, headline = "unknown", "no algorithm-tier evidence on disk"
    elif open_cand or lab_stale or mevo_stale:
        status = "warn"
        bits = []
        if open_cand:
            bits.append(f"{cand.get('severity', '?')} candidate open")
        if lab_stale:
            bits.append("lab evidence stale")
        if mevo_stale:
            bits.append("control plane stale")
        headline = "; ".join(bits)
    else:
        status = "ok"
        headline = f"{served}/{len(qual)} components served; control plane {life or 'idle'}"

    evidence = ["Qualification: " + (", ".join(f"{k}={v}" for k, v in sorted(qual.items()))
                                     or "none on disk")]
    if isinstance(cand, dict):
        evidence.append(f"Candidate: {cand.get('detector')} @ "
                        f"{cand.get('path')}:{cand.get('line')} - {cand.get('why')}")
    evidence.append(f"Ages: lab {_age_label(lab_age) or 'unknown'}, "
                    f"meta-evo {_age_label(mevo_age) or 'unknown'}")
    if me.get("filled") is not None:
        evidence.append(f"Diversity: {me.get('filled')}/{me.get('total_cells')} cells, "
                        f"coverage {me.get('coverage')}")

    if open_cand:
        action = ("Apply or reject the open meta-evolution candidate "
                  f"({cand.get('path')}:{cand.get('line')}); it stays open "
                  "until the control plane re-scans.")
    elif lab_stale or mevo_stale:
        action = "Refresh the stale algorithm-tier producer (chain jobs algolab / meta)."
    else:
        action = "No algorithm-tier action; evidence is current."

    metrics = [
        _metric("Qualification", f"{served}/{len(qual)} served",
                ", ".join(sorted(set(qual.values()))) or "no evidence"),
        _metric("Control plane", life or "unknown",
                f"{open_cand} open candidate(s)"),
        _metric("Diversity",
                f"{me.get('filled', '--')}/{me.get('total_cells', '--')} cells",
                f"coverage {100 * float(me['coverage']):.0f}%"
                if me.get("coverage") is not None else "no map-elites evidence"),
        _metric("Evidence age", _age_label(lab_age) or "unknown",
                f"meta-evo {_age_label(mevo_age) or 'unknown'}"),
    ]
    return _panel("algorithms", "ALGORITHM TIER", status, headline, metrics,
                  evidence, action,
                  {"algorithm_lab": lab, "meta_evolution": mevo})


def collect_snapshot() -> dict[str, Any]:
    collectors = {
        "azazael": collect_azazael,
        "aoa": collect_aoa,
        "exmachina": collect_exmachina,
        "machine": collect_machine,
        "algorithms": collect_algorithms,
    }
    panels: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for key in PANEL_ORDER:
        try:
            panels[key] = collectors[key]()
        except Exception as exc:  # a failed summary must remain visible
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
            panels[key] = _panel(
                key, key.upper(), "unknown", "collector failed", [],
                [f"{type(exc).__name__}: {exc}"],
                "Repair this collector before using the panel.", {"error": str(exc)},
            )
    statuses = [panel["status"] for panel in panels.values()]
    overall = max(statuses, key=lambda value: STATUS_RANK[value]) if statuses else "unknown"
    critical = sum(value == "critical" for value in statuses)
    actions = [panels[key]["action"] for key in PANEL_ORDER
               if panels[key]["status"] in {"critical", "warn"}]
    counts = {name: sum(value == name for value in statuses) for name in STATUS_RANK}
    headline = (
        f"{critical} critical subsystem{'' if critical == 1 else 's'}; "
        f"{counts['warn']} degraded; prioritize causal evidence over cosmetic recovery"
    )
    return {
        "schema": "azazael.command_center.v1",
        "generated_at": _utc_now(),
        "overall": overall,
        "headline": headline,
        "counts": counts,
        "panels": panels,
        "actions": actions[:4],
        "errors": errors,
    }


def render_page(refresh_seconds: int = 60, initial: Mapping[str, Any] | None = None) -> str:
    refresh = max(15, int(refresh_seconds))
    bootstrap = (json.dumps(initial, separators=(",", ":"), default=str).replace("</", "<\\/")
                 if initial is not None else "null")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Azazael Command Center</title>
<style>
:root{{--bg:#07101c;--panel:#0d192a;--line:#21344c;--text:#edf5ff;--muted:#8ea3bd;--ok:#42d392;--warn:#ffbf69;--critical:#ff6b7a;--blue:#67b7ff;--purple:#b995ff;--cyan:#54d6df}}
*{{box-sizing:border-box}}html,body{{width:100%;height:100%;margin:0}}body{{overflow:hidden;background:radial-gradient(circle at 15% 0,#112944 0,transparent 32%),radial-gradient(circle at 90% 100,#221b44 0,transparent 34%),var(--bg);color:var(--text);font:13px/1.35 Inter,Segoe UI,system-ui,sans-serif}}
.app{{height:100vh;display:grid;grid-template-rows:58px 64px minmax(0,1fr) 28px;gap:10px;padding:12px 16px}}
header{{display:flex;align-items:center;justify-content:space-between;border:1px solid var(--line);border-radius:14px;padding:0 16px;background:rgba(10,22,38,.86);box-shadow:0 12px 35px #0005}}h1{{font-size:20px;letter-spacing:.2px;margin:0}}h1 span{{color:var(--blue)}}header p{{margin:2px 0 0;color:var(--muted);font-size:11px}}.verdict{{font-weight:800;letter-spacing:.12em;text-transform:uppercase;padding:8px 12px;border-radius:999px;border:1px solid currentColor}}.critical{{color:var(--critical)}}.warn{{color:var(--warn)}}.ok{{color:var(--ok)}}.unknown{{color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}}.kpi{{border:1px solid var(--line);border-radius:12px;background:rgba(12,27,46,.82);padding:8px 11px;min-width:0}}.kpi b{{display:block;font-size:17px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.kpi small{{color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:9px}}
.grid{{min-height:0;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:10px}}article{{min-height:0;overflow:hidden;border:1px solid var(--line);border-radius:14px;background:linear-gradient(145deg,rgba(15,32,54,.96),rgba(9,20,35,.96));padding:10px 12px;position:relative}}article:before{{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--accent,var(--blue))}}.azazael{{--accent:var(--blue)}}.aoa{{--accent:var(--warn)}}.exmachina{{--accent:var(--critical)}}.machine{{--accent:var(--purple)}}
.phead{{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}}.phead h2{{font-size:12px;letter-spacing:.14em;margin:0;color:var(--accent)}}.pill{{font-size:9px;font-weight:800;letter-spacing:.08em;padding:3px 7px;border:1px solid currentColor;border-radius:999px}}.headline{{font-size:14px;font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:7px}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:7px}}.metric{{background:#091524;border:1px solid #1c3048;border-radius:9px;padding:5px 7px;min-width:0}}.metric b{{font:700 15px/1.1 ui-monospace,Consolas,monospace;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.metric span{{display:block;color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.metric i{{display:block;color:var(--muted);font-style:normal;font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
ul{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:1fr 1fr;gap:3px 10px}}li{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#c4d3e5;font-size:10px}}li:before{{content:"›";color:var(--accent);margin-right:5px;font-weight:900}}.action{{margin-top:7px;border-top:1px solid var(--line);padding-top:6px;color:#d9e7f7;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.action b{{color:var(--accent)}}
details{{position:absolute;right:9px;bottom:7px;font-size:9px;color:var(--muted)}}details summary{{cursor:pointer}}pre{{max-width:700px;max-height:320px;overflow:auto;background:#07101c;padding:8px;border:1px solid var(--line);border-radius:8px;color:#b9cbe0}}
footer{{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:10px}}nav a{{color:var(--muted);text-decoration:none;margin-left:12px}}nav a:hover{{color:var(--text)}}
@media(max-width:1000px){{body{{overflow:auto}}.app{{height:auto;min-height:100vh;grid-template-rows:auto auto auto auto}}.kpis{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr;grid-template-rows:none}}article{{min-height:290px}}}}
</style></head><body><main class="app">
<header><div><h1>AZAZAEL <span>COMMAND CENTER</span></h1><p id="stamp">connecting…</p></div><div id="verdict" class="verdict unknown">UNKNOWN</div></header>
<section class="kpis" id="kpis"></section><section class="grid" id="grid"></section>
<footer><span id="summary">One screen · live evidence · no invented zeros</span><nav><a href="dashboard/monitor_azazael.html">Azazael</a><a href="dashboard/monitor_exmachina.html">Ex Machina</a><a href="dashboard/monitor_ml.html">Machine</a></nav></footer>
</main><script>
const REFRESH={refresh};let BOOT={bootstrap};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const order=['azazael','aoa','exmachina','machine','algorithms'];
function panel(p){{return `<article class="${{p.key}}"><div class="phead"><h2>${{esc(p.title)}}</h2><span class="pill ${{p.status}}">${{esc(p.status)}}</span></div><div class="headline">${{esc(p.headline)}}</div><div class="metrics">${{p.metrics.map(m=>`<div class="metric"><span>${{esc(m.label)}}</span><b>${{esc(m.value)}}</b><i>${{esc(m.detail)}}</i></div>`).join('')}}</div><ul>${{p.evidence.map(x=>`<li>${{esc(x)}}</li>`).join('')}}</ul><div class="action"><b>NEXT</b> · ${{esc(p.action)}}</div><details><summary>raw evidence</summary><pre>${{esc(JSON.stringify(p.raw,null,2))}}</pre></details></article>`}}
function render(d){{if(!d||!d.panels)return;document.getElementById('stamp').textContent='Updated '+new Date(d.generated_at).toLocaleString()+' · refresh '+REFRESH+'s';let v=document.getElementById('verdict');v.className='verdict '+d.overall;v.textContent=d.overall;let counts=d.counts||{{}};document.getElementById('kpis').innerHTML=[['CRITICAL',counts.critical||0],['DEGRADED',counts.warn||0],['HEALTHY',counts.ok||0],['ACTIONS',(d.actions||[]).length],['VERDICT',d.headline]].map(x=>`<div class="kpi"><b>${{esc(x[1])}}</b><small>${{esc(x[0])}}</small></div>`).join('');document.getElementById('grid').innerHTML=order.map(k=>panel(d.panels[k])).join('');document.getElementById('summary').textContent=(d.actions||[])[0]||'All monitored subsystems are current';}}
async function tick(){{try{{let d=BOOT;BOOT=null;if(!d){{const r=await fetch('/api',{{cache:'no-store'}});if(!r.ok)throw Error('HTTP '+r.status);d=await r.json()}}render(d)}}catch(e){{document.getElementById('stamp').textContent='offline: '+e}}}}tick();setInterval(tick,REFRESH*1000);
</script></body></html>"""


__all__ = [
    "PANEL_ORDER", "collect_algorithms", "collect_aoa", "collect_azazael",
    "collect_exmachina", "collect_machine", "collect_snapshot", "render_page",
]
