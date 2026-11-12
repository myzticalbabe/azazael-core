#!/usr/bin/env python3
"""
CODEX_TOOLS — Worm Wrappers for Orphan CODEX/CORE Standalone Tools
====================================================================
Wraps 4 previously standalone CODEX/CORE tools as registered worms,
making them discoverable and executable through the ClusterWORMZ /
MetaCluster / ClusterWORMZs execution pipeline.

Tools wrapped:
  1. CainAnalyzerWorm    — CAIN psychoanalysis framework (analysis)
  2. AutoDownloaderWorm  — ML framework detection & auto-install (orchestration)
  3. EmojiMakerWorm      — Dynamic emotional emoji generation (visualization)
  4. EnhancedAnimationsWorm — CSS animation library export (visualization)

Created: June 4, 2026 — Graphical Vault Review & G: Drive Reorganization Session
See: LESSONS_MASTER.md Lesson #52 (CODEX/CORE Standalone Tools)
"""

import sys
from pathlib import Path
from typing import Any, List

# Ensure CODEX/CORE and src/ are importable
_CODEX_CORE = Path(__file__).resolve().parent.parent.parent / "CODEX" / "CORE"
_SRC = Path(__file__).resolve().parent.parent.parent
if str(_CODEX_CORE) not in sys.path:
    sys.path.insert(0, str(_CODEX_CORE))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from CORE.worm_registry import register_worm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Module-level conditional imports (3.14t safe - no function-body imports)
try:
    from CAIN_ANALYZER import run_complete_analysis as _cain_analyze
except ImportError as exc:
    note_failure("CODEX_TOOLS:L36", exc)
    _cain_analyze = None

try:
    from AUTO_DOWNLOADER import AutoDownloader as _AutoDownloader
except ImportError as exc:
    note_failure("CODEX_TOOLS:L41", exc)
    _AutoDownloader = None

try:
    from emoji_maker import EmojiMaker as _EmojiMaker
except ImportError as exc:
    note_failure("CODEX_TOOLS:L46", exc)
    _EmojiMaker = None

try:
    from enhanced_animations import ANIMATIONS as _ANIMATIONS, generate_css as _generate_css
except ImportError as exc:
    note_failure("CODEX_TOOLS:L51", exc)
    _ANIMATIONS = {}
    _generate_css = None


# ===============================================================================
# 1. CainAnalyzerWorm — CAIN Psychoanalysis Framework
# ===============================================================================

@register_worm(
    category="analysis",
    priority=5,
    cooldown_seconds=120,
    description="CAIN psychoanalysis framework — analyzes session data for psychological accountability patterns (7 sins, Jungian archetypes, stakeholder impact)"
)
class CainAnalyzerWorm:
    """Worm wrapper for CAIN_ANALYZER standalone CLI tool.

    Delegates to CAIN_ANALYZER.run_complete_analysis() for the core logic.
    Outputs: dict with sins_analysis, psychoanalysis, emotion_analysis,
    obfuscation_analysis, and stakeholder_analysis sections.
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._analyzer_ok = _cain_analyze is not None
        self._analyze = _cain_analyze

    def execute(self) -> dict[str, Any]:
        """Run CAIN psychoanalysis and return structured results."""
        analyze = self._analyze
        if analyze is None:
            return {
                "success": False,
                "error": "CAIN_ANALYZER module not importable",
                "worm": "CainAnalyzerWorm",
            }
        try:
            result = analyze()
            return {
                "success": True,
                "worm": "CainAnalyzerWorm",
                "version": result.get("cain_version", "unknown"),
                "analysis_date": result.get("analysis_date", ""),
                "sins_count": len(result.get("sins_analysis", {}).get("sins", {})),
                "frameworks_count": len(result.get("psychoanalysis", {}).get("frameworks", {})),
                "stakeholders_count": len(result.get("stakeholder_analysis", {}).get("stakeholders", [])),
                "data": result,
            }
        except Exception as e:
            note_failure("CODEX_TOOLS:L100", e)
            return {
                "success": False,
                "error": str(e),
                "worm": "CainAnalyzerWorm",
            }
    def get_summary(self):
        # Truthful readiness, not an assumed outcome: this worm keeps no run
        # record, so it cannot report one. A hardcoded "OK" here made a worm
        # that had never executed indistinguishable from a successful one.
        return {
            "worm": "CainAnalyzerWorm",
            "status": "ready" if self._analyzer_ok else "unavailable",
            "module_importable": self._analyzer_ok,
            "run_recorded": False,
        }



# ===============================================================================
# 2. AutoDownloaderWorm — ML Framework Detection & Auto-Install
# ===============================================================================

@register_worm(
    category="orchestration",
    priority=4,
    cooldown_seconds=300,
    description="ML framework detection and auto-install manager — detects missing frameworks, verifies installations, generates status reports"
)
class AutoDownloaderWorm:
    """Worm wrapper for AUTO_DOWNLOADER AutoDownloader class.

    Delegates to AutoDownloader for framework detection, status reporting,
    and (optionally) auto-installation. Default mode is detect-only to
    avoid unexpected package installations.
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._downloader_ok = _AutoDownloader is not None
        self._AutoDownloader = _AutoDownloader

    def execute(self) -> dict[str, Any]:
        """Detect missing ML frameworks and report status."""
        downloader_cls = self._AutoDownloader
        if downloader_cls is None:
            return {
                "success": False,
                "error": "AUTO_DOWNLOADER module not importable",
                "worm": "AutoDownloaderWorm",
            }
        try:
            dl = downloader_cls()
            status = dl.get_status()
            missing = dl.detect_missing()
            return {
                "success": True,
                "worm": "AutoDownloaderWorm",
                "registered": status["frameworks_registered"],
                "installed": status["frameworks_installed"],
                "missing_count": len(missing),
                "missing": [
                    {"name": p.info.name, "category": p.info.category, "priority": p.info.priority}
                    for p in missing
                ] if missing else [],
                "statistics": status["statistics"],
            }
        except Exception as e:
            note_failure("CODEX_TOOLS:L159", e)
            return {
                "success": False,
                "error": str(e),
                "worm": "AutoDownloaderWorm",
            }
    def get_summary(self):
        # Same correction as CainAnalyzerWorm: report the readiness this worm
        # actually holds (its backing module's importability) instead of
        # asserting success for a run that may never have happened.
        return {
            "worm": "AutoDownloaderWorm",
            "status": "ready" if self._downloader_ok else "unavailable",
            "module_importable": self._downloader_ok,
            "run_recorded": False,
        }



