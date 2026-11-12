# coding: utf-8
"""
Azazael Visualizer - PyGame Version
Immersive visualization using Python + PyGame
Includes: Core System Animations + Enrichment Display Protocols
"""

import random
import math
import sys
from dataclasses import dataclass, field
from typing import Any, List
from enum import Enum
import time
import random as rand

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


#: pygame is a GUI dependency and is deliberately OPTIONAL: it is a compiled wheel
#: that is not installed on every host, and only this module's `__main__` block
#: consumes it. MEASURED 2026-10-11: this import used to be BARE and unguarded at
#: the top of the file, which made the `__main__` failure handler UNREACHABLE --
#: `ModuleNotFoundError` fired during import, long before the `try` that was
#: supposed to report it, so the advertised "PyGame not available" message could
#: never print (the module simply died with a traceback). It now sits AFTER
#: `note_failure` is bound, so the handler can record rather than swallow.
try:
    import pygame
except ImportError as exc:                               # optional GUI dependency
    note_failure("azazael_pygame:pygame-import", exc)
    pygame = None

#: Retained as a module contract attribute: tests/test_pygame_shim.py asserts
#: its identity against the shim, and importers may read it. The two in-module
#: gates (the module init below and the `__main__` entry point) now test
#: `pygame is not None` directly, so pyright narrows the optional module
#: instead of reading this bool.
_PYGAME_AVAILABLE = pygame is not None


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
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    size: float
    color: tuple[int, int, int]
    life: float


@dataclass
class EnrichmentField:
    name: str
    value: Any
    type: str = "text"
    priority: int = 0
    parent: (str | None) = None
    children: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.children is None:
            self.children = []


@dataclass
class Badge:
    text: str
    color: tuple[int, int, int]
    x: float
    y: float
    scale: float = 0.0
    rotation: float = 0.0
    opacity: float = 0.0


@dataclass
class Tag:
    text: str
    color: tuple[int, int, int]
    x: float
    y: float
    target_y: float
    rotation: float
    opacity: float = 0.0


@dataclass
class TimelineEvent:
    title: str
    description: str
    timestamp: str
    x: float
    y: float
    line_progress: float = 0.0
    marker_scale: float = 0.0


class EnrichmentDisplay:
    def __init__(self):
        self.fields: dict[str, EnrichmentField] = {}
        self.badges: list[Badge] = []
        self.tags: list[Tag] = []
        self.timeline_events: list[TimelineEvent] = []
        self.hierarchical_animating = False
        self.hierarchical_progress = 0.0
        self.hierarchical_phase = "root"

    def add_field(self, field: EnrichmentField):
        self.fields[field.name] = field

    def add_badge(self, text: str, color: tuple[int, int, int], x: float, y: float):
        self.badges.append(Badge(text, color, x, y))

    def add_tag(
        self, text: str, color: tuple[int, int, int], x: float, target_y: float
    ):

        self.tags.append(
            Tag(
                text,
                color,
                x,
                target_y + rand.randint(-30, 30),
                rand.randint(-15, 15),
                0.0,
            )
        )

    def add_timeline_event(
        self, title: str, description: str, timestamp: str, x: float, y: float
    ):
        self.timeline_events.append(TimelineEvent(title, description, timestamp, x, y))

    def update_enrichment_animations(self, dt: float):
        for badge in self.badges:
            if badge.scale < 1.0:
                badge.scale = min(badge.scale + dt * 2.5, 1.0)
                badge.rotation = badge.rotation * 0.9
                badge.opacity = min(badge.opacity + dt * 2.5, 1.0)
            bounce = math.sin(time.time() * 5) * 0.05
            badge.scale = (1.0 + bounce) if badge.scale >= 1.0 else badge.scale

        for tag in self.tags:
            if tag.opacity < 1.0:
                tag.opacity = min(tag.opacity + dt * 1.5, 1.0)
                tag.y += (tag.target_y - tag.y) * 0.1
                tag.rotation *= 0.95

        for event in self.timeline_events:
            if event.line_progress < 1.0:
                event.line_progress = min(event.line_progress + dt * 2.0, 1.0)
            if event.marker_scale < 1.0:
                event.marker_scale = min(event.marker_scale + dt * 3.0, 1.0)

    def start_hierarchical(self):
        self.hierarchical_animating = True
        self.hierarchical_progress = 0.0
        self.hierarchical_phase = "root"

    def update_hierarchical(self, dt: float, systems: list[dict]):
        if not self.hierarchical_animating:
            return

        self.hierarchical_progress += dt * 0.8

        if self.hierarchical_progress < 0.4:
            self.hierarchical_phase = "root"
            for sys in systems:
                sys["scale"] = self.hierarchical_progress / 0.4
        elif self.hierarchical_progress < 0.8:
            self.hierarchical_phase = "children"
            progress = (self.hierarchical_progress - 0.4) / 0.4
            for i, sys in enumerate(systems):
                if i > 0:
                    sys["scale"] = min(progress, 1.0)
                    sys["opacity"] = min(progress, 1.0)
        elif self.hierarchical_progress < 1.2:
            self.hierarchical_phase = "connections"
            progress = (self.hierarchical_progress - 0.8) / 0.4
            for sys in systems:
                sys["connection_alpha"] = min(progress * 255, 255)
        else:
            self.hierarchical_phase = "complete"
            self.hierarchical_animating = False
            for sys in systems:
                sys["scale"] = 1.0
                sys["opacity"] = 1.0
                sys["connection_alpha"] = 255


