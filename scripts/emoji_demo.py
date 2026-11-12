#!/usr/bin/env python3
"""Emoji Maker quick demo — prints emotion strings, reaction emojis, and library stats."""

import sys
from pathlib import Path

# Windows consoles default to cp1252 and crash on emoji output;
# force UTF-8 so the demo runs everywhere (2026-10-01).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ensure src/ is importable
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from CODEX.CORE.emoji_maker import EmojiMaker, Emotion

m = EmojiMaker()

print("=== Emotion Strings ===")
for e in Emotion:
    print(f"  {e.name:<12} {m.emotion_string(e, 0.8)}")

print()
print("=== Reactions ===")
for reaction in ['success', 'error', 'warning', 'info', 'love', 'thinking', 'celebration', 'danger']:
    r = m.reaction_emoji(reaction, 0.7)
    if r:
        print(f"  {reaction:<14} {r.char}  {r.name}")

print()
print(f"Library: {len(m.library)} emojis")
print(f"Emotions: {len(m.by_emotion)}")
print(f"Moods: {len(m.by_mood)}")
print(f"Categories: {len(m.by_category)}")
