#!/usr/bin/env python3
"""
AZAZAEL Video Frame Generator
==============================
Generates video frames with bitmap-rendered text and pipes them to ffmpeg.
Pure Python stdlib + ffmpeg — no fontconfig, no PIL, no C extensions.
"""

import os
import sys
import json
import math
import struct
import subprocess
from pathlib import Path

# Add parent for bitmap_font import
sys.path.insert(0, str(Path(__file__).parent))
from bitmap_font import (
    render_text, draw_rects_to_frame, draw_grid, draw_horizontal_gradient,
    text_size, CHAR_W, CHAR_H, CELL_W, CELL_H, SPACING,
    CYAN, PURPLE, PINK, GREEN, GOLD, RED, WHITE, YELLOW, MAGENTA, ORANGE, GRAY,
    DARK_BG, HEADER_BG,
)


def animate_fade_in(frame_num, start_frame, duration_frames):
    """Alpha value for fade-in animation."""
    if frame_num < start_frame:
        return 0.0
    if frame_num >= start_frame + duration_frames:
        return 1.0
    t = (frame_num - start_frame) / duration_frames
    # Ease out cubic
    return 1.0 - (1.0 - t) ** 3


def draw_text_line(frame_w, frame_h, lines, frame_num, fps):
    """
    Draw multiple lines of bitmap text with fade-in animation.
    
    Args:
        lines: List of (text, y, scale, color, delay_frames) tuples
        frame_num: Current frame number
        fps: Frames per second
    """
    all_rects = []
    
    for text, y, scale, color, delay_sec in lines:
        delay_frames = int(delay_sec * fps)
        fade_dur = int(0.8 * fps)  # 0.8 second fade
        alpha = animate_fade_in(frame_num, delay_frames, fade_dur)
        
        if alpha <= 0:
            continue
        
        w, h, rects = render_text(text, scale=scale, colors=color)
        x = (frame_w - w) // 2
        
        # Apply alpha to color and offset
        for (rx, ry, rw, rh, rr, rg, rb) in rects:
            all_rects.append((x + rx, y + ry, rw, rh,
                            int(rr * alpha), int(rg * alpha), int(rb * alpha)))
    
    return all_rects


