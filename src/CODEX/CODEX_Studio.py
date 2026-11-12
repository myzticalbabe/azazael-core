#!/usr/bin/env python3
"""
CODEX Studio — Standalone Desktop Application
HTML → MP4/WMV Video Generation Engine
Powered by PIL + FFmpeg 8.1.1

Usage:
    python CODEX_Studio.py          # Launch GUI
    python CODEX_Studio.py --cli    # CLI mode (for scripting)

Author: Azazael CODEX
Version: 3.0.0
"""

import os
import sys
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from html.parser import HTMLParser
from datetime import datetime
from typing import List, Optional
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ─── PIL Import ───
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError as exc:
    note_failure("CODEX_Studio:L29", exc)
    HAS_PIL = False

# ─── Constants ───
APP_NAME = "CODEX Studio"
APP_VERSION = "3.1.0"

# CAIN Standard — Established from complete_remorse_cain.mp4 reverse engineering:
#   Framerate: 25 fps   (H.264, from CAIN MP4's 25/1 r_frame_rate)
#   Resolution: 1920x1080 (standard 1080p, down from CAIN's non-standard 2074x1418)
#   Codec: H.264 High profile with B-frames (not Constrained Baseline with 0 B-frames)
#   CRF: 23 (balance of quality/size; CAIN used Constrained Baseline = every frame keyframe)
#   Keyframes: every 250 frames (~10s at 25fps), not every frame
#   Target: ~80-120 MB for 7min scroll doc (vs CAIN's 477 MB)
CAIN_FPS = 25
CAIN_CRF = 23
CAIN_PROFILE = "high"
CAIN_GOP = 250  # keyframe interval
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

# Theme colors
THEME = {
    "bg": "#0d0d1a",
    "surface": "#151530",
    "surface_alt": "#1a1a3a",
    "border": "#2a2a50",
    "text": "#e0e0e0",
    "text_dim": "#707090",
    "accent": "#00f5ff",
    "accent2": "#bf00ff",
    "success": "#00ff88",
    "warning": "#ffdd00",
    "error": "#ff4757",
    "button": "#1e1e40",
    "button_hover": "#2a2a60",
    "entry_bg": "#0a0a20",
}

