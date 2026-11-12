"""
Azazael IMMERSIVE POPUP
Full visual experience with all systems
"""

import tkinter as tk
from typing import List
from CORE.paths import get_desktop_dir

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DESKTOP_DIR = get_desktop_dir()


def create_immersive_popup():
    root = tk.Tk()
    root.title("Azazael ECOSYSTEM - Immersive Experience")
    root.geometry("1200x800")
    root.configure(bg="#000")
    root.resizable(True, True)

    # Note: tkinter.webview is not a standard module. The immersive HTML
    # display uses a fallback tkinter Text widget which works on all platforms.
    # For full webview support, install: pip install tkinterweb

    # HTML content for reference (used by webview path if available)
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Azazael ECOSYSTEM</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #0a0a1a 0%, #1a1a3a 50%, #0d0d20 100%);
            min-height: 100vh;
            color: #fff;
            overflow-x: hidden;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 30px; }
        
        .header {
            text-align: center;
            padding: 40px 20px;
            background: linear-gradient(135deg, rgba(0, 245, 255, 0.15), rgba(191, 0, 255, 0.15));
            border-radius: 20px;
            margin-bottom: 30px;
            border: 2px solid rgba(0, 245, 255, 0.3);
        }
        .header h1 {
            font-size: 3.5em;
            background: linear-gradient(90deg, #00f5ff, #bf00ff, #ff00aa, #ffdd00, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: gradientMove 4s ease infinite;
        }
        @keyframes gradientMove {
            0%, 100% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
        }
        .stats {
            display: flex;
            justify-content: center;
            gap: 50px;
            margin-top: 30px;
            flex-wrap: wrap;
        }
        .stat {
            text-align: center;
            background: rgba(255,255,255,0.05);
            padding: 20px 30px;
            border-radius: 15px;
        }
        .stat-value {
            font-size: 2.5em;
            font-weight: bold;
            background: linear-gradient(135deg, #00f5ff, #bf00ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stat-label {
            color: rgba(255,255,255,0.6);
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-top: 5px;
        }
        
        .systems-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: rgba(20, 20, 40, 0.95);
            border-radius: 16px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            transition: all 0.4s;
            cursor: pointer;
        }
        .card:hover {
            transform: translateY(-8px);
            box-shadow: 0 20px 50px rgba(0, 245, 255, 0.3);
        }
        .card h3 {
            font-size: 1.4em;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .card .type { color: #888; font-size: 0.85em; margin-bottom: 10px; }
        .card p { color: rgba(255,255,255,0.7); font-size: 0.9em; line-height: 1.5; }
        
        .card-hydra { border-left: 4px solid #ff00aa; }
        .card-hydra h3 { color: #ff00aa; }
        .card-horus { border-left: 4px solid #00f5ff; }
        .card-horus h3 { color: #00f5ff; }
        .card-tarbaby { border-left: 4px solid #00ff88; }
        .card-tarbaby h3 { color: #00ff88; }
        .card-shorty { border-left: 4px solid #ffdd00; }
        .card-shorty h3 { color: #ffdd00; }
        .card-poindexter { border-left: 4px solid #bf00ff; }
        .card-poindexter h3 { color: #bf00ff; }
        .card-persephone { border-left: 4px solid #ff6b35; }
        .card-persephone h3 { color: #ff6b35; }
        .card-twitler { border-left: 4px solid #22c55e; }
        .card-twitler h3 { color: #22c55e; }
        .card-wormz { border-left: 4px solid #ff4757; }
        .card-wormz h3 { color: #ff4757; }
        .card-metaworms { border-left: 4px solid #a855f7; }
        .card-metaworms h3 { color: #a855f7; }
        .card-terence { border-left: 4px solid #06b6d4; }
        .card-terence h3 { color: #06b6d4; }
        .card-klink { border-left: 4px solid #ec4899; }
        .card-klink h3 { color: #ec4899; }
        
        .tiers {
            background: rgba(20, 20, 40, 0.95);
            border-radius: 20px;
            padding: 30px;
        }
        .tiers h2 {
            text-align: center;
            color: #00ff88;
            margin-bottom: 25px;
        }
        .tier-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .tier {
            display: flex;
            align-items: center;
            gap: 20px;
            padding: 15px 20px;
            background: rgba(255,255,255,0.05);
            border-radius: 10px;
            transition: all 0.3s;
        }
        .tier:hover {
            background: rgba(255,255,255,0.1);
            transform: translateX(10px);
        }
        .tier-num {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1.1em;
        }
        .tier-info h4 { margin-bottom: 3px; }
        .tier-info p { color: #888; font-size: 0.85em; }
        
        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(30px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .card { animation: fadeUp 0.6s ease-out forwards; }
        .card:nth-child(1) { animation-delay: 0.05s; }
        .card:nth-child(2) { animation-delay: 0.1s; }
        .card:nth-child(3) { animation-delay: 0.15s; }
        .card:nth-child(4) { animation-delay: 0.2s; }
        .card:nth-child(5) { animation-delay: 0.25s; }
        .card:nth-child(6) { animation-delay: 0.3s; }
        .card:nth-child(7) { animation-delay: 0.35s; }
        .card:nth-child(8) { animation-delay: 0.4s; }
        .card:nth-child(9) { animation-delay: 0.45s; }
        .card:nth-child(10) { animation-delay: 0.5s; }
        .card:nth-child(11) { animation-delay: 0.55s; }
        
        .footer {
            text-align: center;
            padding: 30px;
            margin-top: 30px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }
        .footer h2 { color: #00ff88; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Azazael ECOSYSTEM</h1>
            <p style="color: #888; margin-top: 10px;">ALL 11 SYSTEMS OPERATIONAL</p>
            <div class="stats">
                <div class="stat"><div class="stat-value">11</div><div class="stat-label">Systems</div></div>
                <div class="stat"><div class="stat-value">7</div><div class="stat-label">Tiers</div></div>
                <div class="stat"><div class="stat-value">24</div><div class="stat-label">Animations</div></div>
                <div class="stat"><div class="stat-value">26</div><div class="stat-label">Patterns</div></div>
            </div>
        </div>
        
        <div class="systems-grid">
            <div class="card card-hydra">
                <h3>🚀 HYDRA</h3>
                <div class="type">Orchestration | 6 Stages, 10 Nodes</div>
                <p>Adaptive Sequential ML Cluster with 6-stage processing pipeline</p>
            </div>
            <div class="card card-horus">
                <h3>👁️ HORUS</h3>
                <div class="type">Presentation</div>
                <p>Human-Readable Output with 24 animation protocols</p>
            </div>
            <div class="card card-tarbaby">
                <h3>📦 TARBABY</h3>
                <div class="type">Processing</div>
                <p>Binary and data processing with format conversion</p>
            </div>
            <div class="card card-shorty">
                <h3>>> SHORTY</h3>
                <div class="type">Integration | ~20 Shortcuts</div>
                <p>Batch file shortcuts for quick ecosystem operations</p>
            </div>
            <div class="card card-poindexter">
                <h3>📊 POINDEXTER</h3>
                <div class="type">Analysis</div>
                <p>Mathematical indexer with statistical analysis</p>
            </div>
            <div class="card card-persephone">
                <h3>🔄 PERSEPHONE</h3>
                <div class="type">Utilities</div>
                <p>Cross-platform utilities with OS abstraction</p>
            </div>
            <div class="card card-twitler">
                <h3>🔔 TWITLER</h3>
                <div class="type">Monitoring</div>
                <p>System information and notification system</p>
            </div>
            <div class="card card-wormz">
                <h3>🐛 WORMZ</h3>
                <div class="type">Execution | 6 Worms, 5 Clusters</div>
                <p>WORMZ, SYSTEMZ, PROCESSZ, DISKZ, CLOCKZ, NETWORXZ</p>
            </div>
            <div class="card card-metaworms">
                <h3>🎯 METAWORMS</h3>
                <div class="type">Orchestration | 11 Agents, ML</div>
                <p>ERRORZ, TRIMZ, METAZ, DEPENDZ, VALIDZ, DEADZ, METRICZ, LOGZ, CHANGEZ, OPTIMIZ, HEALTHZ</p>
            </div>
            <div class="card card-terence">
                <h3>🔧 TERENCE</h3>
                <div class="type">Error Resolution | 26 Patterns</div>
                <p>Cross-tier error resolution with pattern matching</p>
            </div>
            <div class="card card-klink">
                <h3>🔗 KLINK</h3>
                <div class="type">Integration</div>
                <p>Linkage and connection system between components</p>
            </div>
        </div>
        
        <div class="tiers">
            <h2>7-Tier Architecture</h2>
            <div class="tier-list">
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #a855f7, #ec4899); color: #fff;">5</div>
                    <div class="tier-info"><h4 style="color: #a855f7;">DISTILLATION</h4><p>Theoretical knowledge synthesis</p></div>
                </div>
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #a855f7, #06b6d4); color: #fff;">4</div>
                    <div class="tier-info"><h4 style="color: #a855f7;">HYDRATIC METAWORMS</h4><p>ML Optimization with HYDRAMETA</p></div>
                </div>
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #ff4757, #ff6b35); color: #fff;">3</div>
                    <div class="tier-info"><h4 style="color: #ff4757;">METAWORMS</h4><p>11 orchestration agents</p></div>
                </div>
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #ff4757, #22c55e); color: #fff;">2</div>
                    <div class="tier-info"><h4 style="color: #ff4757;">CLUSTERWORMS</h4><p>Coordinated worm collectives</p></div>
                </div>
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #ff4757, #00f5ff); color: #fff;">1</div>
                    <div class="tier-info"><h4 style="color: #ff4757;">WORMS</h4><p>6 functional agents</p></div>
                </div>
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #00f5ff, #00ff88); color: #000;">0</div>
                    <div class="tier-info"><h4 style="color: #00f5ff;">CONCURRENCY</h4><p>Parallel execution engine</p></div>
                </div>
                <div class="tier">
                    <div class="tier-num" style="background: linear-gradient(135deg, #00ff88, #ffdd00); color: #000;">-1</div>
                    <div class="tier-info"><h4 style="color: #00ff88;">OS_CONGRUENCY</h4><p>Deep Windows integration</p></div>
                </div>
            </div>
        </div>
        
        <div class="footer">
            <h2>ALL 11 SYSTEMS OPERATIONAL</h2>
            <p style="color: #888; margin-top: 10px;">CODEX: {DESKTOP_DIR}\\CODEX</p>
        </div>
    </div>
</body>
</html>"""

    # Use tkinter Text widget (always available, no external deps)
    try:
        # Attempt webview if available (optional dependency)
        import tkinterweb
        web = tkinterweb.HtmlFrame(root)
        web.load_html(html_content)
        web.pack(fill="both", expand=True)
    except ImportError as exc:
        # Fallback to simple Tkinter display — always works
        # Fallback to simple Tkinter display — always works
        note_failure("POPUP_IMMERSIVE:L322", exc)
        text = tk.Text(root, wrap="word", font=("Consolas", 9))
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert(
            "1.0",
            """
================================================================================
                    Azazael ECOSYSTEM - ALL 11 SYSTEMS
================================================================================

SYSTEMS: 11 | TIERS: 7 | ANIMATIONS: 24 | PATTERNS: 26

+--------------------------------------------------------------------------+
|                           CORE SYSTEMS (6)                              |
+--------------------------------------------------------------------------+
|  🚀 HYDRA        Orchestration | 6 Stages, 10 Nodes, Adaptive ML        |
|  👁️ HORUS        Presentation | 24 Animations, Live Monitoring         |
|  📦 TARBABY      Processing    | Binary/Data Transformation             |
|  >> SHORTY       Integration   | ~20 .bat Shortcuts                    |
|  📊 POINDEXTER   Analysis     | Statistical/Mathematical              |
|  🔄 PERSEPHONE   Utilities    | Cross-Platform Support                |
+--------------------------------------------------------------------------+
|                           MONITORING (1)                                 |
+--------------------------------------------------------------------------+
|  🔔 TWITLER      Monitoring   | Health, Alerts, Status                |
+--------------------------------------------------------------------------+
|                           EXECUTION (2)                                 |
+--------------------------------------------------------------------------+
|  🐛 WORMZ        Execution    | 6 Worms: WORMZ, SYSTEMZ, PROCESSZ      |
|                                DISKZ, CLOCKZ, NETWORXZ                   |
|                                Clusters: BIBL, System, Network, etc.     |
|  🎯 METAWORMS    Orchestration| 11 Agents: ERRORZ, TRIMZ, METAZ       |
|                                DEPENDZ, VALIDZ, DEADZ, METRICZ          |
|                                LOGZ, CHANGEZ, OPTIMIZ, HEALTHZ          |
|                                HYDRAMETA (ML Optimization)               |
+--------------------------------------------------------------------------+
|                           UTILITIES (2)                                 |
+--------------------------------------------------------------------------+
|  🔧 TERENCE      Error        | 26 Fix Patterns, RL, Preview          |
|  🔗 KLINK        Integration  | Component Linking, APIs                |
+--------------------------------------------------------------------------+
|                        7-TIER ARCHITECTURE                              |
+--------------------------------------------------------------------------+
|  Tier 5:  DISTILLATION    Theoretical Knowledge Synthesis               |
|  Tier 4:  HYDRATIC        ML Optimization (HYDRAMETA)                  |
|  Tier 3:  METAWORMS       11 Orchestration Agents                      |
|  Tier 2:  CLUSTERWORMS    Coordinated Collectives                       |
|  Tier 1:  WORMS           6 Functional Worms                            |
|  Tier 0:  CONCURRENCY     Parallel Execution Engine                     |
|  Tier -1: OS_CONGRUENCY   Deep Windows Integration                     |
+--------------------------------------------------------------------------+
|                    ALL SYSTEMS OPERATIONAL                               |
+--------------------------------------------------------------------------+

CODEX: {DESKTOP_DIR}\\CODEX
""",
        )
        text.config(state="disabled", bg="#0a0a15", fg="#00f5ff")

    def close():
        root.destroy()

    btn = tk.Button(
        root,
        text="CLOSE",
        command=close,
        bg="#e74c3c",
        fg="white",
        font=("Arial", 12, "bold"),
        padx=30,
        pady=10,
    )
    btn.pack(pady=10)

    root.mainloop()


if __name__ == "__main__":
    create_immersive_popup()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
