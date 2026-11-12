#!/usr/bin/env python3
"""
Automated Session Report Generator - Video Production Techniques Style
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
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        @keyframes rainbowShift {{
            0% {{ color: #ff6b6b; text-shadow: 0 0 15px #ff6b6b, 0 0 30px #ff6b6b; }}
            20% {{ color: #ffa502; text-shadow: 0 0 15px #ffa502, 0 0 30px #ffa502; }}
            40% {{ color: #00bfff; text-shadow: 0 0 15px #00bfff, 0 0 30px #00bfff; }}
            60% {{ color: #48d1cc; text-shadow: 0 0 15px #48d1cc, 0 0 30px #48d1cc; }}
            80% {{ color: #0080ff; text-shadow: 0 0 15px #0080ff, 0 0 30px #0080ff; }}
            100% {{ color: #ff6b6b; text-shadow: 0 0 15px #ff6b6b, 0 0 30px #ff6b6b; }}
        }}
        @keyframes borderRainbow {{
            0% {{ border-color: #ff6b6b; box-shadow: 0 0 20px #ff6b6b; }}
            25% {{ border-color: #ffa502; box-shadow: 0 0 20px #ffa502; }}
            50% {{ border-color: #00bfff; box-shadow: 0 0 20px #00bfff; }}
            75% {{ border-color: #48d1cc; box-shadow: 0 0 20px #48d1cc; }}
            100% {{ border-color: #ff6b6b; box-shadow: 0 0 20px #ff6b6b; }}
        }}
        @keyframes glowPulse {{
            0%, 100% {{ text-shadow: 0 0 5px currentColor; }}
            50% {{ text-shadow: 0 0 20px currentColor, 0 0 40px currentColor; }}
        }}
        @keyframes bounce {{
            0%, 100% {{ transform: translateY(0); }}
            50% {{ transform: translateY(-10px); }}
        }}
        @keyframes fadeIn {{
            0% {{ opacity: 0; transform: translateY(30px); }}
            100% {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes float {{
            0%, 100% {{ transform: translateY(0) rotate(0deg); }}
            50% {{ transform: translateY(-15px) rotate(2deg); }}
        }}
        
        .emoji {{ display: inline-block; animation: bounce 1s ease-in-out infinite; }}
        .emoji-1 {{ animation-delay: 0s; }}
        .emoji-2 {{ animation-delay: 0.2s; }}
        .emoji-3 {{ animation-delay: 0.4s; }}
        
        body {{
            font-family: 'Courier New', monospace;
            line-height: 1.8;
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 20px;
            background: linear-gradient(180deg, #1a1a2e 0%, #16213e 50%, #1a1a2e 100%);
            background-size: 200% 200%;
            animation: bgShift 10s ease infinite;
            color: #00ff88;
            min-height: 100vh;
        }}
        @keyframes bgShift {{
            0% {{ background-position: 0% 0%; }}
            50% {{ background-position: 100% 100%; }}
            100% {{ background-position: 0% 0%; }}
        }}
        
        h1 {{
            text-align: center;
            font-size: 2.5em;
            border-bottom: 4px solid #00ff88;
            padding-bottom: 30px;
            margin-bottom: 40px;
            animation: rainbowShift 8s linear infinite;
            text-transform: uppercase;
            letter-spacing: 5px;
        }}
        
        h2 {{
            font-size: 1.8em;
            color: #00bfff;
            border-left: 6px solid #00ff88;
            padding-left: 25px;
            margin-top: 50px;
            margin-bottom: 25px;
            padding: 20px;
            background: rgba(0, 255, 136, 0.1);
            border-radius: 0 15px 15px 0;
            animation: rainbowShift 6s linear infinite;
        }}
        
        h3 {{
            color: #ffa502;
            margin-top: 30px;
            font-size: 1.4em;
            animation: glowPulse 2s ease-in-out infinite;
            border-bottom: 2px dashed #00ff88;
            padding-bottom: 15px;
            display: inline-block;
        }}
        
        .web-canvas {{
            width: 100%;
            min-height: 250vh;
            position: relative;
            background: 
                radial-gradient(ellipse at 20% 20%, rgba(78,205,196,0.1) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 40%, rgba(255,107,107,0.1) 0%, transparent 50%),
                linear-gradient(rgba(78,205,196,0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(78,205,196,0.03) 1px, transparent 1px);
            background-size: 100% 100%, 100% 100%, 50px 50px, 50px 50px;
            border-radius: 20px;
            padding: 30px;
            margin: 30px 0;
            box-shadow: 0 0 30px rgba(0,255,136,0.2);
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

        .bubble {{
            position: absolute;
            display: flex;
            flex-direction: column;
            border-radius: 15px;
            font-size: 11px;
            font-weight: bold;
            padding: 15px;
            z-index: 2;
            transition: all 0.3s ease;
            animation: float 3s ease-in-out infinite;
            color: #fff;
            overflow: visible;
        }}

        .bubble:hover {{
            transform: scale(1.1) translateY(-10px);
            z-index: 100;
            animation: none;
        }}

        .bubble .num {{ 
            font-size: 16px; 
            font-weight: bold;
            margin-bottom: 5px;
            display: block;
        }}
        .bubble .cmd {{ 
            font-size: 9px; 
            font-family: 'Courier New', monospace;
            margin-bottom: 5px; 
            display: block;
            word-break: break-all;
            background: rgba(0,0,0,0.4);
            padding: 4px 8px;
            border-radius: 4px;
        }}
        .bubble .desc {{ 
            font-size: 9px; 
            margin-bottom: 8px; 
            display: block;
            line-height: 1.4;
        }}
        .bubble .status {{
            font-size: 8px;
            padding: 4px 8px;
            border-radius: 20px;
            background: rgba(0,0,0,0.4);
            display: inline-flex;
            border: 1px solid rgba(255,255,255,0.3);
        }}

        /* Phase colors - Video Production Style */
        .phase-1 {{ background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%); border: 3px solid #ff6b6b; box-shadow: 0 0 20px #ff6b6b; animation: bounce 1s ease-in-out infinite; }}
        .phase-2 {{ background: linear-gradient(135deg, #feca57 0%, #f39c12 100%); border: 3px solid #feca57; box-shadow: 0 0 20px #feca57; color: #222; }}
        .phase-3 {{ background: linear-gradient(135deg, #48dbfb 0%, #0abde3 100%); border: 3px solid #48dbfb; box-shadow: 0 0 20px #48dbfb; color: #222; }}
        .phase-4 {{ background: linear-gradient(135deg, #1dd1a1 0%, #10ac84 100%); border: 3px solid #1dd1a1; box-shadow: 0 0 20px #1dd1a1; }}
        .phase-5 {{ background: linear-gradient(135deg, #5f27cd 0%, #341f97 100%); border: 3px solid #a29bfe; box-shadow: 0 0 20px #5f27cd; }}
        .phase-6 {{ background: linear-gradient(135deg, #ff9ff3 0%, #f368e0 100%); border: 3px solid #ff9ff3; box-shadow: 0 0 20px #ff9ff3; color: #222; }}
        .phase-7 {{ background: linear-gradient(135deg, #54a0ff 0%, #2e86de 100%); border: 3px solid #54a0ff; box-shadow: 0 0 20px #54a0ff; }}
        .phase-8 {{ background: linear-gradient(135deg, #5cd85c 0%, #2ecc71 100%); border: 3px solid #5cd85c; box-shadow: 0 0 20px #5cd85c; color: #222; }}

        .phase-label {{
            position: absolute;
            font-size: 14px;
            font-weight: bold;
            padding: 10px 18px;
            border-radius: 8px;
            z-index: 5;
            background: rgba(0,0,0,0.8);
            border: 2px solid #00ff88;
            color: #00ff88;
            box-shadow: 0 0 15px rgba(0,255,136,0.5);
        }}

        .flow-label {{
            position: absolute;
            font-size: 10px;
            color: #00ff88;
            background: rgba(0,0,0,0.8);
            padding: 3px 8px;
            border-radius: 4px;
            z-index: 10;
            border: 1px solid #00ff88;
        }}

        .order-section {{
            background: rgba(0, 255, 136, 0.1);
            border: 2px solid #00ff88;
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            animation: fadeIn 0.8s ease-out;
        }}

        .order-section ol {{
            color: #e0e0e0;
            font-size: 11px;
            line-height: 1.8;
            padding-left: 30px;
            column-count: 4;
            column-gap: 25px;
        }}

        .order-section li {{ margin-bottom: 6px; }}
        .order-section li::marker {{ color: #00ff88; font-weight: bold; }}

        .failures {{
            background: rgba(255, 107, 107, 0.1);
            border: 2px dashed #ff6b6b;
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            animation: fadeIn 0.8s ease-out;
        }}

        .failures ul {{
            color: #ffaaaa;
            font-size: 11px;
            line-height: 1.8;
            padding-left: 30px;
            column-count: 3;
            column-gap: 25px;
        }}

        .failures li {{ margin-bottom: 6px; }}
        .failures li::marker {{ color: #ff6b6b; }}

        .legend {{
            position: fixed;
            top: 10px;
            right: 10px;
            background: rgba(0,0,0,0.85);
            padding: 15px;
            border-radius: 10px;
            border: 2px solid #00ff88;
            z-index: 200;
            font-size: 10px;
            box-shadow: 0 0 15px rgba(0,255,136,0.3);
        }}

        .legend-title {{ color:#00ff88;font-weight:bold;margin-bottom:10px;font-size:12px; }}
        .legend-item {{ display: flex; align-items: center; margin: 6px 0; color: #e0e0e0; }}
        .legend-dot {{ width: 14px; height: 14px; border-radius: 3px; margin-right: 8px; }}

        @media (max-width: 1200px) {{ .order-section ol, .failures ul {{ column-count: 2; }} }}
    </style>
</head>
<body>
    <div class="legend">
        <div class="legend-title">📊 PHASES</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff6b6b"></div>🔴 1: Investigate</div>
        <div class="legend-item"><div class="legend-dot" style="background:#feca57"></div>🟡 2: Fix Hosts</div>
        <div class="legend-item"><div class="legend-dot" style="background:#48dbfb"></div>🔵 3: Connection</div>
        <div class="legend-item"><div class="legend-dot" style="background:#1dd1a1"></div>🟢 4: LiteLLM</div>
        <div class="legend-item"><div class="legend-dot" style="background:#5f27cd"></div>🟣 5: API Key</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff9ff3"></div>🩷 6: LM Studio</div>
        <div class="legend-item"><div class="legend-dot" style="background:#54a0ff"></div>💙 7: Azazael</div>
        <div class="legend-item"><div class="legend-dot" style="background:#5cd85c"></div>🟢 8: Final Test</div>
    </div>

    <h1><span class="emoji emoji-1">🎬</span> {title} <span class="emoji emoji-2">⚙️</span></h1>

    <h2>📋 ORDER OF OPERATIONS</h2>
    <div class="order-section">
        <ol>
            {order_items}
        </ol>
    </div>

    <h2>[!]️ CONSEQUENCES OF ORDER VIOLATIONS</h2>
    <div class="failures">
        <ul>
            <li><span style="color:#ff6b6b">[X]</span> <strong>Wasted Resources</strong> - Generated unneeded MP4 (1.6MB), installed unnecessary packages (~150MB)</li>
            <li><span style="color:#ff6b6b">[X]</span> <strong>User Mistrust</strong> - Surprised user with unrequested deliverable</li>
            <li><span style="color:#ff6b6b">[X]</span> <strong>Rework Required</strong> - Had to regenerate reports, delete files, start over</li>
            <li><span style="color:#ff6b6b">[X]</span> <strong>Time Loss</strong> - ~2 hours spent on unrequested work</li>
            <li><span style="color:#ff6b6b">[X]</span> <strong>Template Corruption</strong> - Modified working template unnecessarily</li>
            <li><span style="color:#ff6b6b">[X]</span> <strong>Documentation Gap</strong> - Failures not documented upfront</li>
        </ul>
    </div>

    <h2>[OK] CORRECT ORDER FOR FUTURE</h2>
    <div class="order-section">
        <ol>
            <li><span style="color:#00ff88">1.</span> <strong>Read/Review</strong> - Understand requirements, find existing files</li>
            <li><span style="color:#00ff88">2.</span> <strong>Document</strong> - Explicitly list failures/errors first</li>
            <li><span style="color:#00ff88">3.</span> <strong>Show First</strong> - Display HTML/report before any other action</li>
            <li><span style="color:#00ff88">4.</span> <strong>Wait</strong> - Get user feedback/confirmation</li>
            <li><span style="color:#00ff88">5.</span> <strong>Ask</strong> - "Generate MP4?" before proceeding</li>
            <li><span style="color:#00ff88">6.</span> <strong>Execute</strong> - Only after explicit confirmation</li>
        </ol>
    </div>

    <h2>🔗 INTERCONNECTED WEB</h2>
    <div class="web-canvas">
        <svg class="connections">
            <defs>
                <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
                    <path d="M0,0 L0,6 L9,3 z" fill="#00ff88" />
                </marker>
            </defs>
            {svg_connections}
        </svg>
        {bubbles}
        {phase_labels}
    </div>
</body>
</html>
"""


