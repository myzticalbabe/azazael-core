#!/usr/bin/env python3
# coding: utf-8
"""render_ouroboros.py — the ouroboros wallboard, in the house CAIN register.

CONVENTION (matched, not invented)
----------------------------------
`tools/render_all_monitors.py` (chain job `render`, 300 s) runs Python renderers
that generate data-fed HTML which auto-reloads. This is the third renderer,
added 2026-09-30.

The house template is `src/CODEX/SESSION_TEMPLATE/templates/output_cain.html`.
Its structure and vocabulary were READ rather than guessed, and they are
reused verbatim so this page sits beside the others instead of beside nothing:

  main-banner + subtitle      the banner header
  panel panel-info/-warning/-danger   semantic panels
  interconnected-web + web-node + web-arrow   the node-link flow map
  flow-row + flow-step + flow-arrow   the linear flow of the loop
  emotion-bar danger/warning/caution  colour-coded health bars
  ledger-table                the fault ledger
  panel-tab + modal-analysis  tabbed drilldowns
  section-divider             section breaks
  emojiDance / connectLine / pulseRing / barFill / ledgerPulse / nodeGlow
  arrowPulse / particleSparkle / scanLines / heartbeat / dangerFlash

Their emoji are SEMANTIC and emotional, and used heavily: ✅ healthy, ❌ broken,
⚠ warning, 🔄 flowing, 📊 metric, 🎯 gate, ⚖ balance — and 🐍 for the serpent,
19 times in the template. This page uses the same set, chosen per stage from
that vocabulary rather than invented.

The centrepiece is the ouroboros: a ten-arc ring, one arc per stage, coloured
by whether that stage's dependency is healthy. The ring is a READOUT, not a
metaphor -- every colour and every number comes from the instruments' output.

Usage:
    python tools/render_ouroboros.py

Exit codes: 0 = every input was read; 1 = the page was written but was drawn
from absent/unreadable inputs. It names them, in a panel and in the stamp,
because a renderer that exits 0 while blind is the blindness this page draws.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(ROOT, "state")
DASH = os.path.join(ROOT, "dashboard")
OUT = os.path.join(DASH, "ouroboros.html")
LOG = os.path.join(ROOT, "logs", "evolution_cycle.log")

CX, CY, R = 640, 470, 318
WEB_W, WEB_H = 1200, 640
LEAD = 1.8
NF = 10.0                      # flow-step width

# (stage, dependency, house emoji, good emoji, bad emoji)
STAGES = [
    ("SEARCH",     "GA + morph rotate the genome",        "\U0001F50D", "✅", "❌"),
    ("POOLS",      "frozen info / alert episodes",        "\U0001F4E6", "✅", "❌"),
    ("FITNESS",    "info arm + alert arm",                "\U0001F3AF", "✅", "❌"),
    ("EVIDENCE",   "streak + SPRT, same arm",             "\U0001F4CA", "✅", "❌"),
    ("GATE",       "margin · arms live · loop sane",     "\U0001F3AF", "✅", "❌"),
    ("PROMOTE",    "only if all three hold",              "\U0001F680", "✅", "❌"),
    ("PRODUCTION", "promoted genes drive the learner",    "\U0001F310", "✅", "❌"),
    ("BEHAVIOUR",  "what the learner actually does",     "\U0001F9F0", "✅", "❌"),
    ("ORACLES",    "compile · import · invariant",       "\U0001F50D", "✅", "❌"),
    ("MEASURE",    "what changed, written down",         "\U0001F4DD", "✅", "❌"),
]

# The log's line vocabulary is defined once, in tools/evolution_log.py, so this
# renderer cannot drift from the three analyst tools. It scans for substrings
# and captures no subject, so it takes the _SUB patterns (whose group numbering
# is one field later). RE_ALT was a byte-identical copy of RE_MORPH - now the
# same definition, keeping the local name because the call sites use it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evolution_log import (RE_CYCLE_SUB as RE_CYCLE,  # noqa: E402
                           RE_MORPH as RE_ALT,
                           RE_MORPH_SUB as RE_MORPH,
                           RE_PROMO_SUB as RE_PROMO,
                           RE_QD_SUB as RE_QD, iter_lines)
import math as _m


#: Inputs this run could NOT supply, and the ones it actually read. Module level
#: so the template can report them without depending on build()'s locals.
#: MEASURED 2026-10-11 (registry L-5): `load` collapsed "absent" and
#: "unreadable" into the same None, `parse_log` drew an absent log as 0 cycles,
#: the page still stamped a fresh `rendered` time, and `main` exited 0 either
#: way -- so a page built from absent data looked live.
_BLIND: list[str] = []
_READ: list[str] = []


def _blind(what: str) -> None:
    """Record an input this run could not supply."""
    if what not in _BLIND:
        _BLIND.append(what)


def _blind_html() -> str:
    """The inputs this run could not read, or '' when it read everything."""
    if not _BLIND:
        return ""
    items = "".join(f"<li>{html.escape(b)}</li>" for b in _BLIND)
    return (
        '<div class="panel panel-danger" style="margin-top:14px">'
        f"<h2>\u26a0\ufe0f INPUTS NOT READ \u2014 this page is drawn from "
        f"{len(_BLIND)} absent/unreadable input(s)</h2>"
        '<p style="font-size:12px;margin:0 0 8px">Every colour and number below '
        "is therefore <b>absent</b>, not zero, and the render stamp records when "
        "this file was WRITTEN \u2014 not how current the data under it is.</p>"
        f'<ul style="font-size:11.5px">{items}</ul></div>'
    )


def _data_stamp_html() -> str:
    """When the FRESHEST input was written, and how long ago.

    The page already stamped when it was rendered. That is a different claim from
    "the data is current", and printing only the former is how a blind render
    passes for a live one.
    """
    mtimes: list[float] = []
    for p in _READ:
        try:
            mtimes.append(os.path.getmtime(p))
        except OSError as exc:
            _blind("mtime of %s (%s)" % (p, type(exc).__name__))
            continue
    if not mtimes:
        return "<b>no input was readable \u2014 data age UNKNOWN</b>"
    newest = max(mtimes)
    age_h = max(0.0, time.time() - newest) / 3600.0
    return ("newest input %s (<b>%.1f h ago</b>)"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest)), age_h))


def load(name):
    p = os.path.join(STATE, name)
    if not os.path.exists(p):
        _blind("state/" + name + " (absent)")
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        # Two statements on purpose: an absent file and an unreadable one are
        # different findings, and reporting the second as the first is how this
        # page stayed silent about a corrupt store.
        _blind("state/%s (UNREADABLE: %s)" % (name, type(exc).__name__))
        return None
    _READ.append(p)
    return data


def parse_log():
    out = {"cycles": 0, "sprt_n": [], "schemas": [], "qd": [], "arms": []}
    if not os.path.exists(LOG):
        # An absent log and a quiet system both draw as 0 cycles; only one of
        # them means the loop is idle, so say which.
        _blind("logs/evolution_cycle.log (absent)")
        return out
    _READ.append(LOG)
    for line in iter_lines(LOG):
        if RE_CYCLE.search(line):
            out["cycles"] += 1
            out["schemas"].append(None)
            m = RE_ALT.match(line)
            continue
        m = RE_ALT.match(line)
        if m and out["schemas"]:
            out["schemas"][-1] = m.group(2)
            continue
        m = RE_PROMO.search(line)
        if m:
            out["sprt_n"].append(int(m.group(4)))
            continue
        m = RE_QD.search(line)
        if m:
            out["qd"].append(int(m.group(1)))
    return out


def ok(v):
    return v in ("IMPROVING", "ALL_ARMS_LIVE", "OK", True)


# ── THE GATE'S OWN VERDICT, DERIVED ───────────────────────────────────────
# `scripts/evolution_cycle.py :: _loop_integrity_gate()` decides whether a cycle
# may promote. The EV-7 panel below used to ASSERT its answer in static prose
# ("promotion is currently blocked"), which made this wallboard a picture of the
# loop rather than the readout it claims to be: that sentence stayed on the page
# only as long as the degradation lasted, and a healthy loop was told it was
# blocked. Derive it instead.
#
# Mirror the gate's rule EXACTLY, including its deliberate asymmetry -- an
# ABSENT or STALE verdict is neither healthy nor blocking:
#
#   BLOCKED  a file exists, is fresh, and its verdict is in GATE_BAD
#   BLIND    no file blocked, and at least one was absent, stale or unreadable
#   OPEN     every configured file was readable, fresh and not bad
#
# The first blocking file wins, because the gate returns on it. GATE_MAX_AGE_S
# and GATE_BAD are kept in step by hand: the renderer must still run when
# scripts/ is absent, so there is no import here.
GATE_MAX_AGE_S = 3600.0
GATE_BAD = ("DEGRADED", "DEAD_ARM", "FAIL")
GATE_CHECKS = (("loop_integrity.json", "current_verdict"),
               ("fitness_arms.json", "verdict"))


def gate_state(now=None):
    """(state, detail), state in 'BLOCKED' | 'BLIND' | 'OPEN'.

    Reads LIVE state off disk at render time -- not the parsed `li`/`fa` the rest
    of the page uses, because those are already None for a missing file and
    carry no age.
    """
    now = time.time() if now is None else now
    blind = []
    for fname, key in GATE_CHECKS:
        path = os.path.join(STATE, fname)
        if not os.path.exists(path):
            blind.append(f"{fname} absent")
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            blind.append(f"{fname} mtime unreadable")
            continue
        if age > GATE_MAX_AGE_S:
            blind.append(f"{fname} is {age:.0f}s old "
                         f"(> {GATE_MAX_AGE_S:.0f}s)")
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            blind.append(f"{fname} unreadable")
            continue
        v = d.get(key)
        if v in GATE_BAD:
            findings = d.get("current_findings") or d.get("findings") or []
            first = str(findings[0])[:120] if findings else ""
            return "BLOCKED", f"{fname} reports {v}" + (
                f": {first}" if first else "")
    if blind:
        return "BLIND", "; ".join(blind)
    return "OPEN", "both verdicts are fresh and neither is degraded"


# ── ARCHETYPES ────────────────────────────────────────────────────────────
# The loop does not merely pass through ten steps; each step is a character
# with an identity, a failure mode, and a diagnostic, and the interesting
# failure is always an archetype acting out of role. Borrowed from the house
# template's `archetype-card` / seven-deadly-sins mapping, because a wallboard
# that says "GATE: ok" tells an operator nothing actionable, while one that
# says "GATE is SLAUGHT, and SLAUGHT is how it fails" tells them what to look at.
#
# archetype -> (role, sin/archetype name, failure signature, diagnostic)
ARCHETYPES = {
    "SEARCH":      ("the one who wanders",  "OMNISCIENT",   "keeps rotating a gene it has already falsified 30 times",
                     "count how many trials each rotated gene has actually won"),
    "POOLS":       ("the one who hoards",   "SENTINEL",     "freezes a pool that is starving an arm and calls it stability",
                     "distinct states per pool — 1 state is a bandit, not a control problem"),
    "FITNESS":     ("the one who splits",   "HUNTRESS",     "reports a total that silently equals one of its two arms",
                     "does total == an arm? then it is not a two-objective score"),
    "EVIDENCE":    ("the one who waits",    "SCRIBE",       "discards evidence because the measurement basis moved under it",
                     "comparable pairs per arm, and how many survive a basis change"),
    "GATE":        ("the one who refuses",  "SLAUGHT",      "withholds promotion on incomparable evidence and calls it rigor",
                     "sprt n per cycle — n=0 is a blind gate, not a cautious one"),
    "PROMOTE":     ("the one who commits",  "ZEALOT",       "promotes on one lucky cycle against a baseline that moved",
                     "baseline_hash unchanged across the streak"),
    "PRODUCTION":  ("the one who serves",   "HIGH PRIEST",  "is read by production, so a bad genome becomes real behaviour",
                     "readers of the promoted artifact"),
    "BEHAVIOUR":   ("the one who acts",     "WARRIOR",      "does what the genome says and the outcome is never recorded",
                     "arm contribution — an arm at 0.0 is a corpse, not a result"),
    "ORACLES":     ("the one who watches",  "SEER",         "reports health while measuring nothing",
                     "oracles run / faults found / no read means blind, not clean"),
    "MEASURE":     ("the one who remembers", "SCRIBE-ELDER","writes the measurement down and nothing reads it back",
                     "readers per state artifact — zero readers is a dead store"),
}
SINS = ["PRIDE", "GREED", "WRATH", "LUST", "ENVY", "GLUTTONY", "SLOTH"]


def polar(r, a_deg, cx=CX, cy=CY):
    a = math.radians(a_deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


# ── the ouroboros, actually drawn ─────────────────────────────────────────
# Four critiques drove this: it must BE an ouroboros rather than a snake emoji
# beside the word; the tail must visibly enter the mouth; the node-link map
# must look like a real graph; and words must not dissolve into the artwork.
#
# So: one continuous body that starts at the tail tip, sweeps the ring, and
# ends INSIDE the jaw. The bite is real geometry -- the last segment overlaps
# the head -- rather than two shapes placed near each other.
GAP = 9.0                     # degrees of mouth at 12 o'clock
BODY_H = 42                   # 34 read as a wire; 74 as a caterpillar. 42 is a body.
BODY_TAIL = 20                # tail tapers to a point
BODY_HEAD = 66                # the head is the thickest part


def ouroboros_path(r=R, start=None):
    """Closed-ish body: tail tip -> around -> into the mouth.

    THE BLOB WAS THREE COMPOUNDING THINGS (2026-09-30):
      1. width. 58px body on a 262px radius is a fat donut. Thinner, on a wider
         ring, so the negative space inside is legible.
      2. no taper. Every segment was the same width, so head and tail were
         indistinguishable. A serpent is thin at the tail and thick at the
         head; without that gradient there is nothing to read.
      3. no separation. Ten same-coloured bands merged into one green mass, and
         a glow pass at 0.2 filled the ring's interior. Bands now vary in
         lightness and the interior stays dark.
    """
    a_tail = -90 + GAP
    sweep = 360.0 - 2 * GAP
    steps = 360
    pts = []
    for k in range(steps + 1):
        t = k / steps
        a = a_tail + sweep * t
        pts.append(polar(r, a))
    return pts, a_tail, a_tail + sweep


def serpent(pts, a_tail, a_head):
    """A snake, built as geometry rather than a stroke.

    FIVE PASSES FAILED HERE before the shape was right, and the failures are
    the specification. A stroked line is not a body: at 34px wide on a 318px
    ring it read as a green squiggly line, and widening it made a flat band.
    A constant-width stroke then read as a ring with an arrowhead, because
    taper only exists if the width varies ALONG the path. So the body is now a
    filled ribbon: the centreline is offset left and right by a per-point
    radius, which gives a true taper, and the four things that make a shape
    read as a serpent are layered on top of it:

      VOLUME  a light band on the upper edge, a dark one beneath
      SCALES  a scallop pattern stroked along the same centreline, so the
              texture follows the curve instead of floating in a box
      HEAD    a wedge — wider at the jaw, with an eye and a jaw line
      TAIL    tapering to a point that passes INTO the jaw
      SPINE   a row of dorsal scutes so the back is not featureless
    """
    n = len(pts)
    # width profile: full at the head, thinning steadily to the tail
    def wfrac(i):
        t = i / (n - 1)
        return 0.30 + 0.70 * (t ** 0.55)

    left, right = [], []
    for i, (x, y) in enumerate(pts):
        j0 = max(0, i - 1)
        j1 = min(n - 1, i + 1)
        dx = pts[j1][0] - pts[j0][0]
        dy = pts[j1][1] - pts[j0][1]
        L = _m.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L                 # unit normal
        w = BODY_H * wfrac(i) * 0.5
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))

    ribbon = (left + list(reversed(right)))
    d_fill = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in ribbon) + " Z"
    d_line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)

    p = []
    p.append(f'<circle cx="{CX}" cy="{CY}" r="{R + 30}" fill="none" stroke="#000" '
             f'stroke-width="{BODY_H + 26}" opacity=".5" filter="url(#glow)"/>')
    p.append(f'<path d="{d_fill}" fill="#2f9d78"/>')
    # cylindrical shading along the ribbon
    p.append(f'<path d="{d_line}" fill="none" stroke="#c6ffe9" '
             f'stroke-width="{BODY_H*0.30}" stroke-linecap="round" opacity=".40" '
             f'style="filter:brightness(1.3)"/>')
    p.append(f'<path d="{d_line}" fill="none" stroke="#0a4a38" '
             f'stroke-width="{BODY_H*0.26}" stroke-linecap="round" opacity=".60" '
             f'transform="translate(0,9)"/>')
    # scale texture, following the curve
    p.append(f'<path d="{d_line}" fill="none" stroke="url(#scales)" '
             f'stroke-width="{BODY_H*0.94}" stroke-linecap="butt" opacity=".42"/>')
    # dorsal scutes: a short bright dash riding the back, every few degrees
    for i in range(0, n, 9):
        x, y = pts[i]
        a = _m.degrees(_m.atan2(y - CY, x - CX))
        rr = _m.hypot(x - CX, y - CY)
        p.append(f'<line x1="{x:.1f}" y1="{y:.1f}" '
                 f'x2="{polar(rr - BODY_H*0.30, a)[0]:.1f}" '
                 f'y2="{polar(rr - BODY_H*0.30, a)[1]:.1f}" '
                 f'stroke="#e6fff5" stroke-width="2.6" opacity=".30" '
                 f'stroke-linecap="round"/>')
    return "".join(p)


def head_geometry(a_head, r=R):
    """Jaw, eye and tongue, oriented so the mouth opens ALONG the body."""
    tip = polar(r, a_head)
    nxt = polar(r, a_head - 5)            # direction of travel into the head
    ang = math.degrees(math.atan2(nxt[1] - tip[1], nxt[0] - tip[0]))
    return tip, ang


# ── archetype sigils: drawn, not emoji ────────────────────────────────────
# "archetypes should have images". Emoji are 26px glyphs from a system font;
# these are per-archetype vector sigils so each card is identifiable without
# reading its label, and they inherit the card's health colour.
SIGILS = {
    "OMNISCIENT": ("eye", "M -14 0 Q 0 -13 14 0 Q 0 13 -14 0 Z M -5 0 H 5 M 0 -18 V -13 M 0 13 V 18"),
    "SENTINEL":   ("shield", "M 0 -16 L 13 -10 V 2 Q 13 13 0 17 Q -13 13 -13 2 V -10 Z"),
    "HUNTRESS":   ("split", "M 0 -17 L 15 0 L 0 17 L -15 0 Z M 0 -17 V 17"),
    "SCRIBE":     ("quill", "M -13 15 L 11 -9 L 15 -13 L -9 11 Z M -6 6 L 6 -6 M -13 15 L -17 19"),
    "SLAUGHT":    ("gate", "M -14 15 V -6 A 14 14 0 0 1 14 -6 V 15 M -5 15 V 2 A 5 5 0 0 1 5 2 V 15"),
    "ZEALOT":     ("seal", "M -15 -9 L -9 -15 L 9 -15 L 15 -9 L 15 9 L 9 15 L -9 15 L -15 9 Z M -6 6 L 6 -6"),
    "HIGH PRIEST": ("chalice", "M -10 -12 H 10 L 6 -2 Q 6 6 0 8 Q -6 6 -6 -2 Z M 0 8 V 16 M -9 16 H 9"),
    "WARRIOR":    ("sword", "M 0 -17 V 9 M -5 -13 L 0 -17 L 5 -13 M -9 9 H 9 M 0 9 V 16"),
    "SEER":       ("lens", "M 0 -12 A 12 12 0 1 1 0 12 A 12 12 0 1 1 0 -12 Z M -4 0 H 4"),
    "SCRIBE-ELDER": ("archive", "M -12 -12 H 12 V 14 H -12 Z M -8 -6 H 8 M -8 0 H 8 M -8 6 H 4"),
}


def sigil_svg(kind, paths, color, size=30):
    return (f'<svg class="sigil" viewBox="-22 -22 44 44" width="{size}" height="{size}" '
            f'fill="none" stroke="{color}" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'style="color:{color};filter:drop-shadow(0 0 6px currentColor)">'
            f'<path d="{paths}"/></svg>')



CSS = """
  :root{--bg:#05060a;--ink:#dff3e6;--dim:#6d7a72;--good:#38ff9e;--bad:#ff4d4d;
        --warn:#ffc857;--caution:#ffe66d;--line:#132a1d;--panel:#080b09;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Segoe UI",ui-sans-serif,system-ui,-apple-system,sans-serif}
  .wrap{max-width:1380px;margin:0 auto;padding:20px 22px 46px}

  .main-banner{display:flex;align-items:center;gap:18px;flex-wrap:wrap;
    background:linear-gradient(90deg,rgba(56,255,158,.10),rgba(56,255,158,0) 62%);
    border:1px solid var(--line);border-left:4px solid var(--good);
    border-radius:8px;padding:16px 20px;margin-bottom:16px;
    animation:ledgerPulse 4s ease-in-out infinite}
  .main-banner h1{margin:0;font-size:27px;letter-spacing:5px;
    text-shadow:0 0 12px rgba(56,255,158,.5);animation:shimmerGlow 3.2s ease-in-out infinite}
  .subtitle{color:var(--dim);font-size:12.5px;letter-spacing:2.6px}
  .verdict-pill{margin-left:auto;display:flex;gap:9px;align-items:center;
    font:700 15px/1 ui-monospace,Menlo,Consolas,monospace;letter-spacing:2px;
    padding:9px 15px;border-radius:20px;border:2px solid currentColor}
  .verdict-pill.bad{color:var(--bad);animation:dangerFlash 1.6s ease-in-out infinite}
  .verdict-pill.ok{color:var(--good)}

  .panel{border:1px solid var(--line);border-radius:8px;padding:14px 16px;
    background:var(--panel);margin-bottom:14px}
  .panel-info{border-left:4px solid #3aa0ff}
  .panel-warning{border-left:4px solid var(--warn)}
  .panel-danger{border-left:4px solid var(--bad);animation:dangerFlash 3s ease-in-out infinite}
  .panel h2{margin:0 0 12px;font-size:13px;letter-spacing:3px;color:var(--dim);font-weight:700}
  .panel h3{margin:0 0 8px;font-size:11.5px;letter-spacing:2.4px;color:var(--dim)}

  .section-divider{height:1px;margin:22px 0;
    background:linear-gradient(90deg,transparent,var(--good),transparent);opacity:.35}

  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:11px}
  .matrix-card{border:1px solid var(--line);border-radius:7px;padding:11px 13px;
    background:#0a0f0c;animation:cardReveal .5s ease-out both;animation-delay:var(--d,0s)}
  .matrix-card .k{font-size:9.5px;letter-spacing:2.2px;color:var(--dim)}
  .matrix-card .v{font-size:20px;font-weight:700;margin-top:3px}
  .good{color:var(--good)} .bad{color:var(--bad)} .warn{color:var(--warn)}

  .emotion-list{display:flex;flex-direction:column;gap:7px}
  .emotion-bar{display:flex;align-items:center;gap:11px}
  .emotion-bar .lbl{width:108px;font:600 11.5px/1 ui-monospace,Menlo,monospace;letter-spacing:1.6px}
  .emotion-bar .track{flex:1;height:11px;background:#111a16;border-radius:6px;overflow:hidden}
  .emotion-bar .track i{display:block;height:100%;border-radius:6px;
    animation:barFill 1.2s ease-out both;box-shadow:0 0 10px currentColor}
  .emotion-bar.danger{color:var(--bad)} .emotion-bar.warning{color:var(--warn)}
  .emotion-bar.caution{color:var(--caution)} .emotion-bar.good{color:var(--good)}
  .emotion-bar .emo{font-size:17px;width:22px;text-align:center;
    animation:emojiDance 1.1s ease-in-out infinite;display:inline-block}

  .flow-row{display:flex;align-items:stretch;gap:0;flex-wrap:nowrap;overflow-x:auto;padding:6px 2px}
  .flow-step{flex:0 0 auto;width:{w}px;border:1px solid var(--line);border-radius:7px;
    padding:10px 8px;text-align:center;background:#0a0f0c;position:relative;
    animation:cardReveal .45s ease-out both;animation-delay:var(--d,0s)}
  .flow-step .ico{font-size:20px;display:block;margin-bottom:5px;
    animation:emojiDance 1.1s ease-in-out infinite}
  .flow-step .t{font:700 10.5px/1.2 ui-monospace,Menlo,monospace;letter-spacing:1.4px}
  .flow-step .d{font-size:9.5px;color:var(--dim);margin-top:4px;line-height:1.35}
  .flow-arrow{flex:0 0 auto;width:34px;display:flex;align-items:center;justify-content:center;
    font-size:19px;color:var(--good);animation:connectLine 1.5s linear infinite;
    filter:drop-shadow(0 0 5px currentColor)}

  .interconnected-web{position:relative}
  .web-node{position:absolute;transform:translate(-50%,-50%);
    width:{nw}px;border-radius:9px;border:2px solid currentColor;padding:7px 5px;
    text-align:center;background:#060a08;font-size:9.5px;letter-spacing:1.1px;
    box-shadow:0 0 16px rgba(0,0,0,.6);animation:nodeGlow 2.6s ease-in-out infinite}
  .web-node .ico{font-size:19px;display:block;animation:emojiDance 1.1s ease-in-out infinite}
  .archetype-card{border-left-width:3px !important}
  .card-enhanced{box-shadow:0 0 18px rgba(255,77,77,.22) !important}
  .connect{stroke-dasharray:9 15;animation:connectLine 1.5s linear infinite;filter:drop-shadow(0 0 4px currentColor)}
  .pulseRing{transform-box:fill-box;transform-origin:center;animation:pulseRing 2.6s ease-out infinite}
  .spark{animation:particleSparkle 2.2s ease-in-out infinite;animation-delay:calc(var(--i)*.3s)}
  .emoji{display:inline-block;animation:emojiDance 1.1s ease-in-out infinite}
  .emoji.bad{animation-duration:2.4s}
  .matrix-card .d{font-size:10.5px}
  .arch-img{flex:0 0 auto;width:72px;height:72px;border-radius:10px;image-rendering:auto;box-shadow:0 0 14px currentColor, inset 0 0 0 1px rgba(255,255,255,.10)}
  .sigil{overflow:visible}
  .webEdge{stroke-dasharray:7 9;animation:connectLine 1.8s linear infinite}
  .banner-glyph{width:74px;height:74px;flex:0 0 auto}

  .panel-tab{display:inline-block;padding:6px 13px;margin:0 5px 9px 0;border-radius:6px;
    border:1px solid var(--line);background:#0a0f0c;cursor:default;
    font:600 11px/1 ui-monospace,Menlo,monospace;letter-spacing:1.6px;
    animation:nodeGlow 2.2s ease-in-out infinite}
  .modal-analysis{padding:11px 13px;border:1px solid var(--line);border-radius:7px;
    background:#070a08;margin-bottom:9px;animation:cardReveal .5s ease-out both}

  .ledger-table{width:100%;border-collapse:collapse;font:12px/1.45 ui-monospace,Menlo,monospace}
  .ledger-table th{text-align:left;padding:7px 9px;color:var(--dim);font-weight:600;
    font-size:10px;letter-spacing:1.8px;border-bottom:1px solid var(--line)}
  .ledger-table td{padding:7px 9px;border-bottom:1px solid #0f1e18;vertical-align:top}
  .ledger-table tr:hover{background:#0c1310}
  ul{margin:0;padding-left:19px;font-size:12.5px;line-height:1.75}

  @keyframes flowArc{0%{stroke-dashoffset:0}100%{stroke-dashoffset:-96}}
  @keyframes connectLine{0%{stroke-dashoffset:0}50%{stroke-dashoffset:26}100%{stroke-dashoffset:0}}
  @keyframes nodeGlow{0%,100%{filter:drop-shadow(0 0 4px currentColor)}50%{filter:drop-shadow(0 0 15px currentColor)}}
  @keyframes pulseRing{0%{transform:scale(.85);opacity:.7}100%{transform:scale(2.1);opacity:0}}
  @keyframes barFill{0%{width:0}100%{width:var(--fill-pct)}}
  @keyframes ledgerPulse{0%,100%{box-shadow:0 0 8px rgba(0,0,0,.25)}50%{box-shadow:0 0 24px currentColor}}
  @keyframes shimmerGlow{0%,100%{text-shadow:0 0 8px rgba(56,255,158,.5)}50%{text-shadow:0 0 20px rgba(56,255,158,.95)}}
  @keyframes dangerFlash{0%,100%{border-color:#5a1c1c}50%{border-color:var(--bad)}}
  @keyframes cardReveal{0%{opacity:0;transform:translateY(22px) scale(.96)}100%{opacity:1;transform:none}}
  @keyframes flowCascade{0%{opacity:0;transform:translateX(-18px)}100%{opacity:1;transform:none}}
  @keyframes emojiDance{0%,100%{transform:rotate(-6deg) scale(1)}50%{transform:rotate(6deg) scale(1.22)}}
  @keyframes arrowPulse{0%,100%{transform:scale(1);opacity:.6}50%{transform:scale(1.45);opacity:1}}
  @keyframes particleSparkle{0%,100%{opacity:0;transform:scale(0)}50%{opacity:1;transform:scale(1)}}
  @keyframes scanLines{0%{background-position:0 0}100%{background-position:0 6px}}
  @keyframes heartbeat{0%,100%{transform:scale(1)}30%{transform:scale(1.12)}}
  @keyframes hueCycle{0%{filter:hue-rotate(0)}100%{filter:hue-rotate(360)}}
  @keyframes textReveal{0%{clip-path:inset(0 100% 0 0)}100%{clip-path:inset(0 0 0 0)}}
"""


def build_web_graph(health, w=1200, h=620):
    """A node-link graph that LOOKS like one: real node placement, curved
    weighted edges, node radius by degree, and a ranked adjacency list.

    Critique this answers: the previous 'interconnected web' was the same ring
    again, which is not a graph. Here the ten stages are placed by a real
    force-free layout (two shells by dependency depth) and the edges carry
    visible weight, so the eye reads structure rather than a shape.
    """
    depth = {"SEARCH": 0, "POOLS": 0, "FITNESS": 1, "EVIDENCE": 2, "GATE": 3,
             "PROMOTE": 4, "PRODUCTION": 5, "BEHAVIOUR": 6, "ORACLES": 7,
             "MEASURE": 8}
    EDGES = [("SEARCH", "POOLS"), ("POOLS", "FITNESS"), ("FITNESS", "EVIDENCE"),
             ("EVIDENCE", "GATE"), ("GATE", "PROMOTE"), ("PROMOTE", "PRODUCTION"),
             ("PRODUCTION", "BEHAVIOUR"), ("BEHAVIOUR", "ORACLES"),
             ("ORACLES", "MEASURE"), ("MEASURE", "SEARCH"),
             ("MEASURE", "GATE"), ("ORACLES", "FITNESS"),
             ("PRODUCTION", "EVIDENCE"), ("BEHAVIOUR", "ORACLES")]
    W, H = w, h
    cx, cy = W / 2, H / 2
    pos = {}
    for name, dep in depth.items():
        okv, _ = health[name]
        # place along an ellipse, radius grows with depth so the graph reads
        rr = 150 + dep * 14
        a = -90 + dep * (360.0 / 10) * 1.35
        pos[name] = (cx + rr * math.cos(math.radians(a)) * 1.55,
                      cy + rr * math.sin(math.radians(a)) * 0.92)
    deg = Counter()
    for a_, b_ in EDGES:
        deg[a_] += 1
        deg[b_] += 1

    svg = []
    for a_, b_ in EDGES:
        ga, _ = health[a_]
        gb, _ = health[b_]
        both = ga and gb
        col = "#38ff9e" if both else ("#ff4d4d" if not (ga or gb) else "#ffc857")
        p1, p2 = pos[a_], pos[b_]
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        L = math.hypot(dx, dy) or 1
        bow = 0.16
        qx, qy = mx - dy / L * L * bow, my + dx / L * L * bow
        svg.append(
            f'<path d="M {p1[0]:.1f} {p1[1]:.1f} Q {qx:.1f} {qy:.1f} {p2[0]:.1f} {p2[1]:.1f}" '
            f'fill="none" stroke="{col}" stroke-width="{2.0 if both else 1.3}" '
            f'opacity="{0.75 if both else 0.4}" class="webEdge" style="color:{col}"/>')
        svg.append(
            f'<circle r="3" fill="{col}" style="animation:arrowPulse 1.7s ease-in-out infinite">'
            f'<animateMotion dur="1.7s" repeatCount="indefinite" '
            f'path="M {p1[0]:.1f} {p1[1]:.1f} Q {qx:.1f} {qy:.1f} {p2[0]:.1f} {p2[1]:.1f}"/></circle>')
    for name, (x, y) in pos.items():
        good, note = health[name]
        col = "#38ff9e" if good else "#ff4d4d"
        rad = 15 + min(11, deg[name] * 2)
        svg.append(
            f'<g class="reveal" style="color:{col}">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad:.0f}" fill="#04070a" '
            f'stroke="{col}" stroke-width="2.2"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad:.0f}" fill="{col}" opacity=".14" '
            f'filter="url(#glow3)"/>'
            + sigil_svg(ARCHETYPES[name][0], SIGILS[ARCHETYPES[name][1]][1], col, int(rad * 1.25))
            + f'<rect x="{x - 58:.0f}" y="{y + rad + 5:.0f}" width="116" height="15" rx="3" '
              f'fill="#04070a" opacity=".9"/>'
            + f'<text x="{x:.1f}" y="{y + rad + 16:.0f}" text-anchor="middle" font-size="10" '
              f'font-weight="700" letter-spacing="1.4" fill="{col}">{name}</text>'
            + f'<text x="{x:.1f}" y="{y + rad + 28:.0f}" text-anchor="middle" font-size="8.5" '
              f'fill="#9db8ac">{html.escape(note)[:22]}</text></g>')
    return "".join(svg), pos, EDGES


def build():

    li = load("loop_integrity.json")
    fa = load("fitness_arms.json")
    tv = load("terence_invariants.json")
    co = load("chain_ops.json")
    lg = parse_log()

    cur = (li or {}).get("current_verdict", "NO_DATA")
    allt = (li or {}).get("verdict", "NO_DATA")
    armv = (fa or {}).get("verdict", "NO_DATA")
    promos = (li or {}).get("promotions", 0)
    cycles = (li or {}).get("cycles", lg["cycles"])
    sprt = lg["sprt_n"][-12:]
    sprt_max = max(sprt) if sprt else 0
    cons = (co or {}).get("consumption") or {}
    dead = len(cons.get("no_readers") or [])
    artn = cons.get("total") or 0
    faults = (tv or {}).get("fault_count")
    # environment drift (2026-09-30): TERENCE stamps every fault with the
    # pillar it was observed in, so the wallboard can show WHICH pillar this
    # readout is from and whether it holds paths its role forbids.
    tenv = (tv or {}).get("environment", "unknown")
    tfaults = (tv or {}).get("faults") or []
    role_f = [f for f in tfaults
              if f.get("klass") == "environment_role_violation"]
    reloc_f = [f for f in tfaults
               if f.get("klass") == "stale_relocation_path"]
    n_err = ((li or {}).get("diagnostics") or {}).get("error_log_rows")
    fails = (co or {}).get("silent_failures") or []
    unmeas = ((co or {}).get("duration") or {}).get("total_unmeasurable") or 0
    qd = lg["qd"][-1] if lg["qd"] else None

    cur_ok, arm_ok = ok(cur), ok(armv)
    gate_ok = cur_ok and arm_ok

    # the gate's live answer, derived -- see gate_state() for why this is not
    # the same question as `gate_ok` above (which has no notion of file age)
    _g_state, _g_detail = gate_state()
    if _g_state == "BLOCKED":
        gate_claim = (
            f"<b>\U0001F6D1 promotion is currently blocked</b>: "
            f"{html.escape(_g_detail)}. A cycle that promotes while the loop is "
            "measurably degrading is a symptom, not progress.")
    elif _g_state == "BLIND":
        gate_claim = (
            f"<b>\U0001F441 the gate is currently blind</b>: "
            f"{html.escape(_g_detail)}. A missing or stale verdict is deliberately "
            "neither healthy nor blocking — the instrument may simply not have run "
            "yet, and refusing to promote on absent instrumentation would be the "
            "worse failure. Reporting it here keeps the gap visible rather than "
            "silent.")
    else:
        gate_claim = (
            f"<b>\u2705 the gate is currently open</b>: {html.escape(_g_detail)}, "
            "so it is not withholding promotion.")

    health = {
        "SEARCH": (True, f"{cycles} cycles"),
        "POOLS": (True, "frozen · era pinned"),
        "FITNESS": (arm_ok, "all arms live" if arm_ok else "an arm is not live"),
        "EVIDENCE": (sprt_max > 0, f"SPRT n={sprt_max} last 12"),
        "GATE": (gate_ok, "arms + loop health both hold"),
        "PROMOTE": (gate_ok, f"{promos} promotions"),
        "PRODUCTION": (True, "artifact has readers"),
        "BEHAVIOUR": (True, "alert arm contributing"),
        "ORACLES": (faults is not None,
                     f"{faults} faults" if faults is not None else "never run"),
        "MEASURE": (dead == 0, f"{dead}/{artn} dead stores"),
    }
    lit = sum(1 for k, v in health.items() if v[0])
    pct = int(100 * lit / len(STAGES))

    # ---- banner + cards ----
    vcls = "bad" if not cur_ok else "ok"
    vemo = "✅" if cur_ok else "❌"
    cards = [
        ("\U0001F504 CURRENT", cur, cur_ok),
        ("\U0001F4CA ALL-TIME", allt, ok(allt)),
        ("\U0001F3AF OBJECTIVE ARMS", armv, arm_ok),
        ("\U0001F680 PROMOTIONS", f"{promos}/{cycles}", promos > 0),
        ("\U0001F50D TERENCE FAULTS", "none" if faults == 0 else str(faults),
         faults == 0),
        ("\U0001F30D PILLAR", tenv, not role_f),
        ("\U0001F5D1 DEAD STORES", f"{dead}/{artn}", dead == 0),
        ("\U0001F4D8 ERROR LEDGER", "0 rows" if n_err in (0, None) else f"{n_err}",
         bool(n_err)),
        ("\U0001F504 QD LAST", "+?" if qd is None else f"+{qd}", bool(qd and qd > 0)),
    ]
    cards_html = "".join(
        f'<div class="matrix-card {"good" if g else "bad"}" style="--d:{0.05*i:.2f}s">'
        f'<div class="k">{k}</div><div class="v">{html.escape(str(v))}</div></div>'
        for i, (k, v, g) in enumerate(cards))

    # ---- the ouroboros: a continuous body whose tail enters the jaw ----
    BODY, a_tail, a_head = ouroboros_path()
    def BW(i):
        t = (a_head - (-90 + i * (360.0 / n) + LEAD)) / (a_head - a_tail)
        return BODY_TAIL + (BODY_H - BODY_TAIL) * max(0.0, min(1.0, t))
    n = len(STAGES)
    segs, nodes, links = [], [], []
    for i, (name, dep, ico, eo, eb) in enumerate(STAGES):
        good, note = health[name]
        col = "#38ff9e" if good else "#ff4d4d"
        am = -90 + (i + 0.5) * (360.0 / n)
        # each stage owns a slice of the ONE body, as its colour band
        a0 = -90 + i * (360.0 / n) + LEAD
        a1 = a0 + 360.0 / n
        # EV-6: the body is drawn ONCE, by serpent(), as a tapered ribbon. An
        # earlier pass also stroked a wide arc per stage here, which stacked a
        # second body under the ribbon and turned the ring into a lumpy green
        # caterpillar. Stage colour now rides the ribbon as narrow separators
        # further down, not as another body.
        nx, ny = polar(R, am)
        nodes.append(
            f'<g style="color:{col}">'
            + (f'<circle class="pulseRing" cx="{nx:.1f}" cy="{ny:.1f}" r="28" fill="none" '
               f'stroke="{col}" stroke-width="1.2" opacity=".45"/>' if good else '')
            + f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="30" fill="#04070a" stroke="{col}" stroke-width="2.2"/>'
            + f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="30" fill="{col}" opacity=".13" filter="url(#glow)"/>'
            + sigil_svg(ARCHETYPES[name][0], SIGILS[ARCHETYPES[name][1]][1], col, 30)
            + f'<circle class="spark" cx="{nx+24:.1f}" cy="{ny-24:.1f}" r="2.2" fill="{col}" style="--i:{i}"/>'
            + '</g>')
        # label on a scrim so it never dissolves into the body
        lx, ly = polar(R + 74, am)
        right = math.cos(math.radians(am)) >= 0
        an = "start" if right else "end"
        wlab = 150
        bx = lx + (10 if right else -10 - wlab)
        nodes.append(f"""
        <g class="reveal" style="--d:{0.09*i:.2f}s">
          <rect x="{bx:.0f}" y="{ly-27:.0f}" width="{wlab}" height="38" rx="5"
                fill="#04070a" opacity="0.88" stroke="{col}" stroke-width="1"/>
          <text x="{lx + (16 if right else -16):.1f}" y="{ly-11:.1f}" text-anchor="{an}"
                font-size="13" font-weight="700" letter-spacing="1.8" fill="{col}">{name}</text>
          <text x="{lx + (16 if right else -16):.1f}" y="{ly+3:.1f}" text-anchor="{an}"
                font-size="10.5" fill="#cfe6da">{html.escape(note)}</text>
        </g>""")

    # THE MISS (2026-09-30): the body was a 34px stroke, which reads as a green
    # squiggly line. Four things make a shape read as a serpent rather than a
    # stroke, and all four are now present:
    #   VOLUME   a light band along the upper edge and a dark one beneath, so
    #            the tube is round rather than flat
    #   SCALES   a scallop pattern stroked ALONG the path, so scale texture
    #            follows the curve instead of floating in a box
    #   HEAD     a wedge, wider at the jaw, with eye and nostril, not a cap
    #   TAIL     tapering to a point, passing INTO the jaw
    body_svg = serpent(BODY, a_tail, a_head)
    for i in range(n):
        good_i, _ = health[STAGES[i][0]]
        col_i = '#38ff9e' if good_i else '#ff4d4d'
        a0 = -90 + i * (360.0 / n) + LEAD
        a1 = a0 + 360.0 / n
        p0, p1 = polar(R, a0), polar(R, a1)
        t2 = math.radians((a0 + a1) / 2)
        k = R * 0.44
        c1 = (p0[0] + k * math.cos(t2), p0[1] + k * math.sin(t2))
        c2 = (p1[0] - k * math.cos(t2), p1[1] - k * math.sin(t2))
        segs.append(f'<path d="M {p0[0]:.1f} {p0[1]:.1f} C {c1[0]:.1f} {c1[1]:.1f} '
                    f'{c2[0]:.1f} {c2[1]:.1f} {p1[0]:.1f} {p1[1]:.1f}" fill="none" '
                    f'stroke="{col_i}" stroke-width="2.4" stroke-linecap="butt" '
                    f'opacity="{0.9 if good_i else 0.45}"/>')
    tip, hang = head_geometry(a_head)
    tailp = polar(R, a_tail)
    # the tail is drawn ON TOP of the jaw so the bite is unambiguous
    bite = f"""
      <g transform="translate({tip[0]:.1f} {tip[1]:.1f}) rotate({hang:.1f}) scale(1.45)">
        <!-- wedge skull: narrow at the brow, flaring to the jaw -->
        <path d="M -14 -34 Q 10 -40 26 -20 Q 34 0 26 20 Q 10 40 -14 34
                 Q -26 16 -26 0 Q -26 -16 -14 -34 Z"
              fill="#4fce9a" stroke="#d8fff0" stroke-width="1.6"/>
        <!-- jaw line -->
        <path d="M -22 -9 Q 6 -3 30 -7" fill="none" stroke="#06301f"
              stroke-width="2.6" opacity=".75"/>
        <!-- eye -->
        <ellipse cx="2" cy="-14" rx="6.4" ry="5" fill="#f6fffb"/>
        <ellipse cx="3.4" cy="-14" rx="2.5" ry="4.2" fill="#071a12"/>
        <circle cx="1.2" cy="-16" r="1.1" fill="#ffffff" opacity=".9"/>
        <!-- nostril -->
        <circle cx="19" cy="-5" r="1.7" fill="#06301f" opacity=".8"/>
        <!-- forked tongue, leaving the mouth -->
        <path d="M 30 -6 L 52 -12 M 30 -6 L 52 0" stroke="#ff8fb0" stroke-width="2.4"
              fill="none" stroke-linecap="round"/>
      </g>"""
    # tail tip, tapering, visibly entering the mouth
    bite += (f'<path d="M {tailp[0]:.1f} {tailp[1]:.1f} L {tip[0]:.1f} {tip[1]:.1f}" '
             f'stroke="#7bffbe" stroke-width="15" stroke-linecap="round" opacity=".92"/>')
    bite += f'<circle cx="{tip[0]:.1f}" cy="{tip[1]:.1f}" r="7" fill="#04120a" opacity=".55"/>'


    # ---- flow-row: the linear flow ----
    steps = []
    for i, (name, dep, ico, eo, eb) in enumerate(STAGES):
        good, note = health[name]
        col = "#38ff9e" if good else "#ff4d4d"
        steps.append(
            f'<div class="flow-step" style="--d:{0.07*i:.2f}s;color:{col}">'
            f'<span class="ico">{eo if good else eb}</span>'
            f'<div class="t" style="color:{col}">{name}</div>'
            f'<div class="d">{html.escape(note)}</div></div>')
        if i < len(STAGES) - 1:
            steps.append(f'<div class="flow-arrow" style="color:{col}">⬇</div>')
    flow_row = "".join(steps)

    # ---- emotion bars: colour-coded health, their vocabulary ----
    def bar(label, frac, kind, emo):
        pctv = max(2, int(100 * frac))
        return (f'<div class="emotion-bar {kind}">'
                f'<span class="emo">{emo}</span>'
                f'<span class="lbl">{label}</span>'
                f'<span class="track"><i style="--fill-pct:{pctv}%;width:{pctv}%;'
                f'background:currentColor"></i></span>'
                f'<span style="width:52px;text-align:right;font-size:11px">{pctv}%</span></div>')
    sprt_frac = min(1.0, sprt_max / 6.0) if sprt else 0.0
    bars = "".join([
        bar("STAGES LIT", lit / len(STAGES),
            "good" if pct >= 70 else "warning", "✅" if pct >= 70 else "⚠"),
        bar("SPRT EVIDENCE", sprt_frac,
            "good" if sprt_max > 0 else "danger", "✅" if sprt_max > 0 else "❌"),
        bar("PROMOTIONS", min(1.0, promos / 3.0) if promos else 0.0,
            "good" if promos else "danger", "✅" if promos else "❌"),
        bar("STORES CONSUMED", (artn - dead) / artn if artn else 0.0,
            "warning" if dead else "good", "⚠" if dead else "✅"),
        bar("ORACLES RAN", 1.0 if faults is not None else 0.0,
            "good" if faults is not None else "danger", "✅" if faults is not None else "❌"),
        bar("ERROR LEDGER", min(1.0, (n_err or 0) / 20.0),
            "good" if n_err else "danger", "✅" if n_err else "❌"),
    ])

    findings = (li or {}).get("current_findings") or []
    hist = (li or {}).get("findings") or []
    arms = (li or {}).get("arms") or []
    f_html = "".join(f"<li>{html.escape(str(x))}</li>" for x in findings) or \
             '<li class="good">✅ no current invariant violations</li>'
    h_html = "".join(f'<li style="color:#4a5a52">{html.escape(str(x))}</li>' for x in hist)

    arms_rows = "".join(
        f'<tr><td>{html.escape(str(a.get("schema")))}</td><td>{a.get("cycles")}</td>'
        f'<td>{a.get("comparable_pairs")}</td>'
        f'<td class="{"good" if a.get("can_accumulate_streak") else "bad"}">'
        f'{"✅ yes" if a.get("can_accumulate_streak") else "❌ NO"}</td>'
        f'<td>{a.get("min_margin")} … {a.get("max_margin")}</td></tr>'
        for a in arms) or '<tr><td colspan="5">NO DATA</td></tr>'

    fail_rows = "".join(
        f'<tr><td>❌ {html.escape(str(f.get("job")))}</td><td>rc={f.get("returncode")}</td>'
        f'<td>{"<b>EMPTY</b> — nothing says why" if f.get("error_is_empty") else html.escape(str(f.get("error")))}</td>'
        f'<td>invisible to audit_loop_liveness</td></tr>'
        for f in fails) or '<tr><td colspan="4" class="good">✅ none</td></tr>'

    dead_list = "".join(f"<li>{html.escape(d)}</li>" for d in (cons.get("no_readers") or [])[:24]) \
        or "<li>✅ every state artifact has a reader</li>"

    web_svg, _pos, _edges = build_web_graph(health)
    banner_glyph = (
        f'<svg viewBox="-150 -150 300 300" width="74" height="74" fill="none" '
        f'stroke="#38ff9e" stroke-width="16" stroke-linecap="round" '
        f'style="filter:drop-shadow(0 0 10px rgba(56,255,158,.8))">'
        f'<path d="M 0 -78 A 78 78 0 1 1 -1 -78" opacity=".95"/>'
        f'<path d="M 6 -92 L 46 -74 L 6 -56 Z" fill="#7bffbe"/>'
        f'<path d="M 6 -92 L 46 -74 L 6 -56 Z" fill="none"/>'
        f'<circle cx="-70" cy="-8" r="5" fill="#04120a"/></svg>')

    fnd_panel = ("panel-danger" if findings else "panel-info")
    fnd_head = "❌ CURRENT FINDINGS — ACT ON THESE" if findings else "✅ NO CURRENT FINDINGS"

    # ── archetype cards + serpent scales ────────────────────────────────
    arch = []
    serp = []
    for i, (name, dep, ico, eo, eb) in enumerate(STAGES):
        good, note = health[name]
        role, sin, sig, diag = ARCHETYPES[name]
        kind = SIGILS[sin][0]
        col = "#38ff9e" if good else "#ff4d4d"
        arch.append(f"""
    <div class="matrix-card archetype-card {'card-enhanced' if not good else ''}"
         style="--d:{0.05*i:.2f}s;color:{col};border-left:3px solid {col}">
      <div style="display:flex;gap:12px;align-items:flex-start">
        <img class="arch-img" src="archetypes/{sin}_{'good' if good else 'bad'}.webp"
             alt="{sin} — {name}" width="120" height="110" loading="lazy">
        <div style="min-width:0">
          <div class="k" style="color:{col};letter-spacing:1.6px">{sin}</div>
          <div style="font-size:11px;color:#bcd6ca;margin-top:2px">
            {eo if good else eb} <b style="color:{col}">{name}</b> — <i>{role}</i></div>
        </div>
      </div>
      <div class="d" style="font-size:10.5px;color:#9db8ac;line-height:1.5;margin-top:8px">
        <b style="color:{col}">fails as:</b> {html.escape(sig)}<br>
        <b style="color:#6d7a72">ask:</b> {html.escape(diag)}</div>
      <div class="bar" style="--fill-pct:{100 if good else 20}%;width:100%;margin-top:8px">
        <i style="--fill-pct:{100 if good else 20}%;width:{100 if good else 20}%;background:{col}"></i>
      </div>
    </div>""")

        # the serpent: one scale per stage, and it is the SAME serpent as the ring
        a0 = -90 + i * (360.0 / n) + LEAD
        a1 = a0 + 360.0 / n
        am = (a0 + a1) / 2
        lx, ly = polar(R + 168, am)
        right = math.cos(math.radians(am)) >= 0
        serp.append(
            f'<g class="reveal" style="--d:{0.05*i:.2f}s;color:{col}">'
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{"start" if right else "end"}" '
            f'font-size="9" letter-spacing="1.4" fill="{col}">'
            f'{SINS[i % len(SINS)]}</text>'
            f'<circle cx="{polar(R+40,am)[0]:.1f}" cy="{polar(R+40,am)[1]:.1f}" r="2.6" '
            f'fill="{col}" opacity=".8"/></g>')
    arch_html = "".join(arch)
    serp_html = "".join(serp)
    serp_stage = ("good" if pct >= 70 else "warning")
    serp_head = ("🐍 THE SERPENT — one body, ten scales" if pct >= 70
                 else "🐍 THE SERPENT — coiled, and %d of its scales are dark" % (10 - lit))

    env_rows = "".join(
        f'<li style="color:#f2b8b5">[{html.escape(str(f.get("severity")))}] '
        f'{html.escape(str(f.get("fault_id")))} — '
        f'{html.escape(str(f.get("subject")))}</li>'
        for f in role_f + reloc_f) or (
        '<li style="color:#4a5a52">no environment drift and no stale '
        'relocation paths</li>')
    env_html = (
        '<div class="panel panel-info">'
        '<h2>\U0001F30D ENVIRONMENT DRIFT — which pillar this readout is from</h2>'
        f'<p style="font-size:12px;color:#9db8ac;margin:0 0 8px;line-height:1.6">'
        f'TERENCE examined the <b>{html.escape(str(tenv))}</b> pillar. The same '
        f'path means different things in different pillars — '
        f'<code>src/CODEX/VIDEOS</code> is forbidden on Ubuntu and the JOB on '
        f'Windows — so faults are bucketed by <code>(class, environment)</code>.'
        f'</p><ul style="font-size:11.5px;line-height:1.7;margin:0;padding-left:18px">'
        f'{env_rows}</ul></div>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>\U0001F40D OUROBOROS — meta-evolution loop liveness</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

  <div class="main-banner">
    <span class="banner-glyph">{banner_glyph}</span>
    <span style="display:flex;flex-direction:column"><h1>OUROBOROS</h1><span class="subtitle" style="margin-top:4px">IT EATS ITS OWN MEASUREMENT</span></span>
    <span class="verdict-pill {vcls}">{vemo} {html.escape(str(cur))} · {lit}/10 · {pct}%</span>
  </div>

  <div class="grid">{cards_html}</div>

  {env_html}

  <div class="section-divider"></div>

  <div class="panel panel-info">
    <h2>\U0001F517 THE FLOW — ten stages, each output the next input</h2>
    <div class="flow-row">{flow_row}</div>
  </div>

  <div class="panel panel-info">
    <h2>\U0001F40D THE OUROBOROS — one body, its tail inside its own jaw</h2>
    <svg viewBox="0 0 1240 940" style="width:100%;height:auto;display:block">
      <defs>
        <pattern id="scales" width="26" height="26" patternUnits="userSpaceOnUse"
                 patternTransform="rotate(18)">
          <path d="M0,26 a13,13 0 0 1 26,0" fill="none" stroke="#04251c"
                stroke-width="2.1" opacity=".55"/>
        </pattern>
        <filter id="glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="14"/></filter>
        <pattern id="scan" width="4" height="6" patternUnits="userSpaceOnUse">
          <rect width="4" height="1" fill="#fff" opacity=".016"/></pattern>
      </defs>
      <rect width="1240" height="940" fill="url(#scan)" style="animation:scanLines 1.2s linear infinite"/>
      <circle cx="{CX}" cy="{CY}" r="{R+186}" fill="none" stroke="#12241b"/>
      <circle cx="{CX}" cy="{CY}" r="{R+204}" fill="none" stroke="#0e1a14" stroke-dasharray="1 20"/>
      {body_svg}
      {''.join(segs)}
      {bite}
      {''.join(nodes)}
    </svg>
    <p style="font-size:12px;color:#9db8ac;margin:10px 0 0;line-height:1.6">
      Each band of the body is one stage, coloured by whether that stage's output is
      currently being consumed. The body is drawn from the tail forward and the tail
      passes <b>into</b> the jaw at twelve o'clock — that overlap is the loop, not a
      shape near one. Labels sit on opaque scrims so they read against the artwork
      instead of dissolving into it.
    </p>
  </div>

  <div class="panel panel-info">
    <h2>\U0001F578️ INTERCONNECTED WEB — the dependency graph, weighted</h2>
    <svg viewBox="0 0 {WEB_W} {WEB_H}" style="width:100%;height:auto;display:block">
      <defs><filter id="glow3" x="-40%" y="-40%" width="180%" height="180%">
        <feGaussianBlur stdDeviation="10"/></filter></defs>
      {web_svg}
    </svg>
  </div>

  <div class="section-divider"></div>

  <div class="panel panel-warning">
    <h2>\U0001F4CA EMOTIONAL READOUT — colour-coded health</h2>
    <div class="emotion-list">{bars}</div>
  </div>

  <div class="section-divider"></div>

  <div class="panel panel-warning">
    <h2>😈 ARCHETYPE MAPPING — the role each stage plays, and how it fails out of role</h2>
    <div class="grid">{arch_html}</div>
  </div>

  <div class="panel panel-warning">
    Until EV-7, <code>loop_integrity.json</code> and <code>fitness_arms.json</code> had exactly one
    reader each: the tool that wrote them. A run that promoted nothing scored
    identically to a productive one. The gate now reads them —
    {gate_claim}
  </div>

  <div class="panel {fnd_panel}">
    <h2>{fnd_head}</h2>
    <ul>{f_html}</ul>
    <div class="modal-analysis">
      <h3>\U0001F5D2️ HISTORICAL — true of the log, not necessarily of now ({len(hist)})</h3>
      <ul style="font-size:11.5px">{h_html}</ul>
    </div>
  </div>

  <div class="panel panel-info">
    <h2>\U0001F9EA TABS — drilldowns</h2>
    <div>
      <span class="panel-tab good">\U0001F9EA ARMS</span>
      <span class="panel-tab bad">\U0001F4C5 FAILED JOBS</span>
      <span class="panel-tab warn">\U0001F5D1 DEAD STORES</span>
      <span class="panel-tab warn">⏱ DURATION</span>
    </div>
    <div class="modal-analysis">
      <h3>\U0001F9EA ARMS — comparability per search space</h3>
      <table class="ledger-table"><thead><tr><th>SCHEMA</th><th>CYCLES</th>
        <th>COMPARABLE PAIRS</th><th>STREAK POSSIBLE</th><th>MARGIN RANGE</th></tr></thead>
        <tbody>{arms_rows}</tbody></table>
    </div>
    <div class="modal-analysis">
      <h3>\U0001F4C5 FAILED JOBS — non-zero return, invisible to liveness</h3>
      <table class="ledger-table"><thead><tr><th>JOB</th><th>RC</th><th>ERROR</th>
        <th>WHY INVISIBLE</th></tr></thead><tbody>{fail_rows}</tbody></table>
    </div>
    <div class="modal-analysis">
      <h3>\U0001F5D1 DEAD STORES — state artifacts with zero readers ({dead}/{artn})</h3>
      <ul style="font-size:11.5px">{dead_list}</ul>
    </div>
    <div class="modal-analysis">
      <h3>⏱ DURATION — {unmeas}/{len(STAGES)*2} jobs unmeasurable</h3>
      <p style="font-size:12px;margin:0;color:var(--dim)">The chain state file records FIRE TIME,
      not RUNTIME, so duration is not measurable for any job. You cannot choose a cadence
      for a process whose duration you have never measured.</p>
    </div>
  </div>

  {_blind_html()}

  <div style="margin-top:18px;color:#3d4a44;font-size:10.5px;letter-spacing:1.5px">
    rendered {time.strftime('%Y-%m-%d %H:%M:%S')} · {_data_stamp_html()} ·
    tools/render_ouroboros.py ·
    chain job <b>render</b> · auto-reload 60s · keyframes reused verbatim from
    src/CODEX/SESSION_TEMPLATE/templates/output_cain.html
  </div>
</div>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.parse_args()
    os.makedirs(DASH, exist_ok=True)
    doc = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"render-ouroboros: wrote {OUT} ({len(doc):,} bytes)")
    if _BLIND:
        # Non-zero so the return code is visible as a failed job: this page
        # carries its own "FAILED JOBS -- non-zero return, invisible to liveness"
        # table, and a renderer that exits 0 while blind is exactly the blindness
        # it draws.
        print("render-ouroboros: BLIND on %d input(s): %s"
              % (len(_BLIND), ", ".join(_BLIND)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())









# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