if pygame is not None:
    pygame.init()                                # absent: skip, do not AttributeError

WIDTH, HEIGHT = 1200, 800
FPS = 60

COLORS = {
    "bg": (5, 5, 15),
    "cyan": (0, 245, 255),
    "purple": (191, 0, 255),
    "pink": (255, 0, 170),
    "yellow": (255, 221, 0),
    "green": (0, 255, 136),
    "orange": (255, 107, 53),
    "white": (255, 255, 255),
}

SYSTEMS = [
    ("HYDRA", "Orchestration", COLORS["pink"], "6 Stages, 10 Nodes"),
    ("HORUS", "Presentation", COLORS["cyan"], "24 Animations"),
    ("TARBABY", "Processing", COLORS["green"], "Binary Ops"),
    ("SHORTY", "Integration", COLORS["yellow"], "~20 Shortcuts"),
    ("POINDEXTER", "Analysis", COLORS["purple"], "Statistics"),
    ("PERSEPHONE", "Utilities", COLORS["orange"], "Cross-Platform"),
    ("TWITLER", "Monitoring", COLORS["green"], "Health, Alerts"),
    ("WORMZ", "Execution", COLORS["orange"], "6 Worms, 5 Clusters"),
    ("METAWORMS", "Orchestration", COLORS["purple"], "11 Agents, ML"),
    ("TERENCE", "Error", COLORS["cyan"], "26 Patterns"),
    ("KLINK", "Integration", COLORS["pink"], "Linking, APIs"),
]


