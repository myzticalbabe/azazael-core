#!/usr/bin/env python3
"""
Azazael Dynamic Emotional Emoji Generator
==========================================
Generates expressive emojis with programmable intensity levels,
mood transitions, and CSS animation mappings for rich UI feedback.

Categories: facial, gesture, object, nature, symbol
Emotions: happy, sad, angry, surprised, fearful, disgusted, neutral, loved, tired, determined
Moods: positive, negative, neutral, energetic, calm
"""

import random
from dataclasses import dataclass, field
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents
from enum import Enum
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Emotion & Mood Enums ---------------------------------------------------

class Emotion(Enum):
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    FEARFUL = "fearful"
    DISGUSTED = "disgusted"
    NEUTRAL = "neutral"
    LOVED = "loved"
    TIRED = "tired"
    DETERMINED = "determined"
    CURIOUS = "curious"
    PLAYFUL = "playful"
    RELIEVED = "relieved"
    CONFUSED = "confused"
    PROUD = "proud"


class Mood(Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    ENERGETIC = "energetic"
    CALM = "calm"


class Category(Enum):
    FACIAL = "facial"
    GESTURE = "gesture"
    OBJECT = "object"
    NATURE = "nature"
    SYMBOL = "symbol"


# --- Emoji Database ---------------------------------------------------------

@dataclass
class EmojiDef:
    """Definition of a single emoji with emotional mappings."""
    char: str
    name: str
    category: Category
    primary_emotion: Emotion
    intensity_range: tuple[float, float] = (0.0, 1.0)
    moods: list[Mood] = field(default_factory=list)
    css_class: str = ""
    animation: str = ""

    def to_dict(self) -> dict:
        return {
            "char": self.char,
            "name": self.name,
            "category": self.category.value,
            "primary_emotion": self.primary_emotion.value,
            "intensity_range": list(self.intensity_range),
            "moods": [m.value for m in self.moods],
            "css_class": self.css_class,
            "animation": self.animation,
        }


# --- Complete Emoji Library -------------------------------------------------

EMOJI_LIBRARY: list[EmojiDef] = [
    # -- FACIAL --
    EmojiDef("😀", "Grinning Face", Category.FACIAL, Emotion.HAPPY, (0.5, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-grin", "bounce"),
    EmojiDef("😃", "Big Smile", Category.FACIAL, Emotion.HAPPY, (0.7, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-bigsmile", "elasticScale"),
    EmojiDef("😄", "Laughing", Category.FACIAL, Emotion.HAPPY, (0.6, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-laugh", "shake"),
    EmojiDef("😊", "Blushing Smile", Category.FACIAL, Emotion.LOVED, (0.3, 0.8),
             [Mood.POSITIVE, Mood.CALM], "emoji-blush", "pulse"),
    EmojiDef("😢", "Crying", Category.FACIAL, Emotion.SAD, (0.5, 1.0),
             [Mood.NEGATIVE, Mood.CALM], "emoji-cry", "drip"),
    EmojiDef("😭", "Sobbing", Category.FACIAL, Emotion.SAD, (0.8, 1.0),
             [Mood.NEGATIVE], "emoji-sob", "flood"),
    EmojiDef("😡", "Enraged", Category.FACIAL, Emotion.ANGRY, (0.7, 1.0),
             [Mood.NEGATIVE, Mood.ENERGETIC], "emoji-rage", "glitch"),
    EmojiDef("🤬", "Cursing", Category.FACIAL, Emotion.ANGRY, (0.9, 1.0),
             [Mood.NEGATIVE, Mood.ENERGETIC], "emoji-curse", "shake"),
    EmojiDef("😲", "Shocked", Category.FACIAL, Emotion.SURPRISED, (0.6, 1.0),
             [Mood.NEUTRAL, Mood.ENERGETIC], "emoji-shock", "popIn"),
    EmojiDef("😱", "Screaming Fear", Category.FACIAL, Emotion.FEARFUL, (0.7, 1.0),
             [Mood.NEGATIVE, Mood.ENERGETIC], "emoji-scream", "tremble"),
    EmojiDef("🤢", "Nauseous", Category.FACIAL, Emotion.DISGUSTED, (0.5, 1.0),
             [Mood.NEGATIVE], "emoji-nausea", "turnGreen"),
    EmojiDef("😐", "Neutral", Category.FACIAL, Emotion.NEUTRAL, (0.0, 0.5),
             [Mood.NEUTRAL, Mood.CALM], "emoji-neutral", "fade"),
    EmojiDef("🥰", "In Love", Category.FACIAL, Emotion.LOVED, (0.5, 1.0),
             [Mood.POSITIVE, Mood.CALM], "emoji-love", "heartFloat"),
    EmojiDef("😴", "Sleeping", Category.FACIAL, Emotion.TIRED, (0.3, 0.9),
             [Mood.CALM], "emoji-sleep", "zzz"),
    EmojiDef("😤", "Determined", Category.FACIAL, Emotion.DETERMINED, (0.5, 1.0),
             [Mood.ENERGETIC], "emoji-determined", "fire"),
    EmojiDef("🤔", "Thinking", Category.FACIAL, Emotion.CURIOUS, (0.3, 0.7),
             [Mood.NEUTRAL], "emoji-think", "hmm"),
    EmojiDef("😜", "Playful Wink", Category.FACIAL, Emotion.PLAYFUL, (0.5, 0.9),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-playful", "wink"),
    EmojiDef("😌", "Relieved", Category.FACIAL, Emotion.RELIEVED, (0.2, 0.6),
             [Mood.POSITIVE, Mood.CALM], "emoji-relieved", "sigh"),
    EmojiDef("😵", "Dizzy Confused", Category.FACIAL, Emotion.CONFUSED, (0.5, 1.0),
             [Mood.NEUTRAL], "emoji-dizzy", "spin"),
    EmojiDef("🥳", "Celebrating", Category.FACIAL, Emotion.PROUD, (0.6, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-party", "confetti"),

    # -- GESTURE --
    EmojiDef("👍", "Thumbs Up", Category.GESTURE, Emotion.HAPPY, (0.3, 0.8),
             [Mood.POSITIVE], "emoji-thumbup", "bob"),
    EmojiDef("👎", "Thumbs Down", Category.GESTURE, Emotion.DISGUSTED, (0.3, 0.8),
             [Mood.NEGATIVE], "emoji-thumbdown", "drop"),
    EmojiDef("👏", "Applause", Category.GESTURE, Emotion.PROUD, (0.5, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-clap", "clap"),
    EmojiDef("🙏", "Praying Hands", Category.GESTURE, Emotion.RELIEVED, (0.2, 0.7),
             [Mood.CALM, Mood.POSITIVE], "emoji-pray", "gentlePulse"),
    EmojiDef("💪", "Flexed Biceps", Category.GESTURE, Emotion.DETERMINED, (0.5, 1.0),
             [Mood.ENERGETIC], "emoji-flex", "grow"),
    EmojiDef("🤝", "Handshake", Category.GESTURE, Emotion.NEUTRAL, (0.3, 0.7),
             [Mood.POSITIVE, Mood.NEUTRAL], "emoji-shake", "meet"),
    EmojiDef("✌️", "Victory", Category.GESTURE, Emotion.HAPPY, (0.4, 0.8),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-peace", "peaceFloat"),
    EmojiDef("🤞", "Fingers Crossed", Category.GESTURE, Emotion.CURIOUS, (0.3, 0.6),
             [Mood.NEUTRAL], "emoji-crossed", "hope"),

    # -- OBJECT --
    EmojiDef("🔥", "Fire", Category.OBJECT, Emotion.DETERMINED, (0.4, 1.0),
             [Mood.ENERGETIC], "emoji-fire", "flicker"),
    EmojiDef("💔", "Broken Heart", Category.OBJECT, Emotion.SAD, (0.5, 1.0),
             [Mood.NEGATIVE, Mood.CALM], "emoji-brokenheart", "crack"),
    EmojiDef("❤️", "Red Heart", Category.OBJECT, Emotion.LOVED, (0.3, 1.0),
             [Mood.POSITIVE], "emoji-heart", "heartbeat"),
    EmojiDef("✨", "Sparkles", Category.OBJECT, Emotion.SURPRISED, (0.4, 0.8),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-sparkle", "twinkle"),
    EmojiDef("💡", "Lightbulb", Category.OBJECT, Emotion.CURIOUS, (0.4, 0.8),
             [Mood.NEUTRAL, Mood.ENERGETIC], "emoji-idea", "glow"),
    EmojiDef("🎯", "Direct Hit", Category.OBJECT, Emotion.DETERMINED, (0.5, 1.0),
             [Mood.ENERGETIC], "emoji-target", "bullseye"),
    EmojiDef("🎉", "Party Popper", Category.OBJECT, Emotion.PROUD, (0.6, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-party", "popConfetti"),
    EmojiDef("[!]️", "Warning", Category.OBJECT, Emotion.FEARFUL, (0.4, 0.9),
             [Mood.NEGATIVE, Mood.ENERGETIC], "emoji-warn", "alertPulse"),
    EmojiDef("[X]", "Cross Mark", Category.OBJECT, Emotion.ANGRY, (0.3, 0.8),
             [Mood.NEGATIVE], "emoji-cross", "xOut"),
    EmojiDef("[OK]", "Check Mark", Category.OBJECT, Emotion.HAPPY, (0.3, 0.7),
             [Mood.POSITIVE], "emoji-check", "popIn"),

    # -- NATURE --
    EmojiDef("🌈", "Rainbow", Category.NATURE, Emotion.HAPPY, (0.4, 0.8),
             [Mood.POSITIVE, Mood.CALM], "emoji-rainbow", "aurora"),
    EmojiDef("🌊", "Wave", Category.NATURE, Emotion.NEUTRAL, (0.2, 0.6),
             [Mood.CALM], "emoji-wave", "undulate"),
    EmojiDef(">>", "Lightning", Category.NATURE, Emotion.SURPRISED, (0.5, 1.0),
             [Mood.ENERGETIC], "emoji-lightning", "flash"),
    EmojiDef("🌸", "Cherry Blossom", Category.NATURE, Emotion.LOVED, (0.2, 0.6),
             [Mood.POSITIVE, Mood.CALM], "emoji-blossom", "drift"),
    EmojiDef("🌪️", "Tornado", Category.NATURE, Emotion.ANGRY, (0.6, 1.0),
             [Mood.NEGATIVE, Mood.ENERGETIC], "emoji-tornado", "vortex"),
    EmojiDef("💧", "Droplet", Category.NATURE, Emotion.SAD, (0.2, 0.5),
             [Mood.NEGATIVE, Mood.CALM], "emoji-drop", "fall"),

    # -- SYMBOL --
    EmojiDef("💯", "Hundred Points", Category.SYMBOL, Emotion.PROUD, (0.5, 1.0),
             [Mood.POSITIVE, Mood.ENERGETIC], "emoji-hundred", "stamp"),
    EmojiDef("💫", "Dizzy Symbol", Category.SYMBOL, Emotion.CONFUSED, (0.3, 0.7),
             [Mood.NEUTRAL, Mood.ENERGETIC], "emoji-dizzy", "orbit"),
    EmojiDef("💢", "Anger Symbol", Category.SYMBOL, Emotion.ANGRY, (0.5, 1.0),
             [Mood.NEGATIVE, Mood.ENERGETIC], "emoji-anger", "veinPop"),
    EmojiDef("💤", "Zzz", Category.SYMBOL, Emotion.TIRED, (0.3, 0.7),
             [Mood.CALM], "emoji-zzz", "sleepFloat"),
    EmojiDef("🌀", "Cyclone", Category.SYMBOL, Emotion.CONFUSED, (0.3, 0.8),
             [Mood.NEUTRAL], "emoji-cyclone", "spin"),
    EmojiDef("🔴", "Red Circle", Category.SYMBOL, Emotion.ANGRY, (0.3, 0.8),
             [Mood.NEGATIVE], "emoji-red", "recordPulse"),
    EmojiDef("🟢", "Green Circle", Category.SYMBOL, Emotion.RELIEVED, (0.2, 0.6),
             [Mood.POSITIVE, Mood.CALM], "emoji-green", "softGlow"),
]


# --- CSS Animation Keyframe Generator ----------------------------------------

CSS_ANIMATIONS = {
    "bounce": """
@keyframes emojiBounce {
  0%, 100% { transform: translateY(0); }
  40% { transform: translateY(-12px); }
  60% { transform: translateY(-4px); }
}""",
    "elasticScale": """
@keyframes emojiElasticScale {
  0% { transform: scale(1); }
  30% { transform: scale(1.3); }
  60% { transform: scale(0.9); }
  100% { transform: scale(1); }
}""",
    "shake": """
@keyframes emojiShake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-4px); }
  40% { transform: translateX(4px); }
  60% { transform: translateX(-3px); }
  80% { transform: translateX(2px); }
}""",
    "pulse": """
@keyframes emojiPulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.1); opacity: 0.85; }
}""",
    "glitch": """
@keyframes emojiGlitch {
  0%, 90%, 100% { transform: translate(0); }
  91% { transform: translate(-2px, 1px); }
  93% { transform: translate(2px, -1px); }
  95% { transform: translate(-1px, -1px); }
}""",
    "popIn": """
@keyframes emojiPopIn {
  0% { transform: scale(0); opacity: 0; }
  70% { transform: scale(1.15); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}""",
    "twinkle": """
@keyframes emojiTwinkle {
  0%, 100% { opacity: 1; filter: brightness(1); }
  50% { opacity: 0.6; filter: brightness(1.4); }
}""",
    "heartbeat": """
@keyframes emojiHeartbeat {
  0%, 100% { transform: scale(1); }
  15% { transform: scale(1.25); }
  30% { transform: scale(1); }
  45% { transform: scale(1.15); }
}""",
    "aurora": """
@keyframes emojiAurora {
  0% { filter: hue-rotate(0deg); }
  100% { filter: hue-rotate(360deg); }
}""",
    "spin": """
@keyframes emojiSpin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}""",
    "drip": """
@keyframes emojiDrip {
  0% { transform: translateY(0); opacity: 1; }
  50% { transform: translateY(3px); opacity: 0.8; }
  100% { transform: translateY(6px); opacity: 0.6; }
}""",
    "flicker": """
@keyframes emojiFlicker {
  0%, 100% { opacity: 1; }
  30% { opacity: 0.3; }
  35% { opacity: 1; }
  60% { opacity: 0.1; }
  65% { opacity: 1; }
}""",
    "vortex": """
@keyframes emojiVortex {
  0% { transform: rotate(0deg) scale(1); }
  50% { transform: rotate(180deg) scale(1.2); }
  100% { transform: rotate(360deg) scale(1); }
}""",
    "neuralPulse": """
@keyframes emojiNeuralPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0,245,255,0.4); }
  50% { box-shadow: 0 0 20px 6px rgba(0,245,255,0.1); }
}""",
    "quantumFlicker": """
@keyframes emojiQuantumFlicker {
  0%, 100% { opacity: 1; filter: blur(0); }
  40% { opacity: 0.7; filter: blur(0.5px); }
  45% { opacity: 0.3; filter: blur(1px); }
  50% { opacity: 1; filter: blur(0); }
}""",
}


# --- Emoji Maker Engine ------------------------------------------------------

class EmojiMaker:
    """Dynamic emotional emoji generator with intensity and mood control."""

    def __init__(self):
        self.library = {e.name: e for e in EMOJI_LIBRARY}
        self._build_indexes()

    def _build_indexes(self):
        """Build fast lookup indexes."""
        self.by_emotion: dict[Emotion, list[EmojiDef]] = {}
        self.by_mood: dict[Mood, list[EmojiDef]] = {}
        self.by_category: dict[Category, list[EmojiDef]] = {}

        for emoji in EMOJI_LIBRARY:
            self.by_emotion.setdefault(emoji.primary_emotion, []).append(emoji)
            for mood in emoji.moods:
                self.by_mood.setdefault(mood, []).append(emoji)
            self.by_category.setdefault(emoji.category, []).append(emoji)

    # -- Query Methods --

    def get_by_emotion(self, emotion: Emotion, intensity: float = 0.5) -> list[EmojiDef]:
        """Get emojis matching an emotion with optional intensity filter."""
        candidates = self.by_emotion.get(emotion, [])
        return [e for e in candidates if e.intensity_range[0] <= intensity <= e.intensity_range[1]]

    def get_by_mood(self, mood: Mood) -> list[EmojiDef]:
        """Get all emojis for a mood."""
        return self.by_mood.get(mood, [])

    def get_by_category(self, category: Category) -> list[EmojiDef]:
        """Get all emojis in a category."""
        return self.by_category.get(category, [])

    def get_mood_faces(self, mood: Mood, count: int = 5) -> list[str]:
        """Get face emoji characters for a mood."""
        emojis = [e for e in self.get_by_mood(mood) if e.category == Category.FACIAL]
        selected = random.sample(emojis, min(count, len(emojis))) if emojis else []
        return [e.char for e in selected]

    def random_emoji(self, mood: (Mood | None) = None,
                     emotion: (Emotion | None) = None) -> EmojiDef:
        """Get a random emoji, optionally filtered."""
        pool = EMOJI_LIBRARY
        if mood:
            pool = [e for e in pool if mood in e.moods]
        if emotion:
            pool = [e for e in pool if e.primary_emotion == emotion]
        return random.choice(pool) if pool else random.choice(EMOJI_LIBRARY)

    def emotion_blend(self, emotion1: Emotion, emotion2: Emotion,
                      blend: float = 0.5, intensity: float = 0.5) -> list[EmojiDef]:
        """Blend between two emotions at a given ratio."""
        # EmojiDef is a mutable, non-frozen dataclass (unhashable), so the
        # difference must be deduped by identity, not via set() membership.
        primary_list = self.by_emotion.get(
            emotion1 if blend <= 0.5 else emotion2, [])
        secondary_list = self.by_emotion.get(
            emotion2 if blend <= 0.5 else emotion1, [])
        # Weight toward emotion1 if blend < 0.5, emotion2 otherwise
        weight = abs(blend - 0.5) * 2

        results = list(primary_list)[:max(1, int(5 * (1 - weight)))]
        seen = {id(e) for e in results}
        extra: list[EmojiDef] = []
        for emoji in secondary_list:
            if id(emoji) not in seen:
                seen.add(id(emoji))
                extra.append(emoji)
        results += extra[:max(1, int(5 * weight))]
        return [e for e in results
                if e.intensity_range[0] <= intensity <= e.intensity_range[1]]

    def intensity_scale(self, emoji: EmojiDef, intensity: float) -> dict:
        """Generate CSS scale values based on intensity (0.0-1.0)."""
        scale = 0.6 + (intensity * 0.8)  # 0.6 to 1.4
        duration = max(0.2, 2.0 - intensity * 1.5)  # faster at higher intensity
        return {
            "transform": f"scale({scale:.2f})",
            "animation-duration": f"{duration:.2f}s",
            "filter": f"brightness({0.5 + intensity})",
        }

    # -- Emoji String Builders --

    def emotion_string(self, emotion: Emotion, intensity: float = 0.5,
                       length: int = 3) -> str:
        """Build a string of emojis expressing an emotion at a given intensity."""
        candidates = self.get_by_emotion(emotion, intensity)
        if not candidates:
            candidates = self.get_by_emotion(emotion, 0.5)
        selected = random.choices(candidates, k=length) if candidates else []
        # Sort by intensity: highest first for intensity > 0.5
        if intensity > 0.5:
            selected.sort(key=lambda e: e.intensity_range[1], reverse=True)
        return " ".join(e.char for e in selected)

    def mood_palette(self, mood: Mood, size: int = 12) -> list[EmojiDef]:
        """Build a curated palette of emojis for a mood across categories."""
        palette = []
        used = set()
        all_mood = self.get_by_mood(mood)
        random.shuffle(all_mood)
        for emoji in all_mood:
            if emoji.char not in used:
                palette.append(emoji)
                used.add(emoji.char)
            if len(palette) >= size:
                break
        return palette

    def reaction_emoji(self, event_type: str, intensity: float = 0.5) -> EmojiDef:
        """Map an event type to the most appropriate emoji."""
        event_map = {
            "success": Emotion.PROUD,
            "error": Emotion.ANGRY,
            "warning": Emotion.FEARFUL,
            "info": Emotion.CURIOUS,
            "complete": Emotion.RELIEVED,
            "start": Emotion.DETERMINED,
            "pause": Emotion.CONFUSED,
            "cancel": Emotion.DISGUSTED,
            "heartbeat": Emotion.LOVED,
            "timeout": Emotion.TIRED,
            "connection": Emotion.SURPRISED,
            "love": Emotion.LOVED,
            "thinking": Emotion.PLAYFUL,
            "celebration": Emotion.HAPPY,
            "danger": Emotion.FEARFUL,
        }
        emotion = event_map.get(event_type, Emotion.NEUTRAL)
        candidates = self.get_by_emotion(emotion, intensity)
        return candidates[0] if candidates else self.random_emoji(emotion=emotion)

    # -- CSS Generation --

    def generate_css(self) -> str:
        """Generate complete CSS stylesheet for all emoji animations."""
        lines = ["/* Azazael Dynamic Emoji Animations */", ""]

        # Base styles
        lines.append(".azazael-emoji {")
        lines.append("  display: inline-flex;")
        lines.append("  align-items: center;")
        lines.append("  justify-content: center;")
        lines.append("  font-size: 2em;")
        lines.append("  transition: all 0.3s ease;")
        lines.append("  cursor: default;")
        lines.append("  user-select: none;")
        lines.append("}")
        lines.append("")

        # Intensity modifiers
        for level, opacity in [("low", 0.5), ("mid", 0.8), ("high", 1.0), ("max", 1.0)]:
            lines.append(f".emoji-intensity-{level} {{ opacity: {opacity}; }}")
        lines.append("")

        # Emoji-specific classes
        for emoji in EMOJI_LIBRARY:
            anim_name = CSS_ANIMATIONS.get(emoji.animation, "")
            if anim_name:
                anim_key = list(CSS_ANIMATIONS.keys())[
                    list(CSS_ANIMATIONS.values()).index(anim_name)
                ]
                lines.append(f".{emoji.css_class} {{")
                lines.append(f"  animation: emoji{anim_key[0].upper() + anim_key[1:]} 2s ease-in-out infinite;")
                lines.append("}")
                lines.append("")

        # Keyframes
        for name, css in CSS_ANIMATIONS.items():
            # Rename keyframes to avoid collision with global animation names
            renamed = css.replace("@keyframes ", "@keyframes emoji")
            renamed = renamed[0].upper() + renamed[1:]
            lines.append(renamed)
            lines.append("")

        return "\n".join(lines)

    # -- HTML Generator --

    def generate_html_preview(self, mood: (Mood | None) = None) -> str:
        """Generate an HTML snippet showcasing emojis."""
        emojis = self.mood_palette(mood or Mood.POSITIVE, 16)
        html = ['<div class="azazael-emoji-grid">']
        for i, emoji in enumerate(emojis):
            intensity = "max" if i < 4 else "high" if i < 8 else "mid"
            html.append(
                f'  <span class="azazael-emoji {emoji.css_class} emoji-intensity-{intensity}" '
                f'title="{emoji.name} — {emoji.primary_emotion.value}">'
                f'{emoji.char}</span>'
            )
        html.append('</div>')
        return "\n".join(html)

    # --- Export --------------------------------------------------------------

    def export_library(self) -> dict:
        """Export entire emoji library as JSON-serializable dict."""
        return {
            "version": "1.0.0",
            "total_emojis": len(EMOJI_LIBRARY),
            "categories": [c.value for c in Category],
            "emotions": [e.value for e in Emotion],
            "moods": [m.value for m in Mood],
            "emojis": [e.to_dict() for e in EMOJI_LIBRARY],
            "animations": list(CSS_ANIMATIONS.keys()),
        }


# --- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Azazael Dynamic Emoji Maker")
    parser.add_argument("--mood", choices=[m.value for m in Mood], help="Filter by mood")
    parser.add_argument("--emotion", choices=[e.value for e in Emotion], help="Filter by emotion")
    parser.add_argument("--intensity", type=float, default=0.5, help="Intensity 0.0-1.0")
    parser.add_argument("--count", type=int, default=8, help="Number of emojis to display")
    parser.add_argument("--css", action="store_true", help="Output CSS stylesheet")
    parser.add_argument("--json", action="store_true", help="Export library as JSON")
    parser.add_argument("--reaction", help="Event type for reaction emoji (success, error, etc.)")
    parser.add_argument("--blend", nargs=2, help="Blend two emotions (e.g., happy sad)")
    args = parser.parse_args()

    maker = EmojiMaker()

    if args.json:
        import json as _json
        print(_json.dumps(maker.export_library(), indent=2))
        return

    if args.css:
        print(maker.generate_css())
        return

    if args.reaction:
        emoji = maker.reaction_emoji(args.reaction, args.intensity)
        print(f"  Reaction: {args.reaction} -> {emoji.char} {emoji.name}")
        return

    if args.blend and len(args.blend) == 2:
        try:
            e1 = Emotion(args.blend[0])
            e2 = Emotion(args.blend[1])
        except ValueError:
            print("  Invalid emotion. Options:", [e.value for e in Emotion])
            return
        blended = maker.emotion_blend(e1, e2, 0.5, args.intensity)
        print(f"  Blend: {e1.value} ↔ {e2.value}")
        for e in blended[:args.count]:
            print(f"    {e.char} {e.name} ({e.primary_emotion.value})")
        return

    mood = Mood(args.mood) if args.mood else None
    emotion = Emotion(args.emotion) if args.emotion else None

    if mood:
        palette = maker.mood_palette(mood, args.count)
        print(f"  Mood: {mood.value} ({len(palette)} emojis)")
    elif emotion:
        palette = maker.get_by_emotion(emotion, args.intensity)[:args.count]
        print(f"  Emotion: {emotion.value} @ intensity {args.intensity}")
    else:
        palette = maker.mood_palette(Mood.POSITIVE, args.count)
        print(f"  Default Positive Mood ({len(palette)} emojis)")

    for e in palette:
        print(f"    {e.char}  {e.name:<20} {e.primary_emotion.value:<12} "
              f"[{e.intensity_range[0]:.1f}-{e.intensity_range[1]:.1f}]  {e.animation}")


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
