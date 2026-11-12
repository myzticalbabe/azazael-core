"""
land_all_results.py — Update BCC gap index with all new investigation results.
WRITERSHIP (MS1 2026-10-28): FIELD-UPDATE WRITER of scripts/bcc_gap_index.json.
May only touch per-gap fields: new_sources, new_evidence, last_updated,
investigation_metadata. Never add/remove gap keys (structure belongs to
scripts/build_gap_index.py) and never write *.enriched.json (belongs to
scripts/gap_metadata.py). Coexists with scripts/update_ddap_selenium_status.py,
the blessed writer for the selenium trio only (selenium_status/selenium_checked/
last_selenium_check); no other writers.
"""

import json
import time
from pathlib import Path

# Load original gap index
GAP_INDEX = Path(r"C:\Azazael\azazael\scripts\bcc_gap_index.json")
gaps = json.loads(GAP_INDEX.read_text())

# Load all new results
results = {}

# DMARC check
dmarc = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\dmarc_check\dmarc_check.json").read_text())
results['dmarc'] = dmarc

# ProPublica refresh
propublica = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\propublica_refresh\propublica_refresh.json").read_text())
results['propublica'] = propublica

# PA Courts PACFile
pacfile = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_courts_pacfile\pa_courts_pacfile.json").read_text())
results['pacfile'] = pacfile

# RTKL requests
rtkl = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\rtkl_requests\rtkl_requests_index.json").read_text())
results['rtkl'] = rtkl

# OpenSecrets/FEC
opensecrets = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\opensecrets_fec\opensecrets_fec.json").read_text())
results['opensecrets'] = opensecrets

# IRS BMF
irs = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\irs_bmf\irs_bmf_bulk.json").read_text())
results['irs'] = irs

# PA DOH/SAMHSA
pa_doh = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_doh_samhsa\pa_doh_samhsa.json").read_text())
results['pa_doh_samhsa'] = pa_doh

# Foundation/HCUP/TEDS
foundation = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\foundation_hcup_teds\foundation_hcup_teds.json").read_text())
results['foundation_hcup_teds'] = foundation

# DDAP enumeration (failed - ASP.NET WebForms)
ddap = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\ddap_enumeration\ddap_enumeration.json").read_text())
results['ddap'] = ddap

# PA DOS entity (SSL cert issue)
pa_dos = json.loads(Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_dos_entity\pa_dos_entity.json").read_text())
results['pa_dos'] = pa_dos

# FWA opinion already downloaded
fwa = {
    'pdf': 'fwa_opinion_898_mda_2017.pdf',
    'courtlistener': 'fwa_opinion_courtlistener.html',
    'text': 'fwa_opinion_text.txt',
    'gaps_targeted': [34, 50, 51, 52]
}
results['fwa'] = fwa

# Geospatial coordinates
geo = Path(r"C:\Azazael\azazael\research\bcc_analysis\bcc_geospatial_coordinates.md").read_text()
results['geospatial'] = {'markdown': geo}

# Update each gap with new evidence
gap_updates = {
    1: {'sources': ['propublica'], 'evidence': 'ProPublica API refreshed for BCC/COCA TY2023 financials'},
    9: {'sources': ['propublica'], 'evidence': 'ProPublica API refreshed for BCC/COCA TY2023 financials'},
    11: {'sources': ['propublica'], 'evidence': 'ProPublica API refreshed; Schedule J still not public'},
    12: {'sources': ['propublica'], 'evidence': 'ProPublica API refreshed; no service-line breakdown in 990'},
    22: {'sources': ['propublica'], 'evidence': 'ProPublica API refreshed; campaign finance negative on COCA institutional lines'},
    28: {'sources': ['dmarc'], 'evidence': 'DMARC check: cocaberks.org has SPF, others have none; DMARC/DKIM missing on all'},
    34: {'sources': ['pacfile', 'fwa', 'rtkl'], 'evidence': 'PACFile accessible for 898 MDA 2017 and 15-14644; FWA opinion downloaded; RTKL request generated'},
    50: {'sources': ['fwa', 'rtkl'], 'evidence': 'FWA opinion (898 MDA 2017) downloaded; RTKL for DDAP Event YQM512 generated'},
    51: {'sources': ['rtkl'], 'evidence': 'RTKL request for 523 vs 350 authorized beds generated'},
    52: {'sources': ['rtkl'], 'evidence': 'RTKL request for 2023 revocation ground generated'},
    53: {'sources': ['rtkl'], 'evidence': 'RTKL request for 709.31(b) compliance generated'},
    54: {'sources': ['rtkl'], 'evidence': 'RTKL for post-2017 re-verification generated'},
    57: {'sources': ['rtkl'], 'evidence': 'RTKL for Reading Eagle/WFMZ media sweep generated'},
    58: {'sources': ['rtkl'], 'evidence': 'RTKL for care-gap alert thresholds generated'},
    60: {'sources': ['rtkl'], 'evidence': 'RTKL for unretrieved DDAP records generated'},
    61: {'sources': ['rtkl'], 'evidence': 'RTKL for 2020 survey citing 709.26(b)(3) generated'},
    62: {'sources': ['rtkl'], 'evidence': 'RTKL for unobtained DDAP records generated'},
    38: {'sources': ['rtkl'], 'evidence': 'RTKL for Berks Prothonotary UCS search generated'},
    18: {'sources': ['rtkl'], 'evidence': 'RTKL for 15-14644 judgment amount generated'},
    10: {'sources': ['pa_dos_failed'], 'evidence': 'PA DOS API SSL cert mismatch - needs manual query'},
    17: {'sources': ['pa_dos_failed'], 'evidence': 'PA DOS API SSL cert mismatch - needs manual query'},
    25: {'sources': ['pa_dos_failed'], 'evidence': 'PA DOS API SSL cert mismatch - needs manual query'},
    14: {'sources': ['foundation'], 'evidence': 'Foundation Directory Online strategy documented for funder mapping'},
    20: {'sources': ['pa_doh'], 'evidence': 'PA DOH licensing PDF + SAMHSA N-SSATS strategy documented'},
}

# Apply updates
for gap_id, update in gap_updates.items():
    if str(gap_id) in gaps:
        gaps[str(gap_id)]['new_sources'] = update['sources']
        gaps[str(gap_id)]['new_evidence'] = update['evidence']
        gaps[str(gap_id)]['last_updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')

# Add investigation metadata to all gaps
for gap_id, gap in gaps.items():
    if 'investigation_metadata' not in gap:
        gap['investigation_metadata'] = {}
    gap['investigation_metadata']['last_full_refresh'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    gap['investigation_metadata']['sources_checked'] = list(results.keys())

# Save updated gap index
backup = GAP_INDEX.with_suffix('.json.backup')
GAP_INDEX.rename(backup)
GAP_INDEX.write_text(json.dumps(gaps, indent=2))
print(f"Updated {len(gaps)} gaps. Backup at {backup}")

# Save comprehensive investigation report
report = {
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
    'gaps_updated': len(gap_updates),
    'total_gaps': len(gaps),
    'sources_executed': list(results.keys()),
    'gap_updates': gap_updates,
    'results_summary': {k: str(v)[:200] for k, v in results.items()}
}

report_path = Path(r"C:\Azazael\azazael\research\bcc_analysis\INVESTIGATION_LANDING_REPORT.json")
report_path.write_text(json.dumps(report, indent=2, default=str))
print(f"Landing report saved to {report_path}")

print("Landing complete!")