# coding: utf-8
"""
ENRICHMENT DISPLAY PROTOCOLS
============================
Comprehensive animations for displaying enriched data in the CODEX ecosystem
Implements: Cascade Reveal, Data Pulse, Connect Lines, Expand Card, Counter Increment,
Badge Pop, Tag Cloud, Timeline Draw, and Status-Specific Protocols
"""

from dataclasses import dataclass, field
from typing import Any, List
from enum import Enum
import random

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class AnimationState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED = "paused"


class EnrichmentType(Enum):
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DATA = "data"
    PROGRESS = "progress"
    SYSTEM = "system"
    HIERARCHICAL = "hierarchical"


@dataclass
class EnrichmentField:
    name: str
    value: Any
    type: str = "text"
    priority: int = 0
    parent: (str | None) = None
    children: list[str] = field(default_factory=list)


@dataclass
class AnimationConfig:
    duration: float = 0.5
    stagger: float = 0.1
    easing: str = "ease-out"
    repeat: bool = False
    reverse: bool = False


class EnrichmentDisplay:
    def __init__(self):
        self.fields: dict[str, EnrichmentField] = {}
        self.animations: dict[str, dict] = {}
        self.state = AnimationState.PENDING
        self.counters: dict[str, int] = {}
        self.timelines: dict[str, list[dict]] = {}

    def add_field(self, field: EnrichmentField):
        self.fields[field.name] = field
        self._setup_animation(field)

    def _setup_animation(self, field: EnrichmentField):
        base_delay = field.priority * 0.1
        self.animations[field.name] = {
            "state": AnimationState.PENDING,
            "delay": base_delay,
            "progress": 0.0,
            "config": AnimationConfig(duration=0.5, stagger=0.1),
        }

    def cascade_reveal(self) -> list[dict]:
        """Multi-field records cascade in with stagger delays."""
        sorted_fields = sorted(self.fields.values(), key=lambda f: f.priority)
        return [
            {
                "field": f.name,
                "delay": i * 0.1,
                "keyframes": [
                    {"offset": 0, "opacity": 0, "transform": "translateY(20px)"},
                    {"offset": 0.5, "opacity": 0.5, "transform": "translateY(-5px)"},
                    {"offset": 1, "opacity": 1, "transform": "translateY(0)"},
                ],
            }
            for i, f in enumerate(sorted_fields)
        ]

    def data_pulse(self, field_name: str, intensity: float = 1.0) -> dict:
        """Highlight changes with glow pulse."""
        return {
            "field": field_name,
            "effect": "pulse",
            "keyframes": [
                {
                    "offset": 0,
                    "box-shadow": f"0 0 0 0 rgba(0, 245, 255, {intensity * 0.7})",
                },
                {
                    "offset": 0.25,
                    "box-shadow": f"0 0 20px 10px rgba(0, 245, 255, {intensity * 0.5})",
                },
                {
                    "offset": 0.5,
                    "box-shadow": f"0 0 30px 15px rgba(0, 245, 255, {intensity * 0.3})",
                },
                {"offset": 1, "box-shadow": "0 0 0 0 rgba(0, 245, 255, 0)"},
            ],
            "duration": 0.8,
        }

    def connect_lines(self, source: str, targets: list[str]) -> list[dict]:
        """Draw lines between connected data points."""
        return [
            {
                "from": source,
                "to": target,
                "effect": "draw",
                "stroke": "cyan",
                "stroke-width": 2,
                "stroke-dasharray": 100,
                "stroke-dashoffset": 100,
                "animation": {"from": 100, "to": 0, "duration": 0.6},
            }
            for target in targets
        ]

    def expand_card(self, field_name: str, expanded: bool = True) -> dict:
        """Card expands with elastic bounce."""
        scale_start = 0.8 if expanded else 1.0
        scale_end = 1.0 if expanded else 0.8
        return {
            "field": field_name,
            "effect": "elastic-scale",
            "keyframes": [
                {"offset": 0, "transform": f"scale({scale_start})", "opacity": 0},
                {"offset": 0.4, "transform": f"scale({scale_end + 0.1})", "opacity": 1},
                {"offset": 0.7, "transform": f"scale({scale_end - 0.02})"},
                {"offset": 1, "transform": f"scale({scale_end})"},
            ],
            "duration": 0.5,
            "easing": "cubic-bezier(0.68, -0.55, 0.265, 1.55)",
        }

    def counter_increment(
        self, field_name: str, start: int, end: int, duration: float = 1.0
    ) -> dict:
        """Numbers count up/down to final value."""
        self.counters[field_name] = start
        steps = abs(end - start)
        step_duration = duration / max(steps, 1)
        return {
            "field": field_name,
            "effect": "counter",
            "start": start,
            "end": end,
            "steps": steps,
            "step-duration": step_duration,
            "format": lambda n: f"{n:,}",
        }

    def badge_pop(self, badges: list[dict]) -> list[dict]:
        """Badges pop in with rotation."""
        return [
            {
                "badge": badge.get("text", ""),
                "color": badge.get("color", "cyan"),
                "effect": "pop-rotate",
                "keyframes": [
                    {
                        "offset": 0,
                        "transform": "scale(0) rotate(-180deg)",
                        "opacity": 0,
                    },
                    {"offset": 0.6, "transform": "scale(1.2) rotate(10deg)"},
                    {"offset": 0.8, "transform": "scale(0.95) rotate(-5deg)"},
                    {"offset": 1, "transform": "scale(1) rotate(0deg)", "opacity": 1},
                ],
                "duration": 0.4,
                "delay": i * 0.08,
            }
            for i, badge in enumerate(badges)
        ]

    def tag_cloud(self, tags: list[str]) -> list[dict]:
        """Tags float and settle into place."""
        colors = ["#00f5ff", "#bf00ff", "#00ff88", "#ff0066", "#ffaa00", "#00aaff"]
        return [
            {
                "tag": tag,
                "color": colors[i % len(colors)],
                "effect": "float-settle",
                "keyframes": [
                    {
                        "offset": 0,
                        "transform": f"translateY(-{random.randint(50, 150)}px) rotate({random.randint(-30, 30)}deg)",
                        "opacity": 0,
                    },
                    {
                        "offset": 0.3,
                        "transform": "translateY(10px) rotate(5deg)",
                        "opacity": 0.5,
                    },
                    {
                        "offset": 0.6,
                        "transform": "translateY(-5px) rotate(-2deg)",
                        "opacity": 0.8,
                    },
                    {"offset": 1, "transform": "translateY(0) rotate(0)", "opacity": 1},
                ],
                "duration": 0.6,
                "delay": i * 0.05,
            }
            for i, tag in enumerate(tags)
        ]

    def timeline_draw(self, events: list[dict]) -> list[dict]:
        """Timeline draws itself left-to-right."""
        return [
            {
                "event": event.get("title", ""),
                "description": event.get("description", ""),
                "timestamp": event.get("timestamp", ""),
                "effect": "draw-line",
                "line-progress": 0,
                "marker-effect": "pop",
                "delay": i * 0.3,
                "duration": 0.5,
            }
            for i, event in enumerate(events)
        ]

    def get_status_protocol(self, enrichment_type: EnrichmentType) -> dict:
        """Get animation protocol for result type."""
        protocols = {
            EnrichmentType.SUCCESS: {
                "primary": "checkmark-draw",
                "secondary": "scale-bounce",
                "color": "#00ff88",
                "duration": 0.6,
            },
            EnrichmentType.ERROR: {
                "primary": "shake",
                "secondary": "red-glow",
                "color": "#ff0044",
                "duration": 0.5,
            },
            EnrichmentType.WARNING: {
                "primary": "orange-pulse",
                "secondary": "flash",
                "color": "#ffaa00",
                "duration": 1.0,
            },
            EnrichmentType.INFO: {
                "primary": "slide-in-up",
                "secondary": "fade",
                "color": "#00aaff",
                "duration": 0.4,
            },
            EnrichmentType.DATA: {
                "primary": "bar-growth",
                "secondary": "counter",
                "color": "#00f5ff",
                "duration": 0.8,
            },
            EnrichmentType.PROGRESS: {
                "primary": "linear-bar",
                "secondary": "bounce-dots",
                "color": "#bf00ff",
                "duration": 1.0,
            },
            EnrichmentType.SYSTEM: {
                "primary": "glitch",
                "secondary": "neon-pulse",
                "color": "#ff00aa",
                "duration": 0.3,
            },
            EnrichmentType.HIERARCHICAL: {
                "primary": "cascade",
                "secondary": "connect-lines",
                "color": "#00ff88",
                "duration": 1.2,
            },
        }
        return protocols.get(enrichment_type, protocols[EnrichmentType.INFO])

    def hierarchical_enrichment(self) -> dict:
        """Hierarchical enrichment: Root -> Expand -> Children cascade -> Grandchildren stagger."""
        root = None
        children = []
        grandchildren = []

        for name, field in self.fields.items():
            if field.parent is None:
                root = field
            elif field.parent and field.parent in self.fields:
                children.append(field)
            else:
                grandchildren.append(field)

        return {
            "root": {
                "field": root.name if root else None,
                "effect": "expand-root",
                "delay": 0,
                "duration": 0.4,
            },
            "children": [
                {
                    "field": c.name,
                    "effect": "cascade-in",
                    "delay": 0.4 + i * 0.1,
                    "duration": 0.3,
                }
                for i, c in enumerate(children)
            ],
            "grandchildren": [
                {
                    "field": g.name,
                    "effect": "cascade-stagger",
                    "delay": 0.8 + i * 0.08,
                    "duration": 0.2,
                }
                for i, g in enumerate(grandchildren)
            ],
            "connections": self.connect_lines(
                root.name if root else "", [c.name for c in children] if root else []
            ),
        }

    def generate_css(self) -> str:
        """Generate CSS for all enrichment animations."""
        return """
/* ENRICHMENT DISPLAY CSS */
:root {
    --enrich-cyan: #00f5ff;
    --enrich-purple: #bf00ff;
    --enrich-green: #00ff88;
    --enrich-pink: #ff0066;
    --enrich-orange: #ffaa00;
    --enrich-blue: #00aaff;
}

@keyframes cascadeReveal {
    0% { opacity: 0; transform: translateY(20px); }
    50% { opacity: 0.5; transform: translateY(-5px); }
    100% { opacity: 1; transform: translateY(0); }
}

@keyframes dataPulse {
    0% { box-shadow: 0 0 0 0 rgba(0, 245, 255, 0.7); }
    50% { box-shadow: 0 0 20px 10px rgba(0, 245, 255, 0.5); }
    100% { box-shadow: 0 0 0 0 rgba(0, 245, 255, 0); }
}

@keyframes connectDraw {
    to { stroke-dashoffset: 0; }
}

@keyframes elasticScale {
    0% { transform: scale(0.8); opacity: 0; }
    40% { transform: scale(1.1); }
    70% { transform: scale(0.98); }
    100% { transform: scale(1); opacity: 1; }
}

@keyframes counterIncrement {
    from { content: attr(data-start); }
    to { content: attr(data-end); }
}

@keyframes badgePop {
    0% { transform: scale(0) rotate(-180deg); opacity: 0; }
    60% { transform: scale(1.2) rotate(10deg); }
    80% { transform: scale(0.95) rotate(-5deg); }
    100% { transform: scale(1) rotate(0); opacity: 1; }
}

@keyframes tagFloat {
    0% { transform: translateY(-100px) rotate(-15deg); opacity: 0; }
    60% { transform: translateY(10px) rotate(5deg); opacity: 0.8; }
    100% { transform: translateY(0) rotate(0); opacity: 1; }
}

@keyframes timelineDraw {
    from { stroke-dashoffset: 100; }
    to { stroke-dashoffset: 0; }
}

@keyframes timelineMarker {
    0% { transform: scale(0); opacity: 0; }
    50% { transform: scale(1.3); }
    100% { transform: scale(1); opacity: 1; }
}

@keyframes successCheck {
    0% { stroke-dashoffset: 100; }
    100% { stroke-dashoffset: 0; }
}

@keyframes shake {
    0%, 100% { transform: translateX(0); }
    10%, 30%, 50%, 70%, 90% { transform: translateX(-5px); }
    20%, 40%, 60%, 80% { transform: translateX(5px); }
}

@keyframes neonPulse {
    0%, 100% { text-shadow: 0 0 5px currentColor, 0 0 10px currentColor; }
    50% { text-shadow: 0 0 20px currentColor, 0 0 40px currentColor, 0 0 60px currentColor; }
}

/* Utility Classes */
.enrich-cascade { animation: cascadeReveal var(--duration, 0.5s) ease-out forwards; }
.enrich-pulse { animation: dataPulse 0.8s ease-out; }
.enrich-connect { stroke-dasharray: 100; stroke-dashoffset: 100; animation: connectDraw 0.6s ease-out forwards; }
.enrich-expand { animation: elasticScale 0.5s cubic-bezier(0.68, -0.55, 0.265, 1.55) forwards; }
.enrich-badge { animation: badgePop 0.4s ease-out forwards; }
.enrich-tag { animation: tagFloat 0.6s ease-out forwards; }
.enrich-timeline-line { stroke-dasharray: 100; stroke-dashoffset: 100; animation: timelineDraw 0.5s ease-out forwards; }
.enrich-success { stroke-dasharray: 100; stroke-dashoffset: 100; animation: successCheck 0.6s ease-out forwards; }
.enrich-shake { animation: shake 0.5s ease-in-out; }
.enrich-neon { animation: neonPulse 1s ease-in-out infinite; }
"""

    def get_all_protocols(self) -> dict:
        """Get all enrichment display protocols."""
        return {
            "cascade_reveal": self.cascade_reveal(),
            "data_pulse": [self.data_pulse(f) for f in self.fields],
            "connect_lines": self.connect_lines("", []),
            "expand_card": self.expand_card(""),
            "counter_increment": [
                self.counter_increment(f, 0, 100) for f in self.fields
            ],
            "badge_pop": self.badge_pop([]),
            "tag_cloud": self.tag_cloud([]),
            "timeline_draw": self.timeline_draw([]),
            "hierarchical": self.hierarchical_enrichment(),
            "css": self.generate_css(),
        }


def create_enrichment_display(fields: list[EnrichmentField]) -> EnrichmentDisplay:
    """Create enrichment display with given fields."""
    display = EnrichmentDisplay()
    for field in fields:
        display.add_field(field)
    return display


if __name__ == "__main__":
    print("ENRICHMENT DISPLAY PROTOCOLS - CODEX")
    print("=" * 50)

    display = EnrichmentDisplay()

    display.add_field(EnrichmentField("id", "CODEX-001", priority=0))
    display.add_field(EnrichmentField("name", "System Alpha", priority=1))
    display.add_field(EnrichmentField("status", "active", priority=2))
    display.add_field(EnrichmentField("metrics", [1, 2, 3], priority=3))

    protocols = display.get_all_protocols()

    print(f"\nFields: {len(display.fields)}")
    print(f"Animations: {len(display.animations)}")

    print("\n--- Protocols Available ---")
    for key in protocols:
        print(f"  - {key}")

    print("\n--- Status Protocols ---")
    for etype in EnrichmentType:
        proto = display.get_status_protocol(etype)
        print(f"  {etype.value}: {proto['primary']} ({proto['duration']}s)")
