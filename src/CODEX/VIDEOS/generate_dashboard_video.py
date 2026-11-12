#!/usr/bin/env python3
"""
CODEX Dashboard Video Generator v2.0
=====================================
Generates a scrolling video of the Azazael Observability Dashboard
with PER-CARD COLOR THEMES matching the dashboard CSS.

Instead of monochrome text on a dark grid, each card section is rendered
as a rich visual panel with:
  - Full-bleed radial gradient background in the card's theme color
  - Colored title bar with accent underline
  - Large value text with glow effect
  - Animated mini progress bars
  - Row data with colored labels
  - Pulsing indicator dots for status

Color Themes (11 cards, 11 distinct color identities):
  Health    = Green    (#3fb950)  - status, vitality
  DB        = Blue     (#58a6ff)  - data, database
  Metrics   = Cyan     (#56d4dd)  - measurement, tracking
  Drift     = Orange   (#d29922)  - alert, warning
  Dead      = Red      (#f85149)  - errors, failures
  Session   = Purple   (#bc8cff)  - user, session
  Cluster   = Blue     (#58a6ff)  - infrastructure
  Workflow  = Pink     (#ff7eb3)  - processes, flows
  Training  = Yellow   (#e8d44d)  - ML training
  SB3       = Purple   (#bc8cff)  - SB3 training
  Config    = Muted    (#6a7d9e)  - settings

Usage:
  python generate_dashboard_video.py                    # Default: colorful text mode
  python generate_dashboard_video.py --mode visual      # Browser capture (full CSS)
  python generate_dashboard_video.py --preview          # Show param analysis
  python generate_dashboard_video.py --duration 120     # Longer
  python generate_dashboard_video.py --fps 24           # Film-style
"""
import os
import sys
import subprocess
import shutil
import time
import math
import re
from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser

# --- PIL availability ---
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError as exc:
    note_failure("generate_dashboard_video:L53", exc)
    HAS_PIL = False

# --- Project paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # azazael/
SRC_DIR = PROJECT_ROOT / "src"
DASHBOARD_HTML = PROJECT_ROOT / "dashboard" / "index.html"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "CODEX_Studio_Output"

SCROLL_SEP = "-" * 55
SCROLL_SEP_THICK = "=" * 55

# ===================================================================
# DASHBOARD COLOR THEMES — matches CSS card-theme-* definitions
# ===================================================================
CARD_THEMES = {
    "health check": {
        "accent": (63, 185, 80),
        "accent_str": "#3fb950",
        "bg1": (12, 55, 22),
        "bg2": (6, 22, 10),
        "glow": (63, 185, 80, 40),
        "glow_intense": (63, 185, 80, 80),
        "border": (63, 185, 80, 80),
    },
    "db monitor": {
        "accent": (88, 166, 255),
        "accent_str": "#58a6ff",
        "bg1": (10, 30, 70),
        "bg2": (5, 14, 35),
        "glow": (88, 166, 255, 35),
        "glow_intense": (88, 166, 255, 75),
        "border": (88, 166, 255, 75),
    },
    "metrics": {
        "accent": (86, 212, 221),
        "accent_str": "#56d4dd",
        "bg1": (8, 48, 55),
        "bg2": (4, 22, 28),
        "glow": (86, 212, 221, 35),
        "glow_intense": (86, 212, 221, 75),
        "border": (86, 212, 221, 75),
    },
    "drift alerts": {
        "accent": (210, 153, 34),
        "accent_str": "#d29922",
        "bg1": (50, 35, 8),
        "bg2": (30, 18, 4),
        "glow": (210, 153, 34, 35),
        "glow_intense": (210, 153, 34, 75),
        "border": (210, 153, 34, 75),
    },
    "dead letter queue": {
        "accent": (248, 81, 73),
        "accent_str": "#f85149",
        "bg1": (55, 12, 10),
        "bg2": (35, 6, 5),
        "glow": (248, 81, 73, 35),
        "glow_intense": (248, 81, 73, 75),
        "border": (248, 81, 73, 75),
    },
    "session": {
        "accent": (188, 140, 255),
        "accent_str": "#bc8cff",
        "bg1": (30, 12, 60),
        "bg2": (18, 6, 36),
        "glow": (188, 140, 255, 35),
        "glow_intense": (188, 140, 255, 75),
        "border": (188, 140, 255, 75),
    },
    "metacluster": {
        "accent": (88, 166, 255),
        "accent_str": "#58a6ff",
        "bg1": (12, 32, 65),
        "bg2": (6, 16, 36),
        "glow": (88, 166, 255, 35),
        "glow_intense": (88, 166, 255, 75),
        "border": (88, 166, 255, 75),
    },
    "workflows": {
        "accent": (255, 126, 179),
        "accent_str": "#ff7eb3",
        "bg1": (55, 12, 32),
        "bg2": (35, 6, 18),
        "glow": (255, 126, 179, 35),
        "glow_intense": (255, 126, 179, 75),
        "border": (255, 126, 179, 75),
    },
    "training": {
        "accent": (232, 212, 77),
        "accent_str": "#e8d44d",
        "bg1": (45, 42, 10),
        "bg2": (28, 24, 5),
        "glow": (232, 212, 77, 35),
        "glow_intense": (232, 212, 77, 75),
        "border": (232, 212, 77, 75),
    },
    "sb3 training": {
        "accent": (188, 140, 255),
        "accent_str": "#bc8cff",
        "bg1": (32, 14, 60),
        "bg2": (18, 6, 36),
        "glow": (188, 140, 255, 35),
        "glow_intense": (188, 140, 255, 75),
        "border": (188, 140, 255, 75),
    },
    "configuration": {
        "accent": (106, 125, 158),
        "accent_str": "#6a7d9e",
        "bg1": (18, 22, 35),
        "bg2": (10, 12, 20),
        "glow": (106, 125, 158, 30),
        "glow_intense": (106, 125, 158, 55),
        "border": (106, 125, 158, 55),
    },
}

