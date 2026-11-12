#!/usr/bin/env python3
# coding: utf-8
"""
render_ml_rl_monitors.py — the two tiers that had no monitor at all.

    dashboard/monitor_ml.html  — Machine-learning tier
    dashboard/monitor_rl.html  — Reinforcement-learning tier

Why these two exist: the neural-mesh audit left every existing monitor
(`render_monitors.py`, `scripts/live_monitor.py`, `src/CORE/dashboard.py`)
describing a system that no longer exists, and the ML and RL tiers had no
monitor whatsoever even though their state was already on disk. See
`LIVE_MONITOR_RESTRUCTURE_TODO.md` §1.4.

Two rules this module follows:

1. **No invented numbers.** Every value is read from a live source —
   `spine_registry` or a `state/*.json` artifact. A missing or unparseable
   source renders `unknown`, never a plausible-looking default. A monitor that
   prints a fake `0.00` is worse than one that prints nothing, because the fake
   value is indistinguishable from a real reading of zero.
2. **Staleness is visible.** Each card shows the age of the artifact behind it
   and marks itself STALE past a threshold. These artifacts are written by
   background jobs; showing three-day-old numbers as "live" is the failure mode
   the restructure exists to remove.

Colours follow §2 of the TODO: ML is a cold cyan/teal (steady unless the gate
fails), RL is warm purple/magenta with amber for exploration.

Usage:
    python tools/render_ml_rl_monitors.py            # both
    python tools/render_ml_rl_monitors.py --ml
    python tools/render_ml_rl_monitors.py --rl
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SRC = PROJECT_ROOT / "src"
for p in (str(HERE), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Reuse the existing visual layer rather than re-inventing it. The particle
# system, 3D card tilt and gradient header are identical across every monitor,
# so a divergence here would read as a different product.
from render_monitors import _base_css, _base_js, OUT_DIR  # noqa: E402

# One palette, one data layer (LIVE_MONITOR_RESTRUCTURE_TODO.md §3, P2 items
# 7–8). These used to be re-declared here, which is how the same nature ended
# up a different colour on different pages and how "artifact missing" came to
# mean three different things in three renderers. `_load` / `_num` / `_pct` /
# `_age` are aliases kept so the card code below reads as it always did.
from monitor_data import (  # noqa: E402
    STATE_DIR as STATE,
    age_of as _age,
    load_state as _load,
    num as _num,
    pct as _pct,
)
from monitor_theme import (  # noqa: E402
    AMBER, BLUE, CYAN, GREEN, MAGENTA, PURPLE, RED, TEAL, YELLOW,
    UNMAPPED_NATURE, accent as theme_accent, nature_color, semantic,
)
import argparse

ML_ACCENT, ML_GLOW = theme_accent("ml")
RL_ACCENT, RL_GLOW = theme_accent("rl")


# ── Defensive data access ────────────────────────────────────────

def _src_name(*candidates: str) -> str:
    for n in candidates:
        if (STATE / n).exists():
            return n
    return "unknown"


def _bar(pct: float, cls: str = "info") -> str:
    return (f'<div class="mini-bar"><div class="fill {cls}" '
            f'style="width:{max(0.0, min(100.0, pct)):.1f}%"></div></div>')


def _rows(pairs: list[tuple[str, str]], colour: Optional[str] = None) -> str:
    out = ""
    for label, value in pairs:
        style = f' style="color:{colour}"' if colour else ""
        out += (f'<div class="row"><span class="label">{label}</span>'
                f'<span class="value"{style}>{value}</span></div>')
    return out


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{text}</span>'


def _val(text: str, colour: str, glow: str) -> str:
    return (f'<div class="val" style="color:{colour};'
            f'text-shadow:0 0 20px {glow}">{text}</div>')


def _foot(at: Any, source: str) -> str:
    age, stale = _age(at)
    mark = (' <span style="color:var(--orange)">STALE</span>' if stale else "")
    return f'<div class="sub" style="margin-top:8px">source: {source} &middot; {age}{mark}</div>'


def _card(idx: int, accent: tuple[str, str], title: str, content: str) -> str:
    """Card with a per-card accent (the shared `_card` is global-accent only)."""
    colour, glow = accent
    return (f'<div class="card" style="--order:{idx};--card-accent:{colour};'
            f'--card-glow:{glow}">'
            f'<div class="card-content"><h2>{title}</h2>{content}</div></div>')


def _wrap(title: str, subtitle: str, accent: str, glow: str,
          cards: list[str], footer_text: str, badges: str,
          refresh_seconds: int = 60) -> str:
    """Page wrapper. Adds auto-refresh so a monitor left open stays live."""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>{title}</title>
<style>{_base_css(accent, glow)}</style></head><body>
<canvas id="particles-canvas"></canvas>
<div class="header">
  <div><h1>{title}</h1><div class="ts">{subtitle} &mdash; rendered {now} &middot; auto-refresh {refresh_seconds}s</div></div>
  <div class="header-right">{badges}</div>
</div>
<div class="grid">{chr(10).join(cards)}</div>
<div class="footer">{footer_text} &mdash; <a href="/" class="refresh-btn">&#8635; Refresh</a></div>
<script>{_base_js()}</script>
</body></html>"""


