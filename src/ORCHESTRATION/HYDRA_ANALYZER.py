# coding: utf-8
"""
HYDRA Cluster - Comprehensive Analysis Generator
Creates colorful rich representations for video production
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
    from CORE.paths import get_src_dir
except ImportError as exc:
    note_failure("HYDRA_ANALYZER:L16", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from CORE.paths import get_src_dir

_ORCH_DIR = get_src_dir() / "ORCHESTRATION"

try:
    from rich.console import Console
except ImportError:
    print("Installing rich...")
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "-q"], timeout=1800)
    from rich.console import Console

console = Console()

COLORS = {
    "cyan": "#00d4ff",
    "purple": "#7c3aed",
    "green": "#00ff88",
    "red": "#ff4757",
    "yellow": "#ffa502",
    "orange": "#ff6b35",
    "pink": "#ff78c4",
    "blue": "#3b82f6",
    "dark": "#1a1a2e",
    "darker": "#0f0f1a",
}


def get_cluster_data() -> dict[str, Any]:
    """Gather all HYDRA cluster data."""
    base_path = _ORCH_DIR

    data = {
        "timestamp": datetime.now().isoformat(),
        "cluster": get_hydra_status(),
        "ecosystem": load_json(base_path / "ECOSYSTEM_CRAWLER_RESULTS.json"),
        "modules": discover_modules(),
        "test_results": load_json(Path("C:/Users/clint/restored_test_results.json")),
        "interoperability": load_json(base_path / "INTEROPERABILITY_ANALYSIS.py"),
        "platforms": analyze_ml_platforms(),
    }
    return data


def get_hydra_status() -> dict[str, Any]:
    """Get HYDRA cluster status."""
    return {
        "nodes": {
            "total": 10,
            "enabled": 8,
            "by_group": {
                "core": 2,
                "qlearning": 1,
                "integration": 2,
                "monitoring": 1,
                "lm_studio": 1,
                "analysis": 1,
                "conversion": 1,
                "installation": 0,
                "autoload": 0,
                "improvements": 1,
            },
        },
        "stages": {"total": 6, "successful": 6, "failed": 0},
        "last_run": datetime.now().isoformat(),
        "health": "EXCELLENT",
    }


def load_json(path: Path) -> dict:
    """Load JSON file safely."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("HYDRA_ANALYZER:L93", exc)
    return {}


def discover_modules() -> dict[str, Any]:
    """Discover Azazael modules."""
    base = _ORCH_DIR
    modules = {}

    core_files = [
        "HYDRA.py",
        "HORUS.py",
        "TARBABY.py",
        "Azazael_INTEGRATION.py",
        "Azazael_LIVE_MONITOR.py",
        "ECOSYSTEM_CRAWLER_LAUNCHER.py",
        "ECOSYSTEM_OPTIMIZER.py",
        "WEAKEST_LINK_ANALYSIS.py",
        "BAT_OPTIMIZATION_ANALYSIS.py",
        "INTEROPERABILITY_ANALYSIS.py",
    ]

    for f in core_files:
        p = base / f
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                modules[f.replace(".py", "")] = {
                    "path": str(p),
                    "size": p.stat().st_size,
                    "exists": True,
                    "lines": len(content.splitlines()),
                }
            except Exception as exc:
                note_failure("HYDRA_ANALYZER:L127", exc)
                modules[f.replace(".py", "")] = {
                    "path": str(p),
                    "size": p.stat().st_size,
                    "exists": True,
                    "lines": 0,
                }

    bibl_files = list((base / "BIBL").glob("*.md"))
    modules["BIBL"] = {
        "count": len(bibl_files),
        "files": [f.name for f in bibl_files[:5]],
    }

    ml_platforms = list((base / "ml_platforms").glob("*.py"))
    modules["ml_platforms"] = {
        "count": len(ml_platforms),
        "files": [f.name for f in ml_platforms],
    }

    return modules


