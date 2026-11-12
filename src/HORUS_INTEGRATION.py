# coding: utf-8
"""
HORUS INTEGRATION
================
Integrates Animation Protocols into HORUS Live Monitor
Share animations and visual results with HORUS system
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dynamic path resolution
try:
    from CORE.paths import get_src_dir, get_desktop_dir
except ImportError as exc:
    note_failure("HORUS_INTEGRATION:L18", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from CORE.paths import get_src_dir, get_desktop_dir

# Animation protocols path
ANIMATION_PROTOCOLS_PATH = get_src_dir() / "CODEX" / "ANIMATION_PROTOCOLS.py"
RESULTS_PATH = get_desktop_dir() / "Azazael" / "ANALYSIS_REPORT_DIVERSE.html"


class HORUSAnimationBridge:
    """Bridge between HORUS and Animation Protocols."""

    def __init__(self):
        self.animations = self._load_animation_protocols()
        self.results = {}

    def _load_animation_protocols(self) -> dict:
        """Load available animation protocols."""
        return {
            "STATUS_ANIMATIONS": ["Glitch Status", "Neon Pulse", "Ring Pulse"],
            "PROGRESS_ANIMATIONS": ["Linear Bar", "Shimmer Bar", "Bounce Dots"],
            "DATA_VISUALIZATION": ["Bar Growth", "Pie Fill", "Line Draw"],
            "ENTRY_ANIMATIONS": ["Slide In Up", "Fade In Scale", "Typewriter"],
            "TRANSFORMATION_ANIMATIONS": ["Flip 3D", "Morph", "Elastic Scale"],
            "DECORATIVE_ANIMATIONS": ["Particle Float", "Hex Grid", "Matrix Rain"],
            "TEXT_ANIMATIONS": ["Rainbow Wave", "Gradient Flow", "Blur Reveal"],
            "INTERACTIVE_FEEDBACK": ["Hover Expand", "Click Ripple", "Success Check"],
        }

    def get_animation(self, category: str, name: str) -> dict[str, Any]:
        """Get specific animation configuration."""
        return {
            "category": category,
            "name": name,
            "available": name in self.animations.get(category, []),
            "timestamp": datetime.now().isoformat(),
        }

    def animate_result(
        self, result_type: str, value: Any, style: str = "default"
    ) -> dict[str, Any]:
        """Create animated result for HORUS display."""

        animation_map = {
            "success": {
                "category": "STATUS_ANIMATIONS",
                "name": "Neon Pulse",
                "color": "#00ff88",
            },
            "error": {
                "category": "STATUS_ANIMATIONS",
                "name": "Glitch Status",
                "color": "#ff0044",
            },
            "warning": {
                "category": "STATUS_ANIMATIONS",
                "name": "Neon Pulse",
                "color": "#ffdd00",
            },
            "progress": {
                "category": "PROGRESS_ANIMATIONS",
                "name": "Shimmer Bar",
                "color": "#00f5ff",
            },
            "data": {
                "category": "DATA_VISUALIZATION",
                "name": "Bar Growth",
                "color": "#bf00ff",
            },
            "system": {
                "category": "STATUS_ANIMATIONS",
                "name": "Ring Pulse",
                "color": "#00f5ff",
            },
        }

        config = animation_map.get(result_type, animation_map["system"])

        return {
            "result_type": result_type,
            "value": str(value),
            "animation": config,
            "style": style,
            "timestamp": datetime.now().isoformat(),
            "shared_with_horus": True,
        }

    def generate_horus_widget(self, data: dict[str, Any]) -> str:
        """Generate HORUS-compatible widget HTML."""

        animation = data.get("animation", {})
        category = animation.get("category", "STATUS_ANIMATIONS")
        name = animation.get("name", "Neon Pulse")

        html = f"""
<div class="horus-widget" data-category="{category}" data-animation="{name}">
    <style>
        .horus-widget {{
            padding: 20px;
            border-radius: 12px;
            background: linear-gradient(135deg, rgba(15, 15, 30, 0.9), rgba(22, 33, 62, 0.8));
            border: 2px solid {animation.get("color", "#00f5ff")};
            animation: widget-appear 0.5s ease-out;
        }}
        @keyframes widget-appear {{
            from {{ transform: scale(0.9); opacity: 0; }}
            to {{ transform: scale(1); opacity: 1; }}
        }}
    </style>
    <div class="horus-content">
        <span class="horus-value">{data.get("value", "N/A")}</span>
        <span class="horus-type">{data.get("result_type", "unknown")}</span>
    </div>
    <div class="horus-indicator" style="background: {animation.get("color", "#00f5ff")};"></div>
</div>
"""
        return html

    def export_to_horus(self, filepath: (str | None) = None) -> dict[str, Any]:
        """Export animation protocols for HORUS consumption."""

        export = {
            "timestamp": datetime.now().isoformat(),
            "source": "Azazael Animation Protocols",
            "target": "HORUS Live Monitor",
            "version": "1.0.0",
            "animations": self.animations,
            "recommendations": {
                "success": {"animation": "Bounce Dots", "color": "#00ff88"},
                "error": {"animation": "Shake", "color": "#ff0044"},
                "warning": {"animation": "Neon Pulse", "color": "#ffdd00"},
                "info": {"animation": "Slide In Up", "color": "#00f5ff"},
                "progress": {"animation": "Shimmer Bar", "color": "#bf00ff"},
            },
            "integration_points": [
                "HORUS_LIVE_MONITOR.py",
                "Azazael_LIVE_MONITOR.py",
                "ECOSYSTEM_CRAWLER_RESULTS.json",
            ],
        }

        if filepath:
            path = Path(filepath)
            path.write_text(json.dumps(export, indent=2), encoding="utf-8")

        return export


def share_with_horus():
    """Main function to share animation protocols with HORUS."""

    print("=" * 60)
    print("SHARING WITH HORUS")
    print("=" * 60)

    bridge = HORUSAnimationBridge()

    # Export for HORUS
    export = bridge.export_to_horus()

    export_path = get_desktop_dir() / "Azazael" / "HORUS_ANIMATION_SHARE.json"
    export["export_path"] = str(export_path)
    export_path.write_text(json.dumps(export, indent=2), encoding="utf-8")

    print(f"\n>> Exported to: {export_path}")
    print(f">> Categories: {len(bridge.animations)}")
    print(f">> Integration Points: {len(export['integration_points'])}")

    # Test animations
    print("\n--- Testing Animations ---")
    test_cases = ["success", "error", "warning", "progress", "data"]

    for case in test_cases:
        result = bridge.animate_result(case, f"Test {case.title()}")
        print(
            f"  {case:10} -> {result['animation']['name']} ({result['animation']['color']})"
        )

    # Share paths
    print("\n--- Share Information ---")
    print(f"  Animation Protocols: {ANIMATION_PROTOCOLS_PATH}")
    print(f"  Sample Results: {RESULTS_PATH}")
    print(f"  HORUS Export: {export_path}")

    return export


if __name__ == "__main__":
    share_with_horus()
