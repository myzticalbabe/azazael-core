#!/usr/bin/env python3
"""
Azazael Enhanced CSS Animations
================================
30+ new dynamic CSS animations organized by category:
    - Cinematic: Ken Burns, parallax, crossfade, zoom-reveal
    - Data: stream, pulse, typewriter, counter-increment
    - Particle: confetti, sparkle, rain, snow, firefly
    - UI: shimmer, skeleton, slide-in, scale-bounce, ripple
    - Emotional: mood-shift, intensity-wave, heartbeat, glow-pulse
    - Structural: fold, unfold, collapse, expand, morph

    Usage:
    from enhanced_animations import ANIMATIONS, generate_css
    print(generate_css())
    """

import json
import argparse
# from typing import Dict  # removed — all names have native equivalents
from dataclasses import dataclass
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class AnimationDef:
    name: str
    category: str
    keyframes: str
    duration: str = "1s"
    timing: str = "ease"
    iteration: str = "infinite"
    description: str = ""


    # --- Animation Library ------------------------------------------------------

ANIMATIONS: dict[str, AnimationDef] = {
    # -- CINEMATIC --
"kenBurns": AnimationDef(
        "kenBurns", "cinematic",
        """@keyframes kenBurns {
  0% { transform: scale(1) translate(0, 0); }
  100% { transform: scale(1.15) translate(-2%, -1%); }
    }""",
            "20s", "ease-out", "infinite",
            "Slow zoom and pan for hero backgrounds"
    ),
    "cinematicFade": AnimationDef(
            "cinematicFade", "cinematic",
            """@keyframes cinematicFade {
  0% { opacity: 0; transform: translateY(30px) scale(0.95); filter: blur(10px); }
  100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
    }""",
            "1.2s", "cubic-bezier(0.16, 1, 0.3, 1)", "forwards",
            "Cinematic entrance with blur dissolve"
    ),
    "crossfadeReveal": AnimationDef(
            "crossfadeReveal", "cinematic",
            """@keyframes crossfadeReveal {
  0% { opacity: 0; clip-path: inset(0 100% 0 0); }
  100% { opacity: 1; clip-path: inset(0 0 0 0); }
    }""",
            "0.8s", "cubic-bezier(0.4, 0, 0.2, 1)", "forwards",
            "Text reveals from left to right"
    ),
    "zoomReveal": AnimationDef(
            "zoomReveal", "cinematic",
            """@keyframes zoomReveal {
  0% { opacity: 0; transform: scale(0.3); filter: blur(20px); }
  60% { opacity: 0.8; transform: scale(1.05); filter: blur(0); }
  100% { opacity: 1; transform: scale(1); filter: blur(0); }
    }""",
            "0.8s", "cubic-bezier(0.34, 1.56, 0.64, 1)", "forwards",
            "Bouncy zoom-in reveal"
    ),
    "parallaxDrift": AnimationDef(
            "parallaxDrift", "cinematic",
            """@keyframes parallaxDrift {
  0% { transform: translateY(var(--drift-start, 0px)); }
  100% { transform: translateY(var(--drift-end, -50px)); }
    }""",
            "var(--drift-speed, 3s)", "linear", "infinite alternate",
            "Smooth vertical parallax drift"
    ),

    # -- DATA --
    "dataStream": AnimationDef(
            "dataStream", "data",
            """@keyframes dataStream {
  0% { background-position: 0% 0%; opacity: 0.8; }
  100% { background-position: 100% 100%; opacity: 1; }
    }""",
            "2s", "linear", "infinite",
            "Diagonal data flow background"
    ),
    "typewriter": AnimationDef(
            "typewriter", "data",
            """@keyframes typewriter {
  from { width: 0; }
  to { width: 100%; }
    }""",
            "var(--type-speed, 3s)", "steps(var(--type-steps, 40))", "forwards",
            "Character-by-character text reveal"
    ),
    "counterIncrement": AnimationDef(
            "counterIncrement", "data",
            """@keyframes counterIncrement {
  0% { --num: var(--counter-start, 0); }
  100% { --num: var(--counter-end, 100); }
    }""",
            "2s", "ease-out", "forwards",
            "Animated number counter (requires @property)"
    ),
    "statusPulse": AnimationDef(
            "statusPulse", "data",
            """@keyframes statusPulse {
  0%, 100% { box-shadow: 0 0 0 0 var(--status-color, #00f5ff); }
  50% { box-shadow: 0 0 0 8px transparent; }
    }""",
            "2s", "ease-in-out", "infinite",
            "Pulsing ring around status indicators"
    ),

    # -- PARTICLE --
    "confetti": AnimationDef(
            "confetti", "particle",
            """@keyframes confetti {
  0% { transform: translateY(0) rotate(0deg); opacity: 1; }
  100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
    }""",
            "3s", "ease-in", "infinite",
            "Falling confetti particles"
    ),
    "sparkle": AnimationDef(
            "sparkle", "particle",
            """@keyframes sparkle {
  0%, 100% { opacity: 0; transform: scale(0); }
  50% { opacity: 1; transform: scale(1); }
    }""",
            "1.5s", "ease-in-out", "infinite",
            "Twinkling sparkle effect"
    ),
    "rainDrop": AnimationDef(
            "rainDrop", "particle",
            """@keyframes rainDrop {
  0% { transform: translateY(-10vh); opacity: 0; }
  10% { opacity: 1; }
  100% { transform: translateY(110vh); opacity: 0.3; }
    }""",
            "0.8s", "linear", "infinite",
            "Falling rain droplets"
    ),
    "snowFall": AnimationDef(
            "snowFall", "particle",
            """@keyframes snowFall {
  0% { transform: translateY(-5vh) translateX(0) rotate(0deg); }
  100% { transform: translateY(105vh) translateX(30px) rotate(360deg); }
    }""",
            "6s", "linear", "infinite",
            "Gentle falling snow"
    ),
    "firefly": AnimationDef(
            "firefly", "particle",
            """@keyframes firefly {
  0%, 100% { opacity: 0.2; transform: translate(0, 0) scale(0.8); }
  25% { opacity: 1; transform: translate(30px, -20px) scale(1.2); }
  50% { opacity: 0.3; transform: translate(-20px, -40px) scale(0.7); }
  75% { opacity: 0.9; transform: translate(10px, -10px) scale(1); }
    }""",
            "5s", "ease-in-out", "infinite",
            "Erratic firefly movement"
    ),

    # -- UI --
    "shimmer": AnimationDef(
            "shimmer", "ui",
            """@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
    }""",
            "1.5s", "linear", "infinite",
            "Loading skeleton shimmer"
    ),
    "slideInRight": AnimationDef(
            "slideInRight", "ui",
            """@keyframes slideInRight {
  0% { opacity: 0; transform: translateX(60px); }
  100% { opacity: 1; transform: translateX(0); }
    }""",
            "0.5s", "cubic-bezier(0.16, 1, 0.3, 1)", "forwards",
            "Slide in from right"
    ),
    "slideInUp": AnimationDef(
            "slideInUp", "ui",
            """@keyframes slideInUp {
  0% { opacity: 0; transform: translateY(40px); }
  100% { opacity: 1; transform: translateY(0); }
    }""",
            "0.5s", "cubic-bezier(0.16, 1, 0.3, 1)", "forwards",
            "Slide in from bottom"
    ),
    "scaleBounce": AnimationDef(
            "scaleBounce", "ui",
            """@keyframes scaleBounce {
  0% { transform: scale(0); }
  60% { transform: scale(1.15); }
  80% { transform: scale(0.95); }
  100% { transform: scale(1); }
    }""",
            "0.6s", "cubic-bezier(0.34, 1.56, 0.64, 1)", "forwards",
            "Bouncy scale entrance"
    ),
    "ripple": AnimationDef(
            "ripple", "ui",
            """@keyframes ripple {
  0% { transform: scale(0); opacity: 0.6; }
  100% { transform: scale(4); opacity: 0; }
    }""",
            "0.7s", "ease-out", "forwards",
            "Material design ripple effect"
    ),
    "hoverLift": AnimationDef(
            "hoverLift", "ui",
            """@keyframes hoverLift {
  0% { transform: translateY(0); box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
  100% { transform: translateY(-4px); box-shadow: 0 12px 30px rgba(0,0,0,0.15); }
    }""",
            "0.3s", "ease-out", "forwards",
            "Card lift on hover"
    ),

    # -- EMOTIONAL --
    "moodShift": AnimationDef(
            "moodShift", "emotional",
            """@keyframes moodShift {
  0% { filter: hue-rotate(0deg) saturate(1); }
  50% { filter: hue-rotate(var(--mood-hue, 30deg)) saturate(1.3); }
  100% { filter: hue-rotate(0deg) saturate(1); }
    }""",
            "4s", "ease-in-out", "infinite",
            "Mood-based hue rotation"
    ),
    "intensityWave": AnimationDef(
            "intensityWave", "emotional",
            """@keyframes intensityWave {
  0%, 100% { transform: scale(1); filter: brightness(1); }
  25% { transform: scale(1.08); filter: brightness(1.2); }
  75% { transform: scale(0.95); filter: brightness(0.9); }
    }""",
            "3s", "ease-in-out", "infinite",
            "Emotional intensity wave"
    ),
    "heartbeat": AnimationDef(
            "heartbeat", "emotional",
            """@keyframes heartbeat {
  0%, 100% { transform: scale(1); }
  14% { transform: scale(1.3); }
  28% { transform: scale(1); }
  42% { transform: scale(1.15); }
  56% { transform: scale(1); }
    }""",
            "1.2s", "ease-in-out", "infinite",
            "Realistic heartbeat pulse"
    ),
    "glowPulse": AnimationDef(
            "glowPulse", "emotional",
            """@keyframes glowPulse {
  0%, 100% { text-shadow: 0 0 4px var(--glow-color, #00f5ff), 0 0 8px var(--glow-color, #00f5ff); }
  50% { text-shadow: 0 0 20px var(--glow-color, #00f5ff), 0 0 40px var(--glow-color, #00f5ff), 0 0 60px var(--glow-color, #00f5ff); }
    }""",
            "2s", "ease-in-out", "infinite",
            "Pulsing glow around text"
    ),

    # -- STRUCTURAL --
    "foldIn": AnimationDef(
            "foldIn", "structural",
            """@keyframes foldIn {
  0% { transform: perspective(400px) rotateX(-90deg); opacity: 0; }
  100% { transform: perspective(400px) rotateX(0); opacity: 1; }
    }""",
            "0.5s", "ease-out", "forwards",
            "3D fold-in reveal"
    ),
    "unfoldDown": AnimationDef(
            "unfoldDown", "structural",
            """@keyframes unfoldDown {
  0% { max-height: 0; opacity: 0; overflow: hidden; }
  100% { max-height: 1000px; opacity: 1; overflow: hidden; }
    }""",
            "0.6s", "ease-out", "forwards",
            "Accordion unfold downward"
    ),
    "morphShape": AnimationDef(
            "morphShape", "structural",
            """@keyframes morphShape {
  0% { border-radius: 4px; transform: rotate(0deg); }
  50% { border-radius: 50%; transform: rotate(180deg); }
  100% { border-radius: 4px; transform: rotate(360deg); }
    }""",
            "3s", "ease-in-out", "infinite",
            "Square-to-circle-to-square morph"
    ),
    "expandContract": AnimationDef(
            "expandContract", "structural",
            """@keyframes expandContract {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
    }""",
            "4s", "ease-in-out", "infinite",
            "Subtle breathing expansion"
    ),
    "flipCard": AnimationDef(
            "flipCard", "structural",
            """@keyframes flipCard {
  0% { transform: perspective(600px) rotateY(0); }
  100% { transform: perspective(600px) rotateY(180deg); }
    }""",
            "0.6s", "ease-in-out", "forwards",
            "3D card flip"
    ),

    # -- SCROLL-TRIGGERED --
    "scrollReveal": AnimationDef(
            "scrollReveal", "scroll",
            """@keyframes scrollReveal {
  0% { opacity: 0; transform: translateY(60px) scale(0.9); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
    }""",
            "0.8s", "cubic-bezier(0.16, 1, 0.3, 1)", "forwards",
            "Scroll-triggered reveal (use with IntersectionObserver)"
    ),
    "scrollParallax": AnimationDef(
            "scrollParallax", "scroll",
            """@keyframes scrollParallax {
  /* Per-frame position set via JS based on scroll */
    }""",
            "0s", "linear", "infinite",
            "Parallax driven by scroll position via JS"
    ),
    "staggerChildren": AnimationDef(
            "staggerChildren", "scroll",
            """@keyframes staggerChildren {
  0% { opacity: 0; transform: translateY(30px); }
  100% { opacity: 1; transform: translateY(0); }
    }""",
            "0.5s", "cubic-bezier(0.16, 1, 0.3, 1)", "forwards",
            "Staggered child entrance (delay via JS)"
    ),
    }


    # --- CSS Generation ---------------------------------------------------------