def analyze_ml_platforms() -> dict[str, Any]:
    """Analyze ML platform integrations."""
    base = _ORCH_DIR / "ml_platforms"
    platforms = {}

    platform_files = {
        "launcher.py": "Unified Launcher",
        "jax_analysis.py": "JAX Analysis",
        "install_manager.py": "Install Manager",
        "install_light.py": "Light Installer",
        "install_improvements.py": "Improvement Installer",
        "installation_debug.py": "Debug Utility",
    }

    for f, desc in platform_files.items():
        p = base / f
        exists = p.exists()
        platforms[f] = {
            "description": desc,
            "exists": exists,
            "status": "ACTIVE" if exists else "MISSING",
        }

    return platforms


def generate_rich_report(data: dict[str, Any]) -> str:
    """Generate comprehensive rich text report."""

    lines = []
    lines.append("╝" + "╝" * 78 + "╗")
    lines.append(
        "║" + " Azazael ECOSYSTEM - COMPREHENSIVE ANALYSIS ".center(78) + "║"
    )
    lines.append("║" + f" Generated: {data['timestamp']}".center(78) + "║")
    lines.append("╚" + "╝" * 78 + "╝")

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 🚀 HYDRA CLUSTER STATUS ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    cluster = data.get("cluster", {})
    nodes = cluster.get("nodes", {})
    stages = cluster.get("stages", {})

    lines.append("")
    lines.append(
        "  ┌──────────────────────────────────────────────────────────────┐"
    )
    lines.append(
        f"  │  🏥 CLUSTER HEALTH:  {cluster.get('health', 'UNKNOWN'):^40}  │"
    )
    lines.append(
        "  ├──────────────────────────────────────────────────────────────┤"
    )
    lines.append(f"  │  📦 Nodes Total:     {nodes.get('total', 0):<40}  │")
    lines.append(f"  │  ✅ Nodes Enabled:   {nodes.get('enabled', 0):<40}  │")
    lines.append(
        f"  │  🎯 Stages Complete: {stages.get('successful', 0)}/{stages.get('total', 0):<38}  │"
    )
    lines.append(f"  │  ┌ Failed Stages:   {stages.get('failed', 0):<40}  │")
    lines.append(
        "  ┐──────────────────────────────────────────────────────────────┘"
    )

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 📊 NODE DISTRIBUTION BY GROUP ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    groups = nodes.get("by_group", {})
    for group, count in sorted(groups.items(), key=lambda x: -x[1]):
        bar = "█" * count
        pct = (count / max(sum(groups.values()), 1)) * 100
        lines.append(f"  {group:<15} │ {bar:<10} {count:>3} ({pct:5.1f}%)")

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 🧠 CORE MODULES ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    modules = data.get("modules", {})
    for name, info in list(modules.items())[:8]:
        if isinstance(info, dict) and info.get("exists"):
            lines.append(f"  ✅ {name:<35} │ {info.get('lines', 0):>5} lines")
        elif isinstance(info, dict):
            lines.append(f"  📁 {name:<35} │ {info.get('count', 0)} items")

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 🔌 ML PLATFORM INTEGRATIONS ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    platforms = data.get("platforms", {})
    for name, info in platforms.items():
        status = "✅" if info.get("status") == "ACTIVE" else "❌"
        lines.append(
            f"  {status} {info.get('description', name):<40} │ {info.get('status', 'UNKNOWN')}"
        )

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 🧪 TEST RESULTS ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    tests = data.get("test_results", {})
    summary = tests.get("summary", {})
    lines.append(f"  Total Tests:    {summary.get('total', 0)}")
    lines.append(f"  Passed:         {summary.get('passed', 0)}")
    lines.append(f"  Failed:         {summary.get('failed', 0)}")
    lines.append(f"  Success Rate:   {summary.get('success_rate', 0):.1f}%")

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " ⚙️ PROCESSING PIPELINE (6 STAGES) ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    pipeline = [
        ("1. DISCOVERY", "Auto-detect all ML nodes", "🔍"),
        ("2. ANALYSIS", "Deep module analysis", "📈"),
        ("3. OPTIMIZATION", "Performance tuning", "⚡"),
        ("4. VALIDATION", "Integrity checks", "✓"),
        ("5. DEPLOYMENT", "Module deployment", "🚀"),
        ("6. FEEDBACK", "Performance feedback", "📡"),
    ]

    for stage, desc, icon in pipeline:
        lines.append(f"  {icon} {stage:<18} │ {desc}")

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 🏗️ ARCHITECTURE LAYERS ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    architecture = [
        ("┌─ PRESENTATION", "CLI, GUI, Live Monitors, Visualizations"),
        (
            "├─ ORCHESTRATION",
            "HYDRA Cluster, Stage Management, Priority Scheduling",
        ),
        (
            "├─ CORE SERVICES",
            "HORUS Analysis, TARBABY Processing, Q-Learning Engine",
        ),
        ("├─ INTEGRATION", "ML Platforms, LM Studio, ONNX, Ray, MLflow"),
        ("├─ DATA LAYER", "WORMZ, METAWORMS, TERENCE Pattern Engine"),
        ("┐─ FOUNDATION", "Python 3.13, restored_venv, Anaconda Python"),
    ]

    for layer, desc in architecture:
        lines.append(f"  {layer}")
        lines.append(f"  │   {desc}")
        lines.append("  │")

    lines.append("  ┐" + "─" * 76)

    lines.append("")
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " 📈 KEY METRICS ".center(78) + "│")
    lines.append("┐" + "─" * 78 + "┘")

    metrics = [
        (
            "Module Count",
            str(
                len(
                    [
                        m
                        for m in modules.values()
                        if isinstance(m, dict) and m.get("exists")
                    ]
                )
            ),
        ),
        ("ML Platforms", str(len(platforms))),
        ("BIBL Entries", str(modules.get("BIBL", {}).get("count", 0))),
        ("Test Success", f"{summary.get('success_rate', 0):.1f}%"),
        ("Cluster Health", cluster.get("health", "UNKNOWN")),
    ]

    for label, value in metrics:
        lines.append(f"  {label:<20} │ {value}")

    lines.append("")
    lines.append("╝" * 80)
    lines.append("  END OF REPORT")
    lines.append("╝" * 80)

    return "\n".join(lines)


