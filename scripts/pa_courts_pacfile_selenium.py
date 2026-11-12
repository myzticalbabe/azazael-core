"""
pa_courts_pacfile_selenium.py — Selenium-based parsing of PA Courts PACFile dockets.
Resolves gap: 34 (parse docket details from 898 MDA 2017 and 15-14644)
"""

import json
import time
import re
from pathlib import Path
from typing import List, Dict, Optional

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Dockets to parse
DOCKETS = [
    {"number": "898 MDA 2017", "url": "https://ujsportal.pacourts.us/CaseSearch"},
    {"number": "15-14644", "url": "https://ujsportal.pacourts.us/CaseSearch"},
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_courts_pacfile_selenium")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def create_driver(headless: bool = True) -> Optional[webdriver.Chrome]:
    """Create Chrome WebDriver."""
    if not SELENIUM_AVAILABLE:
        return None
    
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        return driver
    except Exception as e:
        print(f"Failed to create Chrome driver: {e}")
        return None


def search_docket(driver: webdriver.Chrome, docket_number: str) -> Dict:
    """Search for a docket on PACFile."""
    result = {
        'docket_number': docket_number,
        'status': 'pending',
        'case_info': {},
        'docket_entries': [],
        'parties': [],
        'error': None
    }
    
    try:
        driver.get("https://ujsportal.pacourts.us/CaseSearch")
        wait = WebDriverWait(driver, 30)
        
        # Wait for page load and find docket search input
        # PACFile uses ASP.NET WebForms
        search_selectors = [
            "input[id*='Docket']",
            "input[id*='docket']",
            "input[name*='Docket']",
            "#txtDocketNumber",
            "input[type='text']",
        ]
        
        search_input = None
        for selector in search_selectors:
            try:
                search_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                break
            except TimeoutException:
                continue
        
        if not search_input:
            result['error'] = "Could not find docket search input"
            return result
        
        # Clear and enter docket number
        search_input.clear()
        search_input.send_keys(docket_number)
        time.sleep(1)
        
        # Find search button
        search_button_selectors = [
            "input[value*='Search']",
            "input[id*='Search']",
            "button[id*='Search']",
            "#btnSearch",
        ]
        
        search_button = None
        for selector in search_button_selectors:
            try:
                search_button = driver.find_element(By.CSS_SELECTOR, selector)
                break
            except NoSuchElementException:
                continue
        
        if search_button:
            search_button.click()
        else:
            from selenium.webdriver.common.keys import Keys
            search_input.send_keys(Keys.RETURN)
        
        # Wait for results
        time.sleep(5)
        
        # Check if we got results or need to click a link
        # Look for case link or results table
        result_selectors = [
            "table[id*='Grid']",
            "table[id*='Result']",
            "a[id*='Case']",
            "table tr td a",
            "#gvResults a",
        ]
        
        case_link = None
        for selector in result_selectors:
            try:
                links = driver.find_elements(By.CSS_SELECTOR, selector)
                for link in links:
                    if docket_number.replace("-", "").replace(" ", "") in link.text.replace("-", "").replace(" ", ""):
                        case_link = link
                        break
                if case_link:
                    break
            except NoSuchElementException:
                continue
        
        if case_link:
            case_link.click()
            time.sleep(3)
        
        # Now we should be on the case detail page
        # Parse case info
        result['case_info'] = parse_case_info(driver)
        result['parties'] = parse_parties(driver)
        result['docket_entries'] = parse_docket_entries(driver)
        result['status'] = 'success'
        
    except TimeoutException:
        result['error'] = "Timeout waiting for page elements"
    except Exception as e:
        result['error'] = f"{type(e).__name__}: {e}"
    
    return result


def parse_case_info(driver: webdriver.Chrome) -> Dict:
    """Parse case information from the case detail page."""
    info = {}
    
    # Common labels and their value locations
    info_selectors = [
        ("case_number", ["span[id*='CaseNumber']", "span[id*='DocketNumber']", "td:contains('Docket') + td"]),
        ("case_type", ["span[id*='CaseType']", "td:contains('Case Type') + td"]),
        ("case_status", ["span[id*='CaseStatus']", "td:contains('Status') + td"]),
        ("filing_date", ["span[id*='FilingDate']", "td:contains('Filed') + td"]),
        ("court", ["span[id*='Court']", "td:contains('Court') + td"]),
        ("judge", ["span[id*='Judge']", "td:contains('Judge') + td"]),
    ]
    
    for key, selectors in info_selectors:
        for selector in selectors:
            try:
                if ":contains(" in selector:
                    # Handle text-based search differently
                    elements = driver.find_elements(By.XPATH, f"//td[contains(text(), '{selector.split(chr(39))[1]}')]/following-sibling::td[1]")
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                for elem in elements:
                    text = elem.text.strip()
                    if text:
                        info[key] = text
                        break
                if key in info:
                    break
            except NoSuchElementException:
                continue
    
    return info


def parse_parties(driver: webdriver.Chrome) -> List[Dict]:
    """Parse parties from the case detail page."""
    parties = []
    
    # Look for party tables
    party_selectors = [
        "table[id*='Party']",
        "table[id*='Participant']",
        "#gvParties",
        "table tr:has(td:contains('Plaintiff'), td:contains('Defendant'))",
    ]
    
    for selector in party_selectors:
        try:
            tables = driver.find_elements(By.CSS_SELECTOR, selector)
            for table in tables:
                rows = table.find_elements(By.TAG_NAME, "tr")
                for row in rows[1:]:  # Skip header
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 2:
                        parties.append({
                            'role': cells[0].text.strip(),
                            'name': cells[1].text.strip(),
                            'address': cells[2].text.strip() if len(cells) > 2 else '',
                            'attorney': cells[3].text.strip() if len(cells) > 3 else '',
                        })
                if parties:
                    break
        except NoSuchElementException:
            continue
    
    return parties


def parse_docket_entries(driver: webdriver.Chrome) -> List[Dict]:
    """Parse docket entries (the chronological list of filings/events)."""
    entries = []
    
    # Look for docket entry table
    docket_selectors = [
        "table[id*='Docket']",
        "table[id*='History']",
        "table[id*='Entry']",
        "#gvDocketEntries",
        "table[class*='docket']",
    ]
    
    for selector in docket_selectors:
        try:
            tables = driver.find_elements(By.CSS_SELECTOR, selector)
            for table in tables:
                rows = table.find_elements(By.TAG_NAME, "tr")
                for row in rows[1:]:  # Skip header
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 3:
                        entries.append({
                            'date': cells[0].text.strip(),
                            'entry_type': cells[1].text.strip() if len(cells) > 1 else '',
                            'description': cells[2].text.strip() if len(cells) > 2 else '',
                            'filed_by': cells[3].text.strip() if len(cells) > 3 else '',
                        })
                if entries:
                    break
        except NoSuchElementException:
            continue
    
    return entries


def scrape_docket(docket: Dict) -> Dict:
    """Scrape a single docket."""
    driver = create_driver(headless=True)
    if not driver:
        return {'docket_number': docket['number'], 'error': 'Selenium not available'}
    
    try:
        result = search_docket(driver, docket['number'])
        return result
    finally:
        driver.quit()


def main():
    if not SELENIUM_AVAILABLE:
        print("Selenium not available. Install with: pip install selenium")
        result = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'selenium_not_installed',
            'dockets': DOCKETS,
            'note': 'Install selenium and chromedriver'
        }
        OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_courts_pacfile_selenium")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / 'pa_courts_pacfile_selenium.json').write_text(json.dumps(result, indent=2))
        return
    
    print("Starting PA Courts PACFile Selenium scraping...")
    all_results = []
    
    for docket in DOCKETS:
        print(f"\nScraping {docket['number']}...")
        result = scrape_docket(docket)
        all_results.append(result)
        time.sleep(3)  # Be respectful
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'status': 'completed',
        'dockets_scraped': len(all_results),
        'results': all_results,
        'gaps_targeted': [34]
    }
    
    OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_courts_pacfile_selenium")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / 'pa_courts_pacfile_selenium.json').write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved to {OUTPUT_DIR}")


if __name__ == '__main__':
    main()