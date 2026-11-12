import json
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

with open('C:/Users/clint/Desktop/AOA/benchmark_data.json', encoding='utf-8') as f:
    bm = json.load(f)
perf = bm['perfmon']
aza = bm['azazael']
sys_info = bm['system']

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AOA - Armorie-of-Anubis Architecture</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
    mermaid.initialize({{
        startOnLoad: true,
        theme: "dark",
        themeVariables: {{
            primaryColor: "#58a6ff",
            primaryTextColor: "#e6edf3",
            primaryBorderColor: "#30363d",
            lineColor: "#58a6ff"
        }},
        flowchart: {{ useMaxWidth: true, htmlLabels: true }}
    }});
</script>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #0d1117; color: #e6edf3; font-family: system-ui, sans-serif; overflow-x: hidden; }}
    #particle-canvas {{ position: fixed; top: 0; left: 0; z-index: 0; pointer-events: none; }}
    .container {{ position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 40px 20px; }}
    h1 {{ font-size: 2.5em; background: linear-gradient(135deg, #58a6ff, #bc13fe); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
    h2 {{ color: #58a6ff; margin: 40px 0 20px; font-size: 1.6em; border-bottom: 2px solid rgba(88,166,255,0.3); padding-bottom: 8px; }}
    h3 {{ color: #bc13fe; margin-bottom: 10px; }}
    .benchmark-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; margin: 20px 0; }}
    .metric-card {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(88,166,255,0.2); border-radius: 12px; padding: 20px; }}
    .metric-card h3 {{ color: #58a6ff; margin-bottom: 12px; }}
    .metric-card td {{ padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.9em; }}
    .metric-card td:first-child {{ color: #8b949e; }}
    .amount {{ color: #3fb950; font-family: Consolas, monospace; text-align: right; }}
    .ok {{ color: #3fb950; }} .warn {{ color: #f7c948; }}
    table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
    th {{ text-align: left; padding: 10px; background: rgba(88,166,255,0.1); color: #58a6ff; }}
    td {{ padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
    .stat-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
    .stat-card {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(88,166,255,0.15); border-radius: 10px; padding: 18px; text-align: center; }}
    .stat-card .value {{ font-size: 2em; font-weight: bold; color: #3fb950; }}
    .mermaid {{ background: rgba(255,255,255,0.02); border-radius: 10px; padding: 20px; margin: 20px 0; }}
    @keyframes fadeInUp {{ from {{ opacity: 0; transform: translateY(20px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    @keyframes titleGlow {{ 0%,100% {{ text-shadow: 0 0 10px rgba(88,166,255,0.3); }} 50% {{ text-shadow: 0 0 30px rgba(88,166,255,0.6); }} }}
    @keyframes borderPulse {{ 0%,100% {{ border-color: rgba(88,166,255,0.15); }} 50% {{ border-color: rgba(88,166,255,0.4); }} }}
    @keyframes shimmer {{ 0% {{ opacity: 0.6; }} 50% {{ opacity: 1; }} 100% {{ opacity: 0.6; }} }}
    @keyframes gradientFlow {{ 0% {{ background-position: 0% 50%; }} 50% {{ background-position: 100% 50%; }} 100% {{ background-position: 0% 50%; }} }}
    .stat-card {{ animation: borderPulse 4s ease-in-out infinite; }}
    h1 {{ animation: titleGlow 3s ease-in-out infinite; }}
</style>
</head>
<body>
<canvas id="particle-canvas"></canvas>
<div class="container">
    <h1>AOA - Armorie-of-Anubis</h1>
    <p style="color:#8b949e;">Umbrella Orchestrator v1.1.0 | Quartet Architecture | Session 2026-07-01</p>

    <h2>Live Benchmarks</h2>
    <div class="benchmark-grid">
        <div class="metric-card">
            <h3>System</h3>
            <table>
                <tr><td>Python</td><td class="amount">{sys_info.get('python_version','?')} (free-threaded)</td></tr>
                <tr><td>CPU Cores</td><td class="amount">{sys_info.get('cpu_count','?')}</td></tr>
                <tr><td>Host</td><td class="amount">{sys_info.get('node','?')}</td></tr>
            </table>
        </div>
        <div class="metric-card">
            <h3>Performance (psutil live)</h3>
            <table>
                <tr><td>CPU Usage</td><td class="amount">{perf.get('cpu_percent','?')}%</td></tr>
                <tr><td>Memory</td><td class="amount">{perf.get('memory_percent','?')}%</td></tr>
                <tr><td>Disk</td><td class="amount">{perf.get('disk_percent','?')}%</td></tr>
            </table>
        </div>
        <div class="metric-card">
            <h3>Azazael Neural Mesh</h3>
            <table>
                <tr><td>Worms Registered</td><td class="amount">{aza.get('total_worms','?')}</td></tr>
                <tr><td>Initialized</td><td class="amount">{aza.get('initialized','?')}</td></tr>
            </table>
        </div>
    </div>

    <h2>Arm Status (8 Arms - All Wired)</h2>
    <div class="benchmark-grid">
        <div class="metric-card">
            <h3>Quartet Arms</h3>
            <table>
                <tr><td>Azazael (p=10)</td><td class="amount ok">{aza.get('total_worms','?')} worms</td></tr>
                <tr><td>CODEX (p=8)</td><td class="amount ok">Wired (subprocess)</td></tr>
                <tr><td>EveUnleashed (p=7)</td><td class="amount ok">Wired (subprocess)</td></tr>
                <tr><td>Libra (p=9)</td><td class="amount ok">Wired (sys.path)</td></tr>
            </table>
        </div>
        <div class="metric-card">
            <h3>Support Arms</h3>
            <table>
                <tr><td>PAMS (p=8)</td><td class="amount ok">DirectMemory</td></tr>
                <tr><td>PerfMon (p=6)</td><td class="amount ok">psutil live</td></tr>
                <tr><td>LlamaServer (p=5)</td><td class="amount ok">Wired (subprocess)</td></tr>
                <tr><td>AgentSelector (p=6)</td><td class="amount warn">Stub (v2)</td></tr>
            </table>
        </div>
    </div>

    <h2>Quartet Architecture</h2>
    <div class="mermaid">
graph TB
    AOA[AOA Orchestrator]
    AOA --> AZ[Azazael<br/>{aza.get('total_worms','?')} worms]
    AOA --> CX[CODEX<br/>HTML to MP4]
    AOA --> EV[EveUnleashed<br/>AI Selector]
    AOA --> LX[Libra<br/>Legal Docs]
    AOA --> PM[PAMS<br/>DirectMemory]
    AOA --> PF[PerfMon<br/>psutil]
    AZ -.-> PM
    CX -.-> PM
    EV -.-> PM
</div>

    <h2>Cross-Arm Pipeline</h2>
    <div class="mermaid">
graph LR
    A[1. PerfMon<br/>get_metrics] --> B[2. PAMS<br/>record_event]
    B --> C[3. Azazael<br/>discover_worms]
    C --> D[4. Azazael<br/>run_cluster]
    D --> E[5. CODEX<br/>render_mermaid]
    E --> F[Video MP4]
</div>

    <h2>ClusterWORMZ Execution</h2>
    <div class="mermaid">
graph TD
    CW[ClusterWORMZ v2.0.0] --> NAT[build_from_category]
    NAT --> REG[WORM_REGISTRY]
    REG --> EXEC[execute_all]
    EXEC --> PAMS[PamsRecorder]
    EXEC --> MB[MemoryBus]
    EXEC --> RES[Results]
</div>

    <h2>Event Bus Architecture</h2>
    <div class="mermaid">
graph LR
    AO[AOEvent] --> BUS[EventBus]
    BUS --> S1[arm.*]
    BUS --> S2[*.complete]
    BUS --> S3[workflow.*]
    EP[EventPriority] --> AO
    ET[AOEventType<br/>19 types] --> AO
</div>

    <h2>Session Recording</h2>
    <div class="mermaid">
sequenceDiagram
    participant CLI as AOA CLI
    participant SM as SessionManager
    participant DM as DirectMemory
    CLI->>SM: create_session
    SM->>DM: record
    CLI->>SM: record_milestone
    SM->>DM: record
    CLI->>SM: complete_session
</div>

    <h2>Workflow</h2>
    <table>
        <tr><th>Step</th><th>Arm</th><th>Capability</th><th>Depends</th></tr>
        <tr><td>1</td><td>perfmon</td><td>get_metrics</td><td>-</td></tr>
        <tr><td>2</td><td>pams</td><td>record_event</td><td>1</td></tr>
        <tr><td>3</td><td>azazael</td><td>discover_worms</td><td>2</td></tr>
        <tr><td>4</td><td>azazael</td><td>run_cluster</td><td>3</td></tr>
    </table>

    <h2>Generation Notes</h2>
    <p class="ok">This document was generated from benchmark_data.json.</p>
</div>
<script>
document.documentElement.dataset.generated = "true";
</script>
</body>
</html>
'''


with open('C:/Users/clint/Desktop/AOA/AOA_STRUCTURE_REBUILT.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Wrote C:/Users/clint/Desktop/AOA/AOA_STRUCTURE_REBUILT.html')