class AzazaelVisualizer:
    def __init__(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Azazael ECOSYSTEM - Immersive Visualizer")
        self.clock = pygame.time.Clock()
        self.particles: list[Particle] = []
        self.systems = []
        self.enrichment_display = EnrichmentDisplay()
        self.setup_systems()
        self.setup_enrichment()
        self.font_large = pygame.font.Font(None, 72)
        self.font_medium = pygame.font.Font(None, 36)
        self.font_small = pygame.font.Font(None, 24)
        self.font_tiny = pygame.font.Font(None, 18)
        self.running = True
        self.status_effect = None
        self.status_timer = 0.0

    def setup_enrichment(self):
        self.enrichment_display.add_field(
            EnrichmentField("id", "CODEX-001", priority=0)
        )
        self.enrichment_display.add_field(
            EnrichmentField("status", "Active", priority=1)
        )
        self.enrichment_display.add_field(EnrichmentField("enriched", True, priority=2))

        badge_colors = [
            (0, 245, 255),
            (191, 0, 255),
            (255, 0, 170),
            (255, 221, 0),
            (0, 255, 136),
            (255, 107, 53),
        ]
        for i, sys in enumerate(self.systems[:6]):
            self.enrichment_display.add_badge(
                sys["name"][:2].upper(), badge_colors[i], sys["x"], sys["y"]
            )

        for i, sys in enumerate(["HYDRA", "HORUS", "TARBABY", "WORMZ"]):
            self.enrichment_display.add_tag(
                sys, badge_colors[i], WIDTH - 100, 100 + i * 60
            )

        events = [
            ("System Init", "Core modules loaded", "00:00:01", WIDTH // 2 - 200, 650),
            ("Network Connect", "8 nodes active", "00:00:02", WIDTH // 2, 650),
            ("Data Enrich", "24 fields processed", "00:00:03", WIDTH // 2 + 200, 650),
        ]
        for title, desc, ts, x, y in events:
            self.enrichment_display.add_timeline_event(title, desc, ts, x, y)

    def setup_systems(self):
        center_x, center_y = WIDTH // 2, HEIGHT // 2
        radius = 280

        for i, (name, category, color, desc) in enumerate(SYSTEMS):
            angle = (2 * math.pi * i / len(SYSTEMS)) - math.pi / 2
            x = center_x + radius * math.cos(angle)
            y = center_y + radius * math.sin(angle)
            self.systems.append(
                {
                    "name": name,
                    "category": category,
                    "color": color,
                    "desc": desc,
                    "x": x,
                    "y": y,
                    "base_x": x,
                    "base_y": y,
                    "angle": angle,
                    "pulse": 0,
                    "hovered": False,
                }
            )

    def create_particle(self, x: int, y: int, color: tuple[int, int, int]) -> Particle:
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1, 3)
        return Particle(
            x=x,
            y=y,
            vx=math.cos(angle) * speed,
            vy=math.sin(angle) * speed,
            size=random.uniform(2, 5),
            color=color,
            life=random.uniform(30, 60),
        )

    def update(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        mx, my = pygame.mouse.get_pos()

        # Update particles
        for p in self.particles[:]:
            p.x += p.vx
            p.y += p.vy
            p.life -= 1
            p.size *= 0.98
            if p.life <= 0:
                self.particles.remove(p)

        # Update systems
        for sys in self.systems:
            # Mouse interaction
            dx = mx - sys["x"]
            dy = my - sys["y"]
            dist = math.sqrt(dx * dx + dy * dy)

            if dist < 100:
                sys["hovered"] = True
                sys["pulse"] = min(sys["pulse"] + 0.1, 1)
                # Emit particles
                if random.random() < 0.3:
                    self.particles.append(
                        self.create_particle(int(sys["x"]), int(sys["y"]), sys["color"])
                    )
            else:
                sys["hovered"] = False
                sys["pulse"] = max(sys["pulse"] - 0.02, 0)

            # Orbital motion
            sys["angle"] += 0.002
            radius = 280 + sys["pulse"] * 50
            target_x = WIDTH // 2 + radius * math.cos(sys["angle"])
            target_y = HEIGHT // 2 + radius * math.sin(sys["angle"])

            sys["x"] += (target_x - sys["x"]) * 0.05
            sys["y"] += (target_y - sys["y"]) * 0.05

    def draw_badges(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        for badge in self.enrichment_display.badges:
            size = 20 * badge.scale

            glow_surf = pygame.Surface((int(size * 4), int(size * 4)), pygame.SRCALPHA)
            pygame.draw.circle(
                glow_surf,
                (*badge.color, int(30 * badge.opacity)),
                (int(size * 2), int(size * 2)),
                int(size * 1.5),
            )
            self.screen.blit(glow_surf, (badge.x - size * 2, badge.y - size * 2))

            pygame.draw.circle(
                self.screen, badge.color, (int(badge.x), int(badge.y)), int(size)
            )
            pygame.draw.circle(
                self.screen, COLORS["bg"], (int(badge.x), int(badge.y)), int(size - 2)
            )

            text = self.font_tiny.render(badge.text, True, badge.color)
            text_rect = text.get_rect(center=(int(badge.x), int(badge.y)))
            self.screen.blit(text, text_rect)

    def draw_tags(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        for tag in self.enrichment_display.tags:
            tag_surface = self.font_small.render(tag.text, True, tag.color)
            w, h = tag_surface.get_size()

            pygame.draw.rect(
                self.screen,
                (*tag.color, int(tag.opacity * 50)),
                (tag.x - 10, tag.y - h // 2 - 2, w + 20, h + 4),
                border_radius=4,
            )
            pygame.draw.rect(
                self.screen,
                tag.color,
                (tag.x - 10, tag.y - h // 2 - 2, w + 20, h + 4),
                1,
                border_radius=4,
            )

            rotated = pygame.transform.rotate(tag_surface, tag.rotation)
            rect = rotated.get_rect(center=(tag.x + w // 2, tag.y))
            self.screen.blit(rotated, rect)

    def draw_timeline(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        y_base = HEIGHT - 80
        pygame.draw.line(
            self.screen, COLORS["purple"], (100, y_base), (WIDTH - 100, y_base), 2
        )

        prev_x = None
        for event in self.enrichment_display.timeline_events:
            if prev_x is not None:
                line_len = event.x - prev_x
                pygame.draw.line(
                    self.screen,
                    COLORS["purple"],
                    (prev_x, y_base),
                    (prev_x + line_len * event.line_progress, y_base),
                    2,
                )

            marker_size = 12 * event.marker_scale
            pygame.draw.circle(
                self.screen, COLORS["cyan"], (event.x, y_base), int(marker_size)
            )
            pygame.draw.circle(
                self.screen, COLORS["bg"], (event.x, y_base), int(marker_size - 2)
            )

            title = self.font_tiny.render(event.title, True, COLORS["white"])
            self.screen.blit(title, (event.x - title.get_width() // 2, y_base - 25))

            desc = self.font_tiny.render(event.description, True, COLORS["green"])
            self.screen.blit(desc, (event.x - desc.get_width() // 2, y_base + 8))

            ts = self.font_tiny.render(event.timestamp, True, COLORS["orange"])
            self.screen.blit(ts, (event.x - ts.get_width() // 2, y_base + 22))

            prev_x = event.x

    def draw_enrichment_status(self):
        status = self.font_medium.render("ENRICHMENT ACTIVE", True, COLORS["cyan"])
        self.screen.blit(status, (20, HEIGHT - 80))

        field_count = len(self.enrichment_display.fields)
        badge_count = len(self.enrichment_display.badges)
        tag_count = len(self.enrichment_display.tags)
        timeline_count = len(self.enrichment_display.timeline_events)

        info = self.font_tiny.render(
            f"Fields: {field_count} | Badges: {badge_count} | Tags: {tag_count} | Timeline: {timeline_count}",
            True,
            COLORS["green"],
        )
        self.screen.blit(info, (20, HEIGHT - 60))

    def draw(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        self.screen.fill(COLORS["bg"])

        # Draw connections
        center = (WIDTH // 2, HEIGHT // 2)
        for i, sys in enumerate(self.systems):
            # Line to center
            pygame.draw.line(self.screen, sys["color"], (sys["x"], sys["y"]), center, 1)

            # Draw connections between adjacent systems
            next_sys = self.systems[(i + 1) % len(self.systems)]
            pygame.draw.line(
                self.screen,
                sys["color"],
                (sys["x"], sys["y"]),
                (next_sys["x"], next_sys["y"]),
                1,
            )

        # Draw particles
        for p in self.particles:
            s = pygame.Surface((int(p.size * 2), int(p.size * 2)), pygame.SRCALPHA)
            pygame.draw.circle(
                s, (*p.color, int(p.life * 4)), (int(p.size), int(p.size)), int(p.size)
            )
            self.screen.blit(s, (int(p.x - p.size), int(p.y - p.size)))

        # Draw systems
        for sys in self.systems:
            pulse_size = 40 + sys["pulse"] * 20

            # Glow effect
            for i in range(5, 0, -1):
                glow_surface = pygame.Surface(
                    (pulse_size * 2 + i * 10, pulse_size * 2 + i * 10), pygame.SRCALPHA
                )
                pygame.draw.circle(
                    glow_surface,
                    (*sys["color"], int(20 * sys["pulse"] / i)),
                    (pulse_size + i * 5, pulse_size + i * 5),
                    pulse_size + i * 5,
                )
                self.screen.blit(
                    glow_surface,
                    (
                        int(sys["x"]) - pulse_size - i * 5,
                        int(sys["y"]) - pulse_size - i * 5,
                    ),
                )

            # Main circle
            pygame.draw.circle(
                self.screen, sys["color"], (int(sys["x"]), int(sys["y"])), pulse_size
            )
            pygame.draw.circle(
                self.screen,
                COLORS["bg"],
                (int(sys["x"]), int(sys["y"])),
                pulse_size - 2,
            )

            # Icon (first letter)
            letter = self.font_large.render(sys["name"][0], True, sys["color"])
            letter_rect = letter.get_rect(center=(int(sys["x"]), int(sys["y"])))
            self.screen.blit(letter, letter_rect)

            # Name label
            name_text = self.font_small.render(sys["name"], True, COLORS["white"])
            name_rect = name_text.get_rect(
                center=(int(sys["x"]), int(sys["y"]) + pulse_size + 20)
            )
            self.screen.blit(name_text, name_rect)

            # Category
            cat_text = self.font_small.render(
                sys["category"], True, (*sys["color"], 180)
            )
            cat_rect = cat_text.get_rect(
                center=(int(sys["x"]), int(sys["y"]) + pulse_size + 38)
            )
            self.screen.blit(cat_text, cat_rect)

        # Title
        title = self.font_large.render("Azazael ECOSYSTEM", True, COLORS["cyan"])
        title_rect = title.get_rect(center=(WIDTH // 2, 50))
        self.screen.blit(title, title_rect)

        # Stats
        stats = self.font_medium.render(
            "11 Systems | 7 Tiers | 24 Animations | 26 Patterns", True, COLORS["green"]
        )
        stats_rect = stats.get_rect(center=(WIDTH // 2, HEIGHT - 40))
        self.screen.blit(stats, stats_rect)

        self.draw_badges()
        self.draw_tags()
        self.draw_timeline()
        self.draw_enrichment_status()

        pygame.display.flip()

    def handle_events(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_f:
                    pygame.display.toggle_fullscreen()

    def run(self):
        if pygame is None:
            raise RuntimeError("pygame is required: python -m pip install pygame")
        dt = 1.0 / FPS
        while self.running:
            self.handle_events()
            self.update()
            self.enrichment_display.update_enrichment_animations(dt)
            self.draw()
            self.clock.tick(FPS)

        pygame.quit()


if __name__ == "__main__":
    if pygame is None:
        print("azazael_pygame: the 'pygame' package is required and is not installed.")
        print("Install it with:  python -m pip install pygame")
        sys.exit(2)
    try:
        viz = AzazaelVisualizer()
        viz.run()
    except pygame.error as e:
        print(f"PyGame not available: {e}")
        print("Run: python -m pip install pygame")
        sys.exit(2)
