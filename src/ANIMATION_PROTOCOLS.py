# coding: utf-8
"""
ANIMATION PROTOCOLS
==================
Comprehensive guide for animating results in the Azazael Ecosystem
Explore diversity in animating various data types and results
"""

ANIMATION_CATEGORIES = {
    "STATUS_ANIMATIONS": {
        "description": "Animations for displaying system/component status",
        "techniques": [
            {
                "name": "Glitch Status",
                "use_case": "Critical alerts, system warnings",
                "effect": "Creates urgency and draws attention",
                "css_properties": ["clip-path", "text-shadow", "transform"],
                "impact": "HIGH",
            },
            {
                "name": "Neon Pulse",
                "use_case": "Active systems, operational status",
                "effect": "Steady glow indicating live status",
                "css_properties": ["text-shadow", "animation"],
                "impact": "MEDIUM",
            },
            {
                "name": "Ring Pulse",
                "use_case": "Heartbeat monitoring, live data",
                "effect": "Expanding ripples showing activity",
                "css_properties": ["border", "transform", "opacity"],
                "impact": "MEDIUM",
            },
        ],
    },
    "PROGRESS_ANIMATIONS": {
        "description": "Animations for progress and completion indicators",
        "techniques": [
            {
                "name": "Linear Bar",
                "use_case": "File transfers, loading operations",
                "effect": "Smooth left-to-right fill",
                "css_properties": ["width", "transition"],
                "impact": "LOW",
            },
            {
                "name": "Shimmer Bar",
                "use_case": "Premium loading experiences",
                "effect": "Light sweep across progress",
                "css_properties": ["background-position", "linear-gradient"],
                "impact": "MEDIUM",
            },
            {
                "name": "Bounce Dots",
                "use_case": "Indeterminate progress",
                "effect": "Delays create wave-like motion",
                "css_properties": ["animation-delay", "transform"],
                "impact": "LOW",
            },
        ],
    },
    "DATA_VISUALIZATION": {
        "description": "Animations for charts, graphs, and metrics",
        "techniques": [
            {
                "name": "Bar Growth",
                "use_case": "Histograms, comparisons",
                "effect": "Bars animate height from 0",
                "css_properties": ["height", "transition", "transform"],
                "impact": "MEDIUM",
            },
            {
                "name": "Pie Fill",
                "use_case": "Percentages, distributions",
                "effect": "Conic gradient reveals data",
                "css_properties": ["conic-gradient", "animation"],
                "impact": "MEDIUM",
            },
            {
                "name": "Line Draw",
                "use_case": "Time series, trends",
                "effect": "Path draws itself left-to-right",
                "css_properties": ["stroke-dasharray", "stroke-dashoffset"],
                "impact": "HIGH",
            },
        ],
    },
    "ENTRY_ANIMATIONS": {
        "description": "Animations for revealing new content",
        "techniques": [
            {
                "name": "Slide In Up",
                "use_case": "Modal dialogs, notifications",
                "effect": "Content rises from below",
                "css_properties": ["transform", "opacity"],
                "impact": "MEDIUM",
            },
            {
                "name": "Fade In Scale",
                "use_case": "Cards, dashboard widgets",
                "effect": "Elements pop in with scale",
                "css_properties": ["opacity", "transform", "cubic-bezier"],
                "impact": "HIGH",
            },
            {
                "name": "Typewriter",
                "use_case": "Terminal output, logs",
                "effect": "Text appears character-by-character",
                "css_properties": ["width", "border-right"],
                "impact": "HIGH",
            },
        ],
    },
    "TRANSFORMATION_ANIMATIONS": {
        "description": "Animations for state changes and transitions",
        "techniques": [
            {
                "name": "Flip 3D",
                "use_case": "Card flips, before/after views",
                "effect": "Element rotates on axis",
                "css_properties": ["transform-style", "backface-visibility"],
                "impact": "HIGH",
            },
            {
                "name": "Morph",
                "use_case": "Shape changes, icon transitions",
                "effect": "Smooth path morphing",
                "css_properties": ["d", "transition"],
                "impact": "HIGH",
            },
            {
                "name": "Elastic Scale",
                "use_case": "Button feedback, selections",
                "effect": "Bouncy overshoot effect",
                "css_properties": ["transform", "cubic-bezier"],
                "impact": "MEDIUM",
            },
        ],
    },
    "DECORATIVE_ANIMATIONS": {
        "description": "Background and ambient animations",
        "techniques": [
            {
                "name": "Particle Float",
                "use_case": "Background ambiance",
                "effect": "Particles rise slowly",
                "css_properties": ["position", "animation", "opacity"],
                "impact": "LOW",
            },
            {
                "name": "Hex Grid",
                "use_case": "Tech/sci-fi themes",
                "effect": "Geometric pattern with depth",
                "css_properties": ["clip-path", "animation"],
                "impact": "MEDIUM",
            },
            {
                "name": "Matrix Rain",
                "use_case": "Hacker terminal aesthetic",
                "effect": "Falling character streams",
                "css_properties": ["animation", "font-family"],
                "impact": "MEDIUM",
            },
        ],
    },
    "TEXT_ANIMATIONS": {
        "description": "Special effects for text content",
        "techniques": [
            {
                "name": "Rainbow Wave",
                "use_case": "Headlines, highlights",
                "effect": "Gradient moves across text",
                "css_properties": ["background-clip", "background-position"],
                "impact": "MEDIUM",
            },
            {
                "name": "Gradient Flow",
                "use_case": "Emphasis, branding",
                "effect": "Colors cycle through text",
                "css_properties": ["animation", "background-size"],
                "impact": "LOW",
            },
            {
                "name": "Blur Reveal",
                "use_case": "Dramatic reveals",
                "effect": "Text goes from blur to sharp",
                "css_properties": ["filter", "opacity"],
                "impact": "MEDIUM",
            },
        ],
    },
    "INTERACTIVE_FEEDBACK": {
        "description": "Animations responding to user interaction",
        "techniques": [
            {
                "name": "Hover Expand",
                "use_case": "Buttons, cards",
                "effect": "Scale up with slight rotation",
                "css_properties": ["transform", "box-shadow"],
                "impact": "MEDIUM",
            },
            {
                "name": "Click Ripple",
                "use_case": "Button clicks",
                "effect": "Wave emanates from click point",
                "css_properties": ["position", "animation", "border-radius"],
                "impact": "MEDIUM",
            },
            {
                "name": "Success Check",
                "use_case": "Form submissions",
                "effect": "Checkmark draws itself",
                "css_properties": ["stroke-dashoffset", "transform"],
                "impact": "HIGH",
            },
        ],
    },
}


