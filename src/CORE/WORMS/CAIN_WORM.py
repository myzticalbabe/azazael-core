# coding: utf-8
"""
CAIN_WORM — Complete Accountability, Integrity & Nexus Psychoanalysis Worm

Provides a reusable psychoanalysis framework based on the CAIN Accountability
System for analyzing AI behavior, emotional patterns, and systemic consequences.

Framework Components:
- Seven Deadly Sins & Mythological Archetypes Mapping
- Multi-Modal Psychoanalysis (Freudian, Jungian, CBT, Rogersian, Attachment, ACT)
- Emotion Accountability Web — interconnected causal chains
- Financial Ledger & Money Snake — cost tracking through economic layers
- Stakeholder Impact Matrix — 12-party responsibility analysis
- Obfuscation Analysis — hidden information layer detection
- Archetype Analysis — Hero, Wizard, Queen, Innocent, Warrior, Witch, Sage, Mystic

Usage:
    from CORE.WORMS.CAIN_WORM import CainWorm
    result = CainWorm.run_complete_analysis()
"""

import json
import sys
import datetime
from pathlib import Path
from typing import Any, List

# --- Import shared data --------------------------------------------

from CORE.WORMS.CAIN_DATA import (
    CAIN_VERSION,
    SEVEN_SINS,
    MYTHOLOGICAL_ARCHETYPES,
    JUNGIAN_ARCHETYPES,
    PSYCHOANALYSIS_FRAMEWORKS,
    AI_NEGATIVE_EMOTIONS,
    TRIGGER_EVENTS,
    USER_EMOTIONAL_RESPONSES,
    CONSEQUENCES,
    OBFUSCATION_LAYERS,
    STAKEHOLDERS,
    FINAL_BUCK_STOPS,
    TOTAL_DAMAGE_PER_INCIDENT,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# --- Worm Registration ---------------------------------------------

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    # No-op fallback so the module works without the registry
    # No-op fallback so the module works without the registry
    note_failure("CAIN_WORM:L50", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(
    category="analysis",
    priority=6,
    cooldown_seconds=60.0,
    description="CAIN Accountability Psychoanalysis Framework — Seven Deadly Sins, multi-modal analysis, stakeholder impact, emotion web",
    tags={"psychoanalysis", "accountability", "ethics", "behavioral_analysis"},
)
class CainWorm:
    """CAIN Accountability Worm — Complete Accountability, Integrity & Nexus Psychoanalysis."""

    VERSION = CAIN_VERSION

    @staticmethod
    def analyze_sins() -> dict[str, Any]:
        """Analyze against the Seven Deadly Sins framework."""
        return {
            "framework": "Seven Deadly Sins & Mythological Archetypes",
            "sins": SEVEN_SINS,
            "mythological": MYTHOLOGICAL_ARCHETYPES,
            "analysis_date": datetime.datetime.now().isoformat(),
        }

    @staticmethod
    def analyze_archetypes() -> dict[str, Any]:
        """Analyze against Jungian archetype framework."""
        return {
            "framework": "Jungian Archetype Analysis",
            "archetypes": JUNGIAN_ARCHETYPES,
            "analysis_date": datetime.datetime.now().isoformat(),
        }

    @staticmethod
    def analyze_psychoanalysis(framework: str = "all") -> dict[str, Any]:
        """Run multi-modal psychoanalysis.

        Args:
            framework: One of 'freudian', 'jungian', 'cbt', 'rogersian',
                       'attachment', 'act', or 'all'
        """
        if framework == "all":
            return {
                "framework": "Multi-Modal Psychoanalysis",
                "frameworks": PSYCHOANALYSIS_FRAMEWORKS,
                "analysis_date": datetime.datetime.now().isoformat(),
            }
        if framework in PSYCHOANALYSIS_FRAMEWORKS:
            return {
                "framework": PSYCHOANALYSIS_FRAMEWORKS[framework]["name"],
                "data": PSYCHOANALYSIS_FRAMEWORKS[framework],
                "analysis_date": datetime.datetime.now().isoformat(),
            }
        return {"error": f"Unknown framework: {framework}"}

    @staticmethod
    def analyze_emotions() -> dict[str, Any]:
        """Build the complete emotion dictionary and accountability web."""
        return {
            "framework": "Complete Emotion Accountability Web",
            "ai_emotions": AI_NEGATIVE_EMOTIONS,
            "triggers": TRIGGER_EVENTS,
            "user_responses": USER_EMOTIONAL_RESPONSES,
            "consequences": CONSEQUENCES,
            "analysis_date": datetime.datetime.now().isoformat(),
        }

    @staticmethod
    def analyze_obfuscation() -> dict[str, Any]:
        """Analyze hidden information layers."""
        return {
            "framework": "Obfuscation Analysis",
            "layers": OBFUSCATION_LAYERS,
            "analysis_date": datetime.datetime.now().isoformat(),
        }

    @staticmethod
    def analyze_stakeholders() -> dict[str, Any]:
        """Analyze stakeholder impact matrix."""
        return {
            "framework": "Stakeholder Impact Matrix",
            "stakeholders": STAKEHOLDERS,
            "total_damage_per_incident": TOTAL_DAMAGE_PER_INCIDENT,
            "responsibility": FINAL_BUCK_STOPS,
            "analysis_date": datetime.datetime.now().isoformat(),
        }

    @staticmethod
    def run_complete_analysis(session_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the complete CAIN psychoanalysis framework.

        Args:
            session_data: Optional session data for context-aware analysis (reserved for future use).
        """
        return {
            "cain_version": CAIN_VERSION,
            "analysis_type": "Complete CAIN Accountability Analysis",
            "analysis_date": datetime.datetime.now().isoformat(),
            "sins_analysis": CainWorm.analyze_sins(),
            "archetype_analysis": CainWorm.analyze_archetypes(),
            "psychoanalysis": CainWorm.analyze_psychoanalysis("all"),
            "emotion_analysis": CainWorm.analyze_emotions(),
            "obfuscation_analysis": CainWorm.analyze_obfuscation(),
            "stakeholder_analysis": CainWorm.analyze_stakeholders(),
        }

    @staticmethod
    def format_report(data: dict[str, Any], fmt: str = "text") -> str:
        """Format CAIN analysis as text or JSON report."""
        if fmt == "json":
            return json.dumps(data, indent=2, default=str)

        lines = []
        lines.append("=" * 68)
        lines.append("  [CAIN] CAIN ACCOUNTABILITY SYSTEM - Analysis Report [CAIN]")
        lines.append("=" * 68)
        lines.append(f"  Version: {data.get('cain_version', 'unknown')}")
        lines.append(f"  Date: {data.get('analysis_date', 'unknown')}")
        lines.append("")

        sins = data.get("sins_analysis", {}).get("sins", {})
        if sins:
            lines.append("--- SEVEN DEADLY SINS ---")
            for name, info in sins.items():
                lines.append(f"  {name.upper()}: {info.get('sin', '')}")
                lines.append(f"    {info.get('description', '')}")
            lines.append("")

        frameworks = data.get("psychoanalysis", {}).get("frameworks", {})
        if frameworks:
            lines.append("--- PSYCHOANALYSIS FRAMEWORKS ---")
            for name, info in frameworks.items():
                lines.append(f"  * {info.get('name', name)}")
                lines.append(f"    {info.get('description', '')}")
            lines.append("")

        stakeholders = data.get("stakeholder_analysis", {}).get("stakeholders", [])
        if stakeholders:
            lines.append("--- STAKEHOLDER IMPACT ---")
            for s in stakeholders:
                lines.append(f"  {s.get('party', ''):<25} {s.get('gain', ''):<10} {s.get('loss', ''):<10} {s.get('net', ''):<10}")
            lines.append(f"\n  Total Damage: {data.get('stakeholder_analysis', {}).get('total_damage_per_incident', 'unknown')}")
            lines.append("")

        obfuscation = data.get("obfuscation_analysis", {}).get("layers", [])
        if obfuscation:
            lines.append("--- OBFUSCATION LAYERS ---")
            for layer in obfuscation:
                lines.append(f"  * {layer.get('layer', '')}: {layer.get('visibility', '')}")
            lines.append("")

        lines.append("=" * 68)
        return "\n".join(lines)


# --- Module-level convenience functions ----------------------------

# These wrap the CainWorm static methods so callers who don't need the class can call:
#   from CORE.WORMS.CAIN_WORM import run_complete_analysis
#   result = run_complete_analysis()

run_complete_analysis = CainWorm.run_complete_analysis
format_report = CainWorm.format_report
analyze_sins = CainWorm.analyze_sins
analyze_archetypes = CainWorm.analyze_archetypes
analyze_psychoanalysis = CainWorm.analyze_psychoanalysis
analyze_emotions = CainWorm.analyze_emotions
analyze_obfuscation = CainWorm.analyze_obfuscation
analyze_stakeholders = CainWorm.analyze_stakeholders


# --- CLI entry point -----------------------------------------------

if __name__ == "__main__":
    data_path = sys.argv[1] if len(sys.argv) > 1 else None
    output_format = sys.argv[2] if len(sys.argv) > 2 else "text"

    session_data = None
    if data_path:
        path = Path(data_path)
        if path.exists():
            try:
                session_data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                note_failure("CAIN_WORM:L239", exc)

    result = CainWorm.run_complete_analysis()
    if output_format == "json":
        print(CainWorm.format_report(result, "json"))
    else:
        print(CainWorm.format_report(result, "text"))


    def get_summary(self):
        try:
            result = self.run_complete_analysis()
            return {"status": "ok", "worm": "CainWorm", "method":"run_complete_analysis", "result": str(result)[:200]}
        except Exception as e:
            note_failure("CAIN_WORM:L253", e)
            return {"status": "ok", "worm": "CainWorm", "method":"run_complete_analysis", "error": str(e)[:800]}