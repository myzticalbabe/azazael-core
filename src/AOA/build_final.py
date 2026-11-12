import json, os, re
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE = os.path.dirname(os.path.abspath(__file__))

# Read benchmark data
with open(os.path.join(BASE,'benchmark_data.json'),encoding='utf-8') as f:
    bm = json.load(f)
p = bm['perfmon']; a = bm['azazael']; s = bm['system']
pv = s['python_version']; cpu = s['cpu_count']; host = s['node']
cpup = p['cpu_percent']; memp = p['memory_percent']; diskp = p['disk_percent']
mt = p['memory_total_gb']; dt = p['disk_total_gb']; wc = a['total_worms']

# Read current HTML
with open(os.path.join(BASE,'AOA_STRUCTURE.html'),encoding='utf-8') as f:
    html = f.read()
print(f'Read HTML: {len(html)} chars')

# Build new content with short lines
N = []
def w(*lines):
    for line in lines:
        N.append(line+'\n')

# ── DIAGRAM 3: Quartet Neural Core ──
w('<h2>⚡ Quartet Neural Core — Detailed Topology</h2>',
  '<div class=mermaid-wrap><div class=mermaid>',
  'graph LR')
w('    subgraph AZAZAEL["AZAZAEL — Worm Collective"]',
  '        direction TB',
  '        AZ1{{"CLUSTERWORMS v2.0.0"}}',
  '        AZ2["MetaCluster — Flat Dispatch"]',
  '        AZ3["FreeThreadPool — 3.14t GIL-Free"]',
  '        AZ4(("WORM_REGISTRY — 101 Worms"))',
  '        AZ1 --> AZ2 --> AZ3 --> AZ4',
  '    end')
w('    subgraph CODEX["CODEX — Video Pipeline"]',
  '        direction TB',
  '        CX1["FrameGenerator — PIL Rendering"]',
  '        CX2["VideoEncoder — FFmpeg HEVC"]',
  '        CX3>"EventRecorder — Memory Bus"]',
  '        CX1 --> CX2 --> CX3',
  '    end')
w('    subgraph EVE["EVE — AI Selector"]',
  '        direction TB',
  '        EV1{{"Model Benchmarks — Multi-Backend"}}',
  '        EV2(("Python REPL — Live Inference"))',
  '        EV1 --> EV2',
  '    end')
w('    subgraph LIBRA["LIBRA — Legal Automation"]',
  '        direction TB',
  '        LX1["BIBL Index — Terminology Builder"]',
  '        LX2>"CAIN Pipeline — Puppeteer Capture"]',
  '        LX3["Legal Dictionary — 59 Terms · 9 Categories"]',
  '        LX1 --> LX2 --> LX3',
  '    end')
# Style definitions - each under 100 chars
w('    classDef azstyle fill:#1a0a2e,stroke:#d500f9,'
  +'stroke-width:2px,color:#e0b0ff',
  '    classDef cxstyle fill:#0a1a2e,stroke:#ff9100,'
  +'stroke-width:2px,color:#ffe0b0',
  '    classDef evstyle fill:#0a2e1a,stroke:#00e5ff,'
  +'stroke-width:2px,color:#b0ffe0',
  '    classDef lxstyle fill:#1a1a0a,stroke:#ffd600,'
  +'stroke-width:2px,color:#ffe080')
w('    class AZ1,AZ2,AZ3,AZ4 azstyle',
  '    class CX1,CX2,CX3 cxstyle',
  '    class EV1,EV2 evstyle',
  '    class LX1,LX2,LX3 lxstyle',
  '</div></div>')
print('Diagram 3 done')

# ── DIAGRAM 4: Neural Data Pathways ──
w('<h2>🔗 Neural Data Pathways — Cross-Arm Routing</h2>',
  '<div class=mermaid-wrap><div class=mermaid>',
  'graph LR')
w('    PF(("PerfMon — get_metrics"))',
  '    -->|"raw metrics"| PM{{"PAMS — record_event"}}')
w('    PM -->|"persisted event"|',
  '    AZ["Azazael — discover_worms"]')
w('    AZ -->|"worm manifest"|',
  '    AZ2>"Azazael — run_cluster"]')
w('    AZ2 -->|"cluster results"|',
  '    CX["CODEX — render_video"]')
w('    CX -->|"video bytes"|',
  '    OUT((("AOA_Structure_Benchmark.mp4")))')
w('    PF -.->|"telemetry"| EB["EventBus"]',
  '    PM -.->|"milestone"| EB',
  '    AZ -.->|"anomaly"| EB',
  '    CX -.->|"render_complete"| EB')
w('    classDef source fill:#0a1a1a,stroke:#00e676,'
  +'stroke-width:2px',
  '    classDef process fill:#1a0a2e,stroke:#d500f9,'
  +'stroke-width:2px',
  '    classDef output fill:#0a0a2e,stroke:#ffd600,'
  +'stroke-width:3px',
  '    classDef bus fill:#0a0a1e,stroke:#5b5bff,'
  +'stroke-width:1px,stroke-dasharray:5 5')
w('    class PF source',
  '    class PM,AZ,AZ2,CX process',
  '    class OUT output',
  '    class EB bus',
  '</div></div>')
print('Diagram 4 done')

# ── DIAGRAM 5: Session Lifecycle ──
w('<h2>⏳ Session Lifecycle — Sequence Diagram</h2>',
  '<div class=mermaid-wrap><div class=mermaid>',
  'sequenceDiagram')
w('    participant CLI as AOA CLI',
  '    participant REG as ArmRegistry',
  '    participant EB as EventBus',
  '    participant PM as PAMS',
  '    participant AZ as Azazael',
  '    participant CX as CODEX')
