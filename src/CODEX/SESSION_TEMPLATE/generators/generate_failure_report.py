#!/usr/bin/env python3
"""
Automated Failure Report Generator with Vertical Scroll Layout and Dynamic Colors
"""

import json
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        html, body {{
            width: 100vw;
            min-height: 250vh;
            background: linear-gradient(180deg, #1a0a0a 0%, #2d1515 25%, #1a0a0a 50%, #2d1515 75%, #1a0a0a 100%);
            font-family: 'Courier New', monospace;
            overflow-x: hidden;
        }}

        @keyframes gradientShift {{
            0% {{ background-position: 0% 50%; }}
            50% {{ background-position: 100% 50%; }}
            100% {{ background-position: 0% 50%; }}
        }}

        @keyframes bubblePulse {{
            0%, 100% {{ transform: scale(1); box-shadow: 0 0 15px currentColor; }}
            50% {{ transform: scale(1.05); box-shadow: 0 0 25px currentColor; }}
        }}

        @keyframes lineFlow {{
            0% {{ stroke-opacity: 0.3; }}
            50% {{ stroke-opacity: 1; }}
            100% {{ stroke-opacity: 0.3; }}
        }}

        .container {{ width: 100%; padding: 20px; }}

        h1 {{
            text-align: center;
            color: #fff;
            font-size: 26px;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 4px;
            text-shadow: 0 0 30px #ff0044, 0 0 60px #ff4400, 0 0 90px #ff0088;
            animation: gradientShift 3s ease infinite, neonFlicker 2s infinite;
            background: linear-gradient(90deg, #ff0044, #ff4400, #ff0088, #ff0044, #ff4400);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-size: 500% 100%;
        }}

        @keyframes neonFlicker {{
            0%, 19%, 21%, 23%, 25%, 54%, 56%, 100% {{ opacity: 1; text-shadow: 0 0 30px #ff0044, 0 0 60px #ff4400; }}
            20%, 24%, 55% {{ opacity: 0.8; text-shadow: none; }}
        }}

        .subtitle {{ text-align: center; color: #888; font-size: 12px; margin-bottom: 20px; }}

        h2 {{
            text-align: center;
            color: #fff;
            font-size: 16px;
            margin: 25px 0 12px 0;
            padding: 10px;
            background: linear-gradient(90deg, transparent, rgba(255,107,107,0.2), transparent);
            border-top: 1px solid #ff6b6b;
            border-bottom: 1px solid #ff6b6b;
        }}

        .web-canvas {{
            width: 100%;
            min-height: 230vh;
            position: relative;
            background: 
                radial-gradient(ellipse at 30% 20%, rgba(255,107,107,0.1) 0%, transparent 50%),
                radial-gradient(ellipse at 70% 40%, rgba(162,155,254,0.1) 0%, transparent 50%),
                radial-gradient(ellipse at 50% 70%, rgba(254,202,87,0.08) 0%, transparent 50%);
            border-radius: 20px;
            padding: 30px;
        }}

        svg.connections {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 1;
        }}

        svg.connections line {{
            animation: lineFlow 3s ease-in-out infinite;
        }}

        svg.connections line:nth-child(odd) {{
            animation-delay: 0.5s;
        }}

        .derive {{ stroke: #ff6b6b; stroke-width: 2; opacity: 0.7; }}
        .parent {{ stroke: #feca57; stroke-width: 1.5; stroke-dasharray: 4,2; opacity: 0.6; }}
        .same {{ stroke: #a29bfe; stroke-width: 1.5; opacity: 0.6; }}

        .bubble {{
            position: absolute;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            border-radius: 12px;
            font-size: 9px;
            font-weight: bold;
            padding: 8px;
            z-index: 2;
            transition: all 0.3s ease;
            animation: bubblePulse 4s ease-in-out infinite;
            color: #fff;
        }}

        .bubble:hover {{
            transform: scale(1.15);
            z-index: 100;
        }}

        .bubble .id {{ font-size: 12px; font-weight: bold; text-shadow: 0 0 10px currentColor; }}
        .bubble .name {{ font-size: 8px; margin-top: 3px; opacity: 0.9; }}
        .bubble .detail {{ font-size: 7px; margin-top: 2px; opacity: 0.7; text-align: left; }}
        .bubble .label {{ font-size: 6px; color: #888; text-transform: uppercase; }}

        /* Dynamic level colors with neon effects */
        .lvl0 {{ 
            background: linear-gradient(135deg, #1a1a2e, #000); 
            border: 3px solid #ff0044;
            box-shadow: 0 0 30px #ff0044, 0 0 60px #ff4400, inset 0 0 20px rgba(255,0,68,0.3);
            color: #ff0044;
        }}
        
        .lvl1 {{ 
            background: linear-gradient(135deg, #4400ff, #8800ff, #4400ff); 
            border: 3px solid #aa44ff;
            box-shadow: 0 0 30px #4400ff, 0 0 60px #8800ff, inset 0 0 20px rgba(136,0,255,0.3);
            color: #ddd4ff;
        }}
        
        .lvl2 {{ 
            background: linear-gradient(135deg, #ff2200, #ff4400, #ff2200); 
            border: 3px solid #ff6644;
            box-shadow: 0 0 30px #ff2200, 0 0 60px #ff4400, inset 0 0 20px rgba(255,68,0,0.3);
            color: #ffd4cc;
        }}
        
        .lvl3 {{ 
            background: linear-gradient(135deg, #ffaa00, #ffcc00, #ffaa00); 
            border: 3px solid #ffdd44;
            box-shadow: 0 0 30px #ffaa00, 0 0 60px #ffcc00, inset 0 0 20px rgba(255,204,0,0.3);
            color: #332200;
        }}

        .legend {{
            position: fixed;
            top: 10px;
            right: 10px;
            background: rgba(0,0,0,0.85);
            padding: 12px;
            border-radius: 10px;
            border: 1px solid #ff6b6b;
            z-index: 200;
            font-size: 9px;
        }}

        .legend-title {{ color:#ff6b6b;font-weight:bold;margin-bottom:8px;font-size:11px; }}
        .legend-item {{ display: flex; align-items: center; margin: 4px 0; color: #ccc; }}
        .legend-dot {{ width: 12px; height: 12px; border-radius: 3px; margin-right: 5px; }}

        .summary-box {{
            background: rgba(255,107,107,0.05);
            border: 2px dashed rgba(255,107,107,0.5);
            border-radius: 15px;
            padding: 20px;
            margin: 15px auto;
            max-width: 95%;
        }}

        .summary-box ul {{ 
            color: #ffaaaa;
            font-size: 10px;
            line-height: 1.8;
            padding-left: 30px;
            column-count: 2;
            column-gap: 30px;
        }}

        .action-box {{
            background: rgba(78,205,196,0.05);
            border: 2px solid rgba(78,205,196,0.5);
            border-radius: 15px;
            padding: 20px;
            margin: 15px auto;
            max-width: 95%;
        }}

        .action-box ul {{ 
            color: #4ecdc4;
            font-size: 10px;
            line-height: 1.8;
            padding-left: 30px;
        }}

        .detail-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 8px;
            max-width: 98%;
            margin: 10px auto;
        }}

        .detail-card {{
            background: rgba(253,203,110,0.1);
            border: 1px solid rgba(253,203,110,0.5);
            border-radius: 8px;
            padding: 8px;
            font-size: 8px;
            color: #ccc;
        }}

        .detail-card .id {{ 
            color: #f39c12; 
            font-weight: bold; 
            font-size: 8px;
            margin-bottom: 3px;
        }}
    </style>
</head>
<body>
    <div class="legend">
        <div class="legend-title">>> HIERARCHY >></div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff0044"></div>🔴 Root Cause</div>
        <div class="legend-item"><div class="legend-dot" style="background:#4400ff"></div>💜 System Impact</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff2200"></div>🔴 Direct Failure</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ffaa00"></div>🟡 Detail</div>
    </div>

    <div class="container">
        <h1>{title}</h1>
        <div class="subtitle">{session} | {date}</div>

        <h2>FAILURE HIERARCHY - VERTICAL SCROLL FLOW</h2>
        <div class="web-canvas">
            <svg class="connections">
                {svg_connections}
            </svg>
            {bubbles}
        </div>

        <h2>FAILURE DETAILS ({detail_count})</h2>
        <div class="detail-grid">
            {detail_cards}
        </div>

        <h2>FAILURE SUMMARY ({summary_count} failures)</h2>
        <div class="summary-box">
            <ul>
                {summary_items}
            </ul>
        </div>

        <h2>CORRECTIVE ACTIONS ({action_count})</h2>
        <div class="action-box">
            <ul>
                {action_items}
            </ul>
        </div>
    </div>
</body>
</html>
"""


def generate_report(data):
    # Handle nested structure
    if "failureReport" in data:
        data = data["failureReport"]

    title = data.get("title", "Assistant Failure Report")
    session = data.get("session", "Unknown Session")
    date = data.get("date", "")

    root = data.get("rootCause", {})
    system_impacts = data.get("systemImpacts", [])
    direct_failures = data.get("directFailures", [])
    details = data.get("details", [])
    summary = data.get("summary", [])
    actions = data.get("correctiveActions", [])

    # Vertical layout positions
    row1_y = 50  # Root cause
    row2_y = 200  # System impacts
    row3_y = 380  # Direct failures
    row4_y = 650  # Details

    bubbles = []
    svg_lines = []

    # ROW 1: Root cause (centered)
    bubbles.append(f"""
            <div class="bubble lvl0" style="left:35%;top:{row1_y}px;width:30%;height:70px;">
                <span class="id">ROOT CAUSE</span>
                <span class="name">{root.get("id", "")}: {root.get("description", "")[:70]}</span>
                <span class="detail">SEVERITY: {root.get("severity", "N/A").upper()}</span>
            </div>""")

    # ROW 2: System impacts (4 items)
    len(system_impacts)
    impact_spacing = 22

    for i, impact in enumerate(system_impacts):
        x_pos = 5 + i * impact_spacing
        svg_lines.append(
            f'<line class="derive" x1="50%" y1="{row1_y + 70}px" x2="{x_pos + 10}%" y2="{row2_y}px"/>'
        )

        bubbles.append(f"""
            <div class="bubble lvl1" style="left:{x_pos}%;top:{row2_y}px;width:18%;height:80px;">
                <span class="id">{impact.get("id", "")}</span>
                <span class="name">{impact.get("name", "")}</span>
                <span class="detail">{impact.get("description", "")[:45]}</span>
                <span class="detail">IMPACT: {impact.get("impact", "")[:40]}</span>
            </div>""")

        # Connect to direct failures
        if impact.get("children"):
            for j, child_id in enumerate(impact["children"][:3]):
                child_x = x_pos + 3 + j * 4
                svg_lines.append(
                    f'<line class="derive" x1="{x_pos + 9}%" y1="{row2_y + 80}px" x2="{child_x}%" y2="{row3_y}px"/>'
                )

    # ROW 3: Direct failures
    df_count = len(direct_failures)
    df_spacing = 17

    for i, df in enumerate(direct_failures):
        x_pos = 2 + i * df_spacing

        # Connect to next
        if i < df_count - 1:
            svg_lines.append(
                f'<line class="same" x1="{x_pos + 8}%" y1="{row3_y + 40}px" x2="{x_pos + df_spacing + 2}%" y2="{row3_y + 40}px"/>'
            )

        # Connect to details
        svg_lines.append(
            f'<line class="parent" x1="{x_pos + 8}%" y1="{row3_y + 80}px" x2="{x_pos + 7}%" y2="{row4_y}px"/>'
        )

        bubbles.append(f"""
            <div class="bubble lvl2" style="left:{x_pos}%;top:{row3_y}px;width:15%;height:85px;">
                <span class="id">{df.get("id", "")}</span>
                <span class="name">{df.get("name", "")[:22]}</span>
                <span class="detail">CMD: {df.get("command", "N/A")[:25]}</span>
                <span class="detail">EXP: {df.get("expected", "N/A")[:25]}</span>
                <span class="detail">ACT: {df.get("actual", "N/A")[:25]}</span>
            </div>""")

    # ROW 4: Details
    len(details)
    detail_spacing = 18

    for i, detail in enumerate(details):
        x_pos = 1 + i * detail_spacing

        bubbles.append(f"""
            <div class="bubble lvl3" style="left:{x_pos}%;top:{row4_y}px;width:14%;height:65px;">
                <span class="id">{detail.get("id", "")}</span>
                <span class="detail">{detail.get("description", "")[:30]}</span>
                <span class="detail" style="color:#888;">{detail.get("evidence", "")[:30]}</span>
            </div>""")

    # Detail cards
    detail_cards = []
    for df in direct_failures:
        df_details = df.get("details", {})
        if df_details:
            for k, v in df_details.items():
                detail_cards.append(f"""
            <div class="detail-card">
                <div class="id">{df.get("id", "")} - {k}</div>
                <div>{v}</div>
            </div>""")

    for detail in details[:10]:
        detail_cards.append(f"""
            <div class="detail-card">
                <div class="id">{detail.get("id", "")}</div>
                <div>{detail.get("description", "")}</div>
                <div style="color:#888;margin-top:2px;">{detail.get("evidence", "")}</div>
            </div>""")

    bubbles_html = "\n            ".join(bubbles)
    svg_connections = "\n                ".join(svg_lines)
    detail_cards_html = "\n            ".join(detail_cards[:25])
    summary_items = "\n                ".join([f"<li>{s}</li>" for s in summary])
    action_items = "\n                ".join([f"<li>{a}</li>" for a in actions])

    html = HTML_TEMPLATE.format(
        title=title,
        session=session,
        date=date,
        svg_connections=svg_connections,
        bubbles=bubbles_html,
        detail_count=len(details),
        detail_cards=detail_cards_html,
        summary_count=len(summary),
        summary_items=summary_items,
        action_count=len(actions),
        action_items=action_items,
    )

    return html


def main():
    if len(sys.argv) < 2:
        json_path = Path(__file__).parent / "failure_data.json"
    else:
        json_path = Path(sys.argv[1])

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

            html = generate_report(data)

            output_path = json_path.parent / "failure_report.html"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)

                print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
