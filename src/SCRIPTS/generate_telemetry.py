#!/usr/bin/env python
"""generate_telemetry.py — Export PreActionGate telemetry to JSON for dashboard.

Generates azazael/src/scripts/telemetry.json with RULE 0Z-F counters:
  - parallel_batches: Number of compliant parallel spawn_agents calls
  - sequential_violations: Number of sequential spawn violations
  - edit_count: Total successful edits through the gate
  - compliance_ratio: parallel / (parallel + sequential) as percentage
  - timestamp: ISO-8601 timestamp

Usage:
    python azazael/src/scripts/generate_telemetry.py
    python azazael/src/scripts/generate_telemetry.py --output /path/to/telemetry.json
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dashboard embed markers — generate_telemetry.py stamps real telemetry into
# rule_0zf_dashboard.html between these markers so file:// opens show real
# data instead of the old random demo-data fallback.
_EMBED_START = "// __TELEMETRY_EMBED_START__"
_EMBED_END = "// __TELEMETRY_EMBED_END__"


def _stamp_dashboard(data: dict) -> None:
    """Embed telemetry JSON into rule_0zf_dashboard.html between markers.

    The dashboard is opened via file:// where fetch('telemetry.json') is
    blocked by CORS. Stamping the real JSON into the HTML makes the
    dashboard show actual data without a local HTTP server.
    """
    dashboard = Path(__file__).resolve().parent / "rule_0zf_dashboard.html"
    if not dashboard.exists():
        return
    html = dashboard.read_text(encoding="utf-8")
    block = (
        f"{_EMBED_START}\n"
        f"    const EMBEDDED_TELEMETRY = {json.dumps(data)};\n"
        f"    {_EMBED_END}"
    )
    if _EMBED_START in html and _EMBED_END in html:
        pattern = re.compile(
            re.escape(_EMBED_START) + r".*?" + re.escape(_EMBED_END),
            re.DOTALL,
        )
        html = pattern.sub(lambda m: block, html, count=1)
    else:
        # First run — inject after the opening <script> tag
        html = html.replace("<script>", f"<script>\n    {block}", 1)
    dashboard.write_text(html, encoding="utf-8")


def main():
    # Resolve output path
    script_dir = Path(__file__).resolve().parent
    output_path = script_dir / "telemetry.json"

    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])

    # Import PreActionGate
    src_dir = script_dir.parent  # azazael/src/
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    try:
        from CORE.compliance.pre_action_gate import get_pre_action_gate
        gate = get_pre_action_gate()
    except ImportError as e:
        # Fallback: write baseline telemetry if gate unavailable
        data = {
            "parallel_batches": 0,
            "sequential_violations": 0,
            "edit_count": 0,
            "compliance_ratio": 100.0,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "gate_unavailable",
            "error": str(e),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[telemetry] Gate unavailable — wrote baseline to {output_path}")
        return 0

    parallel = gate.parallel_batch_count
    sequential = gate.sequential_spawn_count
    total = parallel + sequential
    ratio = round((parallel / max(total, 1)) * 100, 1)

    data = {
        "parallel_batches": parallel,
        "sequential_violations": sequential,
        "edit_count": gate.edit_count,
        "compliance_ratio": ratio,
        "total_spawns": total,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "ok",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _stamp_dashboard(data)
    print(f"[telemetry] Wrote telemetry to {output_path}")
    print(f"  parallel_batches={parallel}, sequential_violations={sequential}, ratio={ratio}%")
    print(f"[telemetry] Stamped real data into rule_0zf_dashboard.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