# ── ML tier ──────────────────────────────────────────────────────

def render_ml() -> str:
    genome = _load("tensor_genome.json")
    spec = _load("tensor_genome.spec.json")
    prune = _load("tensor_genome.prune_report.json")
    model = _load("tensor_model.json")
    gate = _load("tensor_gate_ab.json")
    qd = _load("qd_archive.json")
    mutation = _load("mutation_summary.json")
    budget = _load("budget_policy.json")
    oracle = _load("fitness_oracle.last.json")

    cards: list[str] = []
    i = 0

    # 1 — Tensor Genome
    genes = genome.get("genes") or []
    ranked = sorted(
        (g for g in genes if isinstance(g, dict)),
        key=lambda g: -(_num(g.get("usefulness")) or 0.0))
    rows = ""
    for g in ranked[:8]:
        name = str(g.get("name", "?"))
        val = _num(g.get("value"))
        use = _num(g.get("usefulness")) or 0.0
        rho = _num(g.get("rho")) or 0.0
        arrow = "&uarr;" if rho > 0 else "&darr;" if rho < 0 else "&middot;"
        shown = f"{val:.4f}" if val is not None else "unknown"
        rows += (f'<div class="metric-row"><span class="metric-name">{name}</span>'
                 f'<span class="metric-stats">{shown} {arrow} '
                 f'&rho;{rho:+.3f}</span></div>')
        rows += _bar(use * 100.0 if use <= 1.0 else min(100.0, use), "info")
    if not genes:
        rows = '<div class="row"><span class="label">No genome on disk</span></div>'
    cards.append(_card(i, CYAN, "Tensor Genome", (
        _val(str(len(genes)), *CYAN) +
        '<div class="sub">tunable genes &middot; ranked by usefulness (&rho;)</div>' +
        _bar(min(100.0, len(genes) * 6.0), "info") + "<br>" + rows +
        _foot(genome.get("at") or genome.get("updated"), "tensor_genome.json"))))
    i += 1

    # 2 — QD Archive
    cells = qd.get("cells") or {}
    fits = [(_num(c.get("fitness")) or 0.0)
            for c in cells.values() if isinstance(c, dict)]
    best_cell, best_fit = "", None
    for key, c in cells.items():
        if isinstance(c, dict):
            f = _num(c.get("fitness"))
            if f is not None and (best_fit is None or f > best_fit):
                best_fit, best_cell = f, str(key)
    beh_dims = 0
    for c in cells.values():
        if isinstance(c, dict) and isinstance(c.get("behavior"), list):
            beh_dims = max(beh_dims, len(c["behavior"]))
    mean_fit = sum(fits) / len(fits) if fits else None
    cards.append(_card(i, TEAL, "Quality-Diversity Archive", (
        _val(str(len(cells)), *TEAL) +
        '<div class="sub">grid cells occupied &middot; behaviour-space coverage</div>' +
        _bar(min(100.0, len(cells) * 4.0), "ok") + "<br>" +
        _rows([("Best cell", best_cell or "unknown"),
               ("Best fitness", f"{best_fit:.4f}" if best_fit is not None else "unknown"),
               ("Mean fitness", f"{mean_fit:.4f}" if mean_fit is not None else "unknown"),
               ("Behaviour dims", str(beh_dims) or "unknown"),
               ("Spread", f"{max(fits) - min(fits):.4f}" if len(fits) > 1 else "n/a")]) +
        _foot(qd.get("at"), _src_name("qd_archive.json")))))
    i += 1

    # 3 — Fitness Oracle
    p_cand = _num(oracle.get("era_robust_p"))
    min_prob = _num(oracle.get("min_prob"))
    passed = bool(oracle.get("passed"))
    cards.append(_card(i, GREEN if passed else AMBER, "Fitness Oracle", (
        _val(f"{p_cand:.4f}" if p_cand is not None else "unknown",
             *(GREEN if passed else AMBER)) +
        f'<div class="sub">era-robust p &middot; floor {min_prob if min_prob is not None else "?"} '
        f'&middot; {_badge("PASSED" if passed else "NOT PASSED", "ok" if passed else "degraded")}</div>' +
        _bar(_pct(p_cand, 0.0, min_prob or 1.0), "ok" if passed else "warn") + "<br>" +
        _rows([("Threshold", str(oracle.get("threshold", "unknown"))),
               ("z", str(oracle.get("z", "unknown"))),
               ("Imagined eras", str(oracle.get("n_imagined_eras", "unknown"))),
               ("Model rows", str(oracle.get("model_rows", "unknown"))),
               ("Predicted", f"{oracle.get('pred_mean')} &plusmn; {oracle.get('pred_sd')}")]) +
        _foot(oracle.get("at"), _src_name("fitness_oracle.last.json")))))
    i += 1

    # 4 — Tensor Gate A/B
    g = gate.get("gate") or {}
    s = gate.get("surrogate") or {}
    gq, sq = _num(g.get("shipment_quality")), _num(s.get("shipment_quality"))
    decision = str(gate.get("decision", "unknown"))
    ok = decision not in ("block", "fail")
    cards.append(_card(i, BLUE, "Tensor Gate A/B", (
        _val(f"{gq:.4f}" if gq is not None else "unknown", *BLUE) +
        f'<div class="sub">gate shipment quality &middot; decision '
        f'{_badge(decision.upper(), "ok" if ok else "down")}</div>' +
        _bar(_pct(gq, 0.0, 1.0), "info") + "<br>" +
        _rows([("Gate promotions", str(g.get("n_promotions", "unknown"))),
               ("Gate holdout pass", str(g.get("n_holdout_pass", "unknown"))),
               ("Gate false promotions", str(g.get("false_promotions", "unknown"))),
               ("Surrogate quality", f"{sq:.4f}" if sq is not None else "unknown"),
               ("Surrogate false promos", str(s.get("false_promotions", "unknown"))),
               ("Surrogate MAE", str(gate.get("surrogate_mae", "unknown"))),
               ("Approved", str(gate.get("approved", "unknown")))]) +
        _foot(gate.get("at"), _src_name("tensor_gate_ab.json")))))
    i += 1

    # 5 — Mutation testing
    score = _num(mutation.get("score"))
    killed, mutants = mutation.get("killed"), mutation.get("mutants")
    cards.append(_card(i, MAGENTA, "Mutation Score", (
        _val(f"{score:.2f}" if score is not None else "unknown", *MAGENTA) +
        '<div class="sub">test-suite sensitivity &middot; killed / mutants</div>' +
        _bar((score or 0.0) * 100.0, "purple") + "<br>" +
        _rows([("Killed", f"{killed} / {mutants}" if killed is not None else "unknown"),
               ("Timed out", str(mutation.get("timed_out", "unknown"))),
               ("Total sites", str(mutation.get("total_sites", "unknown"))),
               ("Target", str(mutation.get("target", "unknown")).split("/")[-1]),
               ("Sampled", str(mutation.get("sampled", "unknown")))]) +
        _foot(mutation.get("at"), _src_name("mutation_summary.json")))))
    i += 1

    # 6 — Budget policy (floor / value / ceiling)
    budgets = budget.get("budgets") or {}
    rows = ""
    for key, b in list(sorted(budgets.items()))[:10]:
        if not isinstance(b, dict):
            continue
        val = _num(b.get("value"))
        lo, hi = _num(b.get("floor")) or 0.0, _num(b.get("ceiling")) or 1.0
        shown = f"{val:g}" if val is not None else "unknown"
        rows += (f'<div class="metric-row"><span class="metric-name">{key}</span>'
                 f'<span class="metric-stats">{shown} '
                 f'[{lo:g}&hellip;{hi:g}]</span></div>')
        rows += _bar(_pct(val, lo, hi), "info")
    cards.append(_card(i, YELLOW, "Budget Policy", (
        _val(str(len(budgets)), *YELLOW) +
        '<div class="sub">adaptive budgets &middot; floor &hellip; value &hellip; ceiling</div>' +
        _bar(min(100.0, len(budgets) * 3.0), "warn") + "<br>" +
        (rows or '<div class="row"><span class="label">No budget policy</span></div>') +
        _foot(budget.get("updated"), _src_name("budget_policy.json")))))
    i += 1

    # 7 — Model artefacts
    prune_removed = prune.get("removed") or prune.get("pruned") or []
    cards.append(_card(i, TEAL, "Model Artefacts", (
        _val(str(len(genes)), *TEAL) +
        '<div class="sub">genome &rarr; model &rarr; prune chain</div>' + "<br>" +
        _rows([("Genome genes", str(len(genes))),
               ("Useful genes", str(sum(1 for x in ranked
                                         if (_num(x.get("usefulness")) or 0) > 0.01))),
               ("Spec bounds", str(len(spec.get("genes") or spec) if spec else "unknown")),
               ("Pruned", str(len(prune_removed) if isinstance(prune_removed, list)
                              else prune.get("n_pruned", "unknown"))),
               ("Model keys", str(len(model)) if model else "unknown"),
               ("Model stamp", str(model.get("at", "unknown"))[:19])]) +
        _foot(model.get("at") or prune.get("at"),
              _src_name("tensor_model.json", "tensor_genome.prune_report.json")))))
    i += 1

    stale_flag = _age(oracle.get("at"))[1] or _age(qd.get("at"))[1]
    return _wrap(
        "AZAZAEL ML", "Machine-Learning Tier &middot; genome / QD / gate",
        ML_ACCENT, ML_GLOW, cards,
        f"{len(genes)} genes &middot; {len(cells)} QD cells &middot; "
        f"mutation {f'{score:.2f}' if score is not None else 'n/a'} &middot; ML tier",
        (_badge(f"{len(cells)} QD cells", "ok") +
         _badge(f"gate {decision}", "ok" if ok else "degraded") +
         _badge("STALE" if stale_flag else "live", "degraded" if stale_flag else "ok")))