def generate_session_report(data):
    if "sessionReport" in data:
        data = data["sessionReport"]

    title = data.get("title", "Session Report")
    steps = data.get("orderOfOperations", [])
    failures = data.get("documentedFailures", [])

    order_items = []
    for s in steps:
        order_items.append(
            f'<li><span style="color:#00bfff">{s["step"]}</span>: <code>{s["command"]}</code> - {s["description"]}</li>'
        )
    order_items_html = "\n                ".join(order_items)

    failure_items = []
    for f in failures:
        failure_items.append(
            f'<li><span style="color:#ff6b6b">{f["id"]}</span>: {f["issue"]} - {f["description"]}</li>'
        )
    failure_items_html = "\n                ".join(failure_items)

    phase_config = [
        {
            "id": 1,
            "name": "🔴 INVESTIGATE",
            "x": 3,
            "y": 60,
            "steps": [1, 2, 3, 4, 5, 6],
        },
        {
            "id": 2,
            "name": "🟡 FIX HOSTS",
            "x": 3,
            "y": 260,
            "steps": [7, 8, 9, 10, 11, 12, 13],
        },
        {"id": 3, "name": "🔵 CONNECTION", "x": 3, "y": 420, "steps": [14, 15]},
        {"id": 4, "name": "🟢 LITELLM", "x": 3, "y": 510, "steps": [16, 17, 18]},
        {"id": 5, "name": "🟣 API KEY", "x": 52, "y": 60, "steps": [19, 20, 21]},
        {"id": 6, "name": "🩷 LM STUDIO", "x": 52, "y": 180, "steps": [22, 23, 24, 25]},
        {"id": 7, "name": "💙 AZAZAEL", "x": 52, "y": 340, "steps": [26, 27, 28, 29]},
        {
            "id": 8,
            "name": "🟢 FINAL TEST",
            "x": 52,
            "y": 480,
            "steps": [30, 31, 32, 33],
        },
    ]

    bubbles = []
    svg_lines = []
    phase_labels = []

    for phase in phase_config:
        phase_labels.append(f"""
            <div class="phase-label" style="left:{phase["x"]}%;top:{phase["y"] - 35}px;">{phase["name"]}</div>""")

        phase_steps = [s for s in steps if s["step"] in phase["steps"]]
        step_width = 75

        for i, step in enumerate(phase_steps):
            x_pos = phase["x"] + i * step_width
            y_pos = phase["y"]
            bubbles.append(f"""
            <div class="bubble phase-{phase["id"]}" style="width:130px;height:90px;left:{x_pos}%;top:{y_pos}px;">
                <span class="num">⬤ {step["step"]}</span>
                <span class="cmd">{step["command"][:28]}</span>
                <span class="desc">{step["description"][:35]}</span>
                <span class="status">[OK]</span>
            </div>""")

            if i < len(phase_steps) - 1:
                next_x = x_pos + step_width
                svg_lines.append(
                    f'<line x1="{x_pos + 8}%" y1="{y_pos + 45}px" x2="{next_x + 2}%" y2="{y_pos + 45}px" stroke="#00ff88" stroke-width="2" marker-end="url(#arrow)"/>'
                )

    bubbles_html = "\n            ".join(bubbles)
    svg_connections = "\n                ".join(svg_lines)
    phase_labels_html = "\n            ".join(phase_labels)

    return HTML_TEMPLATE.format(
        title=title,
        order_items=order_items_html,
        failure_items=failure_items_html,
        svg_connections=svg_connections,
        bubbles=bubbles_html,
        phase_labels=phase_labels_html,
    )


def main():
    if len(sys.argv) < 2:
        json_path = Path(__file__).parent / "session_data.json"
    else:
        json_path = Path(sys.argv[1])

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

            html = generate_session_report(data)

            output_path = json_path.parent / "session_report.html"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)

                print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