def generate_animation_protocol(config: dict) -> str:
    """Generate CSS animation code from config."""

    category = config.get("category", "STATUS_ANIMATIONS")
    technique = config.get("technique", "Glitch Status")

    protocol = ANIMATION_CATEGORIES.get(category, {})
    techniques = protocol.get("techniques", [])

    selected = next(
        (t for t in techniques if t["name"] == technique),
        techniques[0] if techniques else None,
    )

    if not selected:
        return ""

    css = f"""
/* {selected["name"]} */
.{technique.lower().replace(" ", "-")} {{
"""

    for prop in selected.get("css_properties", []):
        css += f"    /* {prop} - configured via animation */\n"

    css += (
        f"    animation: {technique.lower().replace(' ', '-')}-anim 1s ease-in-out;\n"
    )
    css += "}\n"

    return css


def get_best_animation_for_result(result_type: str) -> str:
    """Get recommended animation for result type."""

    recommendations = {
        "success": "Bounce Dots",
        "error": "Shake",
        "warning": "Neon Pulse",
        "info": "Slide In Up",
        "data": "Bar Growth",
        "progress": "Linear Bar",
        "system": "Glitch Status",
        "background": "Particle Float",
        "text": "Rainbow Wave",
        "interactive": "Hover Expand",
    }

    return recommendations.get(result_type.lower(), "Fade In Scale")


def create_animation_system() -> dict:
    """Create complete animation system configuration."""

    return {
        "version": "1.0.0",
        "categories": list(ANIMATION_CATEGORIES.keys()),
        "total_techniques": sum(
            len(cat.get("techniques", [])) for cat in ANIMATION_CATEGORIES.values()
        ),
        "performance_tiers": {
            "LOW": ["Linear Bar", "Fade In", "Bounce"],
            "MEDIUM": ["Shimmer", "Slide In", "Pulse Scale"],
            "HIGH": ["Glitch", "3D Flip", "Matrix Rain"],
        },
        "color_schemes": {
            "cyan_purple": ["#00f5ff", "#bf00ff"],
            "neon_green": ["#00ff88", "#00ff44"],
            "cyberpunk": ["#ff0044", "#00f5ff", "#bf00ff"],
            "matrix": ["#00ff00", "#00cc00", "#009900"],
            "fire": ["#ff6600", "#ff0044", "#ffcc00"],
        },
    }


if __name__ == "__main__":
    print("ANIMATION PROTOCOLS - Azazael Ecosystem")
    print("=" * 50)
    print(f"\nCategories: {len(ANIMATION_CATEGORIES)}")

    total = sum(len(cat.get("techniques", [])) for cat in ANIMATION_CATEGORIES.values())
    print(f"Total Techniques: {total}")

    print("\n--- Category Summary ---")
    for name, cat in ANIMATION_CATEGORIES.items():
        count = len(cat.get("techniques", []))
        print(f"{name}: {count} techniques")

    print("\n--- Recommendation Engine ---")
    for result_type in ["success", "error", "warning", "progress", "data"]:
        anim = get_best_animation_for_result(result_type)
        print(f"{result_type:10} -> {anim}")
