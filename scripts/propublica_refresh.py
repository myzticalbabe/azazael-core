"""
propublica_refresh.py — Refresh ProPublica Nonprofit data for BCC/COCA/TASC.
Resolves gaps: 1, 9, 11, 12, 22
"""

import json
import time
import os
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

PROPUBLICA_API = "https://projects.propublica.org/nonprofits/api/v2"
ORGS = {
    'BCC': '232043478',
    'COCA': '231858138', 
    'TASC': '232409777',
    'BCHC': '231858138',  # related
}

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\propublica_refresh")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_org(ein):
    """Fetch organization data from ProPublica."""
    if not requests:
        return None
    url = f"{PROPUBLICA_API}/organizations/{ein}.json"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
        print(f"  HTTP {r.status_code} for EIN {ein}")
    except Exception as e:
        print(f"  Error fetching {ein}: {e}")
    return None


def fetch_filings(ein):
    """Fetch recent filings."""
    if not requests:
        return None
    url = f"{PROPUBLICA_API}/organizations/{ein}/filings.json"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  Error fetching filings for {ein}: {e}")
    return None


def extract_financials(filing):
    """Extract key financial data from filing."""
    if not filing:
        return {}
    data = filing.get('filing', {})
    return {
        'tax_year': data.get('tax_prd_yr'),
        'revenue': data.get('totrevenue'),
        'expenses': data.get('totfuncexpns'),
        'assets': data.get('totassetsend'),
        'liabilities': data.get('totliabend'),
        'net_assets': data.get('netassetsend'),
        'schedule_j': data.get('compnsatncurrofcrsttrstkeydirct'),  # Schedule J compensation
        'officers': data.get('officers', []),
        'program_services': data.get('programservicerevenue', []),
    }


def refresh_all():
    """Refresh all organizations."""
    results = {}
    for name, ein in ORGS.items():
        print(f"Fetching {name} ({ein})...")
        org = fetch_org(ein)
        filings = fetch_filings(ein)
        
        latest = None
        if filings and filings.get('filings'):
            latest = filings['filings'][0]  # Most recent first
        
        financials = extract_financials(latest) if latest else {}
        
        results[name] = {
            'ein': ein,
            'org': org,
            'latest_filing': latest,
            'financials': financials,
            'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S')
        }
        time.sleep(1)  # Rate limit
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'organizations': results,
        'gaps_targeted': [1, 9, 11, 12, 22]
    }
    
    (OUTPUT_DIR / 'propublica_refresh.json').write_text(json.dumps(output, indent=2, default=str))
    return output


if __name__ == '__main__':
    refresh_all()