DEFAULT_THEME = {
    "accent": (88, 166, 255),
    "accent_str": "#58a6ff",
    "bg1": (8, 16, 32),
    "bg2": (4, 10, 24),
    "glow": (88, 166, 255, 25),
    "glow_intense": (88, 166, 255, 55),
    "border": (88, 166, 255, 55),
}

STATUS_COLORS = {
    "ok": (63, 185, 80),
    "down": (248, 81, 73),
    "degraded": (210, 153, 34),
    "healthy": (63, 185, 80),
    "warning": (210, 153, 34),
    "error": (248, 81, 73),
    "info": (88, 166, 255),
    "critical": (248, 81, 73),
    "true": (63, 185, 80),
    "false": (248, 81, 73),
}


def _find_theme(card_title):
    """Find the matching color theme for a card title."""
    title_lower = card_title.strip().lower()
    for key, theme in CARD_THEMES.items():
        if key in title_lower:
            return theme
    return DEFAULT_THEME


def _blend_color(c1, c2, alpha):
    """Blend two RGB colors: c1 * (1-alpha) + c2 * alpha"""
    return tuple(int(a * (1 - alpha) + b * alpha) for a, b in zip(c1, c2))


def _draw_glow(draw, cx, cy, radius, color, alpha):
    """Draw a soft ambient glow as concentric fading ellipses."""
    for j in range(4, 0, -1):
        a = int(alpha / j)
        if a <= 0:
            continue
        r = max(1, radius // j)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, a))



# ===================================================================
# HTML CARD DATA EXTRACTOR
# ===================================================================
class DashboardCardDataExtractor(HTMLParser):
    """Parse dashboard HTML into structured card data.

    Extracts per card:
      - title (from h2)
      - value (from .val)
      - status (from badges, status-* classes)
      - rows (label/value pairs)
      - mini-bars (fill width and class)
      - sub text
    """

    def __init__(self):
        super().__init__()
        self.cards = []
        self._current_card = None
        self._in_h2 = False
        self._h2_text = ""
        self._in_val = False
        self._val_text = ""
        self._in_sub = False
        self._sub_text = ""
        self._in_row = False
        self._in_row_label = False
        self._in_row_value = False
        self._row_label = ""
        self._row_value = ""
        self._current_rows = []
        self._in_minibar = False
        self._current_bar = None
        self._status_class = ""
        self._tag_stack = []
        self._skip_depth = 0
        self._skip_tags = {"script", "style", "canvas"}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class") or ""

        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if tag == "div" and "card" in classes.split():
            self._flush_card()
            self._current_card = {"title": "", "value": "", "sub": "",
                                  "rows": [], "bars": [], "status": ""}
            for cls in classes.split():
                if cls.startswith("card-theme-"):
                    self._current_card["theme_class"] = cls

        if tag == "h2" and self._current_card is not None:
            self._in_h2 = True
            self._h2_text = ""

        if tag in ("div", "span"):
            if "val" in classes:
                self._in_val = True
                self._val_text = ""
                for cls in classes.split():
                    if cls.startswith("status-"):
                        self._status_class = cls.replace("status-", "")
            if "sub" in classes and not self._in_row:
                self._in_sub = True
                self._sub_text = ""
            if "row" in classes:
                self._in_row = True
                self._row_label = ""
                self._row_value = ""

        if tag == "span":
            if "label" in classes and self._in_row:
                self._in_row_label = True
            elif "value" in classes and self._in_row:
                self._in_row_value = True
            elif "badge" in classes:
                for cls in classes.split():
                    if cls.startswith("badge-"):
                        if self._current_card:
                            self._current_card["status"] = cls.replace("badge-", "")

        if tag == "div" and "mini-bar" in classes:
            self._in_minibar = True

        if tag == "div" and "fill" in classes and self._in_minibar:
            self._current_bar = {"width": 0, "class": ""}
            for cls in classes.split():
                if cls in ("ok", "warn", "error", "info"):
                    self._current_bar["class"] = cls
            # Extract width from style attribute
            style_attr = attrs_dict.get("style") or ""
            wm = re.search(r'width\s*:\s*(\d+)', style_attr, re.IGNORECASE)
            if wm:
                self._current_bar["width"] = int(wm.group(1))

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            if self._current_card is not None:
                self._current_card["title"] = self._h2_text.strip()

        if tag in ("div", "span"):
            if self._in_val:
                self._in_val = False
                if self._current_card is not None:
                    self._current_card["value"] = self._val_text.strip()
                    if self._status_class:
                        self._current_card["status"] = self._status_class
            if self._in_sub and not self._in_row:
                self._in_sub = False
                if self._current_card is not None:
                    self._current_card["sub"] = self._sub_text.strip()
            if self._in_row:
                if self._in_row_label and tag in ("span", "div"):
                    self._in_row_label = False
                if self._in_row_value and tag in ("span", "div"):
                    self._in_row_value = False
                if tag == "div":
                    self._in_row = False
                    if self._row_label or self._row_value:
                        self._current_rows.append({
                            "label": self._row_label.strip(),
                            "value": self._row_value.strip(),
                        })
                    self._row_label = ""
                    self._row_value = ""

        if tag == "div" and self._in_minibar:
            self._in_minibar = False
            if self._current_bar and self._current_card is not None:
                self._current_card["bars"].append(self._current_bar)
            self._current_bar = None

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        stripped = data.strip()
        if not stripped:
            return
        if self._in_h2:
            self._h2_text += stripped
        elif self._in_val:
            self._val_text += stripped
        elif self._in_sub and not self._in_row:
            self._sub_text += stripped
        elif self._in_row_label:
            self._row_label += stripped
        elif self._in_row_value:
            self._row_value += stripped

    def handle_entityref(self, name):
        """Handle named HTML entities like &mdash;"""
        entity_map = {"mdash": "\u2014", "nbsp": " ", "amp": "&",
                      "lt": "<", "gt": ">", "quot": "\"",
                      "apos": "'", "copy": "\u00a9"}
        char = entity_map.get(name, f"&{name};")
        self._append_to_current(char)

    def handle_charref(self, name):
        """Handle numeric HTML entities like &#8635;"""
        try:
            if name.startswith("x"):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
        except (ValueError, OverflowError) as exc:
            note_failure("generate_dashboard_video:L385", exc)
            char = f"&#{name};"
        self._append_to_current(char)

    def _append_to_current(self, text):
        """Append text to whichever field is currently being parsed."""
        if self._skip_depth > 0:
            return
        if self._in_h2:
            self._h2_text += text
        elif self._in_val:
            self._val_text += text
        elif self._in_sub:
            self._sub_text += text

    def _flush_card(self):
        if self._current_card is not None:
            if self._current_rows:
                self._current_card["rows"] = self._current_rows
                self._current_rows = []
            self.cards.append(self._current_card)
        self._current_card = None
        self._status_class = ""

    def get_cards(self):
        self._flush_card()
        return self.cards


# ===================================================================
# CUSTOM VIBRANT PIL FRAME RENDERER
# ===================================================================

# ===================================================================
# MAIN RENDERER — renders all card scenes
# ===================================================================
def render_vibrant_dashboard_frames(cards_data, frames_dir, width, height, fps,
                                    total_frames, transition_frames=30,
                                    progress_callback=None, cancel_check=None):
    """Render all dashboard card sections as colorful video frames.

    Each card gets its own color theme with gradient background, glow effects,
    colored headers, data bars, and smooth crossfade transitions.

    Args:
        cards_data: list of card dicts from DashboardCardDataExtractor
        frames_dir: Output directory for PNG frames
        width, height: Frame dimensions
        fps: Frames per second
        total_frames: Total frames to render
        transition_frames: Frames for crossfade between cards
        progress_callback: Optional (idx, total) -> None
        cancel_check: Optional () -> bool
    """
    if not HAS_PIL:
        print("  [ERROR] PIL/Pillow not installed. Install: pip install Pillow")
        return

    # Load fonts with fallback chain
    def _try_load_font(names, size):
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except Exception as exc:
                note_failure("generate_dashboard_video:L448", exc)
                continue
        # Try loading default with larger size (Pillow >= 9.0)
        try:
            return ImageFont.load_default(size=size)
        except Exception as exc:
            note_failure("generate_dashboard_video:L453", exc)
        return None

    title_font = _try_load_font(["segoeuib.ttf", "arialbd.ttf", "arial.ttf"], 52)
    header_font = _try_load_font(["segoeuib.ttf", "arialbd.ttf", "arial.ttf"], 32)
    body_font = _try_load_font(["consolas.ttf", "cour.ttf", "lucon.ttf", "arial.ttf"], 22)
    value_font = _try_load_font(["segoeuib.ttf", "arialbd.ttf", "arial.ttf"], 56)
    mono_font = _try_load_font(["consolas.ttf", "cour.ttf", "lucon.ttf", "arial.ttf"], 20)

    if not body_font:
        print("  [WARN] No suitable fonts found. Text will be small at 1080p.")
        print("  [WARN] Install Windows fonts or use --mode visual for CSS fidelity.")
        body_font = None

    fonts = (title_font, header_font, body_font, value_font, mono_font)

    n_cards = len(cards_data)
    if n_cards == 0:
        # Fallback: render a title card
        cards_data = [{"title": "Azazael Observability", "value": "",
                       "sub": "No dashboard data", "rows": [], "bars": [],
                       "status": "", "theme_class": ""}]
        n_cards = 1

    # Distribute frames among cards with transitions
    overhead = transition_frames * max(0, n_cards - 1)
    frames_per_card = max(1, (total_frames - overhead) // n_cards)
    remainder = max(0, total_frames - overhead - frames_per_card * n_cards)

    global_frame = 0

    for card_idx, card in enumerate(cards_data):
        title = card.get("title", "Dashboard")
        theme = _find_theme(title)
        n_frames = frames_per_card + (1 if card_idx < remainder else 0)

        for local_idx in range(n_frames):
            if cancel_check and cancel_check():
                return

            frame_progress = local_idx / max(1, n_frames)
            frame = _render_single_card_frame(
                width, height, card, theme, frame_progress, fonts
            )
            frame.save(os.path.join(frames_dir, f"frame_{global_frame:06d}.png"), "PNG")

            if progress_callback:
                progress_callback(global_frame, total_frames)
            global_frame += 1

        # Crossfade to next card
        if card_idx < n_cards - 1 and transition_frames > 0:
            next_theme = _find_theme(cards_data[card_idx + 1].get("title", ""))
            for t in range(transition_frames):
                if cancel_check and cancel_check():
                    return

                alpha = t / transition_frames
                frame = _render_crossfade_frame(
                    width, height, card, cards_data[card_idx + 1],
                    theme, next_theme, alpha, fonts
                )
                frame.save(os.path.join(frames_dir, f"frame_{global_frame:06d}.png"), "PNG")

                if progress_callback:
                    progress_callback(global_frame, total_frames)
                global_frame += 1

    # Pad remaining frames if needed
    while global_frame < total_frames:
        if cancel_check and cancel_check():
            return
        last_card = cards_data[-1]
        theme = _find_theme(last_card.get("title", ""))
        frame = _render_single_card_frame(
            width, height, last_card, theme, 1.0, fonts
        )
        frame.save(os.path.join(frames_dir, f"frame_{global_frame:06d}.png"), "PNG")
        if progress_callback:
            progress_callback(global_frame, total_frames)
        global_frame += 1


def _render_single_card_frame(w, h, card, theme, frame_progress, fonts):
    """Render a single frame for one card."""
    img = Image.new("RGBA", (w, h), (6, 8, 16, 255))
    draw = ImageDraw.Draw(img)

    title_font, header_font, body_font, value_font, mono_font = fonts

    # Background with gradient
    bg1 = theme["bg1"]
    bg2 = theme["bg2"]
    for y in range(h):
        t = y / h
        r = int(bg1[0] * (1 - t) + bg2[0] * t)
        g = int(bg1[1] * (1 - t) + bg2[1] * t)
        b = int(bg1[2] * (1 - t) + bg2[2] * t)
        draw.rectangle([(0, y), (w, y)], fill=(r, g, b))

    # Pulsing ambient glow
    accent: tuple[int, int, int] = theme["accent"]
    pulse = 0.6 + 0.4 * math.sin(frame_progress * math.pi * 4)
    for i in range(3):
        cx = int(w * (0.3 + 0.4 * math.sin(frame_progress * 2 + i * 2)))
        cy = int(h * (0.3 + 0.3 * math.cos(frame_progress * 1.5 + i * 2)))
        r = int(max(w, h) * 0.12)
        for j in range(4, 0, -1):
            a = int(8 * pulse / j)
            if a <= 0:
                continue
            draw.ellipse([cx - r // j, cy - r // j, cx + r // j, cy + r // j],
                         fill=(*accent, a))

    # Subtle grid
    for gx in range(0, w, 80):
        draw.line([(gx, 0), (gx, h)], fill=(*accent, 4))
    for gy in range(0, h, 80):
        draw.line([(0, gy), (w, gy)], fill=(*accent, 4))

    # Card panel
    margin = 60
    px1, py1 = margin, margin
    px2, py2 = w - margin, h - margin
    pw, ph = px2 - px1, py2 - py1

    # Border glow
    border_a = int(theme["border"][3] * (0.5 + 0.5 * pulse))
    draw.rectangle([(px1 - 1, py1 - 1), (px2 + 1, py2 + 1)],
                   fill=None, outline=(*accent, border_a // 2))
    draw.rectangle([(px1, py1), (px2, py2)],
                   fill=(4, 6, 12, 100))

    # Left accent bar (bold colored stripe)
    draw.rectangle([(px1 + 4, py1 + 4), (px1 + 18, py2 - 4)],
                   fill=(*accent, 180))
    # Inner highlight on accent bar
    draw.rectangle([(px1 + 6, py1 + 10), (px1 + 16, py2 - 10)],
                   fill=(*accent, 60))
    # Top accent line (thicker)
    draw.rectangle([(px1 + 16, py1 + 6), (px2 - 16, py1 + 14)],
                   fill=(*accent, 220))

    # Title with accent color
    title = card.get("title", "Dashboard")
    if title_font:
        draw.text((px1 + 20, py1 + 18), title.upper(),
                  font=title_font, fill=(*accent, 255))
    else:
        draw.text((px1 + 20, py1 + 18), title.upper(),
                  fill=(*accent, 255))

    # Value with glow
    value = card.get("value", "")
    status = card.get("status", "")
    val_color = STATUS_COLORS.get(status.lower(), (255, 255, 255))
    val_y = py1 + 56

    if value:
        glow_pulse = 0.5 + 0.5 * math.sin(frame_progress * math.pi * 3 + 1.2)
        # Larger glow behind value
        for dx in range(-4, 5, 2):
            for dy in range(-4, 5, 2):
                dist = math.sqrt(dx*dx + dy*dy)
                a = int(60 * glow_pulse * (1 - dist / 6))
                if a <= 0:
                    continue
                if value_font:
                    draw.text((px1 + 24 + dx, val_y + dy), str(value),
                              font=value_font, fill=(*val_color, a))
                else:
                    draw.text((px1 + 24 + dx, val_y + dy), str(value),
                              fill=(*val_color, a))
        if value_font:
            draw.text((px1 + 24, val_y), str(value),
                      font=value_font, fill=(255, 255, 255))
        else:
            draw.text((px1 + 24, val_y), str(value), fill=(255, 255, 255))

    # Sub text
    sub_text = card.get("sub", "")
    sub_y = val_y + (50 if value else 0)
    if sub_text and body_font:
        draw.text((px1 + 24, sub_y), str(sub_text),
                  font=body_font, fill=(155, 170, 195))
    elif sub_text:
        draw.text((px1 + 24, sub_y), str(sub_text),
                  fill=(155, 170, 195))

    # Status badge (more prominent)
    if status:
        badge_color = STATUS_COLORS.get(status.lower(), accent)
        badge_x = px2 - 110
        badge_y = py1 + 14
        draw.rectangle([(badge_x, badge_y), (badge_x + 90, badge_y + 28)],
                       fill=(*badge_color, 35))
        draw.rectangle([(badge_x, badge_y), (badge_x + 90, badge_y + 28)],
                       fill=None, outline=(*badge_color, 80), width=2)
        # Glow behind badge
        for j in range(3, 0, -1):
            int(badge_x - 5 * j)
            _draw_glow(draw, badge_x + 45, badge_y + 14, 20 + j * 8,
                       badge_color, 10)
        if body_font:
            draw.text((badge_x + 8, badge_y + 4), status.upper(),
                      font=body_font, fill=(*badge_color, 255))
        else:
            draw.text((badge_x + 8, badge_y + 4), status.upper(),
                      fill=(*badge_color, 255))

    # Rows
    rows = card.get("rows", [])
    row_start_y = sub_y + (32 if sub_text else 28 if value else py1 + 50)
    for i, row in enumerate(rows[:10]):
        ry = row_start_y + i * 28
        if ry > py2 - 10:
            break
        # Row bg - visible accent tint
        if i % 2 == 0:
            draw.rectangle([(px1 + 8, ry), (px2 - 8, ry + 24)],
                           fill=(*accent, 35))
        # Label
        label = str(row.get("label", ""))
        if body_font:
            draw.text((px1 + 16, ry + 2), label,
                      font=body_font, fill=(130, 145, 170))
        else:
            draw.text((px1 + 16, ry + 2), label, fill=(130, 145, 170))
        # Value with dynamic color
        val = str(row.get("value", ""))
        vcolor = STATUS_COLORS.get(val.lower(), (200, 210, 230))
        if mono_font:
            tw = draw.textlength(val, font=mono_font) if hasattr(draw, 'textlength') else len(val) * 10
            draw.text((px2 - 16 - tw, ry + 2), val,
                      font=mono_font, fill=vcolor)
        else:
            draw.text((px2 - 16 - len(val) * 9, ry + 2), val, fill=vcolor)

    # Bars
    bars = card.get("bars", [])
    bar_start_y = row_start_y + min(len(rows), 10) * 28 + 10
    for bi, bar in enumerate(bars[:3]):
        by = bar_start_y + bi * 16
        if by > py2 - 10:
            break
        bar_w = bar.get("width", 0)
        bar_cls = bar.get("class", "info")
        bcolor = STATUS_COLORS.get(bar_cls, accent)
        bar_len = int((pw - 40) * min(bar_w / 100, 1.0))
        # Track
        draw.rectangle([(px1 + 16, by), (px2 - 16, by + 5)],
                       fill=(40, 45, 55, 120))
        # Fill (animated)
        fill_len = int(bar_len * min(1.0, frame_progress * 2 + 0.2))
        if fill_len > 0:
            draw.rectangle([(px1 + 16, by), (px1 + 16 + fill_len, by + 5)],
                           fill=(*bcolor, 200))
            # Glow tip
            draw.rectangle([(px1 + 16 + fill_len - 1, by - 1),
                            (px1 + 16 + fill_len + 1, by + 6)],
                           fill=(*bcolor, 80))

    # Layer label
    if body_font:
        draw.text((px2 - 120, py2 - 18), "AZAZAEL OBSERVABILITY",
                  font=body_font, fill=(*accent, 40))
    else:
        draw.text((px2 - 120, py2 - 18), "AZAZAEL OBSERVABILITY",
                  fill=(*accent, 40))

    # Convert to RGB for ffmpeg
    rgb_img = Image.new("RGB", (w, h), (0, 0, 0))
    rgb_img.paste(img, mask=img.split()[3])
    return rgb_img


def _render_crossfade_frame(w, h, from_card, to_card, from_theme, to_theme,
                            alpha, fonts):
    """Render a crossfade transition frame between two cards."""
    # Render both frames
    from_frame = _render_single_card_frame(w, h, from_card, from_theme, 1.0, fonts)
    to_frame = _render_single_card_frame(w, h, to_card, to_theme, 0.0, fonts)

    # Blend
    blended = Image.blend(from_frame, to_frame, alpha)

    # Add transition indicator
    draw = ImageDraw.Draw(blended)
    accent = _blend_color(from_theme["accent"], to_theme["accent"], alpha)
    dot_color = tuple(int(c) for c in accent)
    for i in range(3):
        cx = w // 2 + (i - 1) * 20
        a = int(100 * (1 - abs(i - 1) * 0.5))
        draw.ellipse([cx - 3, h - 40, cx + 3, h - 34], fill=(*dot_color, a))

    return blended


# ===================================================================
# VIDEO GENERATION PIPELINE
# ===================================================================
def generate_vibrant_video(html_path, output_path, fps, duration, width, height,
                           progress_callback=None):
    """Generate a vibrant, colorful dashboard video using custom PIL renderer.

    Parses the dashboard HTML, extracts per-card data, renders each card
    with its own color theme, and encodes with FFmpeg.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        print("  [ERROR] FFmpeg not found. Install: winget install Gyan.FFmpeg")
        return False

    # Step 1: Parse HTML
    print(f"\n  {SCROLL_SEP}")
    print("  [CODEX] Step 1/3: Parsing dashboard HTML for card data...")
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        html_content = f.read()

    extractor = DashboardCardDataExtractor()
    extractor.feed(html_content)
    cards = extractor.get_cards()

    if not cards:
        print("  [WARN] No cards detected in dashboard HTML. Creating fallback.")
        cards = [{"title": "Azazael Observability", "value": "",
                  "sub": "Dashboard loaded", "rows": [],
                  "bars": [], "status": "ok"}]

    print(f"  [CODEX] Detected {len(cards)} card(s):")
    for i, card in enumerate(cards):
        title = card.get("title", "Unknown")
        val = card.get("value", "")
        card.get("sub", "")
        n_rows = len(card.get("rows", []))
        n_bars = len(card.get("bars", []))
        print(f"    Card {i+1}: \"{title}\""
              + (f" | value={val}" if val else "")
              + (f" | {n_rows} rows" if n_rows else "")
              + (f" | {n_bars} bars" if n_bars else ""))

    total_frames = fps * duration
    # Auto-tune: ~6-8 seconds per card for comfortable viewing
    auto_duration_per_card = 7
    ideal_duration = max(duration, len(cards) * auto_duration_per_card)
    if ideal_duration > duration:
        print(f"  [CODEX] Auto-tuning duration: {duration}s -> {ideal_duration}s "
              f"(for ~{auto_duration_per_card}s per card)")
        duration = ideal_duration
        total_frames = fps * duration

    transition_seconds = 1.5
    transition_frames = int(fps * transition_seconds)

    print(f"  [CODEX] Settings: {width}x{height} @ {fps}fps x {duration}s = {total_frames} frames")
    print(f"  [CODEX] Transitions: {transition_seconds}s per card ({transition_frames} frames)")

    # Step 2: Render frames with our vibrant renderer
    print(f"\n  {SCROLL_SEP}")
    print("  [CODEX] Step 2/3: Rendering vibrant frames...")
    frames_dir = os.path.join(os.path.dirname(output_path), "_dashboard_frames")
    if os.path.exists(frames_dir):
        shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(frames_dir, exist_ok=True)

    def _progress_cb(frame_idx, total):
        if frame_idx % (fps * 3) == 0:
            pct = int((frame_idx / total) * 100) if total > 1 else 0
            elapsed = frame_idx / fps
            remaining = (total - frame_idx) / fps
            print(f"    Frame {frame_idx}/{total} ({elapsed:.0f}s / {remaining:.0f}s remaining) - {pct}%")
            if progress_callback:
                progress_callback(frame_idx, total, "rendering")

    render_vibrant_dashboard_frames(
        cards_data=cards,
        frames_dir=frames_dir,
        width=width,
        height=height,
        fps=fps,
        total_frames=total_frames,
        transition_frames=transition_frames,
        progress_callback=_progress_cb,
    )

    print(f"  [CODEX] All {total_frames} frames rendered with vibrant colors.")

    # Step 3: Encode video
    print(f"\n  {SCROLL_SEP}")
    print("  [CODEX] Step 3/3: Encoding video with FFmpeg...")

    out_format = "wmv" if output_path.lower().endswith(".wmv") else "mp4"

    if out_format == "wmv":
        cmd = [
            ffmpeg_path, "-y",
            "-framerate", str(fps),
            "-i", os.path.join(frames_dir, "frame_%06d.png"),
            "-c:v", "wmv2",
            "-q:v", "2",
            output_path
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-framerate", str(fps),
            "-i", os.path.join(frames_dir, "frame_%06d.png"),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            output_path
        ]

    print(f"  [CODEX] Running: {' '.join(str(c) for c in cmd[:6])} ...")
    start_ts = time.time()

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)

    elapsed = time.time() - start_ts
    print(f"  [CODEX] Encoding completed in {elapsed:.1f}s")

    shutil.rmtree(frames_dir, ignore_errors=True)

    if result.returncode == 0:
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"\n  {SCROLL_SEP_THICK}")
        print("  [CODEX] VIBRANT VIDEO GENERATED SUCCESSFULLY")
        print(f"  [CODEX]   Output:  {output_path}")
        print(f"  [CODEX]   Size:    {size_mb:.1f} MB")
        print(f"  [CODEX]   Format:  {out_format.upper()} | {width}x{height} @ {fps}fps")
        print(f"  [CODEX]   Frames:  {total_frames} | Duration: {duration}s")
        print(f"  [CODEX]   Cards:   {len(cards)} with {len(CARD_THEMES)} color themes")
        print("  [CODEX]   Each card gets its own vibrant color identity")
        print(f"  {SCROLL_SEP_THICK}\n")
        if progress_callback:
            progress_callback(total_frames, total_frames, "complete")
        return True
    else:
        print(f"  [ERROR] FFmpeg encoding failed (code {result.returncode})")
        for line in result.stderr.split("\n")[-8:]:
            line = line.strip()
            if line:
                print(f"    {line}")
        return False


# ===================================================================
# VISUAL CAPTURE MODE (browser + ffmpeg gdigrab)
# ===================================================================
def browser_visual_mode(html_path, output_path, fps, duration, width, height,
                        progress_callback=None):
    """Capture the dashboard in a browser for full CSS/JS visual fidelity."""
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        print("  [ERROR] FFmpeg not found. Install: winget install Gyan.FFmpeg")
        return False

    target_px_s = 35
    px_per_down = 40
    presses_per_sec = target_px_s / px_per_down
    frames_between_presses = max(1, int(fps / presses_per_sec)) if presses_per_sec > 0 else fps

    print(f"\n  {SCROLL_SEP}")
    print("  [CODEX] Visual Capture Mode (full CSS/JS fidelity)")
    print("  This will open the dashboard in your default browser.")
    print("  The window must remain VISIBLE and UNCOVERED during capture.")
    print("  Press Ctrl+C to abort.\n")

    abs_html = str(Path(html_path).resolve())

    capture_script = (
        'import time, subprocess, os, sys, shutil\n'
        f'html_path = r"{abs_html}"\n'
        f'output_path = r"{output_path}"\n'
        f'fps = {fps}\n'
        f'duration = {duration}\n'
        f'width = {width}\n'
        f'height = {height}\n'
        f'framespause = {frames_between_presses}\n'
        '\n'
        'print(f"  [CODEX] Opening dashboard in browser...")\n'
        'os.startfile(html_path)\n'
        'time.sleep(3)\n'
        '\n'
        'total_frames = fps * duration\n'
        'ffmpeg_path = shutil.which("ffmpeg")\n'
        'if not ffmpeg_path:\n'
        '    print("  [ERROR] ffmpeg not found")\n'
        '    sys.exit(1)\n'
        '\n'
        'print(f"  [CODEX] Capture area: {width}x{height}")\n'
        'print(f"  [CODEX] Scroll: DOWN arrow every {framespause} frames")\n'
        'print(f"  [CODEX] Duration: {duration}s | Frames: {total_frames}")\n'
        '\n'
        'cmd = [\n'
        '    ffmpeg_path, "-y",\n'
        '    "-f", "gdigrab",\n'
        '    "-framerate", str(fps),\n'
        '    "-offset_x", "0",\n'
        '    "-offset_y", "0",\n'
        '    "-video_size", f"{width}x{height}",\n'
        '    "-i", "desktop",\n'
        '    "-c:v", "libx264",\n'
        '    "-preset", "fast",\n'
        '    "-crf", "23",\n'
        '    "-pix_fmt", "yuv420p",\n'
        '    output_path\n'
        ']\n'
        '\n'
        'def scroll_down():\n'
        '    ps_cmd = (\n'
        '        "powershell -Command \\""\n'
        '        "Add-Type -AssemblyName System.Windows.Forms; "\n'
        '        "[System.Windows.Forms.SendKeys]::SendWait(\\"{DOWN}\\")"\n'
        '        "\\""\n'
        '    )\n'
        '    subprocess.run(ps_cmd, capture_output=True, shell=True)\n'
        '\n'
        'print(f"  [CODEX] Starting FFmpeg capture...")\n'
        'print(f"  [CODEX] DO NOT cover or move the browser window!")\n'
        '\n'
        'ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n'
        'time.sleep(1)\n'
        '\n'
        'scroll_count = 0\n'
        'try:\n'
        '    for frame in range(total_frames):\n'
        '        if frame % framespause == 0:\n'
        '            scroll_down()\n'
        '            scroll_count += 1\n'
        '        time.sleep(1.0 / fps)\n'
        'except KeyboardInterrupt:\n'
        '    print(f"\\n  [CODEX] Capture interrupted.")\n'
        'finally:\n'
        '    ffmpeg_proc.terminate()\n'
        '    ffmpeg_proc.wait(timeout=5)\n'
        '    print(f"  [CODEX] Scroll actions: {scroll_count}")\n'
        '    print(f"  [CODEX] Output: {output_path}")\n'
    )

    capture_script_path = os.path.join(
        os.path.dirname(output_path) or os.path.expanduser("~"),
        "_dashboard_capture_script.py"
    )
    with open(capture_script_path, "w", encoding="utf-8") as f:
        f.write(capture_script)

    print("  [CODEX] Running visual capture script...")
    print(f"  [CODEX] Script: {capture_script_path}\n")

    try:
        result = subprocess.run(
            [sys.executable, capture_script_path],
            timeout=duration + 30
        )
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        print("  [CODEX] Capture completed (timeout reached).")
        success = True
    except KeyboardInterrupt:
        print("\n  [CODEX] Capture aborted.")
        success = False

    try:
        if os.path.exists(capture_script_path):
            os.remove(capture_script_path)
    except Exception as exc:
        note_failure("generate_dashboard_video:L1021", exc)

    if success and os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"\n  {SCROLL_SEP_THICK}")
        print("  [CODEX] VISUAL CAPTURE COMPLETE")
        print(f"  [CODEX]   Output:  {output_path}")
        print(f"  [CODEX]   Size:    {size_mb:.1f} MB")
        print(f"  [CODEX]   Format:  MP4 | {width}x{height} @ {fps}fps")
        print(f"  [CODEX]   Scroll:  {target_px_s} px/s\n")
        return True
    else:
        print("  [ERROR] Visual capture failed.")
        return False


# ===================================================================
# PREVIEW MODE
# ===================================================================
def preview_mode(html_path):
    """Analyze the dashboard HTML and display card themes and parameters."""
    print(f"\n  {SCROLL_SEP_THICK}")
    print("  CODEX DASHBOARD VIDEO - Preview & Theme Analysis")
    print(f"  {SCROLL_SEP_THICK}")

    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    extractor = DashboardCardDataExtractor()
    extractor.feed(html)
    cards = extractor.get_cards()

    file_size_kb = os.path.getsize(html_path) / 1024

    print(f"\n  Dashboard File:     {html_path}")
    print(f"  File Size:          {file_size_kb:.0f} KB")
    print(f"  Cards Detected:     {len(cards)}")
    print("")

    for i, card in enumerate(cards, 1):
        title = card.get("title", "Unknown")
        theme = _find_theme(title)
        ac = theme["accent"]
        val = card.get("value", "-")
        rows_count = len(card.get("rows", []))
        bars_count = len(card.get("bars", []))
        status = card.get("status", "")
        # Display a color block using ANSI (if terminal supports it)
        color_block = f"\033[48;2;{ac[0]};{ac[1]};{ac[2]}m  \033[0m"
        status_tag = f" [{status.upper()}]" if status else ""
        print(f"  {i:2d}. {color_block} \"{title}\"{status_tag}")
        print(f"      Theme: RGB({ac[0]}, {ac[1]}, {ac[2]})"
              f" | Value: {val}"
              f" | {rows_count} rows"
              f" | {bars_count} bars")

    print("\n  --- Scroll Parameter Analysis ---")
    est_duration = max(60, len(cards) * 7)
    print(f"  Cards:     {len(cards)} cards at ~7s each")
    print(f"  Ideal Duration: {est_duration}s ({est_duration/60:.1f} min)")
    print("  FPS:       24-30 recommended")
    print("  Each card rendered with unique color theme and glow effects")

    print(f"\n  {SCROLL_SEP}")
    print("  Quick Start:")
    print(f"    python {Path(__file__).name}                          # Colorful text mode (default)")
    print(f"    python {Path(__file__).name} --mode visual            # Browser capture (all CSS)")
    print(f"    python {Path(__file__).name} --duration 120           # Longer video")
    print(f"    python {Path(__file__).name} --fps 24                 # Film-style")
    print(f"  {SCROLL_SEP_THICK}\n")


def ensure_dashboard_html(html_path, since=None):
    """Generate the dashboard HTML if needed."""
    if not html_path.exists() or since:
        print(f"  [CODEX] Generating fresh dashboard (since={since or '24h'})...")
        try:
            sys.path.insert(0, str(SRC_DIR))
            from CORE.web_dashboard import export_html
            from CORE.worm_registry import discover_worms
            discover_worms()
            html_path.parent.mkdir(parents=True, exist_ok=True)
            path = export_html(str(html_path), since=since or "24h")
            print(f"  [CODEX] Dashboard saved to: {path}")
        except Exception as e:
            print(f"  [WARN] Could not auto-generate dashboard: {e}")
    return html_path


def main():
    parser = argparse.ArgumentParser(
    description="CODEX Dashboard Video Generator v2.0",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=__doc__,
    )
    parser.add_argument("--mode", choices=["text", "visual", "preview"],
                    default="text",
                    help="'text' = custom colorful PIL renderer (default), "
                             "'visual' = browser + gdigrab (full CSS), "
                             "'preview' = analyze HTML only")
    parser.add_argument("--output", "-o", default=None,
                    help="Output video path")
    parser.add_argument("--fps", type=int, default=30,
                    help="FPS (range: 10-60)")
    parser.add_argument("--duration", type=int, default=60,
                    help="Duration in seconds (auto-tuned per card count)")
    parser.add_argument("--resolution", default="1920x1080",
                    help="WxH resolution (default: 1920x1080)")
    parser.add_argument("--since", default=None,
                    help='Dashboard data filter: "24h", "7d"')
    parser.add_argument("--html", default=None,
                    help=f"Path to dashboard HTML (default: {DASHBOARD_HTML})")

    args = parser.parse_args()

    if args.fps < 10 or args.fps > 60:
        print("  [ERROR] FPS must be between 10 and 60.")
        sys.exit(1)

    if args.duration < 10:
        print("  [ERROR] Minimum duration is 10 seconds.")
        sys.exit(1)

    try:
        w, h = map(int, args.resolution.lower().replace("x", " ").split())
    except Exception:
        w, h = 1920, 1080

    html_path = Path(args.html) if args.html else DASHBOARD_HTML

    if args.mode == "preview":
        if not html_path.exists():
            print(f"  [ERROR] Dashboard not found: {html_path}")
            print("  Generate: python scripts/generate_dashboard.py --since 24h")
            sys.exit(1)
        preview_mode(str(html_path))
        return

    if not html_path.exists():
        print("  [CODEX] Dashboard not found, generating...")
        ensure_dashboard_html(html_path, since=args.since or "24h")
        if not html_path.exists():
            print("  [ERROR] Could not generate dashboard.")
            sys.exit(1)

    if args.output:
        output_path = args.output
    else:
        os.makedirs(str(DEFAULT_OUTPUT_DIR), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_tag = "VISUAL" if args.mode == "visual" else "COLOR"
        output_path = str(DEFAULT_OUTPUT_DIR / f"Dashboard_{mode_tag}_{ts}.mp4")

    print(f"\n  {SCROLL_SEP_THICK}")
    print("  CODEX Dashboard Video Generator v2.0")
    print(f"  {SCROLL_SEP}")
    mode_labels = {"text": "Colorful PIL (per-card themes)",
                       "visual": "Browser Capture (full CSS)", "preview": "Preview"}
    print(f"  Mode:     {mode_labels.get(args.mode, args.mode)}")
    print(f"  Source:   {html_path}")
    print(f"  Output:   {output_path}")
    print(f"  Format:   {w}x{h} @ {args.fps}fps")
    print(f"  {SCROLL_SEP}")

    if args.mode == "text":
        success = generate_vibrant_video(
            html_path=str(html_path),
            output_path=output_path,
            fps=args.fps,
            duration=args.duration,
            width=w,
            height=h,
            )
    elif args.mode == "visual":
        success = browser_visual_mode(
            html_path=str(html_path),
            output_path=output_path,
            fps=args.fps,
            duration=args.duration,
            width=w,
            height=h,
            )
    else:
        success = False

    if success:
        print(f"  [CODEX] Open: {output_path}")
    else:
        print("  [CODEX] Generation failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
