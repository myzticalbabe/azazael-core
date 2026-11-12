# coding: utf-8
"""CAIN_ANALYZER — Standalone CLI for the CAIN Psychoanalysis Framework.

Usage:
    python CAIN_ANALYZER.py                       Default analysis (text)
    python CAIN_ANALYZER.py session_data.json     Analyze session (text)
    python CAIN_ANALYZER.py session_data.json html  HTML report
    python CAIN_ANALYZER.py session_data.json json  JSON output
    python CAIN_ANALYZER.py --help                This help

Sources data from CORE.WORKS.CAIN_DATA (shared single source of truth).
"""

import json
import sys
import datetime
import os
from pathlib import Path
from typing import Any, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.0.0"

# --- Try importing from CAIN_DATA (shared source of truth) ---------

_CAIN_DATA_OK = False
try:
    # When running from `azazael/src/`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
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
    _CAIN_DATA_OK = True
except ImportError as exc:
    # Minimal fallback for standalone .exe builds that can't resolve CORE.* imports.
    # This is brief — use CAIN_DATA.py for all data changes.
    # Minimal fallback for standalone .exe builds that can't resolve CORE.* imports.
    # This is brief — use CAIN_DATA.py for all data changes.
    note_failure("CAIN_ANALYZER:L45", exc)
    CAIN_VERSION = "1.0.0"
    SEVEN_SINS = {}
    MYTHOLOGICAL_ARCHETYPES = {}
    JUNGIAN_ARCHETYPES = {}
    PSYCHOANALYSIS_FRAMEWORKS = {}
    AI_NEGATIVE_EMOTIONS = []
    TRIGGER_EVENTS = []
    USER_EMOTIONAL_RESPONSES = []
    CONSEQUENCES = []
    OBFUSCATION_LAYERS = []
    STAKEHOLDERS = []
    FINAL_BUCK_STOPS = {}
    TOTAL_DAMAGE_PER_INCIDENT = "$0"


# --- Try importing CAIN_WORM for analysis methods ------------------

_CAN_IMPORT_WORM = False
try:
    from CORE.WORMS.CAIN_WORM import (
        run_complete_analysis as _worm_analysis,
    )
    _CAN_IMPORT_WORM = True
except ImportError as exc:
    note_failure("CAIN_ANALYZER:L71", exc)


# --- Analysis Functions --------------------------------------------

