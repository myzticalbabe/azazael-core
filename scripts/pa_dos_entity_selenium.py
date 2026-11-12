"""
pa_dos_entity_selenium.py — Selenium-based PA Department of State entity search.
Resolves gaps: 10, 17, 18, 25 (SSL cert issue with API)
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

# PA DOS website
PA_DOS_URL = "https://www.corporations.pa.gov"
SEARCH_URL = "https://www.corporations.pa.gov/Search/CorpSearch"

BCC_ENTITIES = [
    "Berks Counseling Center",
    "Community Care",
    "Berks Coalition",
    "TASC",
    "BCHC",
    "Berks Counseling Center Inc",
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_dos_selenium")
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
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        return driver
    except Exception as e:
        print(f"Failed to create Chrome driver: {e}")
        return None


def search_entities(driver: webdriver.Chrome, query: str) -> List[Dict]:
    """Search PA DOS for entities matching query."""
    results = []
    
    try:
        print(f"  Navigating to PA DOS search...")
        driver.get(SEARCH_URL)
        wait = WebDriverWait(driver, 20)
        
        # Wait for page to load
        time.sleep(3)
        
        # Find search input - PA DOS uses various possible selectors
        search_selectors = [
            "input[id*='Search']",
            "input[name*='Search']",
            "input[type='text']",
            "#txtEntityName",
            "#txtSearchTerm",
        ]
        
        search_input = None
        for selector in search_selectors:
            try:
                search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                break
            except TimeoutException:
                continue
        
        if not search_input:
            print("  Could not find search input")
            return results
        
        # Clear and enter query
        search_input.clear()
        search_input.send_keys(query)
        time.sleep(1)
        
        # Find search button
        search_button_selectors = [
            "input[value*='Search']",
            "button[id*='Search']",
            "button[type='submit']",
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
            search_input.send_keys(Keys.RETURN)
        
        # Wait for results
        time.sleep(5)
        
        # Parse results - look for result table
        result_selectors = [
            "table[id*='Grid']",
            "table[id*='Result']",
            "table[class*='grid']",
            "#gvResults",
            "table tr",
        ]
        
        for selector in result_selectors:
            try:
                rows = driver.find_elements(By.CSS_SELECTOR, selector)
                if rows:
                    print(f"  Found {len(rows)} rows with selector: {selector}")
                    for row in rows[1:20]:  # Skip header, limit to 20
                        cells = row.find_elements(By.TAG_NAME, "td")
                        if len(cells) >= 2:
                            text = row.text.strip()
                            if text:
                                # Try to find entity link
                                link = None
                                try:
                                    link_elem = row.find_element(By.TAG_NAME, "a")
                                    link = link_elem.get_attribute("href")
                                except NoSuchElementException:
                                    pass
                                
                                results.append({
                                    'query': query,
                                    'row_text': text[:500],
                                    'link': link,
                                    'cells': [c.text.strip() for c in cells],
                                    'selector': selector
                                })
                    if results:
                        break
            except NoSuchElementException:
                continue
        
        # Also try to click on entity links for details
        for result in results[:5]:  # Limit to first 5
            if result.get('link'):
                try:
                    print(f"  Fetching details for: {result['link']}")
                    driver.get(result['link'])
                    time.sleep(3)
                    
                    # Parse detail page
                    detail_selectors = [
                        "table[id*='Detail']",
                        "table[id*='Entity']",
                        "div[class*='detail']",
                        "dl",
                    ]
                    
                    for selector in detail_selectors:
                        try:
                            elem = driver.find_element(By.CSS_SELECTOR, selector)
                            result['detail_html'] = elem.get_attribute("outerHTML")[:2000]
                            break
                        except NoSuchElementException:
                            continue
                    
                    # Go back to results
                    driver.back()
                    time.sleep(2)
                except Exception as e:
                    print(f"  Error fetching details: {e}")
        
    except TimeoutException:
        print(f"  Timeout searching for {query}")
    except Exception as e:
        print(f"  Error searching for {query}: {e}")
    
    return results


def search_all():
    """Search for all BCC entities."""
    driver = create_driver(headless=True)
    if not driver:
        return {}
    
    all_results = {}
    
    try:
        for query in BCC_ENTITIES:
            print(f"\nSearching: {query}")
            results = search_entities(driver, query)
            all_results[query] = results
            time.sleep(2)
    finally:
        driver.quit()
    
    return all_results


def main():
    if not SELENIUM_AVAILABLE:
        print("Selenium not available. Install with: pip install selenium")
        result = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'selenium_not_installed',
            'queries': BCC_ENTITIES,
            'note': 'Install selenium and chromedriver'
        }
        OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_dos_selenium")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / 'pa_dos_selenium.json').write_text(json.dumps(result, indent=2))
        return
    
    print("Starting PA DOS entity search with Selenium...")
    results = search_all()
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'status': 'completed',
        'searches': results,
        'gaps_targeted': [10, 17, 18, 25]
    }
    
    OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\pa_dos_selenium")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / 'pa_dos_selenium.json').write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved.")


if __name__ == '__main__':
    main()