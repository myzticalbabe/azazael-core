"""
pa_courts_pacfile.py — Check PA Courts PACFile for BCC dockets.
Resolves gap: 34
"""

import json
import time
import requests
from pathlib import Path

# PACFile public search (limited without registration)
PACFILE_BASE = "https://ujsportal.pacourts.us"
SEARCH_URL = f"{PACFILE_BASE}/CaseSearch"

# Known BCC docket patterns
BCC_DOCKETS = [
    "898 MDA 2017",  # Chilson/Hyser-Staub
    "15-14644",      # Berks CCP (Leinbach)
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_courts_pacfile")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def search_pacfile(docket_number):
    """Search PACFile for a docket (public search)."""
    # Public search requires form submission with anti-forgery token
    # This is a simplified check - full access requires attorney registration
    params = {
        'docketNumber': docket_number,
        'searchType': 'docket'
    }
    try:
        r = requests.get(f"{PACFILE_BASE}/CaseSearch", params=params, timeout=30)
        return {
            'docket': docket_number,
            'status_code': r.status_code,
            'accessible': 'login' not in r.url.lower() and 'register' not in r.url.lower(),
            'html_length': len(r.text)
        }
    except Exception as e:
        return {'docket': docket_number, 'error': str(e)}


def check_courtlistener(docket_number):
    """Cross-reference with CourtListener (free PACER mirror)."""
    # CourtListener API
    url = "https://www.courtlistener.com/api/rest/v3/docket/"
    params = {'search': docket_number}
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            return {
                'docket': docket_number,
                'count': data.get('count', 0),
                'results': data.get('results', [])[:3]
            }
    except Exception as e:
        return {'docket': docket_number, 'error': str(e)}
    return {'docket': docket_number, 'count': 0}


def check_all():
    """Check all known BCC dockets."""
    results = {}
    for docket in BCC_DOCKETS:
        print(f"Checking {docket}...")
        pacfile = search_pacfile(docket)
        cl = check_courtlistener(docket)
        results[docket] = {
            'pacfile': pacfile,
            'courtlistener': cl
        }
        time.sleep(1)
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'dockets': results,
        'gaps_targeted': [34]
    }
    
    (OUTPUT_DIR / 'pa_courts_pacfile.json').write_text(json.dumps(output, indent=2, default=str))
    return output


if __name__ == '__main__':
    check_all()