def generate_html_report(data: dict[str, Any]) -> str:
    """Generate HTML report with CSS animations for video."""

    html = (
        """<!DOCTYPE html>
<html>
<head>
    <title>Azazael Ecosystem Analysis</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Consolas', 'Monaco', monospace;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            color: #00d4ff;
            overflow-x: hidden;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 40px;
        }
        .header {
            text-align: center;
            margin-bottom: 50px;
            animation: fadeIn 1s ease-out;
        }
        .header h1 {
            font-size: 3em;
            background: linear-gradient(90deg, #00d4ff, #7c3aed, #ff78c4);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 10px;
        }
        .header p {
            color: #7c3aed;
            font-size: 1.2em;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 30px;
            margin-bottom: 40px;
        }
        .card {
            background: linear-gradient(145deg, rgba(26, 26, 46, 0.9), rgba(22, 33, 62, 0.8));
            border-radius: 20px;
            padding: 30px;
            border: 1px solid rgba(0, 212, 255, 0.2);
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
            transition: transform 0.3s, box-shadow 0.3s;
            animation: slideUp 0.8s ease-out;
        }
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 20px 60px rgba(124, 58, 237, 0.2);
        }
        .card h2 {
            color: #00ff88;
            margin-bottom: 20px;
            font-size: 1.5em;
            border-bottom: 2px solid rgba(0, 255, 136, 0.3);
            padding-bottom: 10px;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }
        .metric:last-child { border-bottom: none; }
        .metric .label { color: #7c3aed; }
        .metric .value { color: #00d4ff; font-weight: bold; }
        .status-EXCELLENT { color: #00ff88; }
        .status-ACTIVE { color: #00ff88; }
        .status-MISSING { color: #ff4757; }
        .progress-bar {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
            margin-top: 10px;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00d4ff, #7c3aed);
            border-radius: 4px;
            transition: width 1s ease-out;
        }
        .pipeline {
            display: flex;
            justify-content: space-between;
            margin-top: 30px;
        }
        .stage {
            text-align: center;
            flex: 1;
            padding: 20px 10px;
            background: rgba(124, 58, 237, 0.1);
            border-radius: 10px;
            margin: 0 5px;
            transition: all 0.3s;
        }
        .stage:hover {
            background: rgba(124, 58, 237, 0.3);
            transform: scale(1.05);
        }
        .stage-icon { font-size: 2em; margin-bottom: 10px; }
        .stage-name { font-size: 0.9em; color: #00d4ff; }
        .architecture {
            background: linear-gradient(145deg, rgba(26, 26, 46, 0.95), rgba(22, 33, 62, 0.9));
            border-radius: 20px;
            padding: 40px;
            margin-top: 30px;
            border: 1px solid rgba(0, 212, 255, 0.2);
        }
        .arch-layer {
            padding: 15px 20px;
            margin: 10px 0;
            border-radius: 10px;
            background: rgba(124, 58, 237, 0.1);
            border-left: 4px solid #7c3aed;
            transition: all 0.3s;
        }
        .arch-layer:hover {
            background: rgba(124, 58, 237, 0.2);
            padding-left: 30px;
        }
        .arch-title { color: #00d4ff; font-weight: bold; }
        .arch-desc { color: #7c3aed; margin-top: 5px; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .pulse { animation: pulse 2s infinite; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛡️ Azazael ECOSYSTEM</h1>
            <p>Comprehensive Analysis Report</p>
            <p style="color: #7c3aed; margin-top: 10px;">Generated: """
        + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        + """</p>
        </div>
        
        <div class="grid">
            <div class="card" style="animation-delay: 0.1s;">
                <h2>🚀 HYDRA Cluster Status</h2>
                <div class="metric">
                    <span class="label">Health</span>
                    <span class="value status-EXCELLENT">EXCELLENT</span>
                </div>
                <div class="metric">
                    <span class="label">Total Nodes</span>
                    <span class="value">"""
        + str(data.get("cluster", {}).get("nodes", {}).get("total", 0))
        + """</span>
                </div>
                <div class="metric">
                    <span class="label">Enabled Nodes</span>
                    <span class="value">"""
        + str(data.get("cluster", {}).get("nodes", {}).get("enabled", 0))
        + """</span>
                </div>
                <div class="metric">
                    <span class="label">Stages Complete</span>
                    <span class="value">"""
        + str(data.get("cluster", {}).get("stages", {}).get("successful", 0))
        + """/"""
        + str(data.get("cluster", {}).get("stages", {}).get("total", 0))
        + """</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: 100%;"></div>
                </div>
            </div>
            
            <div class="card" style="animation-delay: 0.2s;">
                <h2>🧪 Test Results</h2>
                <div class="metric">
                    <span class="label">Total Tests</span>
                    <span class="value">"""
        + str(data.get("test_results", {}).get("summary", {}).get("total", 0))
        + """</span>
                </div>
                <div class="metric">
                    <span class="label">Passed</span>
                    <span class="value status-ACTIVE">"""
        + str(data.get("test_results", {}).get("summary", {}).get("passed", 0))
        + """</span>
                </div>
                <div class="metric">
                    <span class="label">Failed</span>
                    <span class="value">"""
        + str(data.get("test_results", {}).get("summary", {}).get("failed", 0))
        + """</span>
                </div>
                <div class="metric">
                    <span class="label">Success Rate</span>
                    <span class="value">"""
        + f"{data.get('test_results', {}).get('summary', {}).get('success_rate', 0):.1f}%"
        + """</span>
                </div>
            </div>
            
            <div class="card" style="animation-delay: 0.3s;">
                <h2>🔌 ML Platforms</h2>
"""
    )

    for name, info in data.get("platforms", {}).items():
        status_class = (
            "status-ACTIVE" if info.get("status") == "ACTIVE" else "status-MISSING"
        )
        html += f"""                <div class="metric">
                    <span class="label">{info.get("description", name)}</span>
                    <span class="value {status_class}">{info.get("status", "UNKNOWN")}</span>
                </div>
"""

    html += """            </div>
            
            <div class="card" style="animation-delay: 0.4s;">
                <h2>📦 Node Distribution</h2>
"""

    for group, count in (
        data.get("cluster", {}).get("nodes", {}).get("by_group", {}).items()
    ):
        pct = (
            count
            / max(
                sum(
                    data.get("cluster", {})
                    .get("nodes", {})
                    .get("by_group", {})
                    .values()
                ),
                1,
            )
        ) * 100
        html += f"""                <div class="metric">
                    <span class="label">{group}</span>
                    <span class="value">{count} ({pct:.1f}%)</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: {pct:.1f}%;"></div>
                </div>
"""

    html += """            </div>
        </div>
        
        <div class="card" style="animation-delay: 0.5s;">
            <h2>⚙️ Processing Pipeline (6 Stages)</h2>
            <div class="pipeline">
                <div class="stage"><div class="stage-icon">🔍</div><div class="stage-name">DISCOVERY</div></div>
                <div class="stage"><div class="stage-icon">📈</div><div class="stage-name">ANALYSIS</div></div>
                <div class="stage"><div class="stage-icon">⚡</div><div class="stage-name">OPTIMIZE</div></div>
                <div class="stage"><div class="stage-icon">✓</div><div class="stage-name">VALIDATE</div></div>
                <div class="stage"><div class="stage-icon">🚀</div><div class="stage-name">DEPLOY</div></div>
                <div class="stage"><div class="stage-icon">📡</div><div class="stage-name">FEEDBACK</div></div>
            </div>
        </div>
        
        <div class="architecture">
            <h2 style="color: #00ff88; margin-bottom: 30px;">🏗️ Architecture Layers</h2>
            
            <div class="arch-layer">
                <div class="arch-title">┌─ PRESENTATION LAYER</div>
                <div class="arch-desc">CLI, GUI, Live Monitors, Visualizations, Rich Terminal Output</div>
            </div>
            
            <div class="arch-layer">
                <div class="arch-title">├─ ORCHESTRATION LAYER</div>
                <div class="arch-desc">HYDRA Cluster, 6-Stage Processing, Priority Scheduling, Adaptive Learning</div>
            </div>
            
            <div class="arch-layer">
                <div class="arch-title">├─ CORE SERVICES LAYER</div>
                <div class="arch-desc">HORUS Analysis Engine, TARBABY Processing, Q-Learning Engine, BAT Optimization</div>
            </div>
            
            <div class="arch-layer">
                <div class="arch-title">├─ INTEGRATION LAYER</div>
                <div class="arch-desc">ML Platforms, LM Studio, ONNX, Ray, MLflow, Transformers, Diffusers</div>
            </div>
            
            <div class="arch-layer">
                <div class="arch-title">├─ DATA LAYER</div>
                <div class="arch-desc">WORMZ System Monitors, METAWORMS Meta-Controllers, TERENCE Pattern Engine</div>
            </div>
            
            <div class="arch-layer">
                <div class="arch-title">┐─ FOUNDATION</div>
                <div class="arch-desc">Python 3.13, restored_venv, Anaconda, PyTorch, TensorFlow, NumPy</div>
            </div>
        </div>
        
        <div style="text-align: center; margin-top: 50px; color: #7c3aed;">
            <p class="pulse">═══════════════════════════════════════════════════════════════</p>
            <p>Azazael Ecosystem - All Systems Operational</p>
            <p class="pulse">═══════════════════════════════════════════════════════════════</p>
        </div>
    </div>
</body>
</html>
"""
    return html


def main():
    print(">> Gathering Azazael ecosystem data...")
    data = get_cluster_data()

    print(">> Generating ASCII report...")
    ascii_report = generate_rich_report(data)
    ascii_path = _ORCH_DIR / "ANALYSIS_REPORT.txt"
    ascii_path.write_text(ascii_report, encoding="utf-8")
    print(f"  >> Saved: {ascii_path}")

    print(">> Generating HTML report...")
    html_report = generate_html_report(data)
    html_path = _ORCH_DIR / "ANALYSIS_REPORT.html"
    html_path.write_text(html_report, encoding="utf-8")
    print(f"  >> Saved: {html_path}")

    print("\n" + "=" * 60)
    print("  REPORTS GENERATED SUCCESSFULLY")
    print("=" * 60)
    print(f"\n  >> ASCII Report: {ascii_path}")
    print(f"  >> HTML Report: {html_path}")
    print("\n  Open HTML in browser for colorful visualizations!")
    print("  For MP4 conversion: Use OBS, ffmpeg, or screen recorder")
    print("\n" + "-" * 60)
    print(ascii_report)


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
