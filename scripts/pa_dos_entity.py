"""
pa_dos_entity.py — Query PA Department of State Entity API for BCC entities.
Resolves gaps: 10, 17, 18, 25
"""

import json
import time
import requests
from pathlib import Path

PA_DOS_API = "https://www.corporations.pa.gov/api/v1"
SEARCH_URL = f"{PA_DOS_API}/entities/search"

BCC_ENTITIES = [
    "Berks Counseling Center",
    "Community Care",
    "Berks Coalition",
    "TASC",
    "BCHC",
    "Berks Counseling Center Inc",
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_dos_entity")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def search_entities(query):
    """Search PA DOS entity database."""
    params = {
        'searchTerm': query,
        'pageSize': 20,
        'pageNumber': 1
    }
    try:
        r = requests.get(SEARCH_URL, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        print(f"  HTTP {r.status_code} for query: {query}")
    except Exception as e:
        print(f"  Error searching {query}: {e}")
    return None


def get_entity_details(entity_id):
    """Get detailed entity information."""
    url = f"{PA_DOS_API}/entities/{entity_id}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  Error getting details for {entity_id}: {e}")
    return None


def search_all():
    """Search for all BCC-related entities."""
    results = {}
    for query in BCC_ENTITIES:
        print(f"Searching: {query}")
        data = search_entities(query)
        if data:
            results[query] = data
            # Get details for first few results
            if 'entities' in data:
                for ent in data['entities'][:3]:
                    eid = ent.get('entityId')
                    if eid:
                        details = get_entity_details(eid)
                        if details:
                            key = f"{query}:{ent.get('name')}"
                            results[key] = details
        time.sleep(1)  # Rate limit
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'searches': results,
        'gaps_targeted': [10, 17, 18, 25]
    }
    
    (OUTPUT_DIR / 'pa_dos_entity.json').write_text(json.dumps(output, indent=2, default=str))
    return output


if __name__ == '__main__':
    search_all()