#!/usr/bin/env python3
"""
Pure-Python Bitmap Font Renderer
================================
Renders text as colored blocks on raw RGB frames.
No C extensions, no fontconfig, no PIL — just pure Python stdlib.

Each character is a 5x7 bitmap (plus 1px spacing = 6x8 cell).
Supports uppercase A-Z, 0-9, and basic punctuation.
"""

import struct
import math

# ─── 5x7 Bitmap Font (ASCII 32-90) ──────────────────────────────
# Each character is 7 rows of 5 bits, stored as 7 integers.
# Bits: MSB left, LSB right. 1 = filled, 0 = empty.

FONT_5X7 = {
    # Numbers 0-9
    '0': [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
    '1': [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    '2': [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
    '3': [0b01110, 0b10001, 0b00001, 0b00110, 0b00001, 0b10001, 0b01110],
    '4': [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    '5': [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
    '6': [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    '7': [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    '8': [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    '9': [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],

    # Uppercase A-Z
    'A': [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    'B': [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
    'C': [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
    'D': [0b11100, 0b10010, 0b10001, 0b10001, 0b10001, 0b10010, 0b11100],
    'E': [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
    'F': [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
    'G': [0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110],
    'H': [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    'I': [0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    'J': [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
    'K': [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
    'L': [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
    'M': [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
    'N': [0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001],
    'O': [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    'P': [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
    'Q': [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
    'R': [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
    'S': [0b01110, 0b10001, 0b10000, 0b01110, 0b00001, 0b10001, 0b01110],
    'T': [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
    'U': [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    'V': [0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b01010, 0b00100],
    'W': [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
    'X': [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
    'Y': [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
    'Z': [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],

    # Punctuation
    ' ': [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000],
    '.': [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100],
    ',': [0b00000, 0b00000, 0b00000, 0b00000, 0b00100, 0b01100, 0b01000],
    ':': [0b00000, 0b01100, 0b01100, 0b00000, 0b01100, 0b01100, 0b00000],
    '-': [0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000],
    '/': [0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000],
    '>': [0b01000, 0b00100, 0b00010, 0b00001, 0b00010, 0b00100, 0b01000],
    '(': [0b00010, 0b00100, 0b01000, 0b01000, 0b01000, 0b00100, 0b00010],
    ')': [0b01000, 0b00100, 0b00010, 0b00010, 0b00010, 0b00100, 0b01000],
    '!': [0b01100, 0b01100, 0b01100, 0b01100, 0b00000, 0b01100, 0b01100],
    '[': [0b01110, 0b01000, 0b01000, 0b01000, 0b01000, 0b01000, 0b01110],
    ']': [0b01110, 0b00010, 0b00010, 0b00010, 0b00010, 0b00010, 0b01110],
    '+': [0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000],
    '=': [0b00000, 0b00000, 0b11111, 0b00000, 0b11111, 0b00000, 0b00000],
    '#': [0b01010, 0b01010, 0b11111, 0b01010, 0b11111, 0b01010, 0b01010],
    '_': [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b11111],
}

CHAR_W = 5   # pixels wide
CHAR_H = 7   # pixels tall
SPACING = 1  # gap between characters
CELL_W = CHAR_W + SPACING  # 6
CELL_H = CHAR_H + SPACING  # 8


def get_bitmap(ch):
    """Get the 5x7 bitmap for a character. Returns list of 7 ints."""
    ch = ch.upper()
    if ch in FONT_5X7:
        return FONT_5X7[ch]
    # Unknown character: show as '?'
    return FONT_5X7.get('?', [0b01110, 0b10001, 0b00001, 0b00110, 0b00100, 0b00000, 0b00100])


def text_size(text, scale=1):
    """Calculate pixel width and height of rendered text."""
    w = len(text) * CELL_W - SPACING  # remove trailing spacing
    h = CELL_H
    return w * scale, h * scale


def render_char(char, scale, color, alpha=1.0):
    """
    Render a single character as list of (x, y, w, h, r, g, b) rectangles.
    Scale multiplies the pixel size.
    """
    bitmap = get_bitmap(char)
    rects = []
    for row in range(CHAR_H):
        row_val = bitmap[row]
        for col in range(CHAR_W):
            if row_val & (1 << (CHAR_W - 1 - col)):
                x = col * scale
                y = row * scale
                r, g, b = color
                rects.append((x, y, scale, scale, int(r * alpha), int(g * alpha), int(b * alpha)))
    return rects


def render_text(text, scale=1, colors=None):
    """
    Render a text string as pixel data.
    
    Args:
        text: String to render
        scale: Pixel multiplier (1 = tiny 5x7, 3 = readable 15x21)
        colors: List of (r,g,b) tuples, one per character, or single color for all
    
    Returns:
        (width, height, list of (x, y, w, h, r, g, b) rectangles)
    """
    if colors is None:
        colors = [(255, 255, 255)]
    
    if isinstance(colors, tuple) and len(colors) == 3 and isinstance(colors[0], int):
        colors = [colors] * len(text)
    
    total_w = 0
    all_rects = []
    
    for i, ch in enumerate(text):
        color = colors[i % len(colors)]
        char_bitmap = get_bitmap(ch)
        for row in range(CHAR_H):
            row_val = char_bitmap[row]
            for col in range(CHAR_W):
                if row_val & (1 << (CHAR_W - 1 - col)):
                    x = total_w + col * scale
                    y = row * scale
                    r, g, b = color
                    all_rects.append((x, y, scale, scale, r, g, b))
        total_w += CELL_W * scale
    
    h = CELL_H * scale
    w = total_w - SPACING * scale  # trailing spacing
    return w, h, all_rects


def draw_rects_to_frame(frame_w, frame_h, rects, bg_color=(10, 10, 26)):
    """
    Draw rectangles onto a raw RGB24 byte array frame.
    Returns bytes of length frame_w * frame_h * 3.
    """
    pixel_count = frame_w * frame_h
    frame = bytearray(pixel_count * 3)
    
    # Fill background
    r_bg, g_bg, b_bg = bg_color
    for i in range(0, pixel_count * 3, 3):
        frame[i] = r_bg
        frame[i + 1] = g_bg
        frame[i + 2] = b_bg
    
    # Draw rectangles
    for (rx, ry, rw, rh, rr, rg, rb) in rects:
        # Clamp to frame
        if rx + rw <= 0 or ry + rh <= 0 or rx >= frame_w or ry >= frame_h:
            continue
        
        x_start = max(0, rx)
        y_start = max(0, ry)
        x_end = min(frame_w, rx + rw)
        y_end = min(frame_h, ry + rh)
        
        for y in range(y_start, y_end):
            row_offset = y * frame_w * 3
            for x in range(x_start, x_end):
                idx = row_offset + x * 3
                frame[idx] = rr
                frame[idx + 1] = rg
                frame[idx + 2] = rb
    
    return bytes(frame)


def draw_grid(frame_w, frame_h, grid_size=50, color=(0, 245, 255), alpha=0.02):
    """Add subtle grid lines to a frame bytearray (mutates in place)."""
    frame = bytearray(frame_w * frame_h * 3)
    r, g, b = color
    
    # Fill black first
    for i in range(0, len(frame), 3):
        frame[i] = 10
        frame[i + 1] = 10
        frame[i + 2] = 26
    
    # Grid lines
    a = int(alpha * 255)
    for x in range(0, frame_w, grid_size):
        for y in range(frame_h):
            idx = (y * frame_w + x) * 3
            frame[idx] = (frame[idx] * (255 - a) + r * a) // 255
            frame[idx + 1] = (frame[idx + 1] * (255 - a) + g * a) // 255
            frame[idx + 2] = (frame[idx + 2] * (255 - a) + b * a) // 255
    
    for y in range(0, frame_h, grid_size):
        for x in range(frame_w):
            idx = (y * frame_w + x) * 3
            frame[idx] = (frame[idx] * (255 - a) + r * a) // 255
            frame[idx + 1] = (frame[idx + 1] * (255 - a) + g * a) // 255
            frame[idx + 2] = (frame[idx + 2] * (255 - a) + b * a) // 255
    
    return bytes(frame)


def draw_horizontal_gradient(frame_w, frame_h, left_color, right_color):
    """Create a horizontal gradient frame."""
    frame = bytearray(frame_w * frame_h * 3)
    r1, g1, b1 = left_color
    r2, g2, b2 = right_color
    
    for x in range(frame_w):
        t = x / max(frame_w - 1, 1)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        for y in range(frame_h):
            idx = (y * frame_w + x) * 3
            frame[idx] = r
            frame[idx + 1] = g
            frame[idx + 2] = b
    
    return bytes(frame)


def frame_to_ppm_bytes(frame_w, frame_h, frame_data):
    """Convert raw RGB24 frame data to PPM P6 binary format."""
    header = f"P6\n{frame_w} {frame_h}\n255\n".encode()
    return header + frame_data


# ─── Color Palette ───────────────────────────────────────────────
CYAN = (0, 245, 255)
PURPLE = (191, 0, 255)
PINK = (255, 0, 170)
GREEN = (0, 255, 136)
GOLD = (255, 221, 0)
RED = (255, 71, 87)
WHITE = (255, 255, 255)
YELLOW = (255, 255, 0)
MAGENTA = (255, 0, 255)
ORANGE = (255, 136, 0)
GRAY = (102, 102, 102)
DARK_BG = (10, 10, 26)
HEADER_BG = (26, 26, 58)
