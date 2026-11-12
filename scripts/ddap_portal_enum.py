"""
ddap_portal_enum.py — Enumerate BCC facilities in DDAP provider portal.
Resolves gaps: 50, 51, 52, 53, 54, 58, 61, 62
"""

import json
import time
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

try:
    import requests
except ImportError:
    requests = None

DDAP_PORTAL = "https://www.ddap.pa.gov"
SEARCH_URL = "https://www.ddap.pa.gov/GetHelpNow/Pages/Provider-Directory.aspx"
BCC_FACILITIES = [
    "Berks Counseling Center",
    "BCC",
    "Berks Counseling",
    "Community Care",
    "Reading Treatment",
    "TASC",
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\ddap_enumeration")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_with_retry(url, retries=3, delay=2):
    """Fetch URL with retry logic."""
    if not requests:
        return None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} for {url}")
        except Exception as e:
            print(f"  Attempt {i+1} failed: {e}")
        if i < retries - 1:
            time.sleep(delay)
    return None


def search_ddap_portal(query):
    """Search DDAP portal for a facility."""
    # The DDAP portal uses ASP.NET WebForms with __VIEWSTATE, etc.
    # This is a simplified approach - may need selenium for full interaction
    search_url = f"{DDAP_PORTAL}/GetHelpNow/Pages/Provider-Directory.aspx"
    html = fetch_with_retry(search_url)
    if not html:
        return []
    return html


def parse_provider_list(html):
    """Parse provider directory HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    providers = []
    
    # Look for table rows or list items with facility info
    for row in soup.find_all(['tr', 'li', 'div']):
        text = row.get_text(strip=True)
        if any(fac.lower() in text.lower() for fac in BCC_FACILITIES):
            providers.append({
                'raw_text': text[:500],
                'html': str(row)[:1000],
                'matched_facility': next((f for f in BCC_FACILITIES if f.lower() in text.lower()), None)
            })
    return providers


def enumerate_ddap():
    """Main enumeration function."""
    print("Fetching DDAP provider directory...")
    html = search_ddap_portal("")
    
    if not html:
        print("Failed to fetch DDAP portal - may need Selenium for ASP.NET WebForms")
        # Save what we have
        result = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'failed_fetch',
            'note': 'DDAP portal uses ASP.NET WebForms requiring JavaScript rendering',
            'gaps_targeted': [50, 51, 52, 53, 54, 58, 61, 62],
            'alternative': 'Use Selenium or DDAP API if available'
        }
        (OUTPUT_DIR / 'ddap_enumeration.json').write_text(json.dumps(result, indent=2))
        return result
    
    providers = parse_provider_list(html)
    
    result = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'status': 'success',
        'providers_found': len(providers),
        'providers': providers,
        'gaps_targeted': [50, 51, 52, 53, 54, 58, 61, 62]
    }
    
    (OUTPUT_DIR / 'ddap_enumeration.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    enumerate_ddap()