def generate_dissertation_video(output_path):
    """Generate the comprehensive dissertation video."""
    w, h = 1280, 720
    fps = 30
    total_duration = 45  # seconds
    total_frames = total_duration * fps
    
    # Scene plan: lines with (text, y_pos, scale, color, delay_seconds)
    scenes = [
        # Scene 0: Title (0-6s)
        {
            "start_sec": 0, "end_sec": 6,
            "lines": [
                ("AZAZAEL 3.14t", 90, 4, WHITE, 0.5),
                ("Free-Threaded Agent Ecosystem", 150, 2, CYAN, 1.2),
                ("Comprehensive Functional Dissertation", 200, 1, GRAY, 2.0),
            ],
            "accent_color": PURPLE,
        },
        # Scene 1: Tier 1 - Worms (6-13s)
        {
            "start_sec": 6, "end_sec": 13,
            "lines": [
                ("TIER 1: THE WORMS", 80, 3, YELLOW, 0.3),
                ("Autonomous parallel agents with free-threaded execution", 150, 1, WHITE, 1.0),
                ("37 standalone .exe builds | 52 batch launchers", 200, 1, GREEN, 1.5),
                ("Self-healing | Circuit breakers | Dead letter queues", 250, 1, CYAN, 2.0),
                ("Module: CORE/ | Orchestrator: HYDRA | Cluster: CLOCKZ", 300, 1, GRAY, 2.5),
            ],
            "accent_color": GREEN,
        },
        # Scene 2: Concurrency Engine (13-20s)
        {
            "start_sec": 13, "end_sec": 20,
            "lines": [
                ("TIER 2: CONCURRENCY ENGINE", 80, 3, MAGENTA, 0.3),
                ("Python 3.14.5 Free-Threading — GIL Disabled", 150, 1, WHITE, 1.0),
                ("ThreadPoolExecutor | AsyncIO | Subprocess Orchestration", 200, 1, CYAN, 1.5),
                ("CONCURRENCY.PY: 823 lines of thread-safe execution", 250, 1, GREEN, 2.0),
                ("Atomic primitives | Lock-free queues | Resource pools", 300, 1, GRAY, 2.5),
            ],
            "accent_color": MAGENTA,
        },
        # Scene 3: Inference Studio (20-28s)
        {
            "start_sec": 20, "end_sec": 28,
            "lines": [
                ("TIER 3: INFERENCE STUDIO v5.0", 80, 3, CYAN, 0.3),
                ("7-Tab Professional GUI | 11.2 MB Standalone .exe", 150, 1, WHITE, 1.0),
                ("15 Framework Backends | Hardware Detection via ctypes", 200, 1, YELLOW, 1.5),
                ("Ollama API Integration | 6 Local Models Active", 250, 1, GREEN, 2.0),
                ("Morphing Emotion Engine | Pulsating HUD | Thread-safe UI", 300, 1, MAGENTA, 2.5),
            ],
            "accent_color": CYAN,
        },
        # Scene 4: Communication Layer (28-35s)
        {
            "start_sec": 28, "end_sec": 35,
            "lines": [
                ("TIER 4: COMMUNICATION LAYER", 80, 3, GOLD, 0.3),
                ("ZMQ + Protocol Buffers + Named Pipes + HTTP", 150, 1, WHITE, 1.0),
                ("Message routing | Priority queues | Encryption", 200, 1, CYAN, 1.5),
                ("ERRORZ.PY: Intelligent error classification & recovery", 250, 1, RED, 2.0),
                ("TRIMZ.PY: Automated log rotation & context truncation", 300, 1, GRAY, 2.5),
            ],
            "accent_color": GOLD,
        },
        # Scene 5: Testing & Verification (35-42s)
        {
            "start_sec": 35, "end_sec": 42,
            "lines": [
                ("TESTING & VERIFICATION", 80, 3, GREEN, 0.3),
                ("14/14 Unit Tests Passing (Worm Imports)", 150, 1, WHITE, 1.0),
                ("5/5 Cluster Worm Integration Tests Green", 200, 1, GREEN, 1.5),
                ("Real Inference Benchmarks on Ollama API", 250, 1, CYAN, 2.0),
                ("llama3.2:1b ~1s response | NumPy 30.8ms matmul", 300, 1, YELLOW, 2.5),
            ],
            "accent_color": GREEN,
        },
        # Scene 6: Conclusion (42-45s)
        {
            "start_sec": 42, "end_sec": 45,
            "lines": [
                ("AZAZAEL — OPERATIONAL", 120, 5, WHITE, 0.2),
                ("Free-Threaded AI Agent Ecosystem | Python 3.14t", 220, 2, CYAN, 0.8),
            ],
            "accent_color": PURPLE,
        },
    ]
    
    # Build frame data
    print(f"  Generating {total_frames} frames for dissertation video...")
    
    pipe_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{w}x{h}",
        "-r", str(fps),
        "-i", "-",  # stdin
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        str(output_path)
    ]
    
    proc = subprocess.Popen(
        pipe_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    frame_size = w * h * 3
    
    for frame_num in range(total_frames):
        t = frame_num / fps
        
        # Determine current scene
        current_scene = None
        for scene in scenes:
            if scene["start_sec"] <= t < scene["end_sec"]:
                current_scene = scene
                break
        if current_scene is None:
            current_scene = scenes[-1]
        
        # Build frame
        bg = bytearray(frame_size)
        
        # Gradient background accent
        accent = current_scene.get("accent_color", PURPLE)
        r, g, b = accent
        r_bg, g_bg, b_bg = DARK_BG
        
        for y_px in range(h):
            row_start = y_px * w * 3
            for x_px in range(w):
                idx = row_start + x_px * 3
                # Subtle vertical gradient mixing accent color
                mix = (y_px / h) * 0.05  # 5% accent at bottom
                bg[idx] = int(r_bg + (r - r_bg) * mix)
                bg[idx + 1] = int(g_bg + (g - g_bg) * mix)
                bg[idx + 2] = int(b_bg + (b - b_bg) * mix)
        
        # Header bar
        for y_px in range(65):
            row_start = y_px * w * 3
            for x_px in range(w):
                idx = row_start + x_px * 3
                bg[idx] = (HEADER_BG[0] + bg[idx]) // 2
                bg[idx + 1] = (HEADER_BG[1] + bg[idx + 1]) // 2
                bg[idx + 2] = (HEADER_BG[2] + bg[idx + 2]) // 2
        
        # Grid
        for x_px in range(0, w, 50):
            for y_px in range(h):
                idx = (y_px * w + x_px) * 3
                bg[idx] = min(255, bg[idx] + 5)
                bg[idx + 1] = min(255, bg[idx + 1] + 5)
                bg[idx + 2] = min(255, bg[idx + 2] + 5)
        for y_px in range(0, h, 50):
            for x_px in range(w):
                idx = (y_px * w + x_px) * 3
                bg[idx] = min(255, bg[idx] + 5)
                bg[idx + 1] = min(255, bg[idx + 1] + 5)
                bg[idx + 2] = min(255, bg[idx + 2] + 5)
        
        # Draw text
        frame_bytes = bytes(bg)
        rects = draw_text_line(w, h, current_scene["lines"], frame_num, fps)
        frame_bytes = draw_rects_to_frame(w, h, rects, DARK_BG)
        
        # Merge: draw text rects onto gradient bg
        result = bytearray(bg)
        for (rx, ry, rw, rh, rr, rg, rb) in rects:
            if rx + rw <= 0 or ry + rh <= 0 or rx >= w or ry >= h:
                continue
            x_start = max(0, rx)
            y_start = max(0, ry)
            x_end = min(w, rx + rw)
            y_end = min(h, ry + rh)
            for y_px in range(y_start, y_end):
                row_start = y_px * w * 3
                for x_px in range(x_start, x_end):
                    idx = row_start + x_px * 3
                    result[idx] = rr
                    result[idx + 1] = rg
                    result[idx + 2] = rb
        
        proc.stdin.write(bytes(result))
        
        if frame_num % (fps * 2) == 0:
            print(f"    Frame {frame_num}/{total_frames} ({100*frame_num//total_frames}%)")
    
    proc.stdin.close()
    proc.wait(timeout=120)
    
    if os.path.exists(output_path):
        sz = os.path.getsize(output_path) / (1024 * 1024)
        print(f"    SUCCESS: {sz:.1f} MB")
        return True
    return False

def generate_template_video(template_path, output_path):
    """Generate a video from a JSON template using bitmap font frames."""
    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = json.load(f)
    
    w, h = tmpl["resolution"]
    fps = tmpl.get("fps", 30)
    duration = tmpl["duration"]
    
    name = tmpl.get("name", "Untitled")
    sections = tmpl.get("sections", [])
    accent = tmpl.get("accent_color", "0x00f5ff")
    
    # Parse accent color from hex
    hex_c = accent.replace("0x", "").replace("#", "")
    r_accent = int(hex_c[0:2], 16) if len(hex_c) >= 2 else 0
    g_accent = int(hex_c[2:4], 16) if len(hex_c) >= 4 else 245
    b_accent = int(hex_c[4:6], 16) if len(hex_c) >= 6 else 255
    accent_rgb = (r_accent, g_accent, b_accent)
    
    color_map = {
        "white": WHITE, "cyan": CYAN, "purple": PURPLE, "pink": PINK,
        "green": GREEN, "gold": GOLD, "red": RED, "yellow": YELLOW,
        "magenta": MAGENTA, "orange": ORANGE, "gray": GRAY,
    }
    
    # Build all text lines (full opacity, no fade)
    lines = [(name.upper(), 80, 3, WHITE)]
    for i, sec in enumerate(sections):
        y_title = 160 + i * 120
        y_sub = y_title + 40
        if y_title < h - 51:
            tc = color_map.get(sec.get("title_color", "white"), WHITE)
            lines.append((sec["title"], y_title, 2, tc))
        if y_sub < h - 51:
            sc = color_map.get(sec.get("subtitle_color", "cyan"), CYAN)
            lines.append((sec["subtitle"], y_sub, 1, sc))
    
    # Render all text rects once
    all_rects = []
    for text, y, scale, color in lines:
        _, _, rects = render_text(text, scale=scale, colors=color)
        w_text = text_size(text, scale)[0]
        x_offset = (w - w_text) // 2
        for (rx, ry, rw, rh, rr, rg, rb) in rects:
            all_rects.append((x_offset + rx, y + ry, rw, rh, rr, rg, rb))
    
    # Generate one master frame
    print(f"  Generating {name}: rendering one frame ({w}x{h})...")
    
    # Build background
    bg_frame = bytearray(w * h * 3)
    for y_px in range(h):
        row_start = y_px * w * 3
        for x_px in range(w):
            idx = row_start + x_px * 3
            bg_frame[idx] = DARK_BG[0]
            bg_frame[idx + 1] = DARK_BG[1]
            bg_frame[idx + 2] = DARK_BG[2]
    
    # Header bar
    for y_px in range(65):
        row_start = y_px * w * 3
        for x_px in range(w):
            idx = row_start + x_px * 3
            bg_frame[idx] = (HEADER_BG[0] + DARK_BG[0]) // 2
            bg_frame[idx + 1] = (HEADER_BG[1] + DARK_BG[1]) // 2
            bg_frame[idx + 2] = (HEADER_BG[2] + DARK_BG[2]) // 2
    
    # Grid
    for x_px in range(0, w, 50):
        for y_px in range(h):
            idx = (y_px * w + x_px) * 3
            bg_frame[idx] = min(255, bg_frame[idx] + 5)
            bg_frame[idx + 1] = min(255, bg_frame[idx + 1] + 5)
            bg_frame[idx + 2] = min(255, bg_frame[idx + 2] + 5)
    
    # Draw text rects
    for (rx, ry, rw, rh, rr, rg, rb) in all_rects:
        if rx + rw <= 0 or ry + rh <= 0 or rx >= w or ry >= h:
            continue
        for y_px in range(ry, ry + rh):
            if y_px < 0 or y_px >= h:
                continue
            row_start = y_px * w * 3
            for x_px in range(rx, rx + rw):
                if x_px < 0 or x_px >= w:
                    continue
                idx = row_start + x_px * 3
                bg_frame[idx] = rr
                bg_frame[idx + 1] = rg
                bg_frame[idx + 2] = rb
    
    # Write as PPM then use ffmpeg image2pipe or single-image loop
    ppm_path = str(output_path).replace('.mp4', '_frame.ppm')
    with open(ppm_path, 'wb') as pf:
        pf.write(f"P6\n{w} {h}\n255\n".encode())
        pf.write(bytes(bg_frame))
    
    # Use ffmpeg to loop the frame with fade-in
    print(f"  Encoding: looping frame for {duration}s...")
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", ppm_path,
        "-vf", f"fade=in:0:30,fps={fps}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "ultrafast", "-crf", "28",
        "-t", str(duration),
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    
    # Clean up PPM
    try:
        os.remove(ppm_path)
    except OSError:
        pass  # best-effort cleanup: file may already be gone or locked
    
    if os.path.exists(output_path):
        sz = os.path.getsize(output_path) / (1024 * 1024)
        print(f"    SUCCESS: {sz:.1f} MB")
        return True
    print(f"    FAILED: {result.stderr[-200:] if result.stderr else 'no output'}")
    return False


def convert_to_wmv(mp4_path):
    """Convert .mp4 to .wmv."""
    wmv_path = mp4_path.with_suffix(".wmv")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp4_path),
        "-c:v", "wmv2", "-c:a", "wmav2",
        "-q:v", "3",
        str(wmv_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    if result.returncode == 0 and wmv_path.exists():
        sz = os.path.getsize(wmv_path) / (1024 * 1024)
        print(f"    WMV: {sz:.1f} MB")
        return wmv_path
    return None


# For testing
if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_video.mp4"
    generate_dissertation_video(out)