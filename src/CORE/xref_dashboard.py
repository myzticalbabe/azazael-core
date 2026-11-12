#!/usr/bin/env python3
"""xref_dashboard.py - utility functions (generate_html).

Cross-reference HTML dashboard generator.
"""


__all__ = [
    "generate_html",
]

import sqlite3
from pathlib import Path
from datetime import datetime
from CORE.direct_memory import DB_PATH, DirectMemory
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def generate_html() -> str:
    conn = sqlite3.connect(str(DB_PATH), timeout=3)
    conn.row_factory = sqlite3.Row
    s = DirectMemory.get_mapping_summary()
    total = s.get("total_mappings", 0)
    unlinked = DirectMemory.get_unlinked_report()
    types = conn.execute(
        "SELECT event_type, COUNT(*) as cnt FROM memories GROUP BY event_type ORDER BY cnt DESC LIMIT 15"
    ).fetchall()
    timeline = conn.execute(
        "SELECT date(timestamp) as day, COUNT(*) as cnt FROM memories GROUP BY day ORDER BY day DESC LIMIT 30"
    ).fetchall()
    conn.close()

    types_rows = "".join(
        f"<tr><td>{r['event_type']}</td><td>{r['cnt']}</td></tr>" for r in types
    )
    tl_rows = "".join(
        f"<tr><td>{r['day']}</td><td>{'█' * min(int(r['cnt']/100), 50)} {r['cnt']}</td></tr>"
        for r in reversed(timeline)
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Azazael Cross-Reference Dashboard</title>
<style>body{{font-family:monospace;max-width:900px;margin:auto;padding:20px;background:#1e1e2e;color:#cdd6f4}}
h1,h2{{color:#cba6f7}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #45475a}}
.stat{{display:inline-block;padding:12px 20px;margin:8px;background:#313244;border-radius:8px}}
.stat span{{font-size:2em;color:#a6e3a1;display:block}}</style></head><body>
<h1>🔗 Azazael Cross-Reference Dashboard</h1>
<p>Generated: {datetime.now().isoformat()}</p>
<div class="stat">Total Mappings<span>{total}</span></div>
<div class="stat">Linked DM<span>{s.get('unique_dm_sessions',0)}</span></div>
<div class="stat">Linked AJ<span>{s.get('unique_aj_sessions',0)}</span></div>
<div class="stat">Unlinked AJ<span>{unlinked.get('unlinked_aj_count',0)}</span></div>
<h2>Event Type Distribution</h2><table><tr><th>Type</th><th>Count</th></tr>{types_rows}</table>
<h2>Activity Timeline (last 30 days)</h2><table><tr><th>Day</th><th>Events</th></tr>{tl_rows}</table></body></html>"""

if __name__ == "__main__":
    html = generate_html()
    out = Path("xref_dashboard.html")
    out.write_text(html, encoding="utf-8")
    print(f"Dashboard written to {out.resolve()}")
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
