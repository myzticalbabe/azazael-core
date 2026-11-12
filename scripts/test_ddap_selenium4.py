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
    driver.get('https://www.pa.gov/agencies/ddap/ddap-search')
    print(f'Title: {driver.title}')
    print(f'URL: {driver.current_url}')
    
    import time
    time.sleep(3)
    
    # Try JavaScript click on the links
    link_ids = [
        ('sidenav-title-da-treatment-providers', 'Treatment Providers'),
        ('sidenav-title-prevention-direct-service-providers', 'Prevention Direct Service Providers'),
        ('sidenav-title-for-problem-gambling-services-providers', 'Problem Gambling Providers'),
    ]
    
    for link_id, label in link_ids:
        print(f'\n=== Trying: {label} ({link_id}) ===')
        try:
            elem = driver.find_element(By.ID, link_id)
            print(f'Found element: tag={elem.tag_name}, text={elem.text[:80]}, href={elem.get_attribute("href")}')
            
            # Try JavaScript click
            href = elem.get_attribute('href')
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
                
                # Go back
                driver.back()
                time.sleep(2)
            else:
                print('No href attribute')
        except Exception as e:
            print(f'Error: {e}')
    
finally:
    driver.quit()