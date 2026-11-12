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
    driver.get('https://www.ddap.pa.gov/agencies/ddap/ddap-search')
    print(f'Title: {driver.title}')
    print(f'URL: {driver.current_url}')
    
    import time
    time.sleep(3)
    
    # Find and click the DDAP Search link in the sidebar
    try:
        search_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, 'sidenav-title-ddap-search'))
        )
        print(f'Found search link: {search_link.text}')
        search_link.click()
        time.sleep(3)
        print(f'After click URL: {driver.current_url}')
    except Exception as e:
        print(f'Could not click search link: {e}')
    
    time.sleep(3)
    
    # Find all inputs
    inputs = driver.find_elements(By.TAG_NAME, 'input')
    print(f'Inputs: {len(inputs)}')
    for inp in inputs[:15]:
        print(f'  id={inp.get_attribute("id")}, name={inp.get_attribute("name")}, type={inp.get_attribute("type")}, placeholder={inp.get_attribute("placeholder")}, class={inp.get_attribute("class")}')
    
    selects = driver.find_elements(By.TAG_NAME, 'select')
    print(f'Selects: {len(selects)}')
    for sel in selects:
        print(f'  id={sel.get_attribute("id")}, name={sel.get_attribute("name")}')
    
    buttons = driver.find_elements(By.TAG_NAME, 'button')
    print(f'Buttons: {len(buttons)}')
    for btn in buttons[:15]:
        print(f'  id={btn.get_attribute("id")}, text={btn.text[:40]}')
    
    # Look for search-related elements
    search_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Search') or contains(text(), 'search') or contains(@placeholder, 'search') or contains(@placeholder, 'Search')]")
    print(f'Search-related elements: {len(search_elements)}')
    for elem in search_elements[:10]:
        print(f'  tag={elem.tag_name}, id={elem.get_attribute("id")}, name={elem.get_attribute("name")}, text={elem.text[:50]}, placeholder={elem.get_attribute("placeholder")}')
    
    # Find all forms
    forms = driver.find_elements(By.TAG_NAME, 'form')
    print(f'Forms: {len(forms)}')
    for i, form in enumerate(forms):
        print(f'  Form {i}: id={form.get_attribute("id")}, action={form.get_attribute("action")}')
        inputs = form.find_elements(By.TAG_NAME, 'input')
        for inp in inputs:
            print(f'    input: id={inp.get_attribute("id")}, name={inp.get_attribute("name")}, type={inp.get_attribute("type")}')
    
    # Look for provider/facility related elements
    provider_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Provider') or contains(text(), 'Facility') or contains(text(), 'Directory')]")
    print(f'Provider/Facility elements: {len(provider_elements)}')
    for elem in provider_elements[:10]:
        print(f'  tag={elem.tag_name}, id={elem.get_attribute("id")}, text={elem.text[:50]}')
        
finally:
    driver.quit()