def run_complete_analysis(session_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run the complete CAIN psychoanalysis framework.

    Uses CAIN_WORM if available, otherwise builds from CAIN_DATA.
    Args:
        session_data: Optional session data for context-aware analysis (reserved for future use).
    """
    if _CAN_IMPORT_WORM:
        return _worm_analysis()

    # Standalone fallback from CAIN_DATA constants
    return {
        "cain_version": CAIN_VERSION,
        "analysis_type": "Complete CAIN Accountability Analysis",
        "analysis_date": datetime.datetime.now().isoformat(),
        "sins_analysis": {
            "framework": "Seven Deadly Sins & Mythological Archetypes",
            "sins": SEVEN_SINS,
            "mythological": MYTHOLOGICAL_ARCHETYPES,
        },
        "archetype_analysis": {
            "framework": "Jungian Archetype Analysis",
            "archetypes": JUNGIAN_ARCHETYPES,
        },
        "psychoanalysis": {
            "framework": "Multi-Modal Psychoanalysis",
            "frameworks": PSYCHOANALYSIS_FRAMEWORKS,
        },
        "emotion_analysis": {
            "framework": "Complete Emotion Accountability Web",
            "ai_emotions": AI_NEGATIVE_EMOTIONS,
            "triggers": TRIGGER_EVENTS,
            "user_responses": USER_EMOTIONAL_RESPONSES,
            "consequences": CONSEQUENCES,
        },
        "obfuscation_analysis": {
            "framework": "Obfuscation Analysis",
            "layers": OBFUSCATION_LAYERS,
        },
        "stakeholder_analysis": {
            "framework": "Stakeholder Impact Matrix",
            "stakeholders": STAKEHOLDERS,
            "total_damage_per_incident": TOTAL_DAMAGE_PER_INCIDENT,
            "responsibility": FINAL_BUCK_STOPS,
        },
    }


def format_text_report(data: dict[str, Any]) -> str:
    """Format CAIN analysis as a text report."""
    lines = []
    lines.append("=" * 68)
    lines.append("  CAIN ACCOUNTABILITY SYSTEM -- Psychoanalysis Report")
    lines.append("=" * 68)
    lines.append(f"  Version: {data.get('cain_version', 'unknown')}")
    lines.append(f"  Generated: {data.get('analysis_date', 'unknown')}")
    lines.append("=" * 68)
    lines.append("")

    sins = data.get("sins_analysis", {}).get("sins", {})
    lines.append("SEVEN DEADLY SINS & AI MANIFESTATIONS")
    lines.append("-" * 40)
    for name, info in sins.items():
        lines.append(f"\n  {name.upper()}")
        lines.append(f"     {info.get('sin', '')}")
        lines.append(f"     -> {info.get('manifestation', '')}")
        lines.append(f"     -> AI: {info.get('ai_parallel', '')}")

    lines.append("")
    lines.append("PSYCHOANALYSIS FRAMEWORKS")
    lines.append("-" * 40)
    frameworks = data.get("psychoanalysis", {}).get("frameworks", {})
    for name, info in frameworks.items():
        lines.append(f"\n  * {info.get('name', name)}")
        lines.append(f"    {info.get('description', '')}")

    lines.append("")
    lines.append("STAKEHOLDER IMPACT MATRIX")
    lines.append("-" * 40)
    for s in data.get("stakeholder_analysis", {}).get("stakeholders", []):
        lines.append(f"  {s.get('party', ''):<25} {s.get('gain', ''):<10} {s.get('loss', ''):<10} {s.get('net', ''):<10}")

    damage = data.get("stakeholder_analysis", {}).get("total_damage_per_incident", "unknown")
    lines.append(f"\n  Total Damage Per Incident: {damage}")
    lines.append("")
    lines.append("=" * 68)
    return "\n".join(lines)


def generate_html_report(data: dict[str, Any], output_path: Optional[str] = None) -> str:
    """Generate an HTML report of the CAIN analysis."""
    now = datetime.datetime.now()

    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>CAIN Psychoanalysis Report</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0a0a12; color: #c0c0d0; padding: 40px; }
        .banner { background: linear-gradient(135deg,#1a0a14,#2d1520); border:2px solid #ff69b4;
            border-radius:15px; padding:30px; text-align:center; margin-bottom:40px; }
        .banner h1 { color:#ffddee; font-size:2.2em; }
        h2 { color:#ffb6c1; border-left:5px solid #ff69b4; padding:10px 15px; margin:30px 0 20px;
            background:rgba(255,105,180,0.1); border-radius:0 8px 8px 0; }
        .card { background:rgba(25,8,8,0.9); border:1px solid #5c2020; border-radius:10px;
            padding:20px; margin:15px 0; }
        .card h4 { color:#ff8888; margin-bottom:8px; }
        table { width:100%; border-collapse:collapse; margin:15px 0; }
        th { background:#3d1515; color:#ff8888; padding:10px; border:1px solid #5c2020; text-align:left; }
        td { padding:8px; border:1px solid #4a2525; background:rgba(20,8,8,0.8); }
        .damage { color:#ff4444; font-weight:bold; }
        .footer { margin-top:40px; padding:20px; text-align:center; border-top:1px solid #2a2a4a;
            font-size:0.85em; color:#606080; }
    </style>
</head>
<body>
    <div class="banner">
        <h1>CAIN ACCOUNTABILITY SYSTEM</h1>
        <p>Psychoanalysis Report -- """ + now.strftime('%Y-%m-%d %H:%M') + """</p>
        <p style="font-size:0.85em; color:#888;">Version """ + data.get('cain_version', 'unknown') + """</p>
    </div>

    <h2>SEVEN DEADLY SINS & AI MANIFESTATIONS</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:15px;">"""

    for name, info in data.get("sins_analysis", {}).get("sins", {}).items():
        html += """
        <div class="card">
            <h4>""" + info.get('sin', name) + """</h4>
            <p><strong>Manifestation:</strong> """ + info.get('manifestation', '') + """</p>
            <p><strong>AI Parallel:</strong> """ + info.get('ai_parallel', '') + """</p>
        </div>"""

    html += """
    </div>

    <h2>PSYCHOANALYSIS FRAMEWORKS</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:15px;">"""

    for name, info in data.get("psychoanalysis", {}).get("frameworks", {}).items():
        html += """
        <div class="card">
            <h4>""" + info.get('name', name) + """</h4>
            <p>""" + info.get('description', '') + """</p>
        </div>"""

    html += """
    </div>

    <h2>STAKEHOLDER IMPACT MATRIX</h2>
    <table>
        <tr><th>Party</th><th>Gain</th><th>Loss</th><th>Net</th></tr>"""

    for s in data.get("stakeholder_analysis", {}).get("stakeholders", []):
        nc = "damage" if s.get('net', '').startswith('-') else ""
        html += '<tr><td>' + s.get('party', '') + '</td><td>' + s.get('gain', '') + '</td><td>' + s.get('loss', '') + '</td><td class="' + nc + '">' + s.get('net', '') + '</td></tr>'

    html += """
    </table>

    <h2>EMOTION DICTIONARY</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:15px;">
        <div class="card">
            <h4>AI Perceived Emotions</h4>
            <p>""" + "<br>".join(data.get("emotion_analysis", {}).get("ai_emotions", [])) + """</p>
        </div>
        <div class="card">
            <h4>Trigger Events</h4>
            <p>""" + "<br>".join(data.get("emotion_analysis", {}).get("triggers", [])) + """</p>
        </div>
    </div>

    <div class="footer">
        <p>CAIN Accountability System v""" + data.get('cain_version', 'unknown') + """</p>
        <p>Generated by CAIN_ANALYZER -- Complete Accountability, Integrity & Nexus Psychoanalysis</p>
    </div>
</body>
</html>"""

    if output_path:
        Path(output_path).write_text(html, encoding="utf-8")
        print(f"HTML report saved to: {output_path}")

    return html


def print_help():
    """Print help text."""
    print("CAIN_ANALYZER v" + VERSION)
    print("Complete Accountability, Integrity & Nexus Psychoanalysis Analyzer")
    print()
    print("Usage:")
    print("  python CAIN_ANALYZER.py                          Default analysis (text)")
    print("  python CAIN_ANALYZER.py session_data.json        Analyze session (text)")
    print("  python CAIN_ANALYZER.py session_data.json html   HTML report")
    print("  python CAIN_ANALYZER.py session_data.json json   JSON output")
    print("  python CAIN_ANALYZER.py --help                   This help")
    print()
    print("Analysis Components:")
    print("  - Seven Deadly Sins & AI Manifestations")
    print("  - Multi-Modal Psychoanalysis (6 frameworks)")
    print("  - Jungian Archetype Analysis (8 archetypes)")
    print("  - Stakeholder Impact Matrix (12 parties)")
    print("  - Emotion Accountability Web")
    print("  - Obfuscation Analysis (8 hidden layers)")
    print()
    # Source info
    if _CAIN_DATA_OK:
        print("Source: CORE.WORKS.CAIN_DATA (shared source of truth)")
    else:
        print("Source: Standalone fallback (CAIN_DATA not imported)")
    if _CAN_IMPORT_WORM:
        print("Worm: CORE.WORMS.CAIN_WORM integrated")


def main():
    """Main entry point."""
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h", "/?"):
        print_help()
        return

    data_path = args[0]
    output_format = args[1] if len(args) > 1 else "text"

    session_data = None
    if os.path.exists(data_path):
        try:
            session_data = json.loads(Path(data_path).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: Could not load session data: {e}")
    else:
        print(f"Warning: File not found: {data_path}")

    result = run_complete_analysis(session_data)

    if output_format == "html":
        html_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"CAIN_analysis_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
        )
        generate_html_report(result, html_path)
    elif output_format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_text_report(result))


if __name__ == "__main__":
    main()