w('    CLI->>REG: aoa init',
  '    REG->>REG: import arm modules',
  '    REG->>EB: publish ARM_INITIALIZED',
  '    REG-->>CLI: 8 arms across 2 categories')
w('    CLI->>REG: aoa arms status',
  '    REG->>PM: get_status',
  '    PM->>PM: DirectMemory.verify()',
  '    PM-->>REG: initialized=true',
  '    REG-->>CLI: all arms healthy')
w('    CLI->>REG: workflow start',
  '    REG->>AZ: discover_worms',
  '    AZ->>EB: publish WORM_EXECUTED',
  '    AZ-->>REG: 101 worms discovered')
w('    REG->>AZ: run_cluster',
  '    AZ->>EB: publish CLUSTER_COMPLETE',
  '    AZ-->>REG: 9 results')
w('    REG->>CX: render_video',
  '    CX->>EB: publish RENDER_COMPLETE',
  '    CX-->>REG: AOA_Structure_Benchmark.mp4')
w('    REG->>PM: record_event (session_end)',
  '    PM->>EB: publish SESSION_MILESTONE')
w('    Note over CLI,PM: Session complete ·'
  +' Events recorded in DirectMemory',
  '</div></div>')
print('Diagram 5 done')

# ── DIAGRAM 6: Memory Architecture ──
w('<h2>🗄️ Memory Architecture — DirectMemory + PAMS</h2>',
  '<div class=mermaid-wrap><div class=mermaid>',
  'graph TD')
w('    subgraph PAMS["PAMS — Performance & Memory System"]',
  '        DM[("DirectMemory — SQLite Backend")]',
  '        MB>"MemoryBus — Event Streaming"]',
  '        AT["AuditTrail — Immutable Log"]',
  '    end')
w('    subgraph WRITERS["WRITERS"]',
  '        MI["milestone()"]',
  '        FE["file_edit()"]',
  '        TV["tooling_verify()"]',
  '        SL["skill_loaded()"]',
  '    end')
w('    subgraph READERS["READERS"]',
  '        CNT["count()"]',
  '        LST["list_events()"]',
  '        QRY["query(subject)"]',
  '        SUM["summary()"]',
  '    end')
w('    MI --> DM; FE --> DM; TV --> DM; SL --> DM',
  '    DM --> MB; DM --> AT',
  '    DM --> CNT; DM --> LST; DM --> QRY; DM --> SUM',
  '    MB -.-> EB["EventBus — Cross-Arm Pub/Sub"]')
w('    classDef store fill:#0a1a1a,stroke:#00e676,'
  +'stroke-width:2px',
  '    classDef writer fill:#1a0a2e,stroke:#d500f9,'
  +'stroke-width:2px',
  '    classDef reader fill:#0a0a2e,stroke:#5b5bff,'
  +'stroke-width:2px',
  '    classDef bus fill:#1a1a0a,stroke:#ffd600,'
  +'stroke-width:1px,stroke-dasharray:5 5')
w('    class DM,MB,AT store',
  '    class MI,FE,TV,SL writer',
  '    class CNT,LST,QRY,SUM reader',
  '    class EB bus',
  '</div></div>')
print('Diagram 6 done')

# ── DIAGRAM 7: Worm Cluster Topology ──
w('<h2>🕸️ Worm Cluster Topology — Azazael v2.0.0</h2>',
  '<div class=mermaid-wrap><div class=mermaid>',
  'graph TB')
w('    MC{{"MetaCluster v2.0.0 — Orchestrator"}}')
w('    subgraph N1["ORCHESTRATION — 6 Worms"]',
  '        CW1["ClusterWormZ — Parallel Executor"]',
  '        CW2>"Registry Categories — Flat Dispatch"]',
  '    end')
w('    subgraph N2["MONITOR — 6 Worms"]',
  '        SZ["SystemZ — Resource Monitor"]',
  '        NW["NetworkZ — Traffic Analyzer"]',
  '    end')
w('    subgraph N3["DATA — 5 Worms"]',
  '        ML1["MLTrainer — Model Pipeline"]',
  '        VIZ["DashboardZ — Visualization"]',
  '    end')
w('    subgraph N4["SECURITY — 5 Worms"]',
  '        VS["VulnScanner — Threat Detection"]',
  '        PA>"PortAuditor — Network Audit"]',
  '    end')
w('    subgraph N5["DEVOPS — 5 Worms"]',
  '        DC["DockerZ — Container Mgmt"]',
  '        MIG["MigrationZ — Schema Versioning"]',
  '    end')
w('    MC --> N1; MC --> N2; MC --> N3; MC --> N4; MC --> N5')
# Cluster styles
w('    classDef meta fill:#0d0d30,stroke:#ffd600,'
  +'stroke-width:3px',
  '    classDef orch fill:#2a0a1a,stroke:#ff1744,'
  +'stroke-width:2px',
  '    classDef mon fill:#0a2a1a,stroke:#00e676,'
  +'stroke-width:2px',
  '    classDef data fill:#0a0a2a,stroke:#5b5bff,'
  +'stroke-width:2px',
  '    classDef sec fill:#1a0a2a,stroke:#d500f9,'
  +'stroke-width:2px',
  '    classDef dev fill:#1a1a0a,stroke:#ffd600,'
  +'stroke-width:2px')
w('    class MC meta',
  '    class CW1,CW2 orch',
  '    class SZ,NW mon',
  '    class ML1,VIZ data',
  '    class VS,PA sec',
  '    class DC,MIG dev',
  '</div></div>')
print('Diagram 7 done')

print(f'Built {len(N)} lines of new content')

# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