# ─── FFmpeg Detection ───
def find_ffmpeg():
    """Auto-detect ffmpeg executable path."""
    candidates = [
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
        r"C:\Users\clint\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


# ─── HTML Text Extractor ───
class HTMLTextExtractor(HTMLParser):
    """Extract visible text content from HTML files."""

    def __init__(self):
        super().__init__()
        self.lines = []
        self._current = ""
        self._skip_tags = {"script", "style", "head", "meta", "link", "title"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._current = ""

    def handle_endtag(self, tag):
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr"):
            if self._current.strip():
                self.lines.append(self._current.strip())
            self._current = ""

    def handle_data(self, data):
        stripped = data.strip()
        if stripped:
            self._current += " " + stripped if self._current else stripped

    def get_text(self):
        if self._current.strip():
            self.lines.append(self._current.strip())
        return [l for l in self.lines if l]


# ─── HTML Scene Extractor ───
class HTMLSceneExtractor(HTMLParser):
    """Parse HTML into named scenes for multi-scene video generation.

    Detects scenes from:
    - <section data-scene="Name"> elements
    - <div class="scene" data-title="Name"> elements
    - <article> elements (uses first <h1>-<h3> as title)
    - Falls back to single-scene with all text if no scenes detected
    """

    SCENE_TAGS = {"section", "article", "div"}
    SCENE_CLASSES = {"scene", "slide", "chapter", "panel"}

    def __init__(self):
        super().__init__()
        self.scenes = []  # List of {"title": str, "lines": [str]}
        self._current_scene = None
        self._current_text = ""
        self._in_scene = False
        self._skip_tags = {"script", "style", "head", "meta", "link", "title"}
        self._skip_depth = 0
        self.raw_html: Optional[str] = None  # Set by caller before get_scenes()

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag in self._skip_tags:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        # Check if this tag starts a scene
        classes = (attrs_dict.get("class") or "").split()
        data_scene = attrs_dict.get("data-scene", "")
        data_title = attrs_dict.get("data-title", "")

        is_scene_tag = (
            (tag in self.SCENE_TAGS and any(c in self.SCENE_CLASSES for c in classes))
            or bool(data_scene)
            or bool(data_title)
        )

        if is_scene_tag:
            self._flush_current_scene()
            title = data_scene or data_title or " ".join(classes).title()
            self._current_scene = {"title": title, "lines": []}
            self._in_scene = True

        # Capture heading as potential scene title for <article>
        if tag in ("h1", "h2", "h3") and self._current_scene is None:
            self._current_scene = {"title": "", "lines": []}
            self._in_scene = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
            return

        if self._skip_depth > 0:
            return

        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr", "pre", "code"):
            stripped = self._current_text.strip()
            if stripped:
                if self._current_scene is not None:
                    # Use first heading as scene title
                    if tag in ("h1", "h2", "h3") and not self._current_scene["title"]:
                        self._current_scene["title"] = stripped
                    else:
                        self._current_scene["lines"].append(stripped)
                else:
                    # No scene yet — start implicit scene
                    self._current_scene = {"title": "", "lines": [stripped]}
            self._current_text = ""

        if tag in self.SCENE_TAGS and self._in_scene:
            self._flush_current_scene()
            self._in_scene = False

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        stripped = data.strip()
        if stripped:
            self._current_text += " " + stripped if self._current_text else stripped

    def _flush_current_scene(self):
        if self._current_text.strip():
            if self._current_scene is not None:
                self._current_scene["lines"].append(self._current_text.strip())
            self._current_text = ""
        if self._current_scene is not None and self._current_scene["lines"]:
            self.scenes.append(self._current_scene)
        self._current_scene = None

    def get_scenes(self):
        """Return list of {title, lines} dicts. Falls back to single scene."""
        self._flush_current_scene()
        if not self.scenes:
            # Fallback: extract all text as single scene
            extractor = HTMLTextExtractor()
            extractor.feed(self.raw_html or "")
            lines = extractor.get_text()
            if lines:
                self.scenes = [{"title": "Full Document", "lines": lines}]
        return self.scenes


# ─── Scene-Based Frame Renderer ───
def render_scenes_to_png(scenes, frames_dir, width, height, fps,
                         total_frames, transition_frames=30,
                         cancel_check=None, progress_callback=None):
    """Render multi-scene video frames with crossfade transitions.

    Args:
        scenes: list of {"title": str, "lines": [str]} dicts
        frames_dir: Output directory for PNG frames
        width, height: Frame dimensions
        fps: Frames per second
        total_frames: Total frames across all scenes
        transition_frames: Number of frames for crossfade between scenes
        cancel_check: Optional callable() -> bool
        progress_callback: Optional callable(frame_idx, total_frames)
    """
    _title_font, header_font, body_font, _small_font, mono_font = _load_fonts()
    n_scenes = len(scenes)
    if n_scenes == 0:
        return

    # Divide frames among scenes, reserving transitions
    transition_overhead = transition_frames * (n_scenes - 1)
    content_frames = max(n_scenes, total_frames - transition_overhead)
    frames_per_scene = content_frames // n_scenes
    remainder = content_frames % n_scenes

    # Pre-calculate line heights per scene
    scene_data = []
    for scene in scenes:
        lines = scene["lines"]
        if not lines:
            lines = [scene["title"]]
        heights = _calc_line_heights(lines, body_font, header_font, width)
        total_h = sum(heights) + height + 100
        scene_data.append({
            "title": scene["title"],
            "lines": lines,
            "heights": heights,
            "total_height": total_h,
        })

    margin = 100
    progress_interval = max(1, total_frames // 50, fps * 5)
    global_frame = 0

    for scene_idx, sd in enumerate(scene_data):
        n_frames = frames_per_scene + (1 if scene_idx < remainder else 0)

        for local_idx in range(n_frames):
            if global_frame >= total_frames:
                break
            if cancel_check and cancel_check():
                return

            progress = local_idx / n_frames if n_frames > 1 else 0
            y_offset = int(progress * (sd["total_height"] - height))

            frame = Image.new("RGB", (width, height), color="#08081a")
            draw = ImageDraw.Draw(frame)

            # Grid background
            for gx in range(0, width, 80):
                draw.line([(gx, 0), (gx, height)], fill="#1a1a3a", width=1)
            for gy in range(0, height, 80):
                draw.line([(0, gy), (width, gy)], fill="#1a1a3a", width=1)

            # Gradient bottom
            for gy in range(height - 60, height):
                fade = int(255 * (gy - height + 60) / 60)
                gb = 8 + (26 - 8) * fade // 255
                draw.rectangle([(0, gy), (width, gy + 1)], fill=(8, gb, gb))

            # Scene title bar
            if sd["title"]:
                title_box_h = 50
                draw.rectangle([(0, 0), (width, title_box_h)], fill="#0d0d2a")
                draw.line([(0, title_box_h), (width, title_box_h)], fill="#00f5ff", width=2)
                try:
                    tbbox = draw.textbbox((0, 0), sd["title"], font=header_font)
                    tx = (width - (tbbox[2] - tbbox[0])) // 2
                    draw.text((tx, 5), sd["title"], font=header_font, fill="#00f5ff")
                except Exception as exc:
                    note_failure("CODEX_Studio:L308", exc)
                # Scene counter
                counter = f"Scene {scene_idx + 1}/{n_scenes}"
                try:
                    cbbox = draw.textbbox((0, 0), counter, font=body_font)
                    draw.text((width - (cbbox[2] - cbbox[0]) - 20, 10),
                             counter, font=body_font, fill="#707090")
                except Exception as exc:
                    note_failure("CODEX_Studio:L316", exc)

            # Draw text lines
            current_y = -y_offset + 60
            for i, line in enumerate(sd["lines"]):
                if current_y > height + 80:
                    break
                if current_y < 40:
                    current_y += sd["heights"][i]
                    continue

                line = line.rstrip()
                if not line:
                    current_y += 20
                    continue

                color, font = _pick_text_style(line, header_font, body_font, mono_font)
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    text_w = bbox[2] - bbox[0]
                    x = max(margin, (width - text_w) // 2)
                    draw.text((x, current_y), line, font=font, fill=color)
                except Exception as exc:
                    note_failure("CODEX_Studio:L339", exc)
                current_y += sd["heights"][i]

            # Save frame
            frame.save(os.path.join(frames_dir, f"frame_{global_frame:06d}.png"), "PNG")

            if progress_callback and global_frame % progress_interval == 0:
                progress_callback(global_frame, total_frames)

            global_frame += 1

        # Crossfade transition to next scene
        if scene_idx < n_scenes - 1 and transition_frames > 0:
            _render_crossfade_transition(
                sd, scene_data[scene_idx + 1],
                frames_dir, global_frame, width, height,
                transition_frames, progress_interval,
                cancel_check, progress_callback,
            )
            global_frame += transition_frames


def _render_crossfade_transition(from_scene, to_scene, frames_dir,
                                 start_frame, width, height,
                                 transition_frames, progress_interval,
                                 cancel_check, progress_callback):
    """Render crossfade transition frames between two scenes."""
    _title_font, header_font, body_font, _small_font, mono_font = _load_fonts()
    margin = 100

    for t in range(transition_frames):
        if cancel_check and cancel_check():
            return

        alpha = t / transition_frames  # 0 = from_scene, 1 = to_scene
        frame_idx = start_frame + t

        frame = Image.new("RGB", (width, height), color="#08081a")
        draw = ImageDraw.Draw(frame)

        # Grid
        for gx in range(0, width, 80):
            draw.line([(gx, 0), (gx, height)], fill="#1a1a3a", width=1)
        for gy in range(0, height, 80):
            draw.line([(0, gy), (width, gy)], fill="#1a1a3a", width=1)

        # Gradient bottom
        for gy in range(height - 60, height):
            fade = int(255 * (gy - height + 60) / 60)
            gb = 8 + (26 - 8) * fade // 255
            draw.rectangle([(0, gy), (width, gy + 1)], fill=(8, gb, gb))

        # Transition label
        label = f"{from_scene['title']}  >>  {to_scene['title']}"
        try:
            lbbox = draw.textbbox((0, 0), label, font=body_font)
            lx = (width - (lbbox[2] - lbbox[0])) // 2
            fade_color = int(180 + 75 * (1 - abs(2 * alpha - 1)))
            draw.text((lx, 10), label, font=body_font,
                     fill=(fade_color, fade_color, fade_color))
        except Exception as exc:
            note_failure("CODEX_Studio:L400", exc)

        # Draw from_scene text fading out
        from_y = int(-alpha * height) + 60
        for i, line in enumerate(from_scene["lines"]):
            if from_y > height + 80 or from_y < -60:
                from_y += from_scene["heights"][i]
                continue
            if not line.rstrip():
                from_y += 20
                continue
            color, font = _pick_text_style(line.rstrip(), header_font, body_font, mono_font)
            fade = 1.0 - alpha
            try:
                bbox = draw.textbbox((0, 0), line.rstrip(), font=font)
                tw = bbox[2] - bbox[0]
                blend = _blend_color(color, fade)
                draw.text((max(margin, (width - tw) // 2), from_y),
                         line.rstrip(), font=font, fill=blend)
            except Exception as exc:
                note_failure("CODEX_Studio:L420", exc)
            from_y += from_scene["heights"][i]

        # Draw to_scene text fading in
        to_y = int((1 - alpha) * height) + 60
        for i, line in enumerate(to_scene["lines"]):
            if to_y > height + 80 or to_y < -60:
                to_y += to_scene["heights"][i]
                continue
            if not line.rstrip():
                to_y += 20
                continue
            color, font = _pick_text_style(line.rstrip(), header_font, body_font, mono_font)
            fade = alpha
            try:
                bbox = draw.textbbox((0, 0), line.rstrip(), font=font)
                tw = bbox[2] - bbox[0]
                blend = _blend_color(color, fade)
                draw.text((max(margin, (width - tw) // 2), to_y),
                         line.rstrip(), font=font, fill=blend)
            except Exception as exc:
                note_failure("CODEX_Studio:L441", exc)
            to_y += to_scene["heights"][i]

        frame.save(os.path.join(frames_dir, f"frame_{frame_idx:06d}.png"), "PNG")

        if progress_callback and frame_idx % progress_interval == 0:
            progress_callback(frame_idx, start_frame + transition_frames)


# ─── Shared Frame Renderer ───
def render_frames_to_png(text_lines, frames_dir, width, height, fps, total_frames,
                         cancel_check=None, progress_callback=None):
    """Render scrolling text frames to PNG files using PIL.

    Shared by both GUI and CLI — ensures consistent output (grid + gradient).

    Args:
        text_lines: list of text strings to scroll through
        frames_dir: Directory to save frame_NNNNNN.png files
        width, height: Frame dimensions
        fps: Frames per second (used for progress reporting interval)
        total_frames: Total number of frames to render
        cancel_check: Optional callable() -> bool for cooperative cancellation
        progress_callback: Optional callable(frame_idx, total_frames) for updates
    """
    # Set up fonts
    _title_font, header_font, body_font, _small_font, mono_font = \
        _load_fonts()

    # Calculate line heights for scroll offset
    line_heights = _calc_line_heights(text_lines, body_font, header_font, width)
    total_text_height = sum(line_heights) + height + 100
    margin = 100

    # Throttle progress callback to ~every 1% or every 5s, whichever is less frequent
    progress_interval = max(1, total_frames // 50, fps * 5)

    for frame_idx in range(total_frames):
        if cancel_check and cancel_check():
            break

        progress = frame_idx / total_frames if total_frames > 1 else 0
        y_offset = int(progress * (total_text_height - height))

        frame = Image.new("RGB", (width, height), color="#08081a")
        draw = ImageDraw.Draw(frame)

        # Grid background
        for gx in range(0, width, 80):
            draw.line([(gx, 0), (gx, height)], fill="#1a1a3a", width=1)
        for gy in range(0, height, 80):
            draw.line([(0, gy), (width, gy)], fill="#1a1a3a", width=1)

        # Gradient overlay at bottom (fade to background)
        for gy in range(height - 60, height):
            fade = int(255 * (gy - height + 60) / 60)
            gb = 8 + (26 - 8) * fade // 255
            draw.rectangle([(0, gy), (width, gy + 1)], fill=(8, gb, gb))

        # Draw text lines
        current_y = -y_offset
        for i, line in enumerate(text_lines):
            if current_y > height + 80:
                break
            if current_y < -60:
                current_y += line_heights[i]
                continue

            line = line.rstrip()
            if not line:
                current_y += 20
                continue

            color, font = _pick_text_style(line, header_font, body_font, mono_font)
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
                x = max(margin, (width - text_w) // 2)
                draw.text((x, current_y), line, font=font, fill=color)
            except Exception as exc:
                note_failure("CODEX_Studio:L521", exc)

            current_y += line_heights[i]

        # Save frame
        frame.save(os.path.join(frames_dir, f"frame_{frame_idx:06d}.png"), "PNG")

        # Progress callback
        if progress_callback and frame_idx % progress_interval == 0:
            progress_callback(frame_idx, total_frames)


def _load_fonts():
    """Load font faces with fallback chain."""
    try:
        title_font = ImageFont.truetype("segoeuib.ttf", 64)
        header_font = ImageFont.truetype("segoeuib.ttf", 42)
        body_font = ImageFont.truetype("consolas.ttf", 28)
        small_font = ImageFont.truetype("consolas.ttf", 20)
        mono_font = ImageFont.truetype("consolas.ttf", 24)
    except Exception as exc:
        note_failure("CODEX_Studio:L542", exc)
        try:
            title_font = ImageFont.truetype("arial.ttf", 56)
            header_font = ImageFont.truetype("arial.ttf", 38)
            body_font = ImageFont.truetype("cour.ttf", 26)
            small_font = ImageFont.truetype("cour.ttf", 18)
            mono_font = body_font
        except Exception as exc:
            note_failure("CODEX_Studio:L549", exc)
            title_font = header_font = body_font = \
                small_font = mono_font = ImageFont.load_default()
    return title_font, header_font, body_font, small_font, mono_font


def _calc_line_heights(text_lines, body_font, header_font, width):
    """Pre-calculate pixel heights for each text line."""
    temp_img = Image.new("RGB", (width, 200))
    temp_draw = ImageDraw.Draw(temp_img)
    heights = []
    for line in text_lines:
        if not line.strip():
            heights.append(20)
        elif line == line.upper() and len(line) > 20:
            bbox = temp_draw.textbbox((0, 0), line, font=header_font)
            heights.append(bbox[3] - bbox[1] + 14)
        else:
            bbox = temp_draw.textbbox((0, 0), line, font=body_font)
            heights.append(bbox[3] - bbox[1] + 10)
    return heights


def _blend_color(hex_color, alpha):
    """Blend a hex color #rrggbb toward black by alpha factor (0=black, 1=original)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return (int(r * alpha), int(g * alpha), int(b * alpha))


def _pick_text_style(line, header_font, body_font, mono_font):
    """Choose color and font based on line content."""
    stripped = line.strip()
    if stripped == stripped.upper() and len(stripped) > 15:
        return "#00f5ff", header_font
    if any(kw in stripped.upper() for kw in [
        "TIER", "SYSTEM", "CLUSTER", "WORM", "META",
        "ARCHITECTURE", "API", "PIPELINE", "HYDRA", "OUTPUT"
    ]):
        return "#bf00ff", header_font
    if stripped.startswith(("\u251c", "\u2514", "\u2502", "\u2500",
                             "\u256d", "\u2570", "\u250c", "\u2510", "\u2518")):
        return "#00ff88", mono_font
    if any(c in stripped for c in ["(", ")", "[", "]", "{", "}"]):
        return "#ffdd00", body_font
    if ":" in stripped and len(stripped) < 60:
        return "#00f5ff", body_font
    return "#c0c0d0", body_font


# ─── CODEX Studio App ───
class CODEXStudio:
    """Main Tkinter application window."""

    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("960x720")
        self.root.minsize(800, 600)
        self.root.configure(bg=THEME["bg"])

        # State
        self.ffmpeg_path = find_ffmpeg()
        self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "CODEX_Studio_Output")
        self.is_rendering = False
        self.cancel_requested = False
        self.scene_mode = tk.BooleanVar(value=False)

        # Style
        self._setup_style()
        self._build_ui()

        # Status
        if self.ffmpeg_path:
            self.log(f"FFmpeg detected: {self.ffmpeg_path}", "success")
            self._get_ffmpeg_version()
        else:
            self.log("WARNING: FFmpeg not found. Install with: winget install Gyan.FFmpeg", "warning")

        if not HAS_PIL:
            self.log("WARNING: PIL/Pillow not installed. Install with: pip install Pillow", "warning")

        self.log(f"{APP_NAME} v{APP_VERSION} ready.", "info")

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Configure ttk styles
        style.configure("TFrame", background=THEME["bg"])
        style.configure("TLabelframe", background=THEME["bg"], foreground=THEME["text"],
                        bordercolor=THEME["border"])
        style.configure("TLabelframe.Label", background=THEME["bg"], foreground=THEME["accent"],
                        font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"],
                        font=("Segoe UI", 9))
        style.configure("TButton", background=THEME["button"], foreground=THEME["text"],
                        font=("Segoe UI", 9, "bold"), borderwidth=1,
                        focusthickness=2, focuscolor=THEME["accent"])
        style.map("TButton",
                  background=[("active", THEME["button_hover"]), ("disabled", THEME["surface"])],
                  foreground=[("disabled", THEME["text_dim"])])
        style.configure("TEntry", fieldbackground=THEME["entry_bg"], foreground=THEME["text"],
                        insertcolor=THEME["accent"])
        style.configure("TCombobox", fieldbackground=THEME["entry_bg"], foreground=THEME["text"],
                        arrowcolor=THEME["text"])
        style.configure("TScale", background=THEME["bg"], troughcolor=THEME["surface"])
        style.configure("TProgressbar", background=THEME["accent"], troughcolor=THEME["surface"],
                        borderwidth=0)

        # Accent button style
        style.configure("Accent.TButton", background=THEME["accent"], foreground=THEME["bg"],
                        font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton",
                  background=[("active", "#33f7ff"), ("disabled", THEME["surface_alt"])])

    def _build_ui(self):
        # ─── Main Container ───
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        # ─── Header ───
        header = tk.Frame(main, bg=THEME["bg"])
        header.pack(fill="x", pady=(0, 15))

        title = tk.Label(header, text="CODEX STUDIO", font=("Segoe UI", 22, "bold"),
                        bg=THEME["bg"], fg=THEME["accent"])
        title.pack(side="left")

        version = tk.Label(header, text=f"v{APP_VERSION}", font=("Segoe UI", 9),
                          bg=THEME["bg"], fg=THEME["text_dim"])
        version.pack(side="left", padx=(10, 0), pady=(8, 0))

        # ─── Content Grid ───
        content = ttk.Frame(main)
        content.pack(fill="both", expand=True)

        # Left panel — Configuration
        left = ttk.Frame(content, padding=10)
        left.pack(side="left", fill="y", padx=(0, 10))

        # ─── HTML Source ───
        html_frame = ttk.LabelFrame(left, text=" 📄 HTML Source ", padding=12)
        html_frame.pack(fill="x", pady=(0, 12))

        self.html_path_var = tk.StringVar()
        html_entry = ttk.Entry(html_frame, textvariable=self.html_path_var, font=("Consolas", 9))
        html_entry.pack(fill="x", pady=(0, 8))

        btn_row = tk.Frame(html_frame, bg=THEME["bg"])
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Browse...", command=self._browse_html,
                 bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 9),
                 activebackground=THEME["button_hover"], activeforeground=THEME["text"],
                 relief="flat", padx=16, pady=4, cursor="hand2").pack(side="left")

        self.html_info = tk.Label(html_frame, text="", font=("Segoe UI", 8),
                                  bg=THEME["bg"], fg=THEME["text_dim"])
        self.html_info.pack(fill="x", pady=(6, 0))

        # ─── Output Settings ───
        out_frame = ttk.LabelFrame(left, text=" ⚙️ Output Settings ", padding=12)
        out_frame.pack(fill="x", pady=(0, 12))

        # Format
        tk.Label(out_frame, text="Format:", bg=THEME["bg"], fg=THEME["text_dim"],
                font=("Segoe UI", 9)).pack(anchor="w")
        self.format_var = tk.StringVar(value="mp4")
        fmt_combo = ttk.Combobox(out_frame, textvariable=self.format_var,
                                 values=["mp4", "wmv"], state="readonly", width=10)
        fmt_combo.pack(fill="x", pady=(2, 10))

        # Resolution
        tk.Label(out_frame, text="Resolution:", bg=THEME["bg"], fg=THEME["text_dim"],
                font=("Segoe UI", 9)).pack(anchor="w")
        self.resolution_var = tk.StringVar(value="1920x1080 (1080p)")
        res_combo = ttk.Combobox(out_frame, textvariable=self.resolution_var,
                                 values=["1280x720 (720p)", "1920x1080 (1080p)", "3840x2160 (4K)"],
                                 state="readonly", width=18)
        res_combo.pack(fill="x", pady=(2, 10))

        # FPS (default: 25 — CAIN standard)
        tk.Label(out_frame, text="Frame Rate (FPS):", bg=THEME["bg"], fg=THEME["text_dim"],
                font=("Segoe UI", 9)).pack(anchor="w")
        fps_frame = tk.Frame(out_frame, bg=THEME["bg"])
        fps_frame.pack(fill="x", pady=(2, 10))
        self.fps_var = tk.IntVar(value=CAIN_FPS)
        fps_scale = ttk.Scale(fps_frame, from_=1, to=60, variable=self.fps_var,
                             orient="horizontal", command=self._on_fps_change)
        fps_scale.pack(side="left", fill="x", expand=True)
        self.fps_label = tk.Label(fps_frame, text="30", font=("Segoe UI", 9, "bold"),
                                  bg=THEME["bg"], fg=THEME["accent"], width=3)
        self.fps_label.pack(side="right", padx=(6, 0))

        # Duration
        tk.Label(out_frame, text="Duration (seconds):", bg=THEME["bg"], fg=THEME["text_dim"],
                font=("Segoe UI", 9)).pack(anchor="w")
        dur_frame = tk.Frame(out_frame, bg=THEME["bg"])
        dur_frame.pack(fill="x", pady=(2, 10))
        self.duration_var = tk.IntVar(value=60)
        dur_scale = ttk.Scale(dur_frame, from_=5, to=300, variable=self.duration_var,
                             orient="horizontal", command=self._on_dur_change)
        dur_scale.pack(side="left", fill="x", expand=True)
        self.dur_label = tk.Label(dur_frame, text="60s", font=("Segoe UI", 9, "bold"),
                                  bg=THEME["bg"], fg=THEME["accent"], width=5)
        self.dur_label.pack(side="right", padx=(6, 0))

        # Output directory
        tk.Label(out_frame, text="Output Folder:", bg=THEME["bg"], fg=THEME["text_dim"],
                font=("Segoe UI", 9)).pack(anchor="w")
        outdir_frame = tk.Frame(out_frame, bg=THEME["bg"])
        outdir_frame.pack(fill="x", pady=(2, 0))
        self.outdir_var = tk.StringVar(value=self.output_dir)
        ttk.Entry(outdir_frame, textvariable=self.outdir_var, font=("Consolas", 8),
                 width=30).pack(side="left", fill="x", expand=True)
        tk.Button(outdir_frame, text="...", command=self._browse_outdir,
                 bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 9),
                 activebackground=THEME["button_hover"], activeforeground=THEME["text"],
                 relief="flat", padx=8, pady=2, cursor="hand2").pack(side="right", padx=(4, 0))

        # Scene Mode toggle
        scene_frame = tk.Frame(out_frame, bg=THEME["bg"])
        scene_frame.pack(fill="x", pady=(10, 0))
        self.scene_cb = tk.Checkbutton(scene_frame, text="Scene Mode (detect HTML sections)",
                                       variable=self.scene_mode,
                                       bg=THEME["bg"], fg=THEME["accent"],
                                       selectcolor=THEME["surface"],
                                       font=("Segoe UI", 9),
                                       activebackground=THEME["bg"],
                                       activeforeground=THEME["accent"])
        self.scene_cb.pack(side="left")

        # ─── Generate Button ───
        btn_panel = tk.Frame(left, bg=THEME["bg"])
        btn_panel.pack(fill="x", pady=(0, 8))

        self.generate_btn = tk.Button(btn_panel, text="▶  GENERATE VIDEO", command=self._generate_video,
                                      bg="#00ccdd", fg=THEME["bg"], font=("Segoe UI", 12, "bold"),
                                      activebackground="#00f5ff", activeforeground=THEME["bg"],
                                      relief="flat", padx=24, pady=12, cursor="hand2")
        self.generate_btn.pack(fill="x")

        self.cancel_btn = tk.Button(btn_panel, text="✕  CANCEL", command=self._cancel_render,
                                    bg=THEME["error"], fg="#fff", font=("Segoe UI", 10, "bold"),
                                    activebackground="#ff6b7a", activeforeground="#fff",
                                    relief="flat", padx=16, pady=8, cursor="hand2")
        self.cancel_btn.pack(fill="x", pady=(4, 0))
        self.cancel_btn.configure(state="disabled")

        # ─── Progress ───
        progress_frame = tk.Frame(left, bg=THEME["bg"])
        progress_frame.pack(fill="x")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
                                           mode="determinate", length=200)
        self.progress_bar.pack(fill="x")
        self.progress_label = tk.Label(progress_frame, text="Ready", font=("Segoe UI", 8),
                                       bg=THEME["bg"], fg=THEME["text_dim"])
        self.progress_label.pack(anchor="e", pady=(2, 0))

        # ─── Right Panel — Log ───
        right = ttk.Frame(content, padding=10)
        right.pack(side="right", fill="both", expand=True)

        log_frame = ttk.LabelFrame(right, text=" 📋 Output Log ", padding=8)
        log_frame.pack(fill="both", expand=True)

        # Log text widget with scrollbar
        log_container = tk.Frame(log_frame, bg=THEME["bg"])
        log_container.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_container, bg=THEME["entry_bg"], fg=THEME["text"],
                               font=("Consolas", 9), wrap="word", relief="flat",
                               insertbackground=THEME["accent"], state="disabled",
                               padx=10, pady=10, selectbackground=THEME["accent2"],
                               selectforeground="#fff")
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_container, orient="vertical",
                                 command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # Log controls
        log_ctrl = tk.Frame(log_frame, bg=THEME["bg"])
        log_ctrl.pack(fill="x", pady=(6, 0))
        tk.Button(log_ctrl, text="Clear Log", command=self._clear_log,
                 bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 8),
                 activebackground=THEME["button_hover"], activeforeground=THEME["text"],
                 relief="flat", padx=12, pady=3, cursor="hand2").pack(side="left")
        tk.Button(log_ctrl, text="Save Log", command=self._save_log,
                 bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 8),
                 activebackground=THEME["button_hover"], activeforeground=THEME["text"],
                 relief="flat", padx=12, pady=3, cursor="hand2").pack(side="right")

    # ─── Event Handlers ───
    def _on_fps_change(self, val):
        self.fps_label.configure(text=str(int(float(val))))

    def _on_dur_change(self, val):
        self.dur_label.configure(text=f"{int(float(val))}s")

    def _browse_html(self):
        path = filedialog.askopenfilename(
            title="Select HTML File",
            filetypes=[("HTML Files", "*.html"), ("All Files", "*.*")]
        )
        if path:
            self.html_path_var.set(path)
            # Show file info
            size = os.path.getsize(path)
            if size > 1024 * 1024:
                size_str = f"{size / 1024 / 1024:.1f} MB"
            else:
                size_str = f"{size / 1024:.0f} KB"
            self.html_info.configure(text=f"{os.path.basename(path)} ({size_str})")

            # Extract preview
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    html_content = f.read()
                extractor = HTMLTextExtractor()
                extractor.feed(html_content)
                lines = extractor.get_text()
                self.log(f"HTML loaded: {os.path.basename(path)} — {len(lines)} text lines extracted", "info")
            except Exception as e:
                self.log(f"Could not parse HTML: {e}", "warning")

    def _browse_outdir(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.outdir_var.set(path)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            title="Save Log",
            defaultextension=".log",
            filetypes=[("Log Files", "*.log"), ("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_text.get("1.0", "end"))
            self.log(f"Log saved: {path}", "success")

    # ─── Logging ───
    def log(self, message, level="info"):
        """Append message to log with timestamp and color."""
        colors = {
            "info": THEME["text"],
            "success": THEME["success"],
            "warning": THEME["warning"],
            "error": THEME["error"],
        }
        color = colors.get(level, THEME["text"])
        timestamp = datetime.now().strftime("%H:%M:%S")

        self.log_text.configure(state="normal")

        # Timestamp tag
        ts_tag = f"ts_{timestamp}"
        self.log_text.tag_configure(ts_tag, foreground=THEME["text_dim"])
        self.log_text.insert("end", f"[{timestamp}] ", ts_tag)

        # Message tag
        msg_tag = f"msg_{timestamp}"
        self.log_text.tag_configure(msg_tag, foreground=color)
        self.log_text.insert("end", f"{message}\n", msg_tag)

        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.root.update_idletasks()

    # ─── FFmpeg Version ───
    def _get_ffmpeg_version(self):
        try:
            if not self.ffmpeg_path:
                self.log("FFmpeg executable not found; skipping version query", "warning")
                return
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
            version_line = result.stdout.split("\n")[0] if result.stdout else "Unknown"
            self.log(f"FFmpeg: {version_line}", "info")
        except Exception as e:
            self.log(f"Could not query FFmpeg version: {e}", "warning")

    # ─── Video Generation ───
    def _generate_video(self):
        """Start video generation in a background thread."""
        html_path = self.html_path_var.get()

        if not html_path:
            messagebox.showwarning("No HTML File", "Please select an HTML file first.")
            return

        if not os.path.exists(html_path):
            messagebox.showerror("File Not Found", f"HTML file not found:\n{html_path}")
            return

        if not self.ffmpeg_path:
            messagebox.showerror("FFmpeg Not Found",
                                "FFmpeg is not installed.\n\n"
                                "Install it with:\n"
                                "  winget install Gyan.FFmpeg\n\n"
                                "Then restart CODEX Studio.")
            return

        if not HAS_PIL:
            messagebox.showerror("Pillow Not Installed",
                                "Pillow (PIL) is not installed.\n\n"
                                "Install it with:\n"
                                "  pip install Pillow\n\n"
                                "Then restart CODEX Studio.")
            return

        # Parse settings
        res_str = self.resolution_var.get()
        if "1280x720" in res_str:
            width, height = 1280, 720
        elif "3840x2160" in res_str:
            width, height = 3840, 2160
        else:
            width, height = 1920, 1080

        fps = self.fps_var.get()
        duration = self.duration_var.get()
        out_format = self.format_var.get()
        output_dir = self.outdir_var.get()
        os.makedirs(output_dir, exist_ok=True)

        # Generate output filename
        base_name = os.path.splitext(os.path.basename(html_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"{base_name}_{timestamp}.{out_format}"
        output_path = os.path.join(output_dir, output_name)

        # Disable UI during render
        self.is_rendering = True
        self.cancel_requested = False
        self.generate_btn.configure(state="disabled", text="⏳ RENDERING...")
        self.cancel_btn.configure(state="normal")
        self.progress_var.set(0)
        self.progress_label.configure(text="Starting...")

        self.log(f"{'═'*50}", "info")
        self.log("RENDER STARTED", "success")
        self.log(f"  Source:  {html_path}", "info")
        self.log(f"  Output:  {output_path}", "info")
        self.log(f"  Format:  {out_format.upper()}", "info")
        self.log(f"  Resolution: {width}×{height}", "info")
        self.log(f"  FPS:     {fps}", "info")
        self.log(f"  Duration: {duration}s", "info")
        self.log(f"  Frames:  {fps * duration}", "info")
        self.log(f"{'─'*50}", "info")

        # Start rendering in background thread
        thread = threading.Thread(
            target=self._render_thread,
            args=(html_path, output_path, width, height, fps, duration, out_format),
            daemon=True
        )
        thread.start()

    def _cancel_render(self):
        """Cancel the current render."""
        if self.is_rendering:
            self.cancel_requested = True
            self.log("CANCEL requested — finishing current frame...", "warning")
            self.cancel_btn.configure(state="disabled")

    def _render_thread(self, html_path, output_path, width, height, fps, duration, out_format):
        """Background thread: extract HTML content, render frames, encode video."""
        try:
            # Step 1: Extract text from HTML
            self._log_safe("Step 1/3: Extracting text from HTML...", "info")
            with open(html_path, "r", encoding="utf-8", errors="replace") as f:
                html_content = f.read()
            extractor = HTMLTextExtractor()
            extractor.feed(html_content)
            text_lines = extractor.get_text()

            if not text_lines:
                self._log_safe("HTML file contains no extractable text. Generating placeholder frames.", "warning")
                text_lines = [
                    "CODEX STUDIO — Video Generation Engine",
                    "",
                    f"Source: {os.path.basename(html_path)}",
                    f"Resolution: {width}×{height}",
                    f"FPS: {fps} | Duration: {duration}s",
                    f"Generated: {datetime.now().isoformat()}",
                ]

            self._log_safe(f"  {len(text_lines)} text lines extracted", "info")

            # Step 2: Render frames
            self._log_safe("Step 2/3: Rendering frames...", "info")
            total_frames = fps * duration
            frames_dir = os.path.join(os.path.dirname(output_path), "_frames_temp")
            if os.path.exists(frames_dir):
                shutil.rmtree(frames_dir)
            os.makedirs(frames_dir, exist_ok=True)

            if self.scene_mode.get():
                self._render_scenes_thread(html_content, frames_dir, width, height, fps, total_frames)
            else:
                self._render_frames(text_lines, frames_dir, width, height, fps, total_frames)

            if self.cancel_requested:
                self._log_safe("RENDER CANCELLED", "warning")
                self._finish_render()
                shutil.rmtree(frames_dir, ignore_errors=True)
                return

            # Step 3: Encode video
            self._log_safe("Step 3/3: Encoding video with FFmpeg...", "info")
            self._log_safe(f"  Command: ffmpeg -framerate {fps} -i frame_%06d.png ...", "info")

            success = self._encode_video(frames_dir, output_path, fps, out_format)

            # Cleanup
            shutil.rmtree(frames_dir, ignore_errors=True)

            if success:
                size_mb = os.path.getsize(output_path) / 1024 / 1024
                self._log_safe(f"{'═'*50}", "info")
                self._log_safe("RENDER COMPLETE!", "success")
                self._log_safe(f"  Output: {output_path}", "success")
                self._log_safe(f"  Size:   {size_mb:.1f} MB", "success")
                self._log_safe(f"  Frames: {total_frames}", "info")
                self._log_safe(f"{'═'*50}", "info")
                self.root.after(0, lambda: self.progress_label.configure(text="Complete!"))
                self.root.after(0, lambda: self.progress_var.set(100))
            else:
                self._log_safe("RENDER FAILED — See FFmpeg output above.", "error")

        except Exception as e:
            note_failure("CODEX_Studio:L1087", e)
            self._log_safe(f"RENDER ERROR: {e}", "error")
            import traceback
            self._log_safe(traceback.format_exc(), "error")
            # Clean up temp frames on unexpected errors
            try:
                frames_dir = os.path.join(os.path.dirname(output_path), "_frames_temp")
                if os.path.exists(frames_dir):
                    shutil.rmtree(frames_dir, ignore_errors=True)
                    self._log_safe("  Temp frames cleaned up after error.", "info")
            except Exception as exc:
                note_failure("CODEX_Studio:L1097", exc)
        finally:
            self._finish_render()

    def _render_scenes_thread(self, html_content, frames_dir, width, height, fps, total_frames):
        """Parse HTML into scenes and render with crossfade transitions."""
        self._log_safe("Scene mode: parsing HTML for sections...", "info")
        extractor = HTMLSceneExtractor()
        extractor.raw_html = html_content
        extractor.feed(html_content)
        scenes = extractor.get_scenes()
        self._log_safe(f"  {len(scenes)} scene(s) detected", "info")
        for i, s in enumerate(scenes):
            self._log_safe(f"  Scene {i+1}: '{s['title']}' ({len(s['lines'])} lines)", "info")

        transition_frames = fps * 2  # 2-second crossfade

        self.root.after(0, lambda: self.progress_label.configure(text="Rendering scenes..."))

        def progress_cb(frame_idx, _total):
            pct = int((frame_idx / total_frames) * 90) if total_frames > 1 else 0
            self.root.after(0, lambda p=pct: self.progress_var.set(p))
            if frame_idx % (fps * 5) == 0:
                elapsed = frame_idx / fps
                self.root.after(0, lambda e=elapsed:
                    self.progress_label.configure(
                        text=f"Frame {frame_idx}/{total_frames} ({e:.0f}s)"))

        render_scenes_to_png(
            scenes, frames_dir, width, height, fps, total_frames,
            transition_frames=transition_frames,
            cancel_check=lambda: self.cancel_requested,
            progress_callback=progress_cb,
        )

    def _render_frames(self, text_lines, frames_dir, width, height, fps, total_frames):
        """Render each frame using PIL with scrolling text effect (GUI mode)."""
        self.root.after(0, lambda: self.progress_label.configure(text="Rendering frames..."))

        def progress_cb(frame_idx, _total):
            pct = int((frame_idx / total_frames) * 90) if total_frames > 1 else 0
            self.root.after(0, lambda p=pct: self.progress_var.set(p))
            if frame_idx % (fps * 5) == 0:
                elapsed = frame_idx / fps
                self.root.after(0, lambda e=elapsed:
                    self.progress_label.configure(
                        text=f"Frame {frame_idx}/{total_frames} ({e:.0f}s / {total_frames / fps:.0f}s)"))

        render_frames_to_png(
            text_lines, frames_dir, width, height, fps, total_frames,
            cancel_check=lambda: self.cancel_requested,
            progress_callback=progress_cb,
        )

    def _encode_video(self, frames_dir, output_path, fps, out_format):
        """Encode frames to video using FFmpeg."""
        if out_format == "wmv":
            # WMV encoding
            cmd = [
                self.ffmpeg_path, "-y",
                "-framerate", str(fps),
                "-i", os.path.join(frames_dir, "frame_%06d.png"),
                "-c:v", "wmv2",
                "-q:v", "2",
                output_path
            ]
        else:
            # MP4 encoding with H.264 High profile (CAIN standard)
            # Rejects Constrained Baseline — needs B-frames for efficient compression
            cmd = [
                self.ffmpeg_path, "-y",
                "-framerate", str(fps),
                "-i", os.path.join(frames_dir, "frame_%06d.png"),
                "-c:v", "libx264",
                "-profile:v", CAIN_PROFILE,
                "-preset", "medium",
                "-crf", str(CAIN_CRF),
                "-g", str(CAIN_GOP),
                "-pix_fmt", "yuv420p",
                output_path
            ]

        self._log_safe(f"  Running: {' '.join(cmd[:6])} ...", "info")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)

            if result.returncode == 0:
                self._log_safe("  FFmpeg encoding successful.", "success")
                return True
            else:
                self._log_safe(f"  FFmpeg error (code {result.returncode}):", "error")
                for line in result.stderr.split("\n")[-10:]:
                    if line.strip():
                        self._log_safe(f"    {line.strip()}", "error")
                return False
        except subprocess.TimeoutExpired as exc:
            note_failure("CODEX_Studio:L1194", exc)
            self._log_safe("  FFmpeg encoding timed out (>1 hour)", "error")
            return False
        except Exception as e:
            note_failure("CODEX_Studio:L1197", e)
            self._log_safe(f"  FFmpeg error: {e}", "error")
            return False

    def _log_safe(self, message, level="info"):
        """Thread-safe logging."""
        self.root.after(0, lambda: self.log(message, level))

    def _finish_render(self):
        """Re-enable UI after render completes."""
        self.is_rendering = False
        self.root.after(0, lambda: self.generate_btn.configure(
            state="normal", text="▶  GENERATE VIDEO"))
        self.root.after(0, lambda: self.cancel_btn.configure(state="disabled"))


# ─── CLI Mode ───
def cli_mode():
    """Command-line interface for scripting/automation."""

    parser = argparse.ArgumentParser(
        description="CODEX Studio — HTML to Video Generator (CLI Mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python CODEX_Studio.py --cli --input page.html --output video.mp4
  python CODEX_Studio.py --cli --input page.html --output video.wmv --fps 60 --duration 90
  python CODEX_Studio.py --cli --input page.html --output out.mp4 --resolution 3840x2160

CAIN Standard: 25 fps @ 1920x1080, H.264 High, CRF 23, keyframe every 250 frames
        """
    )
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode")
    parser.add_argument("--input", "-i", required=True, help="Input HTML file")
    parser.add_argument("--output", "-o", required=True, help="Output video file (.mp4 or .wmv)")
    parser.add_argument("--fps", type=int, default=CAIN_FPS, help=f"Frames per second (default: {CAIN_FPS} — CAIN standard)")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds (default: 60)")
    parser.add_argument("--resolution", default="1920x1080",
                       help="Resolution WxH (default: 1920x1080)")

    parser.add_argument("--scenes", action="store_true",
                       help="Enable scene detection mode (multi-scene with transitions)")

    args = parser.parse_args()

    if not args.cli:
        return False

    print(f"\n  CODEX Studio CLI v{APP_VERSION}")
    print(f"  {'-'*40}")

    # Validate
    if not os.path.exists(args.input):
        print(f"  ERROR: Input file not found: {args.input}")
        sys.exit(1)

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("  ERROR: FFmpeg not found. Install with: winget install Gyan.FFmpeg")
        sys.exit(1)

    if not HAS_PIL:
        print("  ERROR: Pillow not installed. Install with: pip install Pillow")
        sys.exit(1)

    # Parse resolution
    try:
        w, h = map(int, args.resolution.lower().replace("x", " ").split())
    except Exception as exc:
        note_failure("CODEX_Studio:L1266", exc)
        w, h = 1920, 1080

    out_format = "wmv" if args.output.lower().endswith(".wmv") else "mp4"
    total_frames = args.fps * args.duration

    print(f"  Source:     {args.input}")
    print(f"  Output:     {args.output}")
    print(f"  Format:     {out_format.upper()}")
    print(f"  Resolution: {w}×{h}")
    print(f"  FPS:        {args.fps}")
    print(f"  Duration:   {args.duration}s")
    print(f"  Frames:     {total_frames}")
    print(f"  FFmpeg:     {ffmpeg_path}")
    print(f"  {'-'*40}")

    # Extract text
    print("  [1/3] Extracting text from HTML...")
    with open(args.input, "r", encoding="utf-8", errors="replace") as f:
        html_content = f.read()
    extractor = HTMLTextExtractor()
    extractor.feed(html_content)
    text_lines = extractor.get_text()
    if not text_lines:
        text_lines = [f"CODEX Studio — {os.path.basename(args.input)}"]
    print(f"         {len(text_lines)} text lines extracted")

    # Render frames using shared renderer (consistent output with GUI)
    print("  [2/3] Rendering frames...")
    frames_dir = os.path.join(os.path.dirname(args.output) or ".", "_frames_temp_cli")
    if os.path.exists(frames_dir):
        shutil.rmtree(frames_dir)
    os.makedirs(frames_dir, exist_ok=True)

    def cli_progress(frame_idx, total):
        if frame_idx % (args.fps * 5) == 0:
            print(f"         Frame {frame_idx}/{total} ({frame_idx/args.fps:.0f}s)")

    if args.scenes:
        print("         Scene mode: parsing HTML for sections...")
        extractor = HTMLSceneExtractor()
        extractor.raw_html = html_content
        extractor.feed(html_content)
        scenes = extractor.get_scenes()
        print(f"         {len(scenes)} scene(s) detected")
        for i, s in enumerate(scenes):
            print(f"         Scene {i+1}: '{s['title']}' ({len(s['lines'])} lines)")
        render_scenes_to_png(
            scenes, frames_dir, w, h, args.fps, total_frames,
            transition_frames=args.fps * 2,
            progress_callback=cli_progress,
        )
    else:
        render_frames_to_png(
            text_lines, frames_dir, w, h, args.fps, total_frames,
            progress_callback=cli_progress,
        )

    # Encode
    print("  [3/3] Encoding video with FFmpeg...")
    if out_format == "wmv":
        cmd = [ffmpeg_path, "-y", "-framerate", str(args.fps),
               "-i", os.path.join(frames_dir, "frame_%06d.png"),
               "-c:v", "wmv2", "-q:v", "2", args.output]
    else:
        cmd = [ffmpeg_path, "-y", "-framerate", str(args.fps),
               "-i", os.path.join(frames_dir, "frame_%06d.png"),
               "-c:v", "libx264",
               "-profile:v", CAIN_PROFILE,
               "-preset", "medium",
               "-crf", str(CAIN_CRF),
               "-g", str(CAIN_GOP),
               "-pix_fmt", "yuv420p", args.output]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)

    shutil.rmtree(frames_dir, ignore_errors=True)

    if result.returncode == 0:
        size_mb = os.path.getsize(args.output) / 1024 / 1024
        print(f"  {'-'*40}")
        print("  [OK] RENDER COMPLETE!")
        print(f"    Output: {args.output}")
        print(f"    Size:   {size_mb:.1f} MB")
        print(f"    Frames: {total_frames}")
        return True
    else:
        print(f"  [FAIL] FFmpeg encoding failed (code {result.returncode})")
        for line in result.stderr.split("\n")[-5:]:
            if line.strip():
                print(f"    {line.strip()}")
        return False


# ─── Entry Point ───
def main():
    """Launch CODEX Studio GUI or CLI mode."""
    if "--cli" in sys.argv:
        cli_mode()
    else:
        root = tk.Tk()

        # Set icon (if available)
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "codex_icon.ico")
            if os.path.exists(icon_path):
                root.iconbitmap(icon_path)
        except Exception as exc:
            note_failure("CODEX_Studio:L1373", exc)

        CODEXStudio(root)
        root.mainloop()


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
