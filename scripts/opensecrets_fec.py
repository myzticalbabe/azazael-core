"""
opensecrets_fec.py — Check OpenSecrets/FEC for BCC political activity.
Resolves gap: political spending gaps
"""

import json
import time
import requests
from pathlib import Path

# OpenSecrets API (requires key, but some endpoints are public)
# FEC API is public
FEC_API = "https://api.open.fec.gov/v1"
OPENSECRETS_API = "https://www.opensecrets.org/api"

BCC_NAMES = [
    "Berks Counseling Center",
    "Community Care",
    "COCA",
    "TASC",
    "Berks County",
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\opensecrets_fec")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fec_search_committees(query):
    """Search FEC committees."""
    url = f"{FEC_API}/committees/"
    params = {'name': query, 'per_page': 20}
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        return {'error': str(e)}
    return None


def fec_candidate_search(name):
    """Search FEC candidates."""
    url = f"{FEC_API}/candidates/"
    params = {'name': name, 'per_page': 20}
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        return {'error': str(e)}
    return None


def opensecrets_org(ein):
    """Query OpenSecrets for org (needs API key for full access)."""
    # Public endpoints limited
    return {'note': 'OpenSecrets requires API key for organization lookup'}


def check_all():
    """Check FEC for BCC-related political activity."""
    results = {}
    for name in BCC_NAMES:
        print(f"Searching FEC for: {name}")
        committees = fec_search_committees(name)
        candidates = fec_candidate_search(name)
        results[name] = {
            'committees': committees,
            'candidates': candidates,
            'opensecrets': opensecrets_org(name)
        }
        time.sleep(0.5)
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'fec_results': results,
        'gaps_targeted': ['political_spending', 'campaign_finance']
    }
    
    (OUTPUT_DIR / 'opensecrets_fec.json').write_text(json.dumps(output, indent=2, default=str))
    return output


if __name__ == '__main__':
    check_all()