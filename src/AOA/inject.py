"""Inject additional Mermaid diagrams and content into AOA_STRUCTURE.html."""
import json
import os
from html import escape
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "benchmark_data.json"), encoding="utf-8") as f:
    bm = json.load(f)

perf = bm.get("perfmon", {})
aza = bm.get("azazael", {})
system = bm.get("system", {})


def diagram(title, body):
    return (
        f'<section class="panel"><h2>{escape(title)}</h2>'
        f'<div class="mermaid-wrap"><div class="mermaid">{escape(body)}</div></div></section>'
    )


sections = [
    diagram(
        "Quartet Neural Core - Detailed Topology",
        """graph LR
AZ1{{\"CLUSTERWORMS v2.0.0\"}} --> AZ2[\"MetaCluster\"] --> AZ3[\"FreeThreadPool\"]
CX1[\"FrameGenerator\"] --> CX2[\"VideoEncoder\"]
EV1{{\"Model Benchmarks\"}} --> EV2((\"Python REPL\"))
LX1[\"BIBL Index\"] --> LX2[\"CAIN Pipeline\"]""",
    ),
    diagram(
        "Neural Data Pathways - Cross-Arm Routing",
        """graph LR
PF((\"PerfMon\")) --> PM{{\"PAMS record_event\"}}
PM --> AZ[\"Azazael discover_worms\"]
AZ --> CX[\"CODEX render_video\"]
CX --> OUT(((\"Benchmark output\")))""",
    ),
    diagram(
        "Session Lifecycle - Sequence Diagram",
        """sequenceDiagram
participant CLI as AOA CLI
participant REG as ArmRegistry
participant PM as PAMS
participant AZ as Azazael
participant CX as CODEX
CLI->>REG: aoa init
REG->>PM: get_status
PM-->>REG: initialized=true
REG->>AZ: discover_worms
AZ-->>REG: worm manifest
REG->>CX: render_video
CX-->>REG: output ready""",
    ),
    diagram(
        "Memory Architecture - DirectMemory + PAMS",
        """graph TD
DM[(\"DirectMemory - SQLite\")]
MB[\"MemoryBus - Event Streaming\"]
AT[\"AuditTrail - Immutable Log\"]
MI[\"milestone()\"] --> DM
FE[\"file_edit()\"] --> DM
DM --> MB
DM --> AT""",
    ),
    diagram(
        "Worm Cluster Topology - Azazael v2.0.0",
        """graph TB
MC{{\"MetaCluster v2.0.0\"}}
N1[\"ORCHESTRATION\"]
N2[\"MONITOR\"]
N3[\"DATA\"]
N4[\"SECURITY\"]
N5[\"DEVOPS\"]
MC --> N1
MC --> N2
MC --> N3
MC --> N4
MC --> N5""",
    ),
]

html_path = os.path.join(BASE, "AOA_STRUCTURE.html")
with open(html_path, encoding="utf-8") as f:
    html = f.read()

marker = "</main>"
if marker in html:
    html = html.replace(marker, "\n".join(sections) + marker, 1)
else:
    html += "\n".join(sections)

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html)

print(
    f"Injected {len(sections)} sections into {html_path} "
    f"({os.path.getsize(html_path)} bytes; "
    f"python={system.get('python_version', '?')}, "
    f"cpu={perf.get('cpu_percent', 0)}%, worms={aza.get('total_worms', 0)})"
)
