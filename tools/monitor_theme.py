#!/usr/bin/env python3
# coding: utf-8
"""
monitor_theme.py — the one palette every live monitor draws from.

WHY THIS EXISTS (`LIVE_MONITOR_RESTRUCTURE_TODO.md` §3, P2 item 7)

`NATURE_COLORS` — the canonical nature palette — lives in
`gephi_mesh_pipeline.py`, where it colours the Gephi graph. Every monitor then
re-invented its own hex strings per card, so the *same* nature could be blue on
the graph and pink on the dashboard, and a new nature silently rendered as an
invented colour rather than an obvious "unmapped" grey.

This module is the single place to change a colour. It is deliberately
import-light: `gephi_mesh_pipeline` only defines constants at module scope (its
work is behind `if __name__ == "__main__"`), so importing it for one dict is
cheap and safe — and if it ever grows an import side effect, the local fallback
below keeps the monitors rendering instead of breaking them.

Two colour families, with different jobs:

* `ACCENTS` — one per monitor *tier*. Chosen for semantics, not variety:
  the mesh is blue (routing), ML is cold cyan/teal (data; should look calm
  unless a gate fails), RL is warm purple/magenta with amber for exploration
  (the volatile tier), Ex Machina is green (epistemic health), Lilith is red
  (pressure).
* `SEMANTIC` — state, and only state: `ok` / `warn` / `error` / `unknown`.
  A card may not use green to mean "pretty".
"""

from typing import Dict, Tuple

# ── Canonical nature palette (one source, imported not copied) ────

_FALLBACK_NATURE_COLORS: Dict[str, str] = {
    "mesh": "#C0392B",
    "synapse": "#8E44AD",
    "consensus": "#D35400",
    "engram": "#27AE60",
    "federated": "#2980B9",
    "servemesh": "#16A085",
    "immune": "#E74C3C",
    "xenomorph": "#FF6B6B",
    "orchestration": "#DDA0DD",
    "monitor": "#4ECDC4",
    "diagnostic": "#45B7D1",
    "security": "#E74C3C",
    "learning": "#2980B9",
    "analysis": "#96CEB4",
    "optimization": "#FFEAA7",
    "repair": "#F7DC6F",
    "profiling": "#3498DB",
    "telemetry": "#1ABC9C",
    "backup": "#2ECC71",
    "config": "#F39C12",
    "container": "#8E44AD",
    "migration": "#E67E22",
    "communication": "#9B59B6",
    "visualization": "#16A085",
    "sync": "#98D8C8",
}

NATURE_COLORS: Dict[str, str] = dict(_FALLBACK_NATURE_COLORS)
NATURE_COLORS_SOURCE = "tools/monitor_theme.py (fallback copy)"
try:  # pragma: no cover - depends on whether the graph tool is present
    from gephi_mesh_pipeline import NATURE_COLORS as _GRAPH_NATURE_COLORS

    if isinstance(_GRAPH_NATURE_COLORS, dict) and _GRAPH_NATURE_COLORS:
        NATURE_COLORS = dict(_GRAPH_NATURE_COLORS)
        NATURE_COLORS_SOURCE = "tools/gephi_mesh_pipeline.py"
except Exception:
    pass

# An unmapped nature is grey, visibly — never a plausible-looking invented hue.
UNMAPPED_NATURE = "#808080"


def nature_color(nature: str) -> str:
    """Colour for a nature/category name; grey when unmapped."""
    c = NATURE_COLORS.get(str(nature))
    return c if isinstance(c, str) and c.startswith("#") else UNMAPPED_NATURE


def nature_is_mapped(nature: str) -> bool:
    return nature_color(nature) != UNMAPPED_NATURE


# ── Per-tier accents ─────────────────────────────────────────────
# (hex, rgba glow) — the glow is the same hue at 0.25 alpha, which is what the
# shared CSS expects for `--accent-glow`.

ACCENTS: Dict[str, Tuple[str, str]] = {
    "mesh": ("#58a6ff", "rgba(88,166,255,0.25)"),
    "ml": ("#56d4dd", "rgba(86,212,221,0.25)"),
    "rl": ("#bc8cff", "rgba(188,140,255,0.25)"),
    "exmachina": ("#1abc9c", "rgba(26,188,156,0.25)"),
    "lilith": ("#f85149", "rgba(248,81,73,0.25)"),
}


def accent(tier: str) -> Tuple[str, str]:
    """(accent, glow) for a monitor tier; falls back to the mesh accent."""
    return ACCENTS.get(str(tier).lower(), ACCENTS["mesh"])


# ── Semantic state colours ───────────────────────────────────────

SEMANTIC: Dict[str, Tuple[str, str]] = {
    "ok": ("#3fb950", "rgba(63,185,80,0.28)"),
    "warn": ("#d29922", "rgba(210,153,34,0.28)"),
    "advisory": ("#d29922", "rgba(210,153,34,0.28)"),
    "error": ("#f85149", "rgba(248,81,73,0.28)"),
    "unknown": ("#8b949e", "rgba(139,148,158,0.22)"),
}

# Named hues used by cards that are not themselves a state (tier tinting).
CYAN = ("#56d4dd", "rgba(86,212,221,0.28)")
TEAL = ("#1abc9c", "rgba(26,188,156,0.28)")
BLUE = ("#58a6ff", "rgba(88,166,255,0.28)")
PURPLE = ("#bc8cff", "rgba(188,140,255,0.28)")
MAGENTA = ("#ff7eb3", "rgba(255,126,179,0.28)")
AMBER = ("#d29922", "rgba(210,153,34,0.28)")
GREEN = SEMANTIC["ok"]
RED = SEMANTIC["error"]
YELLOW = ("#e8d44d", "rgba(232,212,77,0.28)")
GREY = SEMANTIC["unknown"]


def semantic(state: str) -> Tuple[str, str]:
    """(colour, glow) for a state name; `unknown` for anything unrecognised."""
    return SEMANTIC.get(str(state).lower(), SEMANTIC["unknown"])


def state_of(value, *, warn, error, healthy) -> str:
    """Classify a number into ok / warn / error, or `unknown` when not numeric.

    Post-mesh rule: a monitor never guesses a state. `None` (unreadable) is
    `unknown`, never silently `ok`.
    """
    n = None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if healthy(n):
        return "ok"
    if error(n):
        return "error"
    if warn(n):
        return "warn"
    return "ok"