def generate_css(include_utility: bool = True) -> str:
    """Generate complete CSS stylesheet with all animations and utility classes."""
    lines = ["/* Azazael Enhanced CSS Animations — Auto-generated */", ""]

    # Category headers
    categories = {}
    for anim in ANIMATIONS.values():
        categories.setdefault(anim.category, []).append(anim)

    for category, anims in categories.items():
        lines.append(f"/* === {category.upper()} === */")
        lines.append("")
        for anim in anims:
            lines.append(f"/* {anim.description} */")
            lines.append(anim.keyframes)
            lines.append("")
            lines.append(f".azazael-{anim.name} {{")
            lines.append(f"  animation: {anim.name} {anim.duration} {anim.timing} {anim.iteration};")
            lines.append("}")
            lines.append("")

    # Utility classes
    if include_utility:
        lines.append("/* === Utility Classes === */")
        lines.append("")

        # Delay utilities
        for i in range(1, 11):
            lines.append(f".azazael-delay-{i} {{ animation-delay: {i * 0.1}s; }}")
        lines.append("")

        # Duration utilities
        for dur in ["fast", "normal", "slow"]:
            val = {"fast": "0.3s", "normal": "0.6s", "slow": "1.2s"}[dur]
            lines.append(f".azazael-duration-{dur} {{ animation-duration: {val}; }}")
        lines.append("")

        # Reduced motion
        lines.append("@media (prefers-reduced-motion: reduce) {")
        lines.append("  .azazael-kenBurns,")
        lines.append("  .azazael-parallaxDrift,")
        lines.append("  .azazael-morphShape,")
        lines.append("  .azazael-moodShift {")
        lines.append("    animation: none;")
        lines.append("  }")
        lines.append("}")

    return "\n".join(lines)