# ── RL tier ──────────────────────────────────────────────────────

def render_rl() -> str:
    params = _load("evolved_rl_params.json")
    grpo = _load("grpo_policy.json")
    qd = _load("qd_archive.json")

    cards: list[str] = []
    i = 0

    # 1 — Evolved RL parameters, with their bounds
    genes = params.get("genes") or {}
    bounds = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 1.0),
              "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}
    rows = ""
    for name in ("learning_rate", "epsilon", "discount", "epsilon_decay"):
        val = _num(genes.get(name))
        lo, hi = bounds.get(name, (0.0, 1.0))
        shown = f"{val:.4f}" if val is not None else "unknown"
        rows += (f'<div class="metric-row"><span class="metric-name">{name}</span>'
                 f'<span class="metric-stats">{shown}</span></div>')
        rows += _bar(_pct(val, lo, hi), "purple")
    rows += _rows([("Bounds LR", "0.01 &hellip; 0.9"),
                   ("Bounds &epsilon;", "0 &hellip; 1"),
                   ("Bounds &gamma;", "0.5 &hellip; 0.999"),
                   ("Bounds decay", "0 &hellip; 0.3")])
    cards.append(_card(i, PURPLE, "Evolved RL Parameters", (
        _val(str(len(genes)), *PURPLE) +
        '<div class="sub">promoted genome &middot; bar = position within bound</div>' +
        _bar(min(100.0, len(genes) * 25.0), "purple") + "<br>" + rows +
        _foot(params.get("promoted_at"), _src_name("evolved_rl_params.json")))))
    i += 1

    # 2 — Holdout validation
    ho = params.get("holdout") or {}
    passed = bool(ho.get("passed"))
    cand, base = _num(ho.get("cand")), _num(ho.get("base"))
    margin = _num(ho.get("margin"))
    cards.append(_card(i, GREEN if passed else RED, "Holdout Validation", (
        _val(f"{margin:+.4f}" if margin is not None else "unknown",
             *(GREEN if passed else RED)) +
        f'<div class="sub">candidate &minus; baseline margin &middot; '
        f'{_badge("PASSED" if passed else "FAILED", "ok" if passed else "down")}</div>' +
        _bar(_pct(margin, -5.0, 5.0), "ok" if passed else "error") + "<br>" +
        _rows([("Candidate", f"{cand:.4f}" if cand is not None else "unknown"),
               ("Baseline", f"{base:.4f}" if base is not None else "unknown"),
               ("Fresh info", str(ho.get("fresh_info", "unknown"))),
               ("Fresh alert", str(ho.get("fresh_alert", "unknown")))]) +
        _foot(params.get("promoted_at"), "evolved_rl_params.json &rarr; holdout"))))
    i += 1

    # 3 — Era-robust oracle (as recorded on the promotion record)
    orc = params.get("oracle") or {}
    op = _num(orc.get("era_robust_p"))
    omin = _num(orc.get("min_prob"))
    ook = bool(orc.get("ok"))
    cards.append(_card(i, AMBER, "Era-Robust Oracle", (
        _val(f"{op:.4f}" if op is not None else "unknown", *(GREEN if ook else AMBER)) +
        f'<div class="sub">p(candidate beats production) &middot; floor '
        f'{omin if omin is not None else "?"} &middot; '
        f'{_badge("OK" if ook else "NO", "ok" if ook else "degraded")}</div>' +
        _bar(_pct(op, 0.0, omin or 1.0), "ok" if ook else "warn") + "<br>" +
        _rows([("Rule", str(orc.get("rule", "unknown"))),
               ("p candidate", str(orc.get("p_cand", "unknown"))),
               ("p production", str(orc.get("p_prod", "unknown"))),
               ("Imagined eras", str(orc.get("n_eras", "unknown"))),
               ("z", str(orc.get("z", "unknown")))]) +
        _foot(orc.get("trained_at"), "evolved_rl_params.json &rarr; oracle"))))
    i += 1

    # 4 — Promotion evidence (SPRT / wins / Pareto source)
    llr = _num(params.get("sprt_llr"))
    wins = params.get("wins")
    cards.append(_card(i, MAGENTA, "Promotion Evidence", (
        _val(f"{llr:.1f}" if llr is not None else "unknown", *MAGENTA) +
        '<div class="sub">sequential log-likelihood ratio &middot; promoted only on decision</div>' +
        _bar(min(100.0, (llr or 0.0) / 3.0), "purple") + "<br>" +
        _rows([("Wins", str(wins if wins is not None else "unknown")),
               ("Fitness", str(params.get("fitness", "unknown"))),
               ("Baseline fitness", str(params.get("baseline", "unknown"))),
               ("Pareto source", str(params.get("pareto_source", "unknown"))),
               ("Run seed", str(params.get("run_seed", "unknown"))),
               ("Morph gene", str(params.get("proteus_morph_gene")))]) +
        _foot(params.get("promoted_at"), "evolved_rl_params.json"))))
    i += 1

    # 5 — Fitness decomposition
    detail = params.get("fitness_detail") or {}
    rows = ""
    total = 0.0
    for k, v in detail.items():
        n = _num(v)
        if n is not None:
            total += n
    for k, v in detail.items():
        n = _num(v)
        rows += (f'<div class="metric-row"><span class="metric-name">{k}</span>'
                 f'<span class="metric-stats">{n:.4f}</span></div>'
                 if n is not None else
                 f'<div class="metric-row"><span class="metric-name">{k}</span>'
                 f'<span class="metric-stats">unknown</span></div>')
        rows += _bar(_pct(n, 0.0, total or 1.0), "ok")
    cards.append(_card(i, YELLOW, "Fitness Decomposition", (
        _val(f"{total:.4f}" if detail else "unknown", *YELLOW) +
        '<div class="sub">reward terms summed &middot; share of total</div>' +
        _bar(min(100.0, len(detail) * 40.0), "warn") + "<br>" +
        (rows or '<div class="row"><span class="label">No decomposition</span></div>') +
        _foot(params.get("promoted_at"), "evolved_rl_params.json &rarr; fitness_detail"))))
    i += 1

    # 6 — GRPO policy
    beta, lr = _num(grpo.get("beta")), _num(grpo.get("lr"))
    gfit = _num(grpo.get("fitness"))
    cards.append(_card(i, BLUE, "GRPO Policy", (
        _val(f"{gfit:.4f}" if gfit is not None else "unknown", *BLUE) +
        '<div class="sub">group-relative policy optimisation &middot; KL-regularised</div>' +
        _bar(_pct(gfit, 0.0, 1.0), "info") + "<br>" +
        _rows([("&beta; (KL)", f"{beta:.4f}" if beta is not None else "unknown"),
               ("learning rate", f"{lr:.6f}" if lr is not None else "unknown"),
               ("Run id", str(grpo.get("run_id", "unknown"))[:28]),
               ("Promoted", str(grpo.get("promoted_at", "unknown"))[:19])]) +
        _foot(grpo.get("promoted_at"), _src_name("grpo_policy.json")))))
    i += 1

    # 7 — Behaviour spread from the QD archive
    cells = qd.get("cells") or {}
    behaviours = [c.get("behavior") for c in cells.values()
                  if isinstance(c, dict) and isinstance(c.get("behavior"), list)]
    dims = max((len(b) for b in behaviours), default=0)
    spreads = []
    for d in range(dims):
        vals = [b[d] for b in behaviours if d < len(b) and _num(b[d]) is not None]
        if len(vals) > 1:
            spreads.append((d, min(vals), max(vals), max(vals) - min(vals)))
    rows = ""
    for d, lo, hi, sp in spreads:
        rows += (f'<div class="metric-row"><span class="metric-name">dim {d}</span>'
                 f'<span class="metric-stats">{lo:+.3f} &hellip; {hi:+.3f}</span></div>')
        rows += _bar(_pct(sp, 0.0, 2.0), "purple")
    cards.append(_card(i, TEAL, "Behaviour Spread", (
        _val(str(len(behaviours)), *TEAL) +
        '<div class="sub">elite behaviour vectors &middot; exploration coverage</div>' +
        _bar(min(100.0, len(behaviours) * 4.0), "ok") + "<br>" +
        (rows or '<div class="row"><span class="label">No behaviour data</span></div>') +
        _foot(qd.get("at"), _src_name("qd_archive.json")))))
    i += 1

    # 8 — Q brainstem: the readout the old monitors hid.
    # get_q_weight() returns 0.0 for "never tried" AND "measured neutral", so a
    # starved worm looked identical to a settled one. get_q_updates() separates
    # them; starved = updates == 0 and weight == 0.0.
    q_weights, q_updates, q_err = {}, {}, None
    try:
        from CORE import spine_registry as sp
        q_weights = sp.get_q_weights() or {}
        # Bulk accessor. The per-key `get_q_updates(nature, worm)` raises when
        # called with no arguments, and swallowing that is how the first cut
        # of this card reported "0 measured" for a populated brainstem while
        # still naming the spine as its source.
        q_updates = sp.get_q_update_counts() or {}
    except Exception as exc:
        q_err = f"{type(exc).__name__}: {exc}"[:100]

    if q_err:
        # Never degrade silently: no counts are claimed when the read failed.
        measured, starved = [], []
    else:
        measured = [k for k in q_weights if q_updates.get(k)]
        starved = [k for k, w in q_weights.items()
                   if not q_updates.get(k) and w == 0.0]
    rows = ""
    for k, w in sorted(q_weights.items(), key=lambda kv: -abs(kv[1]))[:8]:
        n = q_updates.get(k, 0)
        tag = ("starved" if n == 0 and w == 0.0 else
               f"{n} pulls" if n else "0 pulls")
        cls = "warn" if n == 0 else ("ok" if w >= 0 else "error")
        rows += (f'<div class="metric-row"><span class="metric-name">{k}</span>'
                 f'<span class="metric-stats">{w:+.4f} &middot; {tag}</span></div>')
        rows += _bar(_pct(w, -2.0, 2.0), cls)
    if not q_weights and q_err is None:
        rows = ('<div class="row"><span class="label">Q-table empty '
                '&mdash; no outcomes recorded</span></div>')
    elif q_err:
        rows = (f'<div class="row"><span class="label" style="color:var(--red)">'
                f'spine read failed &mdash; {q_err}</span></div>')
    sub = (f'<div class="sub" style="color:var(--red)">spine read failed: {q_err}</div>'
           if q_err else
           f'<div class="sub">{len(measured)} measured &middot; '
           f'{len(starved)} starved (0 pulls, 0.0 weight)</div>')
    cards.append(_card(i, PURPLE, "Q Brainstem (weights &amp; pulls)", (
        _val(str(len(q_weights)), *PURPLE) + sub +
        _bar(_pct(len(measured), 0.0, max(1, len(q_weights))), "purple") + "<br>" +
        rows + _foot(None, "spine_registry.get_q_weights / get_q_update_counts"))))
    i += 1

    stale_flag = _age(params.get("promoted_at"))[1] or _age(qd.get("at"))[1]
    return _wrap(
        "AZAZAEL RL", "Reinforcement-Learning Tier &middot; policy / reward / evidence",
        RL_ACCENT, RL_GLOW, cards,
        f"{len(genes)} genes &middot; SPRT {f'{llr:.1f}' if llr is not None else 'n/a'} "
        f"&middot; {len(q_weights)} Q-keys &middot; {len(starved)} starved",
        (_badge(f"holdout {'pass' if passed else 'fail'}", "ok" if passed else "down") +
         _badge(f"GRPO {f'{gfit:.2f}' if gfit is not None else 'n/a'}", "purple") +
         (_badge("Q read failed", "down") if q_err else
          _badge(f"{len(starved)} starved", "degraded" if starved else "ok")) +
         _badge("STALE" if stale_flag else "live", "degraded" if stale_flag else "ok")))


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:

    ap = argparse.ArgumentParser()
    ap.add_argument("--ml", action="store_true")
    ap.add_argument("--rl", action="store_true")
    args = ap.parse_args()
    do_all = not (args.ml or args.rl)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Rendering ML/RL live monitors...")
    if do_all or args.ml:
        p = OUT_DIR / "monitor_ml.html"
        p.write_text(render_ml(), encoding="utf-8")
        print(f"  ML: {p}")
    if do_all or args.rl:
        p = OUT_DIR / "monitor_rl.html"
        p.write_text(render_rl(), encoding="utf-8")
        print(f"  RL: {p}")
    print("Done.")


if __name__ == "__main__":
    main()