# ===============================================================================
# 3. EmojiMakerWorm — Dynamic Emotional Emoji Generation
# ===============================================================================

@register_worm(
    category="visualization",
    priority=3,
    cooldown_seconds=60,
    description="Dynamic emotional emoji generator — 50+ emojis across 15 emotions, 5 moods, 5 categories with CSS animation mappings"
)
class EmojiMakerWorm:
    """Worm wrapper for emoji_maker EmojiMaker class.

    Exports the emoji library as structured JSON and generates
    mood palettes for UI feedback integration.
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._emoji_ok = _EmojiMaker is not None
        self._EmojiMaker = _EmojiMaker

    def execute(self) -> dict[str, Any]:
        """Export emoji library and generate mood palette samples."""
        emoji_cls = self._EmojiMaker
        if emoji_cls is None:
            return {
                "success": False,
                "error": "emoji_maker module not importable",
                "worm": "EmojiMakerWorm",
            }
        try:
            maker = emoji_cls()
            library = maker.export_library()
            return {
                "success": True,
                "worm": "EmojiMakerWorm",
                "total_emojis": library["total_emojis"],
                "categories": library["categories"],
                "emotions": library["emotions"],
                "moods": library["moods"],
                "animations": library["animations"],
                "data": library,
            }
        except Exception as e:
            note_failure("CODEX_TOOLS:L214", e)
            return {
                "success": False,
                "error": str(e),
                "worm": "EmojiMakerWorm",
            }


# ===============================================================================
# 4. EnhancedAnimationsWorm — CSS Animation Library Export
# ===============================================================================

@register_worm(
    category="visualization",
    priority=3,
    cooldown_seconds=60,
    description="CSS animation library — 30+ animations across 6 categories (Cinematic, Data, Particle, UI, Emotional, Structural) with keyframe generation"
)
class EnhancedAnimationsWorm:
    """Worm wrapper for enhanced_animations CSS animation library.

    Exports the animation catalog and generates CSS keyframes
    for integration with HORUS rendering and dashboard widgets.
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._anim_ok = bool(_ANIMATIONS and _generate_css)
        self._ANIMATIONS = _ANIMATIONS
        self._generate_css = _generate_css

    def execute(self) -> dict[str, Any]:
        """Export animation catalog with categories and counts."""
        if not self._anim_ok:
            return {
                "success": False,
                "error": "enhanced_animations module not importable",
                "worm": "EnhancedAnimationsWorm",
            }
        try:
            categories = {}
            for name, anim_def in self._ANIMATIONS.items():
                cat = anim_def.category if hasattr(anim_def, 'category') else 'uncategorized'
                categories.setdefault(cat, []).append(name)

            return {
                "success": True,
                "worm": "EnhancedAnimationsWorm",
                "total_animations": len(self._ANIMATIONS),
                "categories": {cat: len(names) for cat, names in categories.items()},
                "animation_names": list(self._ANIMATIONS.keys()),
                # Generate a short CSS sample (first 3 animations only to avoid huge output)
                "css_sample_lines": len(self._generate_css().split('\n')) if self._generate_css else 0,
            }
        except Exception as e:
            note_failure("CODEX_TOOLS:L269", e)
            return {
                "success": False,
                "error": str(e),
                "worm": "EnhancedAnimationsWorm",
            }