def export_manifest() -> dict:
    """Export animation manifest as JSON."""
    return {
        "version": "2.0.0",
        "total_animations": len(ANIMATIONS),
        "categories": list(set(a.category for a in ANIMATIONS.values())),
        "animations": [
            {
                "name": a.name,
                "category": a.category,
                "duration": a.duration,
                "description": a.description,
            }
            for a in ANIMATIONS.values()
        ],
    }


    # --- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Azazael Enhanced CSS Animations")
    parser.add_argument("--css", action="store_true", help="Output complete CSS stylesheet")
    parser.add_argument("--json", action="store_true", help="Output manifest as JSON")
    parser.add_argument("--list", action="store_true", help="list all animations")
    parser.add_argument("--category", help="Filter by category")
    args = parser.parse_args()

    if args.css:
        print(generate_css())

    elif args.json:
        print(json.dumps(export_manifest(), indent=2))

    elif args.list:
        for name, anim in sorted(ANIMATIONS.items()):
            if args.category and anim.category != args.category:
                continue
            print(f"  {name:<22} [{anim.category:<12}] {anim.description}")

    else:
        print("Azazael Enhanced CSS Animations")
    print(f"  {len(ANIMATIONS)} animations in {len(set(a.category for a in ANIMATIONS.values()))} categories")
    print()
    for cat in sorted(set(a.category for a in ANIMATIONS.values())):
        count = sum(1 for a in ANIMATIONS.values() if a.category == cat)
        print(f"  {cat}: {count}")
    print()
    print("  --css   Output CSS stylesheet")
    print("  --json  Export manifest")
    print("  --list  list all animations")


if __name__ == "__main__":
    main()
