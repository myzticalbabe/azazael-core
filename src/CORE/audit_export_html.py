#!/usr/bin/env python3
"""audit_export_html.py - utility functions (audit_to_html).

Render an audit trail file as a readable HTML page.
"""


__all__ = [
    "audit_to_html",
]

import json
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def audit_to_html(filepath: str) -> str:
    fp = Path(filepath)
    entries = []
    with fp.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line: continue
            try:
                d = json.loads(line)
                entries.append((i, d))
            except json.JSONDecodeError as exc:
                note_failure("audit_export_html:L23", exc)
                entries.append((i, {"_corrupt": True, "_raw": line[:200]}))

    rows = ""
    for ln, d in entries:
        ts = d.get("ts", "")
        etype = d.get("type", "?")
        h = d.get("hash", "")[:12]
        cls = "corrupt" if d.get("_corrupt") else "normal"
        more = json.dumps({k:v for k,v in d.items() if k not in ("ts","type","hash","prev_hash","uuid","signature")}, indent=2)[:200]
        rows += f"<tr class='{cls}'><td>{ln}</td><td>{ts}</td><td>{etype}</td><td><code>{h}</code></td><td><pre>{more}</pre></td></tr>\n"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Audit: {fp.name}</title>
<style>body{{font-family:monospace;background:#1e1e2e;color:#cdd6f4}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:6px 10px;text-align:left;border-bottom:1px solid #45475a;font-size:13px}}
.corrupt{{background:#5c1a1a}}pre{{margin:0;max-height:60px;overflow:hidden}}
h1{{color:#cba6f7}}</style></head><body>
<h1>📋 Audit Trail: {fp.name}</h1>
<p>{len(entries)} entries</p>
<table><tr><th>#</th><th>Timestamp</th><th>Type</th><th>Hash</th><th>Data</th></tr>{rows}</table></body></html>"""

if __name__ == "__main__":
    import sys
    fp = sys.argv[1] if len(sys.argv) > 1 else str(list(Path(".audit").glob("session_*.jsonl"))[-1])
    html = audit_to_html(fp)
    out = Path(fp).with_suffix(".html")
    out.write_text(html, encoding="utf-8")
    print(f"Written to {out}")
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
