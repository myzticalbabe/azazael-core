from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')
opts.add_argument('--disable-gpu')
opts.add_argument('--window-size=1920,1080')

driver = webdriver.Chrome(options=opts)
try:
    # Try the "Searching For and Editing Services" link from Prevention page
    driver.get('https://www.pa.gov/agencies/ddap/for-professionals/data-reporting/prevention-direct-service-providers')
    print(f'Title: {driver.title}')
    print(f'URL: {driver.current_url}')
    
    import time
    time.sleep(3)
    
    # Look for the "Searching For and Editing Services" link
    try:
        link = driver.find_element(By.XPATH, "//a[contains(text(), 'Searching For and Editing Services')]")
        print(f'Found: {link.text[:80]}')
        href = link.get_attribute('href')
        print(f'Href: {href}')
        if href:
            print(f'Navigating to: {href}')
            driver.get(href)
            time.sleep(3)
            print(f'New URL: {driver.current_url}')
            print(f'New title: {driver.title}')
            
            # Check for search inputs
            inputs = driver.find_elements(By.TAG_NAME, 'input')
            print(f'Inputs: {len(inputs)}')
            for inp in inputs[:10]:
                print(f'  id={inp.get_attribute("id")}, name={inp.get_attribute("name")}, type={inp.get_attribute("type")}, placeholder={inp.get_attribute("placeholder")}')
            
            selects = driver.find_elements(By.TAG_NAME, 'select')
            print(f'Selects: {len(selects)}')
            for sel in selects:
                print(f'  id={sel.get_attribute("id")}, name={sel.get_attribute("name")}')
            
            # Look for search
            search_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Search') or contains(@placeholder, 'search') or contains(@placeholder, 'Search')]")
            print(f'Search-related elements: {len(search_elements)}')
            for elem in search_elements[:5]:
                print(f'  tag={elem.tag_name}, id={elem.get_attribute("id")}, text={elem.text[:50]}, placeholder={elem.get_attribute("placeholder")}')
            
    except Exception as e:
        print(f'Error: {e}')
    
    # Also try the Agency Directory link from DDAP Search page
    print('\n=== Trying Agency Directory ===')
    driver.get('https://www.pa.gov/agencies/ddap/ddap-search')
    time.sleep(2)
    try:
        link = driver.find_element(By.ID, 'sidenav-title-da-treatment-providers')
        # Actually let's try the Agency Directory link
        # Let me check for it
        links = driver.find_elements(By.XPATH, "//a[contains(text(), 'Agency Directory')]")
        print(f'Agency Directory links: {len(links)}')
        for l in links:
            print(f'  text={l.text[:80]}, href={l.get_attribute("href")}')
    except Exception as e:
        print(f'Error: {e}')
    
finally:
    driver.quit()