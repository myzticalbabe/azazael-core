"""
ddap_portal_selenium.py — Selenium-based enumeration of DDAP provider portal.
Resolves gaps: 50, 51, 52, 53, 54, 58, 61, 62
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

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\ddap_selenium")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def create_driver(headless: bool = True) -> Optional[webdriver.Chrome]:
    """Create Chrome WebDriver with appropriate options."""
    if not SELENIUM_AVAILABLE:
        print("Selenium not installed. Install with: pip install selenium")
        return None
    
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    # Try to find chromedriver
    try:
        driver = webdriver.Chrome(options=chrome_options)
        return driver
    except Exception as e:
        print(f"Failed to create Chrome driver: {e}")
        print("Make sure chromedriver is installed and in PATH")
        return None


def search_ddap_portal(driver: webdriver.Chrome, facility_name: str) -> List[Dict]:
    """Search DDAP portal for a specific facility."""
    results = []
    
    try:
        print(f"  Navigating to DDAP provider directory...")
        driver.get(SEARCH_URL)
        
        # Wait for page to load
        wait = WebDriverWait(driver, 20)
        
        # The DDAP portal uses ASP.NET WebForms - look for search controls
        # Common patterns: txtSearch, btnSearch, ddlCounty, etc.
        
        # Try to find search input
        search_selectors = [
            "input[id*='Search']",
            "input[id*='search']",
            "input[name*='Search']",
            "input[type='text']",
            "#txtProviderName",
            "#txtSearch",
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
        
        # Clear and enter facility name
        search_input.clear()
        search_input.send_keys(facility_name)
        time.sleep(1)
        
        # Find and click search button
        search_button_selectors = [
            "input[id*='Search'][type='submit']",
            "input[value*='Search']",
            "button[id*='Search']",
            "a[id*='Search']",
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
            time.sleep(3)  # Wait for postback
        else:
            # Try pressing Enter
            search_input.send_keys(Keys.RETURN)
            time.sleep(3)
        
        # Parse results
        # Look for result table, grid, or list
        result_selectors = [
            "table[id*='Grid']",
            "table[id*='Result']",
            "table[class*='grid']",
            "#gvProviders",
            "table tr",
            ".provider-row",
            "div[class*='provider']",
        ]
        
        for selector in result_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    print(f"  Found {len(elements)} elements with selector: {selector}")
                    for elem in elements[:20]:  # Limit to first 20
                        text = elem.text.strip()
                        if text and any(fac.lower() in text.lower() for fac in BCC_FACILITIES):
                            results.append({
                                'facility_searched': facility_name,
                                'matched_text': text[:500],
                                'selector': selector,
                                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S')
                            })
                    if results:
                        break
            except NoSuchElementException:
                continue
        
        # Also check for pagination
        pagination_selectors = [
            "a[id*='Next']",
            "a[title*='Next']",
            ".pager a",
            "#gvProviders_Pager a",
        ]
        
        for selector in pagination_selectors:
            try:
                next_btn = driver.find_element(By.CSS_SELECTOR, selector)
                if next_btn.is_displayed() and next_btn.is_enabled():
                    print(f"  Found pagination: {selector}")
            except NoSuchElementException:
                continue
                
    except TimeoutException:
        print(f"  Timeout searching for {facility_name}")
    except Exception as e:
        print(f"  Error searching for {facility_name}: {e}")
    
    return results


def enumerate_all_facilities():
    """Search for all BCC facilities."""
    driver = create_driver(headless=True)
    if not driver:
        return []
    
    all_results = []
    
    try:
        for facility in BCC_FACILITIES:
            print(f"\nSearching for: {facility}")
            results = search_ddap_portal(driver, facility)
            all_results.extend(results)
            time.sleep(2)  # Be respectful
        
        # Also try searching by county (Berks)
        print("\nSearching by county: Berks")
        # Look for county dropdown
        try:
            county_selectors = [
                "select[id*='County']",
                "select[name*='County']",
                "#ddlCounty",
            ]
            for selector in county_selectors:
                try:
                    county_dropdown = Select(driver.find_element(By.CSS_SELECTOR, selector))
                    for option in county_dropdown.options:
                        if 'berks' in option.text.lower():
                            county_dropdown.select_by_visible_text(option.text)
                            time.sleep(1)
                            # Click search
                            search_btn = driver.find_element(By.CSS_SELECTOR, "input[value*='Search'], button[id*='Search']")
                            search_btn.click()
                            time.sleep(3)
                            # Parse results
                            break
                except NoSuchElementException:
                    continue
        except Exception as e:
            print(f"  County search error: {e}")
            
    finally:
        driver.quit()
    
    return all_results


def main():
    if not SELENIUM_AVAILABLE:
        print("Selenium not available. Install with: pip install selenium")
        # Save failure report
        result = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'selenium_not_installed',
            'gaps_targeted': [50, 51, 52, 53, 54, 58, 61, 62],
            'note': 'Install selenium and chromedriver: pip install selenium; then download chromedriver matching Chrome version'
        }
        OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\ddap_selenium")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / 'ddap_selenium.json').write_text(json.dumps(result, indent=2))
        return
    
    print("Starting DDAP portal enumeration with Selenium...")
    results = enumerate_all_facilities()
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'status': 'completed',
        'providers_found': len(results),
        'providers': results,
        'gaps_targeted': [50, 51, 52, 53, 54, 58, 61, 62]
    }
    
    OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\ddap_selenium")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / 'ddap_selenium.json').write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved. Found {len(results)} provider matches.")


if __name__ == '__main__':
    main()