"""
ddap_status_update.py — Update gap index with DDAP portal status.
"""

import json
from pathlib import Path

GAP_INDEX = Path(r"C:\Azazael\azazael\scripts\bcc_gap_index.json")
gaps = json.loads(GAP_INDEX.read_text())

# Update DDAP-related gaps with Selenium findings
ddap_updates = {
    50: {'selenium_status': 'DDAP provider directory search application (apps.ddap.pa.gov) returns 503 Service Unavailable. PA.gov pages are informational only. RTKL request generated.'},
    51: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
    52: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
    53: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
    54: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
    58: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
    61: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
    62: {'selenium_status': 'DDAP provider directory search application unavailable. RTKL request generated.'},
}

for gap_id, update in ddap_updates.items():
    if str(gap_id) in gaps:
        gaps[str(gap_id)].update(update)
        gaps[str(gap_id)]['selenium_checked'] = True
        gaps[str(gap_id)]['last_selenium_check'] = '2026-10-28T12:50:00'

# Save
backup = GAP_INDEX.with_suffix('.json.backup2')
GAP_INDEX.rename(backup)
GAP_INDEX.write_text(json.dumps(gaps, indent=2))
print(f"Updated {len(ddap_updates)} DDAP gaps. Backup at {